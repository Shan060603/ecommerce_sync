import hmac
import hashlib
import time
import frappe

def generate_shopee_sign(path, partner_id, api_key, access_token=None, shop_id=None):
    """
    Generates the HMAC-SHA256 signature required by Shopee v2.
    Returns: (signature, timestamp)
    """
    timestamp = int(time.time())
    
    # 1. Base Auth / Token Get (PartnerID + Path + Timestamp)
    base_string = f"{partner_id}{path}{timestamp}"
    
    # 2. Token Refresh (PartnerID + Path + Timestamp + ShopID)
    if shop_id and not access_token:
        base_string = f"{partner_id}{path}{timestamp}{shop_id}"
        
    # 3. Standard API Calls (PartnerID + Path + Timestamp + AccessToken + ShopID)
    if access_token and shop_id:
        base_string = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
        
    sign = hmac.new(
        api_key.encode(),
        base_string.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return sign, timestamp