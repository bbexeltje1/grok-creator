"""Data models: Account and Settings, plus helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Account:
    """A single saved Grok / x.ai account."""

    email: str
    password: str
    first_name: str = ""
    last_name: str = ""
    proxy: str = ""
    status: str = "active"          # active | failed | pending
    created_at: str = field(default_factory=_now_iso)
    state_file: str = ""            # path to saved Playwright storage state

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Account":
        allowed = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class Settings:
    """Persisted app settings."""

    proxy_enabled: bool = False
    proxy_list: list[str] = field(default_factory=list)
    selected_proxy: str = ""

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, path: Path) -> "Settings":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            proxy_enabled=bool(data.get("proxy_enabled", False)),
            proxy_list=list(data.get("proxy_list", [])),
            selected_proxy=str(data.get("selected_proxy", "")),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2),
            encoding="utf-8",
        )