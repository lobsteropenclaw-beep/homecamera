import os
import requests
from wyze_sdk import Client
from wyze_sdk.errors import WyzeApiError
from dotenv import load_dotenv

# Disable SSL warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

# Monkeypatch requests to disable verification
original_request = requests.Session.request
def patched_request(self, method, url, *args, **kwargs):
    kwargs['verify'] = False
    return original_request(self, method, url, *args, **kwargs)
requests.Session.request = patched_request

email = os.getenv("WYZE_EMAIL")
password = os.getenv("WYZE_PASSWORD")
api_id = os.getenv("WYZE_API_ID")
api_key = os.getenv("WYZE_API_KEY")

client = Client(email=email, password=password, key_id=api_id, api_key=api_key)

try:
    print("Attempting to connect to Wyze...")
    # The SDK handles login via Client initialization if credentials are provided
    devices = client.devices_list()
    print(f"\nFound {len(devices)} devices:")
    for device in devices:
        print(f"- {device.nickname} (Type: {device.product.model}, IP: {device.ip}, MAC: {device.mac})")
        
except WyzeApiError as e:
    print(f"Wyze API Error: {e}")
except Exception as e:
    print(f"An error occurred: {e}")
