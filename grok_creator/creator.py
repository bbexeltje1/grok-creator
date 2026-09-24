"""Playwright automation for the x.ai / Grok sign-up flow.

Sign-up flow (as requested):
    1. https://accounts.x.ai/sign-up?redirect=grok-com&return_to=%2F
    2. Click "Sign up with email"
    3. Get a temp email from instanttempemail.com and paste it in
    4. Wait for the verification email; extract the 6-digit code
    5. Enter the code
    6. Fill First name, Last name, Password
    7. Click "Complete sign up"
    8. Save account info + browser storage state locally
"""

from __future__ import annotations

import asyncio
import random
import re
import string
import time
from pathlib import Path
from typing import Callable, Optional

from playwright.async_api import (
    BrowserContext,
    Page,
    async_playwright,
)


SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com&return_to=%2F"
LOGIN_URL = "https://accounts.x.ai/sign-in?redirect=grok-com&return_to=%2F%3Fq%3D%26reasoningMode%3Dnone%26voice%3Dfalse"
TEMPMAIL_URL = "https://instanttempemail.com/"
GROK_HOME = "https://grok.com/"

# "655-422", "655 422", "655422"
CODE_RE = re.compile(r"\b(\d{3})[-\s]?(\d{3})\b")


# --------------------------------------------------------------------------- #
# Random-data helpers
# --------------------------------------------------------------------------- #
FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie",
    "Avery", "Quinn", "Dakota", "Skyler", "Parker", "Reese", "Rowan",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Wilson", "Moore", "Anderson",
]


def _gen_first_name() -> str:
    return random.choice(FIRST_NAMES)


def _gen_last_name() -> str:
    return random.choice(LAST_NAMES)


def _gen_password(length: int = 16) -> str:
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%^&*"
    pool = lower + upper + digits + special
    pwd = [
        random.choice(lower),
        random.choice(upper),
        random.choice(digits),
        random.choice(special),
    ]
    pwd += [random.choice(pool) for _ in range(length - 4)]
    random.shuffle(pwd)
    return "".join(pwd)


# --------------------------------------------------------------------------- #
# Proxy parsing
# --------------------------------------------------------------------------- #
def parse_proxy(proxy_url: str) -> Optional[dict]:
    """Convert 'scheme://user:pass@host:port' into Playwright's proxy dict."""
    if not proxy_url:
        return None
    proxy_url = proxy_url.strip()
    m = re.match(
        r"^(?:(?P<scheme>\w+)://)?"
        r"(?:(?P<user>[^:@/]+):(?P<pw>[^@/]+)@)?"
        r"(?P<host>[^:/]+):(?P<port>\d+)$",
        proxy_url,
    )
    if not m:
        # Assume it's already a valid server URL
        return {"server": proxy_url}
    scheme = m.group("scheme") or "http"
    proxy = {"server": f"{scheme}://{m.group('host')}:{m.group('port')}"}
    if m.group("user"):
        proxy["username"] = m.group("user")
        proxy["password"] = m.group("pw")
    return proxy


# --------------------------------------------------------------------------- #
# Small generic helpers
# --------------------------------------------------------------------------- #
async def _try_click(page: Page, selectors: list[str], timeout: int = 5000) -> bool:
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=timeout, state="visible")
            if el:
                await el.scroll_into_view_if_needed()
                await el.click()
                return True
        except Exception:
            continue
    return False


async def _try_fill(page: Page, selectors: list[str], text: str, timeout: int = 5000) -> bool:
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=timeout, state="visible")
            if el:
                await el.click()
                await el.fill("")
                await el.type(text, delay=40)
                return True
        except Exception:
            continue
    return False


async def _wait_for_any_selector(page: Page, selectors: list[str], timeout: int = 10000) -> bool:
    """Wait until any of the given selectors appears on the page."""
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, timeout=timeout, state="visible")
            return True
        except Exception:
            continue
    return False


