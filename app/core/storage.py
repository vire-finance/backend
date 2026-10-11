"""Private receipt storage; database references never contain signed URLs or keys."""
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Protocol
from uuid import UUID

import httpx

from app.core.config import settings

BUCKET = "receipts"
EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}


class StorageError(Exception):
    """Only safe, fixed diagnostic codes may leave the storage boundary."""

    def __init__(self, code="unavailable", *, retryable=False, uncertain=False):
        super().__init__("Receipt storage operation failed")
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain


class ReceiptStorage(Protocol):
    def save(self, content: bytes, user_id: UUID, filename: str, mime: str) -> str: ...
    def delete(self, reference: str) -> None: ...
    def materialize(self, reference: str, expected_size: int): ...


def _filename(filename):
    extension = Path(filename).suffix.lower()
    if extension not in EXTENSIONS or str(UUID(Path(filename).stem)) + extension != filename:
        raise StorageError("invalid_reference")
    return filename


class LocalReceiptStorage:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def _path(self, reference):
        path = Path(reference).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise StorageError("missing")
        return path

    def save(self, content, user_id, filename, mime):
        path = self.root / _filename(filename)
        created = False
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                created = True
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        except OSError:
            if created:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise StorageError("unavailable", retryable=True) from None
        return str(path)

    def delete(self, reference):
        try:
            self._path(reference).unlink(missing_ok=True)
        except OSError:
            raise StorageError("unavailable", retryable=True) from None

    @contextmanager
    def materialize(self, reference, expected_size):
        path = self._path(reference)
        if not path.is_file():
            raise StorageError("missing")
        # Legacy development records are preserved, including their file paths.
        yield path


