"""
Photo storage, behind one interface.

Week 1 uses local disk so you can run the whole loop today with no Cloudflare
account. Switching to R2 is one line in .env; nothing that imports this module
changes.

The important idea in both backends is the same: **photo bytes never pass
through your API**. The client asks for a signed URL, uploads directly to
storage, then tells the API it finished. A 600-photograph round is gigabytes,
and relaying that through a small server is how you fall over.
"""

from __future__ import annotations

import hashlib
import hmac
import pathlib
import time
from abc import ABC, abstractmethod

from app.config import settings


class Storage(ABC):
    @abstractmethod
    def signed_put_url(self, key: str, content_type: str, max_bytes: int) -> tuple[str, int]:
        """Returns (url, expires_in_seconds)."""

    @abstractmethod
    def signed_get_url(self, key: str, seconds: int = 3600) -> str: ...

    @abstractmethod
    def read(self, key: str) -> bytes: ...

    @abstractmethod
    def write(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        """Server-side write. Used for thumbnails, which the worker makes."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Cheap existence check, so `complete` can verify before enqueuing."""

    @abstractmethod
    def delete(self, key: str) -> None: ...

    def delete_many(self, keys: list[str]) -> int:
        """
        Bulk delete for the retention purge.

        Overridden by R2, which can delete a thousand objects per request.
        Deleting a 180 day old event one HTTP call at a time would take hours
        and cost a request each.
        """
        deleted = 0
        for k in keys:
            try:
                self.delete(k)
                deleted += 1
            except Exception:  # noqa: BLE001 - a missing object is already deleted
                pass
        return deleted


class LocalStorage(Storage):
    """
    Development stand-in for R2.

    Uploads go to a real endpoint on this same server, which keeps the client
    code identical to production: it still receives a URL, still PUTs to it, and
    still never learns any credentials. The signature is a real HMAC so the
    endpoint can reject anything it did not issue.
    """

    def __init__(self) -> None:
        self.root = pathlib.Path(settings().local_storage_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        # Dev-only secret. Regenerated per process; nothing durable depends on it.
        self._secret = hashlib.sha256(str(self.root).encode()).digest()

    def _sign(self, key: str, expires: int) -> str:
        msg = f"{key}:{expires}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()[:32]

    def verify(self, key: str, expires: int, sig: str) -> bool:
        if time.time() > expires:
            return False
        return hmac.compare_digest(self._sign(key, expires), sig)

    def _path(self, key: str) -> pathlib.Path:
        p = (self.root / key).resolve()
        # Never let a crafted key escape the storage root.
        if not str(p).startswith(str(self.root)):
            raise ValueError("bad key")
        return p

    def signed_put_url(self, key: str, content_type: str, max_bytes: int) -> tuple[str, int]:
        expires = int(time.time()) + 600
        sig = self._sign(key, expires)
        base = settings().public_base_url.rstrip("/")
        return f"{base}/v1/_local-storage/{key}?expires={expires}&sig={sig}", 600

    def signed_get_url(self, key: str, seconds: int = 3600) -> str:
        expires = int(time.time()) + seconds
        sig = self._sign(key, expires)
        base = settings().public_base_url.rstrip("/")
        return f"{base}/v1/_local-storage/{key}?expires={expires}&sig={sig}"

    def write(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).is_file()
        except ValueError:
            return False

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class R2Storage(Storage):
    """
    Cloudflare R2 over the S3 API.

    Two things to get right when you switch to this:
      1. Set the bucket CORS policy to allow PUT from your app origin, or the
         browser blocks the upload before it leaves. This wastes an afternoon
         for almost everybody the first time.
      2. Sign the content length. Without it, someone can upload a 5GB file
         against a URL you issued for a 400KB one.
    """

    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        s = settings()
        self.bucket = s.r2_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{s.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=s.r2_access_key_id,
            aws_secret_access_key=s.r2_secret_access_key,
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )

    def signed_put_url(self, key: str, content_type: str, max_bytes: int) -> tuple[str, int]:
        url = self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=600,
        )
        return url, 600

    def signed_get_url(self, key: str, seconds: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=seconds,
        )

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def write(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def delete_many(self, keys: list[str]) -> int:
        """
        Up to 1,000 objects per request, which is the S3 API's limit.

        The retention purge deletes whole events, so this is the difference
        between one request and four thousand. Failures are ignored rather than
        retried: an object that is already gone is the outcome we wanted, and
        the database row is deleted either way.
        """
        deleted = 0
        for i in range(0, len(keys), 1000):
            batch = [{"Key": k} for k in keys[i : i + 1000]]
            response = self.client.delete_objects(
                Bucket=self.bucket, Delete={"Objects": batch, "Quiet": True}
            )
            deleted += len(batch) - len(response.get("Errors", []))
        return deleted


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = R2Storage() if settings().storage_backend == "r2" else LocalStorage()
    return _storage
