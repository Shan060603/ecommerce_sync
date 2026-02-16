import frappe
import hmac
import hashlib
import time
import requests
from frappe import _
from frappe.utils import flt, get_url, nowdate, now_datetime

@frappe.whitelist()
def get_erpnext_item(marketplace_sku, platform):
    """Returns mapping details for a marketplace SKU."""
    mapping = frappe.db.get_value(
        "Marketplace Item Mapping", 
        {"marketplace_sku": marketplace_sku, "marketplace": platform, "enabled": 1},
        ["erpnext_item", "conversion_factor"],
        as_dict=True
    )
    if not mapping:
        frappe.log_error(title="Mapping Missing", message=f"SKU {marketplace_sku} on {platform}")
        return None
    return mapping

# --- LOGGING HELPER ---

def create_sync_log(platform, status, count, message, raw_data=None):
    """Records the result of a sync attempt into the Marketplace Sync Log DocType."""
    try:
        frappe.get_doc({
            "doctype": "Marketplace Sync Log",
            "sync_date": now_datetime(),
            "platform": platform,
            "status": status,
            "orders_processed": count,
            "details": message,
            "log_json": frappe.as_json(raw_data) if raw_data else None
        }).insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        # Fallback to standard error log if the custom DocType fails
        frappe.log_error(frappe.get_traceback(), _("Sync Log Creation Failed"))

# --- AUTHENTICATION & SHOP INFO ---

@frappe.whitelist()
def initiate_auth():
    """Generates the Auth URL for the user to click in the Marketplace Settings UI."""
    settings = frappe.get_doc("Marketplace Settings")
    partner_id = settings.partner_id
    api_key = settings.get_password("api_key")
    
    path = "/api/v2/shop/auth_partner"
    timestamp = int(time.time())
    
    base_string = f"{partner_id}{path}{timestamp}"
    sign = hmac.new(api_key.encode(), base_string.encode(), hashlib.sha256).hexdigest()
    
    redirect_url = f"{get_url()}/api/method/ecommerce_sync.ecommerce_sync.api_gateway.auth_callback"
    
    auth_url = (
        f"https://partner.shopeemobile.com{path}?"
        f"partner_id={partner_id}&timestamp={timestamp}&sign={sign}&redirect={redirect_url}"
    )
    return auth_url

@frappe.whitelist(allow_guest=True)
def auth_callback(code=None, shop_id=None, **kwargs):
    """Receiver for the redirection from Shopee after user login."""
    if not code or not shop_id:
        return "Authorization failed: Missing code or shop_id."

    return exchange_code_for_token(code, shop_id)

def exchange_code_for_token(code, shop_id):
    """Exchanges Auth Code for tokens and fetches Shop Name."""
    settings = frappe.get_doc("Marketplace Settings")
    partner_id = int(settings.partner_id)
    api_key = settings.get_password("api_key")
    
    path = "/api/v2/auth/token/get"
    timestamp = int(time.time())
    
    base_string = f"{partner_id}{path}{timestamp}"
    sign = hmac.new(api_key.encode(), base_string.encode(), hashlib.sha256).hexdigest()
    
    url = f"https://partner.shopeemobile.com{path}?partner_id={partner_id}&timestamp={timestamp}&sign={sign}"
    
    payload = {
        "code": code,
        "shop_id": int(shop_id),
        "partner_id": partner_id
    }

    response = requests.post(url, json=payload)
    data = response.json()

    if data.get("access_token"):
        settings.access_token = data.get("access_token")
        settings.refresh_token = data.get("refresh_token")
        settings.shop_id = shop_id
        settings.expiry_time = timestamp + data.get("expire_in", 14400)
        
        shop_info = fetch_shop_info(settings)
        if shop_info:
            settings.shop_name = shop_info.get("shop_name")

        settings.save(ignore_permissions=True)
        frappe.db.commit()
        
        frappe.local.response.type = "redirect"
        frappe.local.response.location = "/app/marketplace-settings"
    else:
        frappe.log_error(message=str(data), title="Shopee Token Exchange Failed")
        return _("Token exchange failed. Check Error Logs.")

def fetch_shop_info(settings):
    """Fetches shop profile details from Shopee."""
    path = "/api/v2/shop/get_shop_info"
    timestamp = int(time.time())
    partner_id = int(settings.partner_id)
    shop_id = int(settings.shop_id)
    access_token = settings.get_password("access_token")
    api_key = settings.get_password("api_key")

    base_string = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    sign = hmac.new(api_key.encode(), base_string.encode(), hashlib.sha256).hexdigest()

    url = f"https://partner.shopeemobile.com{path}"
    params = {
        "partner_id": partner_id, "timestamp": timestamp, "sign": sign,
        "access_token": access_token, "shop_id": shop_id
    }

    try:
        res = requests.get(url, params=params)
        return res.json().get("response")
    except Exception:
        return None

