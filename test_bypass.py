import sys
from curl_cffi import requests as curl_requests
import tls_client

url = "https://ticker.finology.in/company/TCS"
headers = {"Referer": "https://ticker.finology.in/"}

def test_impersonate(impersonate):
    try:
        s = curl_requests.Session(impersonate=impersonate)
        r = s.get(url, headers=headers, timeout=15)
        print(f"curl_cffi ({impersonate}): Status {r.status_code}")
    except Exception as e:
        print(f"curl_cffi ({impersonate}): Error {e}")

def test_tls_client(identifier):
    try:
        s = tls_client.Session(client_identifier=identifier, random_tls_extension_order=True)
        r = s.get(url, headers=headers, timeout_seconds=15)
        print(f"tls_client ({identifier}): Status {r.status_code}")
    except Exception as e:
        print(f"tls_client ({identifier}): Error {e}")

print("Testing curl_cffi targets...")
targets = [
    "chrome", "chrome100", "chrome110", "chrome116", "chrome120", "chrome124", "safari15_3", "safari15_5", "safari_ios"
]
for t in targets:
    test_impersonate(t)

print("\nTesting tls_client targets...")
tls_targets = ["chrome_110", "chrome_116", "chrome_120", "safari_15_6_1", "safari_ios_16_0"]
for t in tls_targets:
    test_tls_client(t)
