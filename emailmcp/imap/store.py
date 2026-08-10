"""The four mailbox operations, one ``MailStore`` per configured mailbox.

All per-mailbox state — the connection, the discovered folder names and the
caches — lives on the instance, so mailboxes never share anything.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import threading
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import imapclient

from ..config import Mailbox
from ..refs import format_ref
from . import folders, mime, threads
from .connection import Connection
from .quoting import QuotedMessage, build_html_quote, build_text_quote

METADATA_FIELDS = ["ENVELOPE", "FLAGS", "INTERNALDATE", "BODY.PEEK[TEXT]<0.300>"]

# Window in which an identical draft save is treated as a client retry.
DRAFT_DEDUPE_SECONDS = 10


class MessageNotFound(ValueError):
    pass


class MailStore:
    def __init__(self, mailbox: Mailbox, password: str) -> None:
        self.mailbox = mailbox
        self._conn = Connection(mailbox.host, mailbox.port, mailbox.address, password)
        self._sent_folder: str | None = None
        self._sent_folder_resolved = False
        self._drafts_folder: str | None = None
        self._sent_reply_cache: dict[str, bool] = {}
        self._draft_dedupe: dict[str, float] = {}
        self._draft_lock = threading.Lock()

    @property
    def key(self) -> str:
        return self.mailbox.key

    @property
    def address(self) -> str:
        return self.mailbox.address

    def ref(self, uid: int) -> str:
        return format_ref(self.key, uid)

    def close(self) -> None:
        self._conn.close()

    # --- folders -----------------------------------------------------------

    def _resolve_sent(self) -> str | None:
        if self._sent_folder_resolved:
            return self._sent_folder
        if self.mailbox.sent_folder:
            self._sent_folder = self.mailbox.sent_folder
        else:
            with self._conn.session() as conn:
                self._sent_folder = folders.find_sent(conn)
        self._sent_folder_resolved = True
        return self._sent_folder

    def _resolve_drafts(self) -> str:
        if self._drafts_folder is not None:
            return self._drafts_folder
        if self.mailbox.drafts_folder:
            self._drafts_folder = self.mailbox.drafts_folder
        else:
            with self._conn.session() as conn:
                self._drafts_folder = folders.find_drafts(conn) or "Drafts"
        return self._drafts_folder

    # --- reply detection ---------------------------------------------------

    def _has_sent_reply(self, message_id: str) -> bool:
        if not message_id:
            return False
        if message_id in self._sent_reply_cache:
            return self._sent_reply_cache[message_id]

        sent = self._resolve_sent()
        if not sent:
            self._sent_reply_cache[message_id] = False
            return False

        try:
            with self._conn.folder(sent, readonly=True) as conn:
                found = bool(
                    conn.search(
                        ["OR", "HEADER", "In-Reply-To", message_id, "HEADER", "References", message_id]
                    )
                )
        except Exception:
            found = False

        self._sent_reply_cache[message_id] = found
        return found

    def _needs_reply(self, data: dict) -> bool:
        envelope = data.get(b"ENVELOPE")
        sender = mime.envelope_from(envelope) if envelope is not None else ""
        if mime.is_from(sender, self.address):
            return False
        if "\\Answered" in mime.flag_set(data):
            return False
        message_id = mime.envelope_message_id(envelope) if envelope is not None else ""
        return not self._has_sent_reply(message_id)

    # --- shaping -----------------------------------------------------------

    def _summary(self, uid: int, data: dict) -> dict:
        envelope = data.get(b"ENVELOPE")
        return {
            "id": self.ref(uid),
            "mailbox": self.key,
            "from": mime.envelope_from(envelope),
            "subject": mime.envelope_subject(envelope),
            "date": mime.format_date(mime.message_date(data)),
            "snippet": mime.snippet(data),
        }

    # --- operations --------------------------------------------------------

    def list_inbox(self) -> list[dict]:
        with self._conn.folder(self.mailbox.inbox_folder, readonly=True) as conn:
            uids = conn.search(["ALL"])
            if not uids:
                return []

            messages = conn.fetch(uids, METADATA_FIELDS)
            groups = threads.fetch_thread_uids(conn) or [[uid] for uid in uids]

            result = []
            for group in groups:
                members = [uid for uid in group if uid in messages]
                if not members:
                    continue

                latest_uid = max(members, key=lambda uid: mime.date_key(mime.message_date(messages[uid])))
                data = messages[latest_uid]
                if data.get(b"ENVELOPE") is None:
                    continue

                entry = self._summary(latest_uid, data)
                entry["needs_reply"] = self._needs_reply(data)
                entry["thread_ids"] = [self.ref(uid) for uid in members]
                result.append(entry)

            return result

    def _fetch_message(self, conn, uid: int) -> email.message.Message:
        fetched = conn.fetch([uid], ["RFC822"])
        raw = fetched.get(uid, {}).get(b"RFC822") if uid in fetched else None
        if not raw:
            raise MessageNotFound(
                f"message {self.ref(uid)} not found in {self.mailbox.inbox_folder}"
            )
        return email.message_from_bytes(raw, policy=email.policy.default)

    def read_email(self, uid: int) -> dict:
        with self._conn.folder(self.mailbox.inbox_folder, readonly=True) as conn:
            msg = self._fetch_message(conn, uid)

            thread_uids = threads.group_for(threads.fetch_thread_uids(conn), uid)
            meta = conn.fetch(thread_uids, METADATA_FIELDS) if thread_uids else {}

            entries = []
            for thread_uid, data in meta.items():
                if data.get(b"ENVELOPE") is None:
                    continue
                entries.append((mime.date_key(mime.message_date(data)), thread_uid, data))
            entries.sort(key=lambda item: item[0])

            thread_context = [self._summary(thread_uid, data) for _key, thread_uid, data in entries]

            needs_reply = False
            if entries:
                needs_reply = self._needs_reply(entries[-1][2])

        return {
            "id": self.ref(uid),
            "mailbox": self.key,
            "from": mime.decode_header(msg["From"]),
            "to": mime.decode_header(msg["To"]),
            "cc": mime.decode_header(msg.get("Cc", "")),
            "subject": mime.decode_header(msg["Subject"]),
            "date": mime.decode_header(msg["Date"]),
            "body": mime.strip_quoted_reply(mime.parse_body(msg)),
            "message_id": msg.get("Message-ID", ""),
            "references": msg.get("References", ""),
            "thread_ids": [self.ref(thread_uid) for thread_uid in thread_uids],
            "thread_context": thread_context,
            "needs_reply": needs_reply,
        }

    def save_reply_draft(self, uid: int, body: str) -> str:
        with self._conn.folder(self.mailbox.inbox_folder, readonly=True) as conn:
            original = self._fetch_message(conn, uid)

        subject = mime.decode_header(original["Subject"]) or "(no subject)"
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        message_id = original.get("Message-ID", "")
        prior_refs = original.get("References", "")
        references = " ".join(part for part in (prior_refs, message_id) if part)
        to_addr = mime.decode_header(original["From"])

        quoted = [QuotedMessage(uid, original)]
        text_quote = build_text_quote(quoted, uid)
        text_body = "\n\n".join(part for part in (body.strip(), text_quote) if part)

        if mime.extract_html(original):
            html_quote = build_html_quote(quoted, uid)
            html_body = "<br><br>".join(
                part for part in (mime.text_to_html(body.strip()), html_quote) if part
            )
            draft = MIMEMultipart("alternative")
            draft.attach(MIMEText(text_body, "plain", "utf-8"))
            draft.attach(MIMEText(html_body, "html", "utf-8"))
        else:
            draft = MIMEText(text_body, "plain", "utf-8")

        draft["From"] = self.address
        draft["To"] = to_addr
        draft["Subject"] = subject
        draft["Date"] = email.utils.formatdate(localtime=True)
        if message_id:
            draft["In-Reply-To"] = message_id
        if references:
            draft["References"] = references

        if not self._append_deduped(draft, f"{uid}:{hash(text_body)}"):
            return (
                f"Draft already saved (duplicate suppressed) in {self.mailbox.display}: "
                f"reply to '{subject}' → {to_addr}"
            )
        return f"Draft saved in {self.mailbox.display}: reply to '{subject}' → {to_addr}"

    def save_new_draft(self, to_addr: str, subject: str, body: str) -> str:
        draft = MIMEText(body.strip(), "plain", "utf-8")
        draft["From"] = self.address
        draft["To"] = to_addr
        draft["Subject"] = subject
        draft["Date"] = email.utils.formatdate(localtime=True)

        self._append_draft(draft)
        return f"Draft saved in {self.mailbox.display}: new email to '{to_addr}'"

    # --- draft plumbing ----------------------------------------------------

    def _is_duplicate(self, signature: str) -> bool:
        previous = self._draft_dedupe.get(signature)
        if not previous:
            return False
        return (datetime.now(timezone.utc).timestamp() - previous) < DRAFT_DEDUPE_SECONDS

    def _append_deduped(self, draft, signature: str) -> bool:
        """Append unless an identical draft landed inside the dedupe window.

        The check, the record and the append are one critical section: a client
        that retries while the first save is still in flight is exactly the case
        the window exists for, and testing before recording would let both
        through. The record is rolled back if the append fails, so a save that
        did not land can be retried immediately.
        """
        with self._draft_lock:
            if self._is_duplicate(signature):
                return False
            self._draft_dedupe[signature] = datetime.now(timezone.utc).timestamp()
            try:
                self._append_draft(draft)
            except Exception:
                self._draft_dedupe.pop(signature, None)
                raise
            return True

    def _append_draft(self, draft) -> None:
        folder = self._resolve_drafts()
        with self._conn.session() as conn:
            conn.append(
                folder,
                draft.as_bytes(),
                flags=[imapclient.DRAFT],
                msg_time=datetime.now(timezone.utc),
            )
