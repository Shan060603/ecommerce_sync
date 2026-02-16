import hmac
import hashlib
import time
import frappe

def generate_shopee_sign(path, partner_id, api_key, access_token=None, shop_id=None):
    """
    Generates the HMAC-SHA256 signature required by Shopee.
    """
    timestamp = int(time.time())
    
    # Base string construction varies by API version/platform
    # This is a standard Shopee v2 example:
    if access_token and shop_id:
        base_string = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    else:
        base_string = f"{partner_id}{path}{timestamp}"
        
    sign = hmac.new(
        api_key.encode(),
        base_string.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return sign, timestamp