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

# User-Agent profiles for rotation
_UA_PROFILES = [
    {"platform": "Linux", "ua_hint": "Chromium\";v=\"124\", \"Google Chrome\";v=\"124\", \"Not-A.Brand\";v=\"99\""},
    {"platform": "Windows", "ua_hint": "Chromium\";v=\"124\", \"Google Chrome\";v=\"124\", \"Not-A.Brand\";v=\"99\""},
    {"platform": "macOS", "ua_hint": "Chromium\";v=\"124\", \"Google Chrome\";v=\"124\", \"Not-A.Brand\";v=\"99\""},
]

def init_cloudflare_bypass(max_retries: int = 3) -> bool:
    """
    Solves the Cloudflare Turnstile challenge using SeleniumBase (UC mode).
    Includes robust retry logic with wait intervals and UA rotation.
    """
    global _SESSION, _EXTRA_HEADERS
    url_solve = f"{_BASE_URL}TCS"

    for attempt in range(1, max_retries + 1):
        logger.info(f"Cloudflare bypass attempt {attempt}/{max_retries}...")
        
        try:
            from seleniumbase import SB
            from pyvirtualdisplay import Display

            display = Display(visible=0, size=(1280, 720))
            display.start()

            cf_clearance = None
            user_agent = None
            browser_headers: dict = {}
            
            # Rotate UA profile on retry
            ua_profile = _UA_PROFILES[(attempt - 1) % len(_UA_PROFILES)]

            with SB(uc=True, headless=False) as sb:
                # Force a specific platform via JS override if possible, or just let SB handle it
                sb.uc_open_with_reconnect(url_solve, 4)
                time.sleep(5)

                title = sb.get_title()
                if "Just a moment" in title or "Cloudflare" in title:
                    logger.info("Challenge detected — attempting GUI solve...")
                    time.sleep(7)
                    try:
                        sb.uc_gui_click_captcha()
                    except Exception:
                        pass
                    time.sleep(12)
                    title = sb.get_title()

                for cookie in sb.get_cookies():
                    if cookie['name'] == 'cf_clearance':
                        cf_clearance = cookie['value']
                        break

                user_agent = sb.execute_script("return navigator.userAgent;")
                accept_language = sb.execute_script("return navigator.language || 'en-US,en;q=0.9';")
                
                browser_headers = {
                    "User-Agent": user_agent,
                    "Accept-Language": accept_language,
                    "sec-ch-ua": ua_profile["ua_hint"],
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": f"\"{ua_profile['platform']}\"",
                }

            display.stop()

            if cf_clearance:
                logger.success(f"Bypass successful on attempt {attempt}.")
                _SESSION = curl_requests.Session(impersonate="chrome")
                _EXTRA_HEADERS.update(browser_headers)
                _SESSION.cookies.set("cf_clearance", cf_clearance, domain=".finology.in")
                return True
            
            logger.warning(f"Attempt {attempt} failed. Final title: {title}")

        except Exception as e:
            logger.error(f"Error during bypass attempt {attempt}: {e}")
            try:
                display.stop()
            except:
                pass

        if attempt < max_retries:
            wait_sec = 120
            logger.info(f"Retrying in {wait_sec} seconds...")
            for i in range(wait_sec, 0, -10):
                if i % 30 == 0 or i <= 10:
                    logger.info(f"Cooldown: {i}s remaining...")
                time.sleep(10)

    logger.critical("All Cloudflare bypass attempts failed.")
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
        elif status == 404:
            logger.warning(f"[{symbol}] Direct URL failed with 404. Trying search fallback...")
        else:
            logger.error(f"[{symbol}] HTTP {status} error: {exc}")
            return None # Move on to next company

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
