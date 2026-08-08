from functools import cached_property
import json
from pathlib import Path

DEFAULTS = {
    "uidvalidity": 0,
    "last_processed_uid": 0,
    "message_count": 0,
    "uncompressed_bytes": 0,
    "compressed_bytes": 0,
}

class State:
    def __init__(self, config, mailbox="INBOX"):
        self.config = config
        self.mailbox = mailbox
        self._state = DEFAULTS | self.pull_remote_state()

    @cached_property
    def path(self) -> Path:
        return Path(self.config.email_address) / self.mailbox / "state.json"

    def pull_remote_state(self):
        return {
            "last_processed_uid": 417050,
            "uidvalidity": 1308597530,

            "message_count": 0,
            "compressed_bytes": 0,
            "uncompressed_bytes": 0,
        }

    def push_to_remote(self):
        self.path.write_text(json.dumps(self._state))

    _attrs = [
        "last_processed_uid",
        "uidvalidity",
        "message_count",
        "compressed_bytes",
        "uncompressed_bytes",
    ]

    for attr in _attrs:
        def _make_func(name=attr):
            def func(self, val=None):
                if val is not None:
                    self._state[name] = val
                return self._state[name]
            return func
        locals()[attr] = _make_func()
