"""Persistence for accounts and settings (plain JSON on disk)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .models import Account, Settings


class AccountStore:
    """Thread-safe JSON-backed account store."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.data_dir = base_dir / "data"
        self.states_dir = self.data_dir / "states"
        self.settings_path = self.data_dir / "settings.json"
        self.accounts_path = self.data_dir / "accounts.json"

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.states_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self.settings = Settings.load(self.settings_path)
        self.accounts: list[Account] = self._load_accounts()

    # ------------------------------------------------------------------ #
    # Accounts
    # ------------------------------------------------------------------ #
    def _load_accounts(self) -> list[Account]:
        if not self.accounts_path.exists():
            return []
        try:
            raw = json.loads(self.accounts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [Account.from_dict(item) for item in raw if isinstance(item, dict)]

    def save_accounts(self) -> None:
        with self._lock:
            self.accounts_path.write_text(
                json.dumps([a.to_dict() for a in self.accounts], indent=2),
                encoding="utf-8",
            )

    def add_account(self, account: Account) -> None:
        with self._lock:
            self.accounts.append(account)
        self.save_accounts()

    def remove_account(self, account: Account) -> None:
        with self._lock:
            self.accounts = [a for a in self.accounts if a is not account]
        self.save_accounts()

    # ------------------------------------------------------------------ #
    # Settings
    # ------------------------------------------------------------------ #
    def save_settings(self) -> None:
        self.settings.save(self.settings_path)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def state_path_for(self, email: str) -> Path:
        safe = email.replace("@", "_at_").replace("/", "_")
        folder = self.states_dir / safe
        folder.mkdir(parents=True, exist_ok=True)
        return folder / "state.json"