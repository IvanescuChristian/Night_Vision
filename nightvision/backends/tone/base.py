# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Tone (gamma ramp) backend base class with safety checks and crash-safe restore."""
from __future__ import annotations

import abc
import atexit
import json
import logging
import os
import pathlib
import tempfile
import threading
import weakref

import numpy as np

log = logging.getLogger(__name__)

BACKUP_ENV = "NIGHTVISION_GAMMA_BACKUP"
BACKUP_NAME = "gamma_backup.json"
BACKUP_VERSION = 1


def backup_path() -> pathlib.Path:
    """Where the original ramp is parked while a modified ramp is live.

    Overridable with the NIGHTVISION_GAMMA_BACKUP environment variable.
    """
    override = os.environ.get(BACKUP_ENV)
    if override:
        return pathlib.Path(override)
    from ...profiles import default_config_dir  # lazy: keeps this module import-light
    return default_config_dir() / BACKUP_NAME


class ToneBackendError(Exception):
    pass


_live: "weakref.WeakSet[ToneBackend]" = weakref.WeakSet()
_live_lock = threading.Lock()


def _restore_all() -> None:
    with _live_lock:
        backends = list(_live)
    for b in backends:
        try:
            b.restore()
        except Exception:  # noqa: BLE001 - at exit, keep restoring the rest
            pass


# WHY: a crash/Ctrl-C must not leave the desktop with a modified gamma ramp.
atexit.register(_restore_all)


def validate_ramp(ramp) -> np.ndarray:
    """Return a validated (3, N) uint16 array or raise ToneBackendError.

    Inline checks (not imported from core) because a bad ramp can black out the screen.
    """
    arr = np.asarray(ramp)
    if arr.ndim != 2 or arr.shape[0] != 3 or arr.shape[1] < 2:
        raise ToneBackendError(f"ramp must have shape (3, N>=2), got {arr.shape}")
    if arr.dtype != np.uint16:
        raise ToneBackendError(f"ramp dtype must be uint16, got {arr.dtype}")
    if np.any(np.diff(arr.astype(np.int32), axis=1) < 0):
        raise ToneBackendError("unsafe ramp: not monotonic non-decreasing")
    if int(arr.max()) < 0.25 * 65535:
        raise ToneBackendError("unsafe ramp: max below 25% of range (screen would go dark)")
    if int(arr.min()) > 0.5 * 65535:
        raise ToneBackendError("unsafe ramp: min above 50% of range (screen would wash out)")
    return arr


def _json_to_u16(v) -> np.ndarray:
    """JSON ints -> uint16; out-of-range values stay int64 so validate_ramp rejects them."""
    a = np.asarray(v)
    if a.ndim == 2 and a.size and a.dtype.kind == "i" and a.min() >= 0 and a.max() <= 65535:
        return a.astype(np.uint16)
    return a


def _unlink_quiet(path: pathlib.Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def resample_ramp(ramp, n: int) -> np.ndarray:
    ramp = np.asarray(ramp)
    if ramp.shape[1] == n:
        return ramp.astype(np.uint16)
    src = np.linspace(0.0, 1.0, ramp.shape[1])
    dst = np.linspace(0.0, 1.0, n)
    out = np.stack([np.interp(dst, src, ramp[c].astype(np.float64)) for c in range(3)])
    return np.clip(np.rint(out), 0, 65535).astype(np.uint16)


class ToneBackend(abc.ABC):
    name: str = "base"
    # Opt-in: only backends that write the real hardware ramp need crash recovery.
    persist_backup: bool = False

    def __init__(self) -> None:
        self._original: np.ndarray | None = None
        self._captured = False
        self._backup_written = False
        self._applied = False
        with _live_lock:
            _live.add(self)

    @abc.abstractmethod
    def available(self) -> bool:
        """Cheap probe; must never raise."""

    def unavailable_reason(self) -> str:
        return "unavailable"

    @abc.abstractmethod
    def _read_ramp(self) -> np.ndarray | None:
        """Current hardware ramp (3, N) uint16, or None if it cannot be read."""

    @abc.abstractmethod
    def _write_ramp(self, ramp: np.ndarray) -> None:
        ...

    def _restore_original(self) -> None:
        """Default: write back the captured ramp. Backends without readback override."""
        if self._original is not None:
            self._write_ramp(self._original)

    # ---- crash recovery: the original ramp is persisted before our first write
    def _capture_original(self) -> None:
        self._original = self._read_ramp()

    def _backup_outputs(self) -> dict[str, np.ndarray]:
        """Original ramp(s) keyed by output id, for the backup file."""
        return {} if self._original is None else {"0": self._original}

    def _adopt_recovered(self, outputs: dict[str, np.ndarray]) -> None:
        """Make the recovered ramp the in-memory original (not a stale modified one)."""
        self._original = outputs["0"]

    def _write_recovered(self, outputs: dict[str, np.ndarray]) -> None:
        self._write_ramp(outputs["0"])

    def _write_backup(self) -> None:
        if not self.persist_backup or self._backup_written:
            return
        outputs = self._backup_outputs()
        if not outputs:
            return
        path = backup_path()
        payload = json.dumps({
            "version": BACKUP_VERSION, "backend": self.name,
            "outputs": {k: np.asarray(v).astype(int).tolist() for k, v in outputs.items()},
        })
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._backup_written = True

    def _delete_backup(self) -> None:
        if not self._backup_written:
            return
        self._backup_written = False
        try:
            backup_path().unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("could not remove gamma backup %s: %s", backup_path(), exc)

    def recover(self) -> bool:
        """Restore a ramp left behind by a crashed session. True if one was applied.

        A file from another backend is left alone; an unreadable or unsafe one is
        deleted (never applied).
        """
        if not self.persist_backup:
            return False
        path = backup_path()
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return False
        except OSError as exc:
            log.warning("gamma backup %s unreadable (%s); ignoring", path, exc)
            return False
        try:
            data = json.loads(raw)
            if data["version"] != BACKUP_VERSION:
                raise ToneBackendError(f"unsupported backup version {data['version']!r}")
            owner = data["backend"]
            if owner != self.name:
                log.warning("gamma backup %s belongs to backend %r, not %r; leaving it",
                            path, owner, self.name)
                return False
            outputs = {str(k): validate_ramp(_json_to_u16(v))
                       for k, v in data["outputs"].items()}
            if not outputs:
                raise ToneBackendError("no outputs in backup")
        except (ValueError, KeyError, TypeError, AttributeError, ToneBackendError) as exc:
            log.warning("gamma backup %s is invalid (%s); deleting it", path, exc)
            _unlink_quiet(path)
            return False
        try:
            self._write_recovered(outputs)
        except Exception as exc:  # noqa: BLE001 - keep the file so a later start can retry
            log.warning("gamma recovery from %s failed (%s); keeping the file", path, exc)
            return False
        self._adopt_recovered(outputs)
        self._captured = True
        _unlink_quiet(path)
        log.warning("restored display gamma left by a previous session (%s backend)", self.name)
        return True

    def apply(self, ramp) -> None:
        arr = validate_ramp(ramp)
        if not self._captured:
            # Captured once: later applies must not overwrite it with our own output.
            self._capture_original()
            self._captured = True
        self._write_backup()  # before the first non-original write
        self._write_ramp(arr)
        self._applied = True

    def restore(self) -> None:
        if not self._applied:
            return
        self._applied = False  # idempotent even if the write below fails
        self._restore_original()
        self._delete_backup()  # only reached when the restore did not raise

    def close(self) -> None:
        try:
            self.restore()
        finally:
            with _live_lock:
                _live.discard(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