# --------------------------------------------------------------------------- #
# Temp email
# --------------------------------------------------------------------------- #
async def _read_temp_email(page: Page, context: BrowserContext, log: Callable[[str], None]) -> str:
    """Try several strategies to obtain the temp email address."""
    await asyncio.sleep(2)

    # Strategy 1: dedicated email elements
    selectors = [
        "#email",
        ".email",
        "[data-testid='email']",
        "[data-testid='email-address']",
        "[class*='mail']",
        "input[readonly]",
    ]
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if not el:
                continue
            text = (await el.inner_text()) or ""
            value = await el.get_attribute("value") or ""
            blob = f"{text} {value}"
            m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", blob)
            if m:
                return m.group(0)
        except Exception:
            continue

    # Strategy 2: scan page body
    try:
        body = await page.inner_text("body")
        m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", body)
        if m:
            return m.group(0)
    except Exception:
        pass

    # Strategy 3: click "Copy" and read clipboard
    try:
        await context.grant_permissions(["clipboard-read", "clipboard-write"])
        clicked = await _try_click(
            page,
            [
                "button:has-text('Copy')",
                "button:has-text('copy')",
                "text=Copy",
                "[aria-label='Copy']",
            ],
            timeout=3000,
        )
        if clicked:
            await asyncio.sleep(0.5)
            data = await page.evaluate("navigator.clipboard.readText()")
            if data and "@" in data:
                return data.strip()
    except Exception as e:
        log(f"Clipboard fallback failed: {e}")

    raise RuntimeError("Could not read temp email address from instanttempemail.com")


async def _extract_code_from_page(page: Page) -> Optional[str]:
    """Read the newest email body on the temp-mail page and return the code."""
    # Give the page a moment and try to click the newest email row first
    try:
        await _try_click(
            page,
            [
                "tr:first-child",
                ".email-item:first-child",
                ".message:first-child",
                "[data-testid='email-item']:first-child",
            ],
            timeout=1500,
        )
    except Exception:
        pass

    # Look in any iframes first (many temp-mail providers render bodies there)
    frames = [page] + [f for f in page.frames if f != page.main_frame]
    for frame in frames:
        try:
            text = await frame.inner_text("body")
        except Exception:
            continue
        if not text:
            continue
        m = CODE_RE.search(text)
        if m:
            return m.group(1) + m.group(2)
        m = re.search(r"\b(\d{6})\b", text)
        if m:
            return m.group(1)
    return None


async def _wait_for_code(
    mail_page: Page,
    log: Callable[[str], None],
    timeout: int = 180,
) -> Optional[str]:
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        log(f"Checking inbox… ({attempt})")
        try:
            await mail_page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(2.5)
            code = await _extract_code_from_page(mail_page)
            if code:
                return code
        except Exception as e:
            log(f"Inbox check error: {e}")
        await asyncio.sleep(5)
    return None


# --------------------------------------------------------------------------- #
# Sign-up flow
# --------------------------------------------------------------------------- #
async def _signup_step_email(page: Page, log: Callable[[str], None]) -> None:
    log("Opening x.ai sign-up page…")
    await page.goto(SIGNUP_URL, wait_until="domcontentloaded")
    await asyncio.sleep(3)

    log("Clicking 'Sign up with email'…")
    ok = await _try_click(
        page,
        [
            "button:has-text('Sign up with email')",
            "text=Sign up with email",
            "[role='button']:has-text('Sign up with email')",
            "text=Sign up with Email",
        ],
        timeout=10000,
    )
    if not ok:
        log("! Could not find 'Sign up with email' button — continuing anyway")
    await asyncio.sleep(2)


async def _signup_fill_email(page: Page, email: str, log: Callable[[str], None]) -> None:
    log(f"Filling email field with {email}")
    ok = await _try_fill(
        page,
        [
            "input[type='email']",
            "input[name='email']",
            "input[autocomplete='email']",
            "input[placeholder*='mail' i]",
        ],
        email,
        timeout=10000,
    )
    if not ok:
        raise RuntimeError("Could not locate the email input field")

    await asyncio.sleep(0.5)
    log("Submitting email…")
    await _try_click(
        page,
        [
            "button[type='submit']",
            "button:has-text('Continue')",
            "button:has-text('Next')",
            "button:has-text('Sign up')",
        ],
        timeout=5000,
    )
    await asyncio.sleep(3)


