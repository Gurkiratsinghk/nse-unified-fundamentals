"""
Integration tests for the Cloudflare bypass mechanism.

This test requires actual internet access and may fail if Cloudflare's
challenge parameters change, or if run from a blocked datacenter IP.
"""
import sys
import time
import pytest

from bs4 import BeautifulSoup

import shutil

def test_cloudflare_bypass_handoff():
    """
    Test that SeleniumBase can successfully solve the Turnstile challenge,
    extract the cf_clearance cookie, and hand it off to curl_cffi for
    subsequent high-speed requests.
    """
    if sys.platform.startswith('linux') and not shutil.which('Xvfb'):
        pytest.skip("Xvfb not installed. Skipping SeleniumBase bypass test.")

    url_solve = "https://ticker.finology.in/company/TCS"
    url_test = "https://ticker.finology.in/company/INFY"
    
    cf_clearance = None
    user_agent = None

    try:
        from pyvirtualdisplay import Display
        from seleniumbase import SB
        
        if sys.platform.startswith('linux'):
            display = Display(visible=0, size=(1280, 720))
            display.start()
        else:
            display = None

        with SB(uc=True, headless=False) as sb:
            sb.uc_open_with_reconnect(url_solve, 4)
            time.sleep(5)
            
            title = sb.get_title()
            if "Just a moment" in title or "Cloudflare" in title:
                time.sleep(6)
                try:
                    sb.uc_gui_click_captcha()
                except Exception:
                    pass
                time.sleep(4)
            
            for c in sb.get_cookies():
                if c['name'] == 'cf_clearance':
                    cf_clearance = c['value']
                    break
            
            user_agent = sb.execute_script("return navigator.userAgent;")

        if display:
            display.stop()
            
    except Exception as e:
        pytest.fail(f"SeleniumBase phase failed: {e}")

    assert cf_clearance is not None, "Failed to get cf_clearance cookie from SeleniumBase"

    # Now test with curl_cffi using the obtained cookie
    try:
        from curl_cffi import requests as curl_requests
        s = curl_requests.Session(impersonate="chrome")
        
        headers = {
            "Referer": "https://ticker.finology.in/",
            "User-Agent": user_agent
        }
        cookies = {"cf_clearance": cf_clearance}
        
        r = s.get(url_test, headers=headers, cookies=cookies, timeout=15)
        
        assert r.status_code == 200, f"curl_cffi bypass failed. Status code: {r.status_code}"
        
        # Verify it's the actual page and not a WAF block returning 200
        soup = BeautifulSoup(r.text, "lxml")
        assert soup.find("div", id="companyessentials") is not None, "companyessentials div not found, likely a silent WAF block"
            
    except Exception as e:
        pytest.fail(f"curl_cffi phase failed: {e}")
