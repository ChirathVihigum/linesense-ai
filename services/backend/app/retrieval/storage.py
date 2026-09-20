"""Content-addressed-by-key file storage for uploaded documents (task-17-brief.md req. 1).

Uploaded bytes are stored under a key generated with ``uuid4().hex`` —
**never** derived from the caller-supplied filename, so a path like
``../../etc/passwd`` can never escape the storage directory. Two
subdirectories: ``quarantine/`` (unprocessed/rejected uploads) and
``store/`` (activated versions). ``storage_dir`` is resolved against
``services/backend`` by ``app.settings.resolve_backend_path`` (the default,
``.local/documents``, is git-ignored) unless an absolute path is given
directly (as tests do, with a ``tmp_path``).
"""

from __future__ import annotations

from pathlib import Path

QUARANTINE_DIR = "quarantine"
STORE_DIR = "store"


class DocumentStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.quarantine_dir = root / QUARANTINE_DIR
        self.store_dir = root / STORE_DIR

    def _quarantine_path(self, key: str) -> Path:
        return self.quarantine_dir / key

    def _store_path(self, key: str) -> Path:
        return self.store_dir / key

    def put(self, *, key: str, data: bytes) -> str:
        """Write ``data`` into quarantine under ``key``; returns ``key``."""
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self._quarantine_path(key).write_bytes(data)
        return key

    def read_quarantine(self, key: str) -> bytes:
        return self._quarantine_path(key).read_bytes()

    def read_store(self, key: str) -> bytes:
        return self._store_path(key).read_bytes()

    def read(self, key: str) -> bytes:
        """Read ``key`` from wherever it currently lives (store, else quarantine)."""
        store_path = self._store_path(key)
        if store_path.exists():
            return store_path.read_bytes()
        return self._quarantine_path(key).read_bytes()

    def activate(self, key: str) -> None:
        """Move ``key`` from quarantine to the permanent store."""
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._quarantine_path(key).replace(self._store_path(key))

    def delete_quarantine(self, key: str) -> None:
        """Remove a rejected upload from quarantine (no-op if already gone)."""
        self._quarantine_path(key).unlink(missing_ok=True)
