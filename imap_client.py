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
        self._sent_folder: str | None = None
        self._sent_reply_cache: dict[str, bool] = {}

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

    def _parse_email_addr(self, value: str | None) -> str:
        if not value:
            return ""
        _, addr = email.utils.parseaddr(value)
        return addr.lower()

    def _is_from_user(self, from_value: str | None) -> bool:
        from_addr = self._parse_email_addr(from_value)
        user_addr = self._parse_email_addr(self.user)
        return bool(from_addr) and from_addr == user_addr

    def _find_sent_folder(self) -> str | None:
        if self._sent_folder is not None:
            return self._sent_folder

        conn = self.conn
        try:
            folders = conn.list_folders()
        except Exception:
            self._sent_folder = None
            return None

        # Prefer exact "Sent" (case-insensitive)
        for flags, _delim, name in folders:
            if name.lower() == "sent":
                self._sent_folder = name
                return name

        # Check IMAP \Sent flag
        for flags, _delim, name in folders:
            flag_set = {f.decode() if isinstance(f, bytes) else f for f in flags}
            if "\\Sent" in flag_set:
                self._sent_folder = name
                return name

        # Fallback common names
        candidates = [
            "Sent Items",
            "Sent Mail",
            "Sent Messages",
            "INBOX.Sent",
            "[Gmail]/Sent Mail",
        ]
        lower_map = {name.lower(): name for _f, _d, name in folders}
        for cand in candidates:
            hit = lower_map.get(cand.lower())
            if hit:
                self._sent_folder = hit
                return hit

        self._sent_folder = None
        return None

    def _parse_thread_response(self, data) -> list[list[int]]:
        if not data:
            return []
        if isinstance(data, list):
            parts = []
            for item in data:
                if item is None:
                    continue
                if isinstance(item, bytes):
                    parts.append(item.decode("utf-8", errors="replace"))
                else:
                    parts.append(str(item))
            s = " ".join(parts)
        else:
            if data is None:
                return []
            if isinstance(data, bytes):
                s = data.decode("utf-8", errors="replace")
            else:
                s = str(data)

        tokens = []
        num = ""
        for ch in s:
            if ch.isdigit():
                num += ch
            else:
                if num:
                    tokens.append(int(num))
                    num = ""
                if ch in ("(", ")"):
                    tokens.append(ch)
        if num:
            tokens.append(int(num))

        stack: list[list] = []
        threads: list[list] = []
        for tok in tokens:
            if tok == "(":
                stack.append([])
            elif tok == ")":
                if not stack:
                    continue
                node = stack.pop()
                if stack:
                    stack[-1].append(node)
                else:
                    threads.append(node)
            else:
                if not stack:
                    # Single UID outside parens
                    threads.append([tok])
                else:
                    stack[-1].append(tok)

        def flatten(node) -> list[int]:
            if isinstance(node, int):
                return [node]
            out: list[int] = []
            for item in node:
                out.extend(flatten(item))
            return out

        return [flatten(t) for t in threads if t]

    def _thread_uids(self, conn: imapclient.IMAPClient) -> list[list[int]]:
        def flatten_thread(node) -> list[int]:
            if isinstance(node, int):
                return [node]
            if isinstance(node, (list, tuple)):
                out: list[int] = []
                for item in node:
                    out.extend(flatten_thread(item))
                return out
            return []

        # Prefer imapclient THREAD if available
        try:
            threads = conn.thread("REFERENCES", "ALL")
            if isinstance(threads, (bytes, str)):
                return self._parse_thread_response(threads)
            if isinstance(threads, list):
                if threads and all(isinstance(t, int) for t in threads):
                    return [list(map(int, threads))]
                out: list[list[int]] = []
                for t in threads:
                    if isinstance(t, (int, list, tuple)):
                        out.append(flatten_thread(t))
                    else:
                        out.extend(self._parse_thread_response(t))
                return out
        except Exception:
            pass

        # Raw IMAP THREAD
        try:
            typ, data = conn._imap.uid("THREAD", "REFERENCES", "UTF-8", "ALL")
            if typ == "OK":
                return self._parse_thread_response(data)
        except Exception:
            pass

        return []

    def _message_date(self, data) -> datetime | None:
        internal = data.get(b"INTERNALDATE")
        if isinstance(internal, datetime):
            return internal
        env = data.get(b"ENVELOPE")
        if env and env.date:
            return env.date
        return None

    def _date_key(self, dt: datetime | None) -> float:
        if not dt:
            return -1.0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()

    def _has_sent_reply(self, message_id: str) -> bool:
        if not message_id:
            return False
        if message_id in self._sent_reply_cache:
            return self._sent_reply_cache[message_id]

        sent_folder = self._find_sent_folder()
        if not sent_folder:
            self._sent_reply_cache[message_id] = False
            return False

        conn = self.conn
        try:
            conn.select_folder(sent_folder, readonly=True)
            criteria = [
                "OR",
                "HEADER",
                "In-Reply-To",
                message_id,
                "HEADER",
                "References",
                message_id,
            ]
            uids = conn.search(criteria)
            found = bool(uids)
            self._sent_reply_cache[message_id] = found
            return found
        except Exception:
            self._sent_reply_cache[message_id] = False
            return False

    def _thread_needs_reply(self, latest_data) -> bool:
        env = latest_data.get(b"ENVELOPE")
        if env and env.from_:
            addr = env.from_[0]
            name = addr.name.decode("utf-8", errors="replace") if addr.name else ""
            mailbox = addr.mailbox.decode("utf-8", errors="replace") if addr.mailbox else ""
            host = addr.host.decode("utf-8", errors="replace") if addr.host else ""
            from_addr = f"{name} <{mailbox}@{host}>" if name else f"{mailbox}@{host}"
        else:
            from_addr = ""

        if self._is_from_user(from_addr):
            return False

        flags = latest_data.get(b"FLAGS") or []
        flag_set = {f.decode() if isinstance(f, bytes) else f for f in flags}
        if "\\Answered" in flag_set:
            return False

        msg_id = ""
        if env and env.message_id:
            msg_id = env.message_id.decode("utf-8", errors="replace")
        return not self._has_sent_reply(msg_id)

    def list_inbox(self) -> list[dict]:
        """List all messages in INBOX with metadata, grouped by thread."""
        conn = self.conn
        conn.select_folder("INBOX", readonly=True)
        uids = conn.search(["ALL"])
        if not uids:
            return []

        messages = conn.fetch(
            uids,
            [
                "ENVELOPE",
                "FLAGS",
                "INTERNALDATE",
                "BODY.PEEK[TEXT]<0.300>",
            ],
        )

        threads = self._thread_uids(conn)
        if not threads:
            threads = [[uid] for uid in uids]

        result = []
        for thread in threads:
            thread_items = [uid for uid in thread if uid in messages]
            if not thread_items:
                continue

            # Determine latest message in thread
            latest_uid = None
            latest_date = None
            for uid in thread_items:
                date = self._message_date(messages[uid])
                if latest_date is None or self._date_key(date) > self._date_key(latest_date):
                    latest_date = date
                    latest_uid = uid

            if latest_uid is None:
                continue

            data = messages[latest_uid]
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
            if latest_date:
                date_str = latest_date.strftime("%Y-%m-%d %H:%M")

            # Snippet from body peek
            snippet = ""
            body_key = b"BODY[TEXT]<0>"
            if body_key in data and data[body_key]:
                raw = data[body_key]
                if isinstance(raw, bytes):
                    snippet = raw.decode("utf-8", errors="replace")[:150].strip()
                    snippet = " ".join(snippet.split())

            needs_reply = self._thread_needs_reply(data)

            result.append({
                "uid": latest_uid,
                "thread_uids": thread_items,
                "from": from_addr,
                "subject": subject,
                "date": date_str,
                "snippet": snippet,
                "needs_reply": needs_reply,
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

        # Thread context
        conn.select_folder("INBOX", readonly=True)
        threads = self._thread_uids(conn)
        thread_uids = [uid]
        for thread in threads:
            if uid in thread:
                thread_uids = [t for t in thread if isinstance(t, int)]
                break

        thread_context = []
        meta = {}
        if thread_uids:
            meta = conn.fetch(
                thread_uids,
                [
                    "ENVELOPE",
                    "FLAGS",
                    "INTERNALDATE",
                    "BODY.PEEK[TEXT]<0.300>",
                ],
            )
            # Sort by date ascending
            items = []
            for tuid, data in meta.items():
                env = data.get(b"ENVELOPE")
                if not env:
                    continue
                date = self._message_date(data)
                date_str = date.strftime("%Y-%m-%d %H:%M") if date else ""
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

                snippet = ""
                body_key = b"BODY[TEXT]<0>"
                if body_key in data and data[body_key]:
                    raw = data[body_key]
                    if isinstance(raw, bytes):
                        snippet = raw.decode("utf-8", errors="replace")[:150].strip()
                        snippet = " ".join(snippet.split())

                items.append((date, {
                    "uid": tuid,
                    "from": from_addr,
                    "subject": subject,
                    "date": date_str,
                    "snippet": snippet,
                }))

            items.sort(key=lambda x: self._date_key(x[0]))
            thread_context = [i[1] for i in items]

        # Determine needs_reply based on latest message in thread
        needs_reply = False
        if thread_uids and thread_context:
            # Find latest by date in thread_context
            latest_uid = thread_context[-1]["uid"]
            latest_data = None
            if thread_uids:
                latest_data = meta.get(latest_uid)
            if latest_data:
                needs_reply = self._thread_needs_reply(latest_data)

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
            "thread_uids": thread_uids,
            "thread_context": thread_context,
            "needs_reply": needs_reply,
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