class SupabaseReceiptStorage:
    def __init__(self, url, secret, *, timeout=15, client=None):
        self.url = url.rstrip("/") + "/storage/v1"
        self.timeout = timeout
        self.client = client
        self.headers = {"apikey": secret}
        # New sb_secret keys belong only in apikey. Legacy service-role JWTs
        # additionally need Authorization; never send the VIRE user's token.
        if not secret.startswith("sb_secret_"):
            try:
                import jwt
                claims = jwt.decode(secret, options={"verify_signature": False})
                if claims.get("role") != "service_role":
                    raise ValueError()
            except Exception:
                raise StorageError("configuration") from None
            self.headers["Authorization"] = "Bearer " + secret

    def _client(self):
        return nullcontext(self.client) if self.client is not None else httpx.Client(
            timeout=self.timeout, follow_redirects=False, trust_env=False)

    @staticmethod
    def _key(reference):
        # Restrict keys to generated UUID paths in the fixed private bucket.
        prefix = "supabase://receipts/"
        if not reference.startswith(prefix):
            raise StorageError("invalid_reference")
        parts = reference[len(prefix):].split("/")
        try:
            if len(parts) != 2 or str(UUID(parts[0])) != parts[0]:
                raise ValueError()
            _filename(parts[1])
        except (ValueError, AttributeError):
            raise StorageError("invalid_reference") from None
        return "/".join(parts)

    @staticmethod
    def _check(response):
        if 200 <= response.status_code < 300:
            return
        missing = response.status_code == 404
        # Storage can return HTTP 400 with statusCode=404 / NoSuchKey.
        if response.status_code == 400:
            content = bytearray()
            for chunk in response.iter_bytes(chunk_size=1024):
                content.extend(chunk)
                if len(content) > 4096:
                    break
            try:
                data = json.loads(content)
                missing = str(data.get("statusCode")) == "404" or data.get("code") == "NoSuchKey"
            except (ValueError, AttributeError):
                pass
        raise StorageError("missing" if missing else "unavailable",
                           retryable=response.status_code == 429 or response.status_code >= 500,
                           uncertain=response.status_code >= 500)

    def validate_bucket(self):
        try:
            with self._client() as client:
                response = client.get(self.url + "/bucket/receipts", headers=self.headers,
                                      timeout=self.timeout, follow_redirects=False)
                self._check(response)
                data = response.json()
                if data.get("id") != BUCKET or data.get("public") is not False:
                    raise StorageError("bucket_not_private")
        except (httpx.HTTPError, ValueError, AttributeError):
            raise StorageError("unavailable", retryable=True) from None

    def save(self, content, user_id, filename, mime):
        key = str(UUID(str(user_id))) + "/" + _filename(filename)
        reference = "supabase://receipts/" + key
        try:
            with self._client() as client:
                response = client.post(self.url + "/object/receipts/" + key,
                    headers={**self.headers, "x-upsert": "false", "cache-control": "max-age=0"},
                    files={"file": (filename, content, mime)}, timeout=self.timeout,
                    follow_redirects=False)
                self._check(response)
        except httpx.HTTPError:
            raise StorageError("unavailable", retryable=True, uncertain=True) from None
        return reference

    def delete(self, reference):
        key = self._key(reference)
        try:
            with self._client() as client:
                response = client.request("DELETE", self.url + "/object/receipts",
                    headers=self.headers, json={"prefixes": [key]}, timeout=self.timeout,
                    follow_redirects=False)
                try:
                    self._check(response)
                except StorageError as error:
                    if error.code != "missing":
                        raise
        except httpx.HTTPError:
            raise StorageError("unavailable", retryable=True) from None

    @contextmanager
    def materialize(self, reference, expected_size):
        key = self._key(reference)
        if not 0 < expected_size <= settings.MAX_UPLOAD_SIZE_BYTES:
            raise StorageError("invalid_size")
        try:
            # Per-operation directories prevent concurrent reads/OCR from sharing
            # files. The context removes them on success, failure or disconnect.
            with tempfile.TemporaryDirectory(prefix="vire-receipt-") as directory:
                path = Path(directory) / Path(key).name
                deadline = time.monotonic() + self.timeout
                with self._client() as client:
                    with client.stream("GET", self.url + "/object/authenticated/receipts/" + key,
                            headers=self.headers, timeout=self.timeout, follow_redirects=False) as response:
                        self._check(response)
                        total = 0
                        with path.open("xb") as output:
                            for chunk in response.iter_bytes(chunk_size=65536):
                                total += len(chunk)
                                if total > expected_size:
                                    raise StorageError("invalid_size")
                                if time.monotonic() > deadline:
                                    raise StorageError("download_timeout", retryable=True)
                                output.write(chunk)
                        if total != expected_size:
                            raise StorageError("invalid_size")
                yield path
        except (httpx.HTTPError, OSError):
            raise StorageError("unavailable", retryable=True) from None


def get_receipt_storage() -> ReceiptStorage:
    if settings.APP_ENV == "production":
        return SupabaseReceiptStorage(settings.SUPABASE_URL,
            settings.SUPABASE_SECRET_KEY.get_secret_value(), timeout=settings.STORAGE_TIMEOUT_SECONDS)
    return LocalReceiptStorage(settings.UPLOAD_DIR)


def storage_for_reference(reference) -> ReceiptStorage:
    if reference.startswith("supabase://"):
        if settings.APP_ENV != "production":
            raise StorageError("configuration")
        return get_receipt_storage()
    return LocalReceiptStorage(settings.UPLOAD_DIR)


@contextmanager
def materialize_document(document):
    reference = document.storage_path
    if reference.startswith("supabase://"):
        key = SupabaseReceiptStorage._key(reference)
        if key != f"{document.user_id}/{document.stored_filename}" or Path(document.stored_filename).stem != str(document.id):
            raise StorageError("invalid_reference")
    with storage_for_reference(reference).materialize(reference, document.file_size) as path:
        yield path


def validate_production_storage():
    if settings.APP_ENV == "production":
        get_receipt_storage().validate_bucket()
