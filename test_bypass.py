import sys
import time

def test_cookie_handoff():
    print("Testing SeleniumBase -> curl_cffi Cookie Handoff...")
    
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
            print("Launching SeleniumBase to solve challenge...")
            sb.uc_open_with_reconnect(url_solve, 4)
            time.sleep(5)
            
            title = sb.get_title()
            if "Just a moment" in title or "Cloudflare" in title:
                print("Cloudflare challenge encountered, waiting for auto-solve...")
                time.sleep(6)
                try:
                    sb.uc_gui_click_captcha()
                except Exception:
                    pass
                time.sleep(4)
            
            # Extract cookie and User-Agent
            print("Extracting cf_clearance cookie...")
            for c in sb.get_cookies():
                if c['name'] == 'cf_clearance':
                    cf_clearance = c['value']
                    break
            
            user_agent = sb.execute_script("return navigator.userAgent;")
            print(f"Obtained cf_clearance: {cf_clearance}")
            print(f"Obtained User-Agent: {user_agent}")

        if display:
            display.stop()
            
    except Exception as e:
        print(f"seleniumbase phase error: {e}")
        return

    if not cf_clearance:
        print("Failed to get cf_clearance. Exiting test.")
        return

    # Now test with curl_cffi using the obtained cookie
    try:
        print("\n--- Testing curl_cffi with extracted cookie ---")
        from curl_cffi import requests as curl_requests
        s = curl_requests.Session(impersonate="chrome")
        
        # Override headers with the exact User-Agent from Selenium
        headers = {
            "Referer": "https://ticker.finology.in/",
            "User-Agent": user_agent
        }
        cookies = {"cf_clearance": cf_clearance}
        
        print(f"Fetching {url_test} via curl_cffi...")
        r = s.get(url_test, headers=headers, cookies=cookies, timeout=15)
        print(f"curl_cffi Status: {r.status_code}")
        if r.status_code == 200:
            print("curl_cffi bypass successful using SeleniumBase cookie!")
        else:
            print("curl_cffi bypass failed even with cookie.")
            
    except Exception as e:
        print(f"curl_cffi phase error: {e}")

if __name__ == "__main__":
    test_cookie_handoff()
