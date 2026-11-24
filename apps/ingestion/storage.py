from __future__ import annotations

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


def delete_key(key: str) -> None:
    if key and default_storage.exists(key):
        default_storage.delete(key)