def refresh_market_token():
    """Refreshes the token if it's near expiry."""
    settings = frappe.get_doc("Marketplace Settings")
    timestamp = int(time.time())

    if not settings.expiry_time or timestamp > (settings.expiry_time - 300):
        partner_id = int(settings.partner_id)
        api_key = settings.get_password("api_key")
        
        path = "/api/v2/auth/access_token/get"
        sign = hmac.new(api_key.encode(), f"{partner_id}{path}{timestamp}".encode(), hashlib.sha256).hexdigest()
        
        url = f"https://partner.shopeemobile.com{path}?partner_id={partner_id}&timestamp={timestamp}&sign={sign}"
        
        payload = {
            "refresh_token": settings.get_password("refresh_token"),
            "partner_id": partner_id,
            "shop_id": int(settings.shop_id)
        }

        response = requests.post(url, json=payload)
        data = response.json()

        if data.get("access_token"):
            settings.access_token = data.get("access_token")
            settings.refresh_token = data.get("refresh_token")
            settings.expiry_time = timestamp + data.get("expire_in", 14400)
            settings.save(ignore_permissions=True)
            frappe.db.commit()
            return True
        return False
    return True

# --- ORDER SYNC LOGIC ---

def get_order_details(order_ids):
    """Fetches full details (items, prices) for a list of Order SNs."""
    settings = frappe.get_doc("Marketplace Settings")
    partner_id = int(settings.partner_id)
    shop_id = int(settings.shop_id)
    api_key = settings.get_password("api_key")
    access_token = settings.get_password("access_token")
    
    path = "/api/v2/order/get_order_detail"
    timestamp = int(time.time())
    
    base_string = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    sign = hmac.new(api_key.encode(), base_string.encode(), hashlib.sha256).hexdigest()
    
    url = f"https://partner.shopeemobile.com{path}"
    params = {
        "partner_id": partner_id, "timestamp": timestamp, "sign": sign,
        "access_token": access_token, "shop_id": shop_id,
        "order_sn_list": ",".join(order_ids),
        "response_optional_fields": "item_list"
    }

    response = requests.get(url, params=params)
    return response.json().get("response", {}).get("order_list", [])

@frappe.whitelist()
def sync_orders_background():
    """Background task to fetch and create orders with full logging."""
    if not refresh_market_token():
        create_sync_log("Shopee", "Failed", 0, "Token refresh failed during background sync.")
        return

    settings = frappe.get_doc("Marketplace Settings")
    partner_id = int(settings.partner_id)
    shop_id = int(settings.shop_id)
    api_key = settings.get_password("api_key")
    access_token = settings.get_password("access_token")
    
    path = "/api/v2/order/get_order_list"
    timestamp = int(time.time())
    
    base_string = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    sign = hmac.new(api_key.encode(), base_string.encode(), hashlib.sha256).hexdigest()
    
    params = {
        "partner_id": partner_id, "timestamp": timestamp, "sign": sign,
        "access_token": access_token, "shop_id": shop_id,
        "time_range_field": "create_time",
        "time_from": timestamp - 86400,
        "time_to": timestamp,
        "page_size": 20,
        "order_status": "READY_TO_SHIP"
    }

    try:
        res = requests.get(f"https://partner.shopeemobile.com{path}", params=params)
        res_data = res.json()
        order_list = res_data.get("response", {}).get("order_list", [])
        
        if not order_list:
            create_sync_log("Shopee", "Success", 0, "No new 'READY_TO_SHIP' orders found.")
            return

        order_ids = [o['order_sn'] for o in order_list]
        details = get_order_details(order_ids)
        
        success_count = 0
        for order_data in details:
            so_name = create_sales_order_from_market(order_data, "Shopee")
            if so_name:
                success_count += 1

        status = "Success" if success_count == len(order_ids) else "Partial"
        msg = f"Successfully created {success_count} out of {len(order_ids)} orders."
        create_sync_log("Shopee", status, success_count, msg, raw_data=res_data)

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Shopee Sync Loop Failed")
        create_sync_log("Shopee", "Failed", 0, f"Critical Error: {str(e)}")

@frappe.whitelist()
def create_sales_order_from_market(order_data, platform):
    """Converts marketplace payload into ERPNext Sales Order."""
    order_id = order_data.get('order_sn')
    if frappe.db.exists("Sales Order", {"market_order_id": order_id}):
        return None

    items = []
    skipped_items = []
    
    for item in order_data.get('item_list', []):
        sku = item.get('item_sku')
        mapping = get_erpnext_item(sku, platform)
        
        if mapping:
            items.append({
                "item_code": mapping.erpnext_item,
                "qty": flt(item.get('model_quantity') or 1) * flt(mapping.conversion_factor or 1.0),
                "rate": item.get('model_original_price'),
                "warehouse": "Stores - Local",
                "delivery_date": nowdate()
            })
        else:
            skipped_items.append(sku)

    # If any item in the order isn't mapped, we shouldn't create a partial order
    if skipped_items:
        frappe.log_error(
            title="Order Sync Skipped", 
            message=f"Order {order_id} skipped. Unmapped SKUs: {', '.join(skipped_items)}"
        )
        return None

    if items:
        try:
            so = frappe.get_doc({
                "doctype": "Sales Order",
                "naming_series": "SO-MKT-",
                "customer": "Marketplace Customer", 
                "transaction_date": nowdate(),
                "items": items,
                "market_order_id": order_id,
                "order_type": "Sales"
            })
            so.insert(ignore_permissions=True)
            # so.submit() # UNCOMMENT THIS ONCE EXPERIMENT IS SUCCESSFUL
            
            # Commit after each successful order to ensure data persistence
            frappe.db.commit() 
            return so.name
        except Exception:
            frappe.log_error(frappe.get_traceback(), _("Sales Order Creation Failed"))
            return None
    return None