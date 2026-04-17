import sys
import time
import os

url = "https://ticker.finology.in/company/TCS"
cf_clearance = os.environ.get("CF_CLEARANCE", "")

if cf_clearance:
    print(f"Using provided cf_clearance cookie.")

def test_curl_cffi():
    print("\n--- Testing curl_cffi ---")
    try:
        from curl_cffi import requests as curl_requests
        s = curl_requests.Session(impersonate="chrome")
        headers = {"Referer": "https://ticker.finology.in/"}
        cookies = {"cf_clearance": cf_clearance} if cf_clearance else {}
        r = s.get(url, headers=headers, cookies=cookies, timeout=15)
        print(f"curl_cffi Status: {r.status_code}")
        if r.status_code == 200:
            print("curl_cffi: Bypass Success!")
    except Exception as e:
        print(f"curl_cffi: Error {e}")

def test_seleniumbase():
    print("\n--- Testing SeleniumBase UC Mode ---")
    try:
        from seleniumbase import SB
        
        # Determine if we need Xvfb (Linux only)
        if sys.platform.startswith('linux'):
            from pyvirtualdisplay import Display
            display = Display(visible=0, size=(1280, 720))
            display.start()
        else:
            display = None

        with SB(uc=True, headless=False) as sb:
            # Set cookie if provided before loading main page
            if cf_clearance:
                sb.uc_open_with_reconnect("https://ticker.finology.in/favicon.ico", 4)
                time.sleep(2)
                sb.add_cookie({"name": "cf_clearance", "value": cf_clearance, "domain": ".finology.in"})
                
            sb.uc_open_with_reconnect(url, 4)
            time.sleep(5)
            
            title = sb.get_title()
            print(f"Initial Page Title: {title}")
            
            if "Just a moment" in title or "Cloudflare" in title:
                print("Cloudflare challenge encountered, waiting for auto-solve...")
                time.sleep(6)
                try:
                    sb.uc_gui_click_captcha()
                except Exception:
                    pass
                time.sleep(4)
                title = sb.get_title()
            
            source = sb.get_page_source()
            if "companyessentials" in source or "Tata Consultancy Services" in title:
                print("seleniumbase: Status 200 (Bypass Success!)")
            else:
                print(f"seleniumbase: Bypass Failed. Final Title: {title}")
                
        if display:
            display.stop()
    except Exception as e:
        print(f"seleniumbase: Error {e}")

if __name__ == "__main__":
    test_curl_cffi()
    test_seleniumbase()
