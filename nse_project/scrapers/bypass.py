"""
Cloudflare Bypass and Page Fetching Utilities.
"""
import sys
import os
import time
import random
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from loguru import logger
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL  = "https://ticker.finology.in/company/"
_SLEEP_SEC = 2          # Base inter-request delay (seconds); jitter is applied on top
_TIMEOUT   = 20
COOKIE_REFRESH_INTERVAL = 25  # Re-solve Cloudflare challenge every N requests

# Do NOT pre-populate User-Agent or Accept-Language here.
_EXTRA_HEADERS: dict = {
    "Referer": "https://ticker.finology.in/",
}

# Session object reused across requests
_SESSION = curl_requests.Session(impersonate="chrome")

# Hardcoded mapping for problematic symbols
_SPECIAL_CASES = {
    "M&M":    "SCRIP-100520",
    "L&TFH":  "SCRIP-105164",
    "M&MFIN": "SCRIP-105212",
    "ARE&M": "SCRIP-100008",
    "GMRP&UI": "SCRIP-305653",
    "GVT&D": "SCRIP-122275",
    "J&KBANK": "SCRIP-132209",
    "SURANAT&P": "SCRIP-117530",
}

class CloudflareBlockError(Exception):
    """Raised when Cloudflare returns a 403 Forbidden, indicating a block."""
    pass

def init_cloudflare_bypass() -> bool:
    """
    Solves the Cloudflare Turnstile challenge using SeleniumBase (UC mode)
    inside a virtual framebuffer, then extracts the cf_clearance cookie and
    the full browser header set, injecting both into the shared curl_cffi
    session for subsequent high-speed requests.
    """
    global _SESSION, _EXTRA_HEADERS
    logger.info("Initializing Cloudflare bypass via SeleniumBase UC mode...")
    url_solve = f"{_BASE_URL}TCS"

    try:
        # Lazy load heavy dependencies
        from seleniumbase import SB
        from pyvirtualdisplay import Display

        display = Display(visible=0, size=(1280, 720))
        display.start()
        logger.info(f"Virtual display started on: {os.environ.get('DISPLAY', 'N/A')}")

        cf_clearance = None
        user_agent = None
        browser_headers: dict = {}

        with SB(uc=True, headless=False) as sb:
            sb.uc_open_with_reconnect(url_solve, 4)
            time.sleep(5)

            title = sb.get_title()
            if "Just a moment" in title or "Cloudflare" in title:
                logger.info("Cloudflare challenge detected — attempting GUI click to solve Turnstile...")
                time.sleep(7)
                try:
                    sb.uc_gui_click_captcha()
                    logger.info("GUI click sent. Waiting for handshake to complete...")
                except Exception as click_err:
                    logger.warning(f"GUI click skipped or failed: {click_err}")
                time.sleep(10)
                title = sb.get_title()

            for cookie in sb.get_cookies():
                if cookie['name'] == 'cf_clearance':
                    cf_clearance = cookie['value']
                    break

            user_agent = sb.execute_script("return navigator.userAgent;")
            accept_language = sb.execute_script(
                "return navigator.language || navigator.userLanguage || 'en-US,en;q=0.9';"
            )
            sec_ch_ua = sb.execute_script(
                "return navigator?.userAgentData?.brands"
                "  ? navigator.userAgentData.brands.map(b => `\"${b.brand}\";v=\"${b.version}\"`).join(', ')"
                "  : '\"Chromium\";v=\"124\", \"Google Chrome\";v=\"124\", \"Not-A.Brand\";v=\"99\"';"
            )
            browser_headers = {
                "User-Agent": user_agent,
                "Accept-Language": accept_language,
                "sec-ch-ua": sec_ch_ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Linux"' if sys.platform.startswith('linux') else '"Windows"',
            }

        try:
            display.stop()
        except Exception:
            pass

        if not cf_clearance:
            logger.error(f"cf_clearance cookie not found after bypass attempt. Final title: {title}")
            return False

        logger.success("cf_clearance cookie obtained. Synchronizing headers with curl_cffi session...")
        
        # Reset session to clear old cookies/state on refresh
        _SESSION = curl_requests.Session(impersonate="chrome")
        _EXTRA_HEADERS.update(browser_headers)
        _SESSION.cookies.set("cf_clearance", cf_clearance, domain=".finology.in")
        return True

    except Exception as e:
        logger.exception(f"Failed to initialize Cloudflare bypass: {e}")
        return False