async def _signup_enter_code(page: Page, code: str, log: Callable[[str], None]) -> None:
    log(f"Entering verification code {code}")

    # Case 1: single 6-char input
    single = await _try_fill(
        page,
        [
            "input[name='code']",
            "input[autocomplete='one-time-code']",
            "input[inputmode='numeric']",
            "input[placeholder*='code' i]",
        ],
        code,
        timeout=8000,
    )
    if not single:
        # Case 2: six separate inputs
        boxes = await page.query_selector_all("input[maxlength='1']")
        if len(boxes) >= len(code):
            for el, ch in zip(boxes, code):
                try:
                    await el.click()
                    await el.type(ch, delay=60)
                except Exception:
                    pass
        else:
            raise RuntimeError("Could not locate verification code input")

    await asyncio.sleep(1)
    log("Submitting code…")
    await _try_click(
        page,
        [
            "button[type='submit']",
            "button:has-text('Verify')",
            "button:has-text('Continue')",
            "button:has-text('Submit')",
        ],
        timeout=5000,
    )
    await asyncio.sleep(3)


async def _signup_fill_profile(
    page: Page,
    first_name: str,
    last_name: str,
    password: str,
    log: Callable[[str], None],
) -> None:
    log("Filling profile fields…")

    await _try_fill(
        page,
        [
            "input[name='firstName']",
            "input[name='first_name']",
            "input[autocomplete='given-name']",
            "input[placeholder*='First' i]",
        ],
        first_name,
        timeout=10000,
    )
    await asyncio.sleep(0.4)

    await _try_fill(
        page,
        [
            "input[name='lastName']",
            "input[name='last_name']",
            "input[autocomplete='family-name']",
            "input[placeholder*='Last' i]",
        ],
        last_name,
        timeout=10000,
    )
    await asyncio.sleep(0.4)

    await _try_fill(
        page,
        [
            "input[name='password']",
            "input[type='password']",
            "input[autocomplete='new-password']",
        ],
        password,
        timeout=10000,
    )
    await asyncio.sleep(0.5)


async def _signup_submit(page: Page, log: Callable[[str], None]) -> None:
    log("Clicking 'Complete sign up'…")
    await _try_click(
        page,
        [
            "button:has-text('Complete sign up')",
            "button:has-text('Complete Sign Up')",
            "button:has-text('Create account')",
            "button[type='submit']",
            "button:has-text('Sign up')",
        ],
        timeout=8000,
    )
    await asyncio.sleep(5)


