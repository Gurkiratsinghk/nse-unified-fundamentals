import sys
import time

url = "https://ticker.finology.in/company/TCS"

def test_seleniumbase():
    print("Testing SeleniumBase UC Mode (Virtual Display)...")
    try:
        from pyvirtualdisplay import Display
        from seleniumbase import SB
        
        display = Display(visible=0, size=(1280, 720))
        display.start()

        # Execute headed Chrome inside the virtual framebuffer to evade headless detection
        with SB(uc=True, headless=False) as sb:
            sb.uc_open_with_reconnect(url, 4)
            time.sleep(5)
            
            title = sb.get_title()
            print(f"Initial Page Title: {title}")
            
            # If Cloudflare page is detected, wait a little extra or attempt click
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
        display.stop()
    except Exception as e:
        print(f"seleniumbase: Error {e}")

if __name__ == "__main__":
    test_seleniumbase()
