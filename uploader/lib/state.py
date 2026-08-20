from functools import cached_property
import json
from pathlib import Path
from lib.util import NoSuchKey

DEFAULTS1 = {
    "uidvalidity": 0,
    "last_processed_uid": 0,
    "message_count": 0,
    "uncompressed_bytes": 0,
    "compressed_bytes": 0,
}

DEFAULTS = {
    "last_processed_uid": 417880,
    # "uidvalidity": 1308597530,
    "uidvalidity": 0,
    "message_count": 0,
    "compressed_bytes": 0,
    "uncompressed_bytes": 0,
}

class State:
    defaults = DEFAULTS

    def __init__(self, config, mailbox="INBOX"):
        self.email_address = config.email_address
        self.store = config.store
        self.mailbox = mailbox
        self._state = DEFAULTS | self.pull_remote_state()

    @cached_property
    def path(self) -> Path:
        return Path(self.email_address) / self.mailbox / "state.json"

    def pull_remote_state(self):
        try:
            state = self.store.read(self.path)
            return json.loads(state)
        except NoSuchKey:
            return DEFAULTS

    def push_to_remote(self):
        return self.store.write(self.path, json.dumps(self._state))

    for attr in DEFAULTS.keys():
        def _make_func(name=attr):
            def func(self, val=None):
                if val is not None: self._state[name] = val
                return self._state[name]
            return func
        locals()[attr] = _make_func()
