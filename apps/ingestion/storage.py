from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage


def save_bytes(key: str, data: bytes) -> str:
    if default_storage.exists(key):
        default_storage.delete(key)
    return default_storage.save(key, ContentFile(data))


def read_bytes(key: str) -> bytes:
    with default_storage.open(key, "rb") as fh:
        return fh.read()


def save_text(key: str, text: str) -> str:
    return save_bytes(key, text.encode("utf-8"))


def read_text(key: str) -> str:
    return read_bytes(key).decode("utf-8")


def get_local_path(key: str) -> Path | None:
    """Return local filesystem path when backed by local temp storage, else None."""
    if not key:
        return None
    try:
        path = Path(default_storage.path(key))
        return path if path.exists() else None
    except NotImplementedError:
        return None


def delete_key(key: str) -> None:
    if key and default_storage.exists(key):
        default_storage.delete(key)


def cleanup_temp_files(*keys: str) -> None:
    """Remove temporary local files once processing reaches a terminal state."""
    if getattr(settings, "USE_S3_STORAGE", False):
        return
    if not getattr(settings, "CLEANUP_TEMP_FILES_AFTER_PROCESSING", True):
        return
    for key in keys:
        delete_key(key)
