# Grok / x.ai Account Creator

A **100% local** Windows desktop app that automates creating and logging into
Grok / x.ai accounts. It drives a real Chromium browser via Playwright, uses
`instanttempemail.com` for throwaway verification emails, supports optional
proxies (tested before use), and stores everything in plain JSON on disk.

No cloud, no server, no paid APIs.

---

## Features

- **One-click account creation** — full x.ai sign-up flow automated end-to-end
- **Temp email + verification code extraction** — parses 6-digit codes in
  formats like `655-422` and `655422`
- **Human-like typing** — every field is typed with a per-character delay
- **Optional proxy support**
  - Downloads the free ProxyScrape list (~20k entries)
  - **Tests each candidate live** and only keeps working ones
  - Uses the selected proxy for both sign-up and login
- **Account list** — persistent, saved to `data/accounts.json`
- **Per-account actions**
  - **Play / Open** — fresh private browser, runs the full login flow with
    the saved credentials (email → Next → password → Login)
  - **Copy credentials** — `email:password` to clipboard
  - **Delete** — removes account and its saved session state
- **Live log pane** — shows every step the automation is taking

---

## Project structure

```
grok_creator_app/
│
├── main.py                          # Entry point — launches the GUI
├── requirements.txt                 # pip dependencies
├── README.md                        # This file
├── .gitignore                       # Excludes data/, __pycache__/, .venv/
│
├── grok_creator/                    # Python package (all app logic)
│   ├── __init__.py                  # Package marker + version
│   ├── models.py                    # Dataclasses: Account, Settings
│   ├── store.py                     # JSON persistence (accounts + settings)
│   ├── proxy_manager.py             # Fetch + test free proxies concurrently
│   ├── creator.py                   # Playwright automation (the core)
│   └── gui.py                       # CustomTkinter UI
│
└── data/                            # ← GITIGNORED, created at runtime
    ├── accounts.json                # Saved accounts (email, password, ...)
    ├── settings.json                # Proxy preferences
    └── states/                      # Saved Playwright browser sessions
        └── <email>/
            └── state.json
```

---

## How it works

### Sign-up flow (`creator.run_creator`)

1. Opens `instanttempemail.com` and reads the temp email address
2. Opens the x.ai sign-up page
3. Clicks **"Sign up with email"**, pastes the temp address, submits
4. Polls the temp inbox until the verification email arrives
5. Extracts the 6-digit code (`655-422` or `655422` both work)
6. Enters the code on the x.ai page
7. Fills **First name**, **Last name**, and a generated strong password
8. Clicks **"Complete sign up"**
9. Saves the browser storage state to `data/states/<email>/state.json`

### Login flow (`creator.open_existing_account`)

Triggered by the **Play / Open** button. Always starts a fresh isolated
browser context (effectively private) and runs:

1. Navigate to the x.ai sign-in URL
2. Click **"Login with email"**
3. Fill email → click **Next**
4. Wait for the password field → fill password → click **Login**
5. Save the refreshed session state
6. Leave the window open for the user

### Proxy handling (`proxy_manager.get_working_proxies`)

1. Downloads `proxyscrape/free-proxy-list` JSON from the jsDelivr CDN
2. Filters by country + protocol
3. Sorts by uptime + latency
4. Tests up to `max_threads` proxies concurrently via `api.ipify.org`
5. Returns only the ones that responded successfully

Tested proxies are the **only** ones added to the dropdown — no dead entries.

---

## Architecture at a glance

```
┌─────────────────────────────────────────────────────────────┐
│                       GrokCreatorApp (gui.py)               │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │  Top bar     │  │  Log pane    │  │  Account table    │  │
│  │  Create      │  │  (live)      │  │  Play/Copy/Delete │  │
│  │  Proxy ctrl  │  │              │  │                   │  │
│  └──────┬───────┘  └──────────────┘  └─────────┬─────────┘  │
│         │                                       │            │
│         │  User action → background thread      │            │
│         ▼                                       ▼            │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  threading.Thread → asyncio.run(creator.xxx)         │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                    │
│                         ▼                                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  creator.py — Playwright async API                    │   │
│  │  ├── run_creator(...)              sign-up flow       │   │
│  │  └── open_existing_account(...)    login flow         │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                    │
│                         ▼                                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Chromium (Playwright-managed)                        │   │
│  │  Proxy: from settings if enabled                      │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  Persistence:                                                │
│  gui.py ──▶ AccountStore (store.py) ──▶ data/*.json          │
└─────────────────────────────────────────────────────────────┘
```

**Threading model:** Tkinter main loop stays on the main thread. All
Playwright work runs in a `threading.Thread` and reports progress back
through a `queue.Queue` that the main thread polls every 100 ms.

---

## Setup

Requires **Python 3.10+** on Windows.

```cmd
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```cmd
python main.py
```

---

## Usage

### Create an account

1. Click **Create Account**
2. Watch the live log — the automation does the rest
3. When done, the account appears in the table below

### Use a proxy

1. Check **Use proxy**
2. Pick a proxy from the dropdown — or click **Generate proxies** to fetch
   fresh tested ones
3. The proxy is used for the next sign-up / login

### Log into an existing account

1. Select the account in the table
2. Click **Play / Open**
3. A private Chromium window opens and logs in automatically

### Copy / delete

- **Copy credentials** puts `email:password` on the clipboard
- **Delete** removes the account and its saved session

---

## Key files explained

| File | Purpose |
|---|---|
| `main.py` | Minimal entry point — creates `GrokCreatorApp` and calls `mainloop()` |
| `grok_creator/models.py` | `Account` and `Settings` dataclasses with `to_dict` / `from_dict` |
| `grok_creator/store.py` | Thread-safe JSON store for accounts + settings; also builds state file paths |
| `grok_creator/proxy_manager.py` | Free proxy list fetch + concurrent liveness testing |
| `grok_creator/creator.py` | All Playwright logic — sign-up flow, login flow, DOM helpers |
| `grok_creator/gui.py` | CustomTkinter UI, worker threads, queue polling |

---

## Data & privacy

Everything is stored **locally** under `./data/`:

- `accounts.json` — plaintext email + password for each saved account
- `settings.json` — proxy preferences and list
- `states/<email>/state.json` — Playwright browser session (cookies + local storage)

**`data/` is gitignored.** Never commit it — it contains credentials.

To wipe all data, just delete the `data/` folder.

---

## Common issues

**`ModuleNotFoundError: No module named 'customtkinter'`**
→ Run `pip install -r requirements.txt`

**`Executable doesn't exist at ... chrome-win/chrome.exe`**
→ Run `python -m playwright install chromium`

**Login button not found**
→ x.ai changed its DOM. Open the sign-in page, inspect the button,
add its selector to the top of the relevant list in `creator.py`:
`_login_click_email_option`, `_login_fill_email`, `_login_fill_password`,
or `_login_submit`.

**Free proxy is slow / fails**
→ Expected. Free proxies are unreliable. Tested ones work better but
paid residential proxies are the only truly reliable option.

---

## Disclaimer

This tool is provided for **educational and personal-automation purposes only**.
Automating account creation may violate x.ai / Grok's Terms of Service.
Use at your own risk. The authors are not responsible for any account
suspensions, bans, or legal consequences resulting from its use.
