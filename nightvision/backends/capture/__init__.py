# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Screen-capture backends (OS capture APIs only; display-side, no injection)."""
from .base import CaptureError, CaptureSource
from .select import select_capture_backend

__all__ = ["CaptureError", "CaptureSource", "select_capture_backend"]
