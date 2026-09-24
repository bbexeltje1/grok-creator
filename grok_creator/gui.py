"""CustomTkinter GUI for the Grok / x.ai account creator."""

from __future__ import annotations

import asyncio
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Optional

import customtkinter as ctk

from .creator import open_existing_account, run_creator
from .models import Account
from .proxy_manager import get_working_proxies
from .store import AccountStore


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class GrokCreatorApp(ctk.CTk):
    def __init__(self, base_dir: Path):
        super().__init__()

        self.title("Grok / x.ai Account Creator")
        self.geometry("1040x720")
        self.minsize(900, 600)

        self.store = AccountStore(base_dir)
        self._queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._busy = False

        self._build_top_bar()
        self._build_log_area()
        self._build_account_table()
        self._build_status_bar()

        self._refresh_table()
        self.after(100, self._poll_queue)

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def _build_top_bar(self) -> None:
        bar = ctk.CTkFrame(self, corner_radius=8)
        bar.pack(fill="x", padx=10, pady=(10, 6))

        # Row 1: create + proxy controls
        row1 = ctk.CTkFrame(bar, fg_color="transparent")
        row1.pack(fill="x", padx=10, pady=(10, 4))

        self.create_btn = ctk.CTkButton(
            row1,
            text="➕  Create Account",
            width=180,
            height=38,
            command=self._on_create_account,
        )
        self.create_btn.pack(side="left")

        self.proxy_var = tk.BooleanVar(value=self.store.settings.proxy_enabled)
        self.proxy_check = ctk.CTkCheckBox(
            row1,
            text="Use proxy",
            variable=self.proxy_var,
            command=self._save_proxy_settings,
        )
        self.proxy_check.pack(side="left", padx=(20, 10))

        ctk.CTkLabel(row1, text="Proxy:").pack(side="left")

        self.proxy_combo = ctk.CTkComboBox(
            row1,
            values=self.store.settings.proxy_list or [""],
            width=320,
            command=lambda _v: self._save_proxy_settings(),
        )
        if self.store.settings.selected_proxy:
            self.proxy_combo.set(self.store.settings.selected_proxy)
        self.proxy_combo.pack(side="left", padx=6)

        # Row 2: proxy management
        row2 = ctk.CTkFrame(bar, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=(4, 10))

        self.gen_proxy_btn = ctk.CTkButton(
            row2,
            text="🌐  Generate proxies",
            width=180,
            command=self._on_generate_proxies,
        )
        self.gen_proxy_btn.pack(side="left")

        self.remove_proxy_btn = ctk.CTkButton(
            row2,
            text="Remove selected",
            width=140,
            fg_color="#444",
            hover_color="#555",
            command=self._on_remove_proxy,
        )
        self.remove_proxy_btn.pack(side="left", padx=8)

        ctk.CTkLabel(
            row2,
            text="(proxies are tested before being added)",
            text_color="#888",
        ).pack(side="left", padx=6)

    def _build_log_area(self) -> None:
        frame = ctk.CTkFrame(self, corner_radius=8)
        frame.pack(fill="both", expand=False, padx=10, pady=6)

        ctk.CTkLabel(
            frame,
            text="Live log",
            anchor="w",
            font=ctk.CTkFont(weight="bold"),
        ).pack(fill="x", padx=12, pady=(8, 0))

        self.log_box = ctk.CTkTextbox(
            frame,
            height=180,
            wrap="word",
            activate_scrollbars=True,
        )
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.log_box.configure(state="disabled")

    def _build_account_table(self) -> None:
        frame = ctk.CTkFrame(self, corner_radius=8)
        frame.pack(fill="both", expand=True, padx=10, pady=6)

        ctk.CTkLabel(
            frame,
            text="Accounts",
            anchor="w",
            font=ctk.CTkFont(weight="bold"),
        ).pack(fill="x", padx=12, pady=(8, 0))

        columns = ("email", "password", "status", "created", "proxy")
        self.tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            height=10,
        )
        for col, label, width in [
            ("email", "Email", 260),
            ("password", "Password", 180),
            ("status", "Status", 90),
            ("created", "Created", 160),
            ("proxy", "Proxy", 220),
        ]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w")

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Treeview",
            background="#2b2b2b",
            fieldbackground="#2b2b2b",
            foreground="#eaeaea",
            rowheight=24,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#3a3a3a",
            foreground="#eaeaea",
            relief="flat",
        )
        style.map("Treeview", background=[("selected", "#1f6aa5")])

        self.tree.pack(fill="both", expand=True, padx=12, pady=(4, 4))

        actions = ctk.CTkFrame(frame, fg_color="transparent")
        actions.pack(fill="x", padx=12, pady=(0, 10))

        ctk.CTkButton(
            actions,
            text="▶  Play / Open",
            width=140,
            command=self._on_play,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            actions,
            text="📋  Copy credentials",
            width=160,
            command=self._on_copy,
        ).pack(side="left", padx=6)

        ctk.CTkButton(
            actions,
            text="🗑  Delete",
            width=110,
            fg_color="#8b2c2c",
            hover_color="#a33",
            command=self._on_delete,
        ).pack(side="left", padx=6)

    def _build_status_bar(self) -> None:
        self.status_var = tk.StringVar(value="Ready.")
        ctk.CTkLabel(
            self,
            textvariable=self.status_var,
            anchor="w",
            height=26,
        ).pack(fill="x", padx=14, pady=(0, 6))

    # ------------------------------------------------------------------ #
    # Logging / queue plumbing
    # ------------------------------------------------------------------ #
    def _log(self, msg: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self.status_var.set(msg.splitlines()[-1] if msg else "")
        print(msg)

    def _queue_log(self, msg: str) -> None:
        self._queue.put(("log", msg))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "proxies":
                    self._on_proxies_found(payload)  # type: ignore[arg-type]
                elif kind == "created":
                    self._on_created(payload)  # type: ignore[arg-type]
                elif kind == "error":
                    self._on_error(str(payload))
                elif kind == "busy_off":
                    self._set_busy(False)
        except queue.Empty:
            pass
        finally:
            self.after(100, self._poll_queue)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.create_btn.configure(state=state)
        self.gen_proxy_btn.configure(state=state)

    # ------------------------------------------------------------------ #
    # Proxy controls
    # ------------------------------------------------------------------ #
    def _save_proxy_settings(self) -> None:
        self.store.settings.proxy_enabled = bool(self.proxy_var.get())
        self.store.settings.selected_proxy = self.proxy_combo.get()
        self.store.save_settings()

    def _on_remove_proxy(self) -> None:
        sel = self.proxy_combo.get()
        if sel and sel in self.store.settings.proxy_list:
            self.store.settings.proxy_list.remove(sel)
            self.store.settings.selected_proxy = ""
            self.store.save_settings()
            self._reload_proxy_combo()
            self._log(f"Removed proxy: {sel}")

    def _reload_proxy_combo(self) -> None:
        values = self.store.settings.proxy_list or [""]
        self.proxy_combo.configure(values=values)
        self.proxy_combo.set(self.store.settings.selected_proxy or values[0])

    def _on_generate_proxies(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._log("Searching for working proxies (this may take a minute)…")

        def worker() -> None:
            try:
                working = get_working_proxies(
                    target_count=5,
                    countries=["US", "GB", "DE", "NL"],
                    protocols=["socks5", "http"],
                    max_threads=30,
                    log=self._queue_log,
                )
                self._queue.put(("proxies", working))
            except Exception as exc:
                self._queue.put(("error", f"Proxy search failed: {exc}"))
            finally:
                self._queue.put(("busy_off", None))

        threading.Thread(target=worker, daemon=True).start()

    def _on_proxies_found(self, proxies: list) -> None:
        added = 0
        for p in proxies:
            if p not in self.store.settings.proxy_list:
                self.store.settings.proxy_list.append(p)
                added += 1
        if not self.store.settings.selected_proxy and self.store.settings.proxy_list:
            self.store.settings.selected_proxy = self.store.settings.proxy_list[0]
        self.store.save_settings()
        self._reload_proxy_combo()
        self._log(f"Added {added} new working proxy(ies).")

    # ------------------------------------------------------------------ #
    # Account table
    # ------------------------------------------------------------------ #
    def _refresh_table(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for idx, acct in enumerate(self.store.accounts):
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    acct.email,
                    acct.password,
                    acct.status,
                    acct.created_at,
                    acct.proxy or "—",
                ),
            )

    def _selected_account(self) -> Optional[Account]:
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            idx = int(sel[0])
        except (ValueError, TypeError):
            return None
        if 0 <= idx < len(self.store.accounts):
            return self.store.accounts[idx]
        return None

    # ------------------------------------------------------------------ #
    # Create account
    # ------------------------------------------------------------------ #
    def _on_create_account(self) -> None:
        if self._busy:
            return
        self._set_busy(True)

        use_proxy = bool(self.proxy_var.get())
        proxy = self.proxy_combo.get() if use_proxy else ""
        if use_proxy and not proxy:
            self._log("Proxy is enabled but none is selected — running without proxy.")

        self._log("=" * 60)
        self._log("Starting account creation…")

        def worker() -> None:
            try:
                # Pre-allocate a temp state file inside data/states/_pending
                tmp_state = self.store.states_dir / "_pending" / "state.json"
                tmp_state.parent.mkdir(parents=True, exist_ok=True)

                creds = asyncio.run(
                    run_creator(
                        proxy_url=proxy or None,
                        log=self._queue_log,
                        state_path=tmp_state,
                    )
                )

                # Move the state file into the account-specific folder
                final_state = self.store.state_path_for(creds["email"])
                try:
                    if tmp_state.exists():
                        final_state.write_bytes(tmp_state.read_bytes())
                        creds["state_file"] = str(final_state)
                except OSError:
                    creds["state_file"] = ""

                creds["proxy"] = proxy
                self._queue.put(("created", creds))
            except Exception as exc:
                self._queue.put(("error", f"Creation failed: {exc}"))
            finally:
                self._queue.put(("busy_off", None))

        threading.Thread(target=worker, daemon=True).start()

    def _on_created(self, payload: dict) -> None:
        try:
            email = payload["email"]
            password = payload["password"]
            first_name = payload.get("first_name", "")
            last_name = payload.get("last_name", "")
            state_file = payload.get("state_file", "")
            proxy = payload.get("proxy", "")

            account = Account(
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                proxy=proxy,
                status="active",
                state_file=state_file,
            )
            self.store.add_account(account)
            self._refresh_table()
            self._log(f"Saved account: {email}")
        except Exception as exc:
            self._log(f"Error saving account: {exc}")

    def _on_error(self, message: str) -> None:
        self._log(message)
        messagebox.showerror("Error", message)

    # ------------------------------------------------------------------ #
    # Play / Delete / Copy
    # ------------------------------------------------------------------ #
    def _on_play(self) -> None:
        acct = self._selected_account()
        if not acct:
            messagebox.showinfo("Play", "Select an account first.")
            return
        if self._busy:
            return
        self._set_busy(True)

        proxy = self.proxy_combo.get() if self.proxy_var.get() else ""
        state_path = Path(acct.state_file) if acct.state_file else None

        self._log(f"Opening browser for {acct.email}…")

        def worker() -> None:
            try:
                asyncio.run(
                    open_existing_account(
                        email=acct.email,
                        password=acct.password,
                        proxy_url=proxy,
                        state_path=state_path,
                        log=self._queue_log,
                        # Release the GUI lock as soon as login finishes —
                        # the browser window stays open independently.
                        on_login_complete=lambda: self._queue.put(("busy_off", None)),
                    )
                )
            except Exception as exc:
                self._queue.put(("error", f"Open failed: {exc}"))
                # Make sure we never leave the UI locked on error
                self._queue.put(("busy_off", None))

        threading.Thread(target=worker, daemon=True).start()

    def _on_delete(self) -> None:
        acct = self._selected_account()
        if not acct:
            messagebox.showinfo("Delete", "Select an account first.")
            return
        if not messagebox.askyesno("Delete", f"Delete {acct.email}?"):
            return
        self.store.remove_account(acct)
        self._refresh_table()
        self._log(f"Deleted: {acct.email}")

    def _on_copy(self) -> None:
        acct = self._selected_account()
        if not acct:
            messagebox.showinfo("Copy", "Select an account first.")
            return
        text = f"{acct.email}:{acct.password}"
        self.clipboard_clear()
        self.clipboard_append(text)
        self._log(f"Copied to clipboard: {text}")