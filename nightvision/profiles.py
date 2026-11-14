# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Per-game profiles persisted as JSON."""
from __future__ import annotations

import json
import logging
import os
import pathlib
import tempfile
from dataclasses import dataclass, field
from typing import Any, Optional

from .core.params import EnhanceParams
from .core.presets import PRESETS

log = logging.getLogger(__name__)


def default_config_dir() -> pathlib.Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(pathlib.Path.home() / "AppData" / "Roaming")
        return pathlib.Path(base) / "NightVision"
    base = os.environ.get("XDG_CONFIG_HOME") or str(pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "nightvision"


def default_config_path() -> pathlib.Path:
    return default_config_dir() / "profiles.json"


@dataclass
class Profile:
    name: str
    params: EnhanceParams
    exe_names: list[str] = field(default_factory=list)
    hotkey: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "params": self.params.to_dict(),
                "exe_names": list(self.exe_names), "hotkey": self.hotkey}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Profile":
        hk = d.get("hotkey")
        return cls(name=str(d["name"]),
                   params=EnhanceParams.from_dict(d.get("params") or {}),
                   exe_names=[str(e) for e in d.get("exe_names") or []],
                   hotkey=str(hk) if hk else None)


def _default_profiles() -> dict[str, Profile]:
    profs = {n: Profile(n, p) for n, p in PRESETS.items()}
    profs["Escape from Tarkov"] = Profile(
        "Escape from Tarkov", PRESETS["tarkov_dark"], ["EscapeFromTarkov.exe"])
    profs["Rust"] = Profile("Rust", PRESETS["rust_night"], ["RustClient.exe"])
    return profs


class ProfileStore:
    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self.profiles: dict[str, Profile] = _default_profiles()
        self.active: str = "off"

    def load(self) -> "ProfileStore":
        if not self.path.exists():
            self.profiles, self.active = _default_profiles(), "off"
            return self
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            profs = {}
            for d in data["profiles"]:
                p = Profile.from_dict(d)
                profs[p.name] = p
            active = data.get("active", "off")
            self.profiles = profs
            self.active = active if active in profs else next(iter(profs), "off")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            backup = self.path.with_name(self.path.name + ".corrupt")
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            log.warning("profiles file %s unreadable (%s); backed up to %s, using defaults",
                        self.path, e, backup)
            self.profiles, self.active = _default_profiles(), "off"
        return self

    def save(self) -> None:
        """Write to a temp file in the same directory, then os.replace, so a
        crash mid-write leaves the previous file intact rather than truncated."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": 1, "active": self.active,
             "profiles": [p.to_dict() for p in self.profiles.values()]},
            indent=2)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + ".",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, name: str) -> Optional[Profile]:
        return self.profiles.get(name)

    def upsert(self, profile: Profile) -> None:
        self.profiles[profile.name] = profile

    def delete(self, name: str) -> bool:
        removed = self.profiles.pop(name, None) is not None
        if removed and self.active == name:
            self.active = "off" if "off" in self.profiles else next(iter(self.profiles), "off")
        return removed

    def match_exe(self, exe_name: str) -> Optional[Profile]:
        want = os.path.basename(exe_name).lower()
        for p in self.profiles.values():
            if any(e.lower() == want for e in p.exe_names):
                return p
        return None
