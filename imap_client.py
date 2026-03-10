import os
import email
import email.utils
import email.policy
from email.mime.text import MIMEText
from datetime import datetime, timezone

import html2text
import imapclient

h2t = html2text.HTML2Text()
h2t.ignore_links = False
h2t.ignore_images = True
h2t.body_width = 0


class IMAPClient:
    def __init__(self):
        self.host = os.environ["IMAP_HOST"]
        self.port = int(os.environ.get("IMAP_PORT", "993"))
        self.user = os.environ["IMAP_USER"]
        self.password = os.environ["IMAP_PASS"]
        self._conn: imapclient.IMAPClient | None = None

    def _connect(self) -> imapclient.IMAPClient:
        conn = imapclient.IMAPClient(self.host, port=self.port, ssl=True)
        conn.login(self.user, self.password)
        return conn

    @property
    def conn(self) -> imapclient.IMAPClient:
        if self._conn is None:
            self._conn = self._connect()
            return self._conn
        try:
            self._conn.noop()
        except Exception:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = self._connect()
        return self._conn

    def _parse_body(self, msg: email.message.Message) -> str:
        """Extract readable body from email message. Prefer plain text, fall back to HTML."""
        plain = None
        html_part = None

        if not msg.is_multipart():
            ct = msg.get_content_type()
            payload = msg.get_payload(decode=True)
            if payload is None:
                return ""
            charset = msg.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                text = payload.decode("utf-8", errors="replace")
            if ct == "text/plain":
                return text.strip()
            if ct == "text/html":
                return h2t.handle(text).strip()
            return ""

        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and plain is None:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        plain = payload.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        plain = payload.decode("utf-8", errors="replace")
            elif ct == "text/html" and html_part is None:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        html_part = payload.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        html_part = payload.decode("utf-8", errors="replace")

        if plain:
            return plain.strip()
        if html_part:
            return h2t.handle(html_part).strip()
        return ""

    def _strip_quoted_reply(self, body: str) -> str:
        """Remove quoted reply chains to keep context clean."""
        lines = body.splitlines()
        result = []
        for line in lines:
            # Common reply markers
            if line.strip().startswith(">"):
                continue
            if line.strip().startswith("On ") and line.strip().endswith("wrote:"):
                break
            if line.strip().startswith("----") and "Original Message" in line:
                break
            if line.strip().startswith("From:") and len(result) > 3:
                break
            result.append(line)
        return "\n".join(result).strip()

    def _decode_header(self, value: str | None) -> str:
        if not value:
            return ""
        parts = email.header.decode_header(value)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(part)
        return " ".join(decoded)

    def list_inbox(self) -> list[dict]:
        """List all messages in INBOX with metadata."""
        conn = self.conn
        conn.select_folder("INBOX", readonly=True)
        uids = conn.search(["ALL"])
        if not uids:
            return []

        messages = conn.fetch(uids, ["ENVELOPE", "BODY.PEEK[TEXT]<0.300>"])
        result = []
        for uid, data in messages.items():
            env = data.get(b"ENVELOPE")
            if not env:
                continue

            # Extract sender
            from_addr = ""
            if env.from_ and len(env.from_) > 0:
                addr = env.from_[0]
                name = addr.name.decode("utf-8", errors="replace") if addr.name else ""
                mailbox = addr.mailbox.decode("utf-8", errors="replace") if addr.mailbox else ""
                host = addr.host.decode("utf-8", errors="replace") if addr.host else ""
                from_addr = f"{name} <{mailbox}@{host}>" if name else f"{mailbox}@{host}"

            subject = self._decode_header(
                env.subject.decode("utf-8", errors="replace") if env.subject else "(no subject)"
            )

            date_str = ""
            if env.date:
                date_str = env.date.strftime("%Y-%m-%d %H:%M")

            # Snippet from body peek
            snippet = ""
            body_key = b"BODY[TEXT]<0>"
            if body_key in data and data[body_key]:
                raw = data[body_key]
                if isinstance(raw, bytes):
                    snippet = raw.decode("utf-8", errors="replace")[:150].strip()
                    snippet = " ".join(snippet.split())

            result.append({
                "uid": uid,
                "from": from_addr,
                "subject": subject,
                "date": date_str,
                "snippet": snippet,
            })

        return result

    def read_email(self, uid: int) -> dict:
        """Read full email by UID."""
        conn = self.conn
        conn.select_folder("INBOX", readonly=True)
        messages = conn.fetch([uid], ["RFC822"])

        if uid not in messages:
            raise ValueError(f"Email with UID {uid} not found")

        raw = messages[uid][b"RFC822"]
        msg = email.message_from_bytes(raw, policy=email.policy.default)

        body = self._parse_body(msg)
        body_clean = self._strip_quoted_reply(body)

        return {
            "uid": uid,
            "from": self._decode_header(msg["From"]),
            "to": self._decode_header(msg["To"]),
            "cc": self._decode_header(msg.get("Cc", "")),
            "subject": self._decode_header(msg["Subject"]),
            "date": self._decode_header(msg["Date"]),
            "body": body_clean,
            "message_id": msg.get("Message-ID", ""),
            "references": msg.get("References", ""),
        }

    def save_draft(self, reply_to_uid: int, body: str) -> str:
        """Create a reply draft and save to Drafts folder."""
        original = self.read_email(reply_to_uid)

        # Build reply subject
        subject = original["subject"]
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        # Build references chain
        refs = original["references"]
        msg_id = original["message_id"]
        if refs and msg_id:
            references = f"{refs} {msg_id}"
        elif msg_id:
            references = msg_id
        else:
            references = ""

        # Extract reply-to address (the original sender)
        to_addr = original["from"]

        # Build the draft message
        draft = MIMEText(body, "plain", "utf-8")
        draft["From"] = self.user
        draft["To"] = to_addr
        draft["Subject"] = subject
        draft["Date"] = email.utils.formatdate(localtime=True)
        if msg_id:
            draft["In-Reply-To"] = msg_id
        if references:
            draft["References"] = references

        # Save to Drafts via IMAP APPEND
        conn = self.conn
        conn.append("Drafts", draft.as_bytes(), flags=[imapclient.DRAFT],
                     msg_time=datetime.now(timezone.utc))

        return f"Draft saved: reply to '{original['subject']}' → {to_addr}"
