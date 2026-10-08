"""Small-demo durability: local SQLite, exclusively leased Azure Blob checkpoints.

Provision one private block blob with the exact bootstrap_marker(binding) bytes.
Missing, corrupt or mismatched blobs never initialize an empty database. Every
transaction commits locally, then conditionally replaces the remote blob under a
60-second lease renewed every 15 seconds BEFORE the caller can receive a response. Ambiguous
upload failures permanently poison this process; restart restores authoritative
remote state. This is bounded to 16 MiB, low traffic, and one active revision.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .live_auth import DependencyUnavailable
from .live_state import DurableState

MAX_STATE_BYTES = 16 * 1024 * 1024
OPERATION_TIMEOUT = 15
logger = logging.getLogger(__name__)


def bootstrap_marker(binding):
    return ("KX_BROKER_STATE_V1\n" + binding).encode("utf-8")


class LeasedBlobCheckpoint:
    def __init__(self, blob, *, clock=time.monotonic, renew_interval=15):
        self.blob, self.clock, self.renew_interval = blob, clock, renew_interval
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.failed, self.closed = False, False
        self.lease, self.thread, self.etag = None, None, None
        self.deadline = 0

    def healthy(self):
        return not self.failed and not self.closed and self.lease is not None and self.clock() < self.deadline

    def _require_healthy(self):
        if not self.healthy():
            self.failed = True
            raise DependencyUnavailable("durable blob lease unavailable; restart required")

    def open(self):
        from azure.core import MatchConditions
        try:
            started = self.clock()
            self.lease = self.blob.acquire_lease(lease_duration=60, timeout=OPERATION_TIMEOUT)
            self.deadline = started + 45
            self._require_healthy()
            properties = self.blob.get_blob_properties(lease=self.lease, timeout=OPERATION_TIMEOUT)
            if (not 1 <= properties.size <= MAX_STATE_BYTES
                    or not isinstance(properties.etag, str) or not properties.etag):
                raise ValueError("invalid checkpoint size")
            self.etag = properties.etag
            data = self.blob.download_blob(lease=self.lease, etag=self.etag,
                                           match_condition=MatchConditions.IfNotModified,
                                           max_concurrency=1, timeout=OPERATION_TIMEOUT).readall()
            if len(data) != properties.size or len(data) > MAX_STATE_BYTES:
                raise ValueError("checkpoint size mismatch")
            self._require_healthy()
            self.thread = threading.Thread(target=self._keep_alive, name="broker-blob-lease", daemon=True)
            self.thread.start()
            return data
        except Exception as exc:
            self.failed = True
            self.close()
            raise DependencyUnavailable("durable blob unavailable; existing private bootstrap blob required") from exc

    def renew(self):
        with self.lock:
            self._require_healthy()
            started = self.clock()
            try:
                self.lease.renew(timeout=OPERATION_TIMEOUT)
                if self.clock() >= started + 45:
                    raise TimeoutError("lease renewal exceeded safety deadline")
                self.deadline = started + 45
            except Exception as exc:
                self.failed = True
                raise DependencyUnavailable("durable blob lease renewal failed; restart required") from exc

    def _keep_alive(self):
        while not self.stopped.wait(self.renew_interval):
            try:
                self.renew()
            except DependencyUnavailable:
                logger.error("broker_blob_lease_renewal_failed: restart required")
                return

    def save(self, data):
        from azure.core import MatchConditions
        with self.lock:
            try:
                self.renew()
                if not data or len(data) > MAX_STATE_BYTES:
                    raise ValueError("checkpoint exceeds the demo storage limit")
                response = self.blob.upload_blob(
                    data, blob_type="BlockBlob", overwrite=True, length=len(data), lease=self.lease,
                    etag=self.etag, match_condition=MatchConditions.IfNotModified,
                    max_concurrency=1, validate_content=True, timeout=OPERATION_TIMEOUT)
                etag = response.get("etag")
                if not isinstance(etag, str) or not etag:
                    raise ValueError("checkpoint did not return an ETag")
                self._require_healthy()
                self.etag = etag
            except Exception as exc:
                self.failed = True
                raise DependencyUnavailable("durable checkpoint not confirmed; response withheld, restart required") from exc

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stopped.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=20)
        with self.lock:
            if self.lease is not None:
                try:
                    self.lease.release(timeout=OPERATION_TIMEOUT)
                except Exception as exc:
                    logger.warning("broker_blob_lease_release_failed: %s; lease expires naturally",
                                   type(exc).__name__)
            try:
                self.blob.close()
            except Exception as exc:
                logger.warning("broker_blob_client_close_failed: %s", type(exc).__name__)


class BlobDurableState(DurableState):
    def __init__(self, path, binding, checkpoint):
        self.checkpoint, self.failed = checkpoint, False
        self.local_path = Path(path).with_name(Path(path).name + "." + uuid.uuid4().hex)
        try:
            data = checkpoint.open()
            bootstrap = data == bootstrap_marker(binding)
            if not bootstrap and not data.startswith(b"SQLite format 3\x00"):
                raise ValueError("invalid or mismatched broker checkpoint")
            with self.local_path.open("xb") as stream:
                if not bootstrap:
                    stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(self.local_path, 0o600)
            super().__init__(self.local_path, binding, initialize=bootstrap)
        except BaseException:
            checkpoint.close()
            self._remove_local()
            raise

    @contextmanager
    def transaction(self):
        with self.lock:
            if not self.healthy():
                raise DependencyUnavailable("durable checkpoint unavailable; restart required")
            try:
                self.checkpoint.renew()
                with super().transaction():
                    yield
            except DependencyUnavailable:
                if not self.checkpoint.healthy():
                    self.failed = True
                raise
            except BaseException:
                self.failed = True
                raise
            try:
                self.checkpoint.save(self.db.serialize())
            except Exception as exc:
                self.failed = True
                raise DependencyUnavailable("durable checkpoint unavailable; restart required") from exc
            except BaseException:
                self.failed = True
                raise

    def healthy(self):
        return not self.failed and not getattr(self, "closed", False) and self.checkpoint.healthy()

    def _remove_local(self):
        for path in (self.local_path, *(Path(str(self.local_path) + suffix)
                                       for suffix in ("-journal", "-wal", "-shm"))):
            path.unlink(missing_ok=True)

    def close(self):
        try:
            super().close()
        finally:
            self.checkpoint.close()
            self._remove_local()