# --------------------------------------------------------------------------- #
# Public API — the whole sign-up flow
# --------------------------------------------------------------------------- #
async def run_creator(
    proxy_url: Optional[str],
    log: Callable[[str], None],
    state_path: Optional[Path] = None,
    headless: bool = False,
) -> dict:
    """Run the full sign-up flow. Returns a dict with the new account's data."""
    proxy = parse_proxy(proxy_url) if proxy_url else None
    if proxy:
        log(f"Using proxy: {proxy['server']}")
    else:
        log("No proxy — using direct connection")

    async with async_playwright() as p:
        launch_args: dict = {
            "headless": headless,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if proxy:
            launch_args["proxy"] = proxy

        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 820},
        )
        # Hide obvious automation markers
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        try:
            # ---------------------------------------------------------- #
            # Step 1 — get a temp email
            # ---------------------------------------------------------- #
            log("Opening instanttempemail.com…")
            mail_page = await context.new_page()
            await mail_page.goto(TEMPMAIL_URL, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            email = await _read_temp_email(mail_page, context, log)
            log(f"Temp email acquired: {email}")

            # ---------------------------------------------------------- #
            # Step 2 — start sign-up at x.ai
            # ---------------------------------------------------------- #
            signup_page = await context.new_page()
            await _signup_step_email(signup_page, log)
            await _signup_fill_email(signup_page, email, log)

            # ---------------------------------------------------------- #
            # Step 3 — wait for verification email, extract code
            # ---------------------------------------------------------- #
            log("Waiting for verification email…")
            code = await _wait_for_code(mail_page, log, timeout=180)
            if not code:
                raise RuntimeError("Verification code never arrived")
            log(f"Verification code: {code}")

            await _signup_enter_code(signup_page, code, log)

            # ---------------------------------------------------------- #
            # Step 4 — fill profile
            # ---------------------------------------------------------- #
            first_name = _gen_first_name()
            last_name = _gen_last_name()
            password = _gen_password()

            await _signup_fill_profile(signup_page, first_name, last_name, password, log)
            await _signup_submit(signup_page, log)

            # ---------------------------------------------------------- #
            # Step 5 — persist browser state for future logins
            # ---------------------------------------------------------- #
            if state_path is not None:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    await context.storage_state(path=str(state_path))
                    log(f"Saved browser session to {state_path.name}")
                except Exception as e:
                    log(f"Could not save browser session: {e}")

            # Final URL sanity check
            current_url = signup_page.url
            log(f"Done. Landing URL: {current_url}")

            return {
                "email": email,
                "password": password,
                "first_name": first_name,
                "last_name": last_name,
            }

        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Login helpers — new dedicated flow
# --------------------------------------------------------------------------- #
async def _login_click_email_option(page: Page, log: Callable[[str], None]) -> bool:
    """Click the 'Login with email' / 'Sign in with email' button on the sign-in page."""
    log("Looking for 'Login with email' button…")

    # Wait a moment for the page to fully render
    await asyncio.sleep(2)

    # Try a broad set of possible button text variations
    variants = [
        "Login with email",
        "Log in with email",
        "Sign in with email",
        "Sign up with email",
        "Continue with email",
        "Login with Email",
        "Log in with Email",
        "Sign in with Email",
        "Use email",
        "Email",
    ]

    # First try: get_by_text / has-text selectors
    for text in variants:
        selectors = [
            f"button:has-text('{text}')",
            f"[role='button']:has-text('{text}')",
            f"a:has-text('{text}')",
            f"text={text}",
        ]
        try:
            ok = await _try_click(page, selectors, timeout=3000)
            if ok:
                log(f"Clicked '{text}' button.")
                return True
        except Exception:
            continue

    # Second try: use Playwright's get_by_role with name regex
    try:
        btn = page.get_by_role("button", name=re.compile(r"email", re.IGNORECASE))
        if await btn.count() > 0:
            await btn.first.click()
            log("Clicked email button via role locator.")
            return True
    except Exception:
        pass

    # Third try: any button containing "email" text
    try:
        buttons = await page.query_selector_all("button, a, [role='button']")
        for b in buttons:
            try:
                text = (await b.inner_text()).strip().lower()
                if "email" in text and len(text) < 40:
                    await b.click()
                    log(f"Clicked button with text: {text}")
                    return True
            except Exception:
                continue
    except Exception:
        pass

    log("! Could not find 'Login with email' button.")
    return False


async def _login_fill_email(page: Page, email: str, log: Callable[[str], None]) -> bool:
    """Fill the email input on the sign-in page."""
    log(f"Filling email: {email}")

    selectors = [
        "input[type='email']",
        "input[name='email']",
        "input[autocomplete='email']",
        "input[placeholder*='Email' i]",
        "input[placeholder*='email' i]",
        "input[placeholder*='mail' i]",
        "input[id*='email' i]",
        "input[aria-label*='Email' i]",
    ]

    ok = await _try_fill(page, selectors, email, timeout=10000)
    if not ok:
        # Fallback: get_by_placeholder
        try:
            el = page.get_by_placeholder(re.compile(r"email", re.IGNORECASE))
            await el.first.click()
            await el.first.fill("")
            await el.first.type(email, delay=40)
            ok = True
        except Exception:
            pass

    if ok:
        log("Email filled.")
    else:
        log("! Could not fill email field.")
    return ok


async def _login_click_next(page: Page, log: Callable[[str], None]) -> bool:
    """Click the 'Next' / 'Continue' button after entering the email."""
    log("Clicking Next / Continue…")

    selectors = [
        "button[type='submit']",
        "button:has-text('Next')",
        "button:has-text('Continue')",
        "button:has-text('Log in')",
        "button:has-text('Sign in')",
        "button:has-text('Submit')",
        "[role='button']:has-text('Next')",
        "[role='button']:has-text('Continue')",
    ]

    ok = await _try_click(page, selectors, timeout=8000)

    if not ok:
        # Fallback: get_by_role
        try:
            btn = page.get_by_role("button", name=re.compile(r"next|continue|submit", re.IGNORECASE))
            if await btn.count() > 0:
                await btn.first.click()
                ok = True
        except Exception:
            pass

    if ok:
        log("Next / Continue clicked.")
    else:
        log("! Could not click Next / Continue.")
    return ok


async def _login_fill_password(page: Page, password: str, log: Callable[[str], None]) -> bool:
    """Fill the password input on the sign-in page."""
    log("Filling password…")

    # Wait for the password field to appear
    await asyncio.sleep(2)

    selectors = [
        "input[type='password']",
        "input[name='password']",
        "input[autocomplete='current-password']",
        "input[placeholder*='Password' i]",
        "input[placeholder*='password' i]",
        "input[id*='password' i]",
        "input[aria-label*='Password' i]",
    ]

    ok = await _try_fill(page, selectors, password, timeout=10000)

    if not ok:
        # Fallback: get_by_placeholder
        try:
            el = page.get_by_placeholder(re.compile(r"password", re.IGNORECASE))
            await el.first.click()
            await el.first.fill("")
            await el.first.type(password, delay=40)
            ok = True
        except Exception:
            pass

    if ok:
        log("Password filled.")
    else:
        log("! Could not fill password field.")
    return ok


async def _login_submit(page: Page, log: Callable[[str], None]) -> bool:
    """Click the final Login / Sign in button."""
    log("Clicking Login…")

    selectors = [
        "button[type='submit']",
        "button:has-text('Log in')",
        "button:has-text('Login')",
        "button:has-text('Sign in')",
        "button:has-text('Continue')",
        "button:has-text('Submit')",
        "[role='button']:has-text('Log in')",
        "[role='button']:has-text('Login')",
        "[role='button']:has-text('Sign in')",
    ]

    ok = await _try_click(page, selectors, timeout=8000)

    if not ok:
        # Fallback: get_by_role
        try:
            btn = page.get_by_role("button", name=re.compile(r"log ?in|sign ?in|submit|continue", re.IGNORECASE))
            if await btn.count() > 0:
                await btn.first.click()
                ok = True
        except Exception:
            pass

    if ok:
        log("Login button clicked.")
    else:
        log("! Could not click Login button.")
    return ok


async def _perform_login(
    page: Page,
    email: str,
    password: str,
    log: Callable[[str], None],
) -> bool:
    """Execute the full two-step x.ai login flow.

    Step 1: Click "Login with email"
    Step 2: Enter email → click Next
    Step 3: Enter password → click Login
    """
    log("=" * 50)
    log("Starting x.ai login flow…")

    # Step 1: Click "Login with email"
    clicked = await _login_click_email_option(page, log)
    if not clicked:
        log("! Could not click 'Login with email' — the page may already show the email form.")

    await asyncio.sleep(2)

    # Step 2: Fill email and click Next
    filled = await _login_fill_email(page, email, log)
    if not filled:
        log("! Could not fill email — aborting login.")
        return False

    await asyncio.sleep(1)
    await _login_click_next(page, log)

    # Wait for the password step to load
    log("Waiting for password field…")
    try:
        await page.wait_for_selector(
            "input[type='password'], input[name='password'], input[autocomplete='current-password']",
            timeout=15000,
        )
    except Exception:
        log("! Password field did not appear within 15s — continuing anyway.")
    await asyncio.sleep(2)

    # Step 3: Fill password and click Login
    filled = await _login_fill_password(page, password, log)
    if not filled:
        log("! Could not fill password — aborting login.")
        return False

    await asyncio.sleep(1)
    await _login_submit(page, log)

    # Give it a moment to process
    await asyncio.sleep(5)
    log(f"Login flow done. Current URL: {page.url}")
    log("=" * 50)
    return True


## --------------------------------------------------------------------------- #
# Open existing account (Play / Open button)
# --------------------------------------------------------------------------- #
async def open_existing_account(
    email: str,
    password: str,
    proxy_url: Optional[str],
    state_path: Optional[Path],
    log: Callable[[str], None],
    headless: bool = False,
) -> None:
    """Launch a fresh private browser and log in with the saved credentials.

    Flow:
      1. Open a fresh (private / isolated) browser context — no saved cookies.
      2. Navigate to the x.ai sign-in page.
      3. Click "Login with email".
      4. Fill email → click Next.
      5. Fill password → click Login.
      6. Leave the browser window open for the user.
    """
    proxy = parse_proxy(proxy_url) if proxy_url else None

    async with async_playwright() as p:
        launch_args: dict = {
            "headless": headless,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if proxy:
            launch_args["proxy"] = proxy
            log(f"Using proxy {proxy['server']}")
        else:
            log("No proxy — using direct connection")

        browser = await p.chromium.launch(**launch_args)

        # Fresh, isolated context each time (effectively a private window)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 820},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page = await context.new_page()

        try:
            # ---------------------------------------------------------- #
            # Step 1 — open the sign-in page
            # ---------------------------------------------------------- #
            log("Opening x.ai sign-in page…")
            await page.goto(LOGIN_URL, wait_until="domcontentloaded")
            await asyncio.sleep(3)
            log(f"Loaded: {page.url}")

            # ---------------------------------------------------------- #
            # Step 2 — click "Login with email"
            # ---------------------------------------------------------- #
            await _login_click_email_option(page, log)
            await asyncio.sleep(2)

            # ---------------------------------------------------------- #
            # Step 3 — fill email and click Next
            # ---------------------------------------------------------- #
            filled = await _login_fill_email(page, email, log)
            if not filled:
                log("! Could not fill email field — aborting login.")
                # Still keep the browser open so the user can finish manually
                await _keep_browser_open(browser)
                return

            await asyncio.sleep(1)
            await _login_click_next(page, log)

            # ---------------------------------------------------------- #
            # Step 4 — wait for the password field to appear
            # ---------------------------------------------------------- #
            log("Waiting for password field…")
            try:
                await page.wait_for_selector(
                    "input[type='password'], "
                    "input[name='password'], "
                    "input[autocomplete='current-password']",
                    timeout=15000,
                )
                log("Password field detected.")
            except Exception:
                log("! Password field did not appear within 15s — continuing anyway.")
            await asyncio.sleep(2)

            # ---------------------------------------------------------- #
            # Step 5 — fill password and click Login
            # ---------------------------------------------------------- #
            filled = await _login_fill_password(page, password, log)
            if not filled:
                log("! Could not fill password field — aborting login.")
                await _keep_browser_open(browser)
                return

            await asyncio.sleep(1)
            await _login_submit(page, log)

            # Give the site a moment to process the login
            await asyncio.sleep(5)
            log(f"Login submitted. Current URL: {page.url}")

            # ---------------------------------------------------------- #
            # Step 6 — optionally persist the new session state
            # ---------------------------------------------------------- #
            if state_path is not None:
                try:
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    await context.storage_state(path=str(state_path))
                    log(f"Saved session state to {state_path.name}")
                except Exception as e:
                    log(f"Could not save session state: {e}")

            log("Login flow complete. Browser window is ready.")

        except Exception as exc:
            log(f"Login flow error: {exc}")

        # Keep the browser alive until the user closes it.
        await _keep_browser_open(browser)


# --------------------------------------------------------------------------- #
# Helper — keep the Playwright browser alive until the user closes it
# --------------------------------------------------------------------------- #
async def _keep_browser_open(browser) -> None:
    """Block until the Playwright browser window is closed by the user."""
    try:
        while True:
            if not browser.is_connected():
                break
            await asyncio.sleep(2)
    except Exception:
        pass
        
        
        
        
        