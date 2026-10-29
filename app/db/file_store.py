"""Storage for uploaded workbooks.

`FileStore` is the interface the rest of the app uses. GridFS is the first
implementation. If GridFS causes problems on Cosmos DB, add an Azure Blob Storage
implementation with the same three methods and switch to it in app/main.py.
"""

from typing import Protocol

import gridfs
from bson import ObjectId
from pymongo.database import Database


class FileNotFound(Exception):
    pass


class FileStore(Protocol):
    def save(self, data: bytes, filename: str, content_type: str) -> str: ...
    def load(self, file_id: str) -> bytes: ...
    def delete(self, file_id: str) -> None: ...


class GridFSFileStore:
    def __init__(self, db: Database, bucket: str = "files") -> None:
        if db.client.__class__.__module__.startswith("mongomock"):
            from mongomock.gridfs import enable_gridfs_integration

            enable_gridfs_integration()
        self._fs = gridfs.GridFS(db, collection=bucket)

    def save(self, data: bytes, filename: str, content_type: str) -> str:
        return str(self._fs.put(data, filename=filename, content_type=content_type))

    def load(self, file_id: str) -> bytes:
        try:
            return self._fs.get(ObjectId(file_id)).read()
        except gridfs.errors.NoFile as exc:
            raise FileNotFound(file_id) from exc

    def delete(self, file_id: str) -> None:
        self._fs.delete(ObjectId(file_id))


class MemoryFileStore:
    """In-memory store for tests."""

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    def save(self, data: bytes, filename: str, content_type: str) -> str:
        file_id = str(ObjectId())
        self._files[file_id] = data
        return file_id

    def load(self, file_id: str) -> bytes:
        if file_id not in self._files:
            raise FileNotFound(file_id)
        return self._files[file_id]

    def delete(self, file_id: str) -> None:
        self._files.pop(file_id, None)
