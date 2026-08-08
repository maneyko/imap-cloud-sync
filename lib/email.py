import email
from email import policy
from functools import cached_property
import re

from lib.util import zstd_compress


class EmailRFC822:
    def __init__(self, config, header: bytes, body: bytes):
        self.config = config
        self.header = header
        self.body = body

    @cached_property
    def internaldate(self) -> datetime:
        date_str = re.search(br'INTERNALDATE "([^"]+)"', self.header).group(1)
        return email.utils.parsedate_to_datetime(date_str.decode()).astimezone(self.config.timezone)

    @cached_property
    def epoch(self) -> int:
        return self.internaldate.timestamp()

    @cached_property
    def uid(self):
        return int(re.search(br'UID (\d+)', self.header).group(1))

    @cached_property
    def date(self):
        if self.msg is None: return
        timestamp = msg["date"]
        if timestamp is None:
            if received := msg["received"]:
                timestamp = received.split(";")[-1].strip()
        if timestamp:
            return email.utils.parsedate_to_datetime(timestamp).astimezone(self.config.timezone)

    @cached_property
    def msg(self):
        try:
            return email.message_from_bytes(self.body, policy=policy.default)
        except Exception:
            pass

    @cached_property
    def message_id(self):
        if msg_id := self.msg["message-id"]:
            return msg_id.strip().strip("<>")

    @cached_property
    def metadata(self):
        result = {"size": len(self.body), "internaldate": self.internaldate.isoformat()}
        if self.msg is not None:
            result.update({
                "message_id": self.message_id,
                "subject": self.msg["subject"],
            })
            result.update({field.replace("-", "_"): self._getaddresses(field) for field in [
                "from", "to", "bcc", "reply-to", "in-reply-to", "references"
            ]})
        return result

    @cached_property
    def body_compressed(self):
        return zstd_compress(self.body)

    def _getaddresses(self, header_name):
        try:
            return [
                {"display_name": display_name, "email_address": addr}
                for display_name, addr in email.utils.getaddresses(self.msg.get_all(header_name, []))
            ]
        except Exception:
            return []
