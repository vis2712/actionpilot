"""Uploaded recordings on local disk (a Docker volume shared by the API and the worker)."""

import asyncio
from pathlib import Path

from app.config import settings


def _path(key: str) -> Path:
    root = Path(settings.upload_dir).resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):  # never let a key like "../../etc/passwd" escape the folder
        raise ValueError("Invalid storage key")
    return path


async def save(key: str, data: bytes) -> None:
    path = _path(key)
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_bytes, data)


async def read(key: str) -> bytes:
    return await asyncio.to_thread(_path(key).read_bytes)


async def delete(key: str) -> None:
    await asyncio.to_thread(_path(key).unlink, missing_ok=True)
