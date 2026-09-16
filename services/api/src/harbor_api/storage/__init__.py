"""Object storage for uploaded originals.

Originals are kept so a document can be re-parsed and re-indexed after a chunker or parser
change without asking the client to upload it again — the same reason `CHUNKER_VERSION`
feeds the content hash.

Local disk today; the protocol is what S3/Cloudflare R2 will implement for the VPS
deployment, so nothing above this layer changes.
"""

import hashlib
import shutil
from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> str: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


def storage_key(tenant_id: str, content_hash: str, filename: str) -> str:
    """Content-addressed, tenant-scoped: re-uploading the same file overwrites itself."""
    suffix = Path(filename).suffix.lower()[:16]
    return f"{tenant_id}/{content_hash[:2]}/{content_hash}{suffix}"


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalDiskStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        root = self._root.resolve()
        # Keys come from user-supplied filenames; refuse anything that escapes the root.
        if not path.is_relative_to(root):
            raise ValueError(f"invalid storage key: {key}")
        return path

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def clear(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)
