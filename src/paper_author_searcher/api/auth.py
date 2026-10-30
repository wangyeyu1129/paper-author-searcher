"""API keys: callers send ``Authorization: Bearer <key>``; the server keeps only each key's SHA-256.

A key is shown once, when it is created (``paper-search-api create-key``). The keys file maps a key ID to the
hash; jobs belong to the key ID that created them, and one caller never sees another's jobs.
"""

import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

KEY_PREFIX = 'pas_'


@dataclass(frozen=True)
class ApiKey:
    id: str
    name: str
    sha256: str


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode('utf-8')).hexdigest()


class ApiKeys:
    def __init__(self, keys: list[ApiKey]) -> None:
        self.keys = list(keys)

    @classmethod
    def load(cls, path: str | Path) -> 'ApiKeys':
        file = Path(path)
        if not file.exists():
            return cls([])
        data = json.loads(file.read_text(encoding='utf-8'))
        return cls([ApiKey(item['id'], item.get('name', ''), item['sha256']) for item in data.get('keys', [])])

    def save(self, path: str | Path) -> None:
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps({'keys': [vars(key) for key in self.keys]}, indent=2)
        # Hashes are not secrets, but the file decides who may call the API: owner-only.
        descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(text)

    def create(self, name: str) -> tuple[ApiKey, str]:
        """A new key: its record (stored) and its secret (shown to the caller once, never stored)."""
        secret = KEY_PREFIX + secrets.token_urlsafe(32)
        key = ApiKey('key_' + secrets.token_hex(6), name, digest(secret))
        self.keys.append(key)
        return key, secret

    def revoke(self, key_id: str) -> bool:
        before = len(self.keys)
        self.keys = [key for key in self.keys if key.id != key_id]
        return len(self.keys) < before

    def authenticate(self, headers: Mapping[str, str]) -> ApiKey | None:
        """The key a request's Authorization header carries, if it is a current one."""
        scheme, _, secret = (headers.get('authorization') or '').partition(' ')
        if scheme.lower() != 'bearer' or not secret.strip():
            return None
        presented = digest(secret.strip())
        match = None
        for key in self.keys:
            # Compare against every key, so the time taken does not reveal which one matched.
            if hmac.compare_digest(presented, key.sha256):
                match = key
        return match
