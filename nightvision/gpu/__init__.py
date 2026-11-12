# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Optional GPU (OpenGL via moderngl) frame enhancement.

Importing this package must stay cheap and must not require moderngl; the
import happens inside processor.py so callers can probe availability.
"""