def jitter_sleep(base_sec: float = _SLEEP_SEC):
    """Sleep with randomized jitter to mimic human inter-page navigation delays."""
    jitter = random.uniform(0.75, 2.0)
    sleep_time = base_sec * jitter
    if random.random() < 0.10:
        sleep_time += random.uniform(5.0, 12.0)
    time.sleep(sleep_time)

def set_referer(symbol: str):
    """Update Referer header to simulate natural navigation flow."""
    _EXTRA_HEADERS["Referer"] = f"{_BASE_URL}{quote(symbol, safe='')}"

def fetch_page(symbol: str) -> BeautifulSoup | None:
    """
    Fetch and parse the Data Source page for a given symbol.
    Raises CloudflareBlockError if a 403 is encountered.
    """
    if symbol in _SPECIAL_CASES:
        scrip_url = f"{_BASE_URL}{_SPECIAL_CASES[symbol]}"
        try:
            resp = _SESSION.get(scrip_url, headers=_EXTRA_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
            if resp.status_code == 403:
                raise CloudflareBlockError("403 Forbidden on special case URL")
            resp.raise_for_status()
            logger.info(f"  [{symbol}] Resolved via special case mapping to: {scrip_url}")
            return BeautifulSoup(resp.text, "lxml")
        except curl_requests.RequestsError as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            if status == 403:
                raise CloudflareBlockError("403 Forbidden on special case URL")
            logger.warning(f"[{symbol}] Special case mapping failed: {exc}")

    encoded_symbol = quote(symbol, safe="")
    url = f"{_BASE_URL}{encoded_symbol}"
    try:
        response = _SESSION.get(url, headers=_EXTRA_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
        if response.status_code == 403:
            raise CloudflareBlockError("403 Forbidden on direct URL")
        response.raise_for_status()
        if response.url != url:
            logger.info(f"  [{symbol}] Redirected to: {response.url}")
        return BeautifulSoup(response.text, "lxml")
    except curl_requests.RequestsError as exc:
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        if status == 403:
            raise CloudflareBlockError("403 Forbidden on direct URL")
        elif status in (400, 404):
            logger.debug(f"[{symbol}] Direct URL failed with {status}, trying search fallback...")
        else:
            logger.error(f"[{symbol}] Request failed: {exc}")
            return None

    search_query = encoded_symbol
    success_soup = None
    
    for query in [search_query, quote(symbol.replace("&", " "), safe="")]:
        search_url = f"https://ticker.finology.in/GetSearchData.ashx?q={query}"
        try:
            search_resp = _SESSION.get(search_url, headers=_EXTRA_HEADERS, timeout=_TIMEOUT)
            if search_resp.status_code == 403:
                raise CloudflareBlockError("403 Forbidden on search URL")
            search_resp.raise_for_status()
            results = search_resp.json()
            if results and isinstance(results, list) and len(results) > 0:
                first = results[0]
                fincode = first.get("FINCODE")
                if fincode:
                    resolved_url = f"{_BASE_URL}SCRIP-{fincode}"
                    logger.info(f"  [{symbol}] Search ({query}) resolved via FINCODE to: {resolved_url}")
                    resp2 = _SESSION.get(resolved_url, headers=_EXTRA_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
                    if resp2.status_code == 403:
                        raise CloudflareBlockError("403 Forbidden on resolved search URL")
                    resp2.raise_for_status()
                    success_soup = BeautifulSoup(resp2.text, "lxml")
                    break
        except curl_requests.RequestsError as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            if status == 403:
                raise CloudflareBlockError("403 Forbidden on search URL")
            logger.debug(f"[{symbol}] Search fallback for '{query}' failed: {exc}")
            
    if success_soup:
        return success_soup

    logger.warning(f"[{symbol}] Could not resolve Data Source page.")
    return None
