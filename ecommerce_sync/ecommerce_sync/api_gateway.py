import frappe
import requests
import time
from frappe import _
from frappe.utils import flt, get_url, nowdate, now_datetime
from ecommerce_sync.ecommerce_sync.utils import generate_shopee_sign

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
        frappe.log_error(frappe.get_traceback(), _("Sync Log Creation Failed"))

# --- AUTHENTICATION & SHOP INFO ---

@frappe.whitelist()
def initiate_auth():
    """Generates the Auth URL using centralized utils."""
    settings = frappe.get_doc("Marketplace Settings")
    path = "/api/v2/shop/auth_partner"
    
    sign, timestamp = generate_shopee_sign(
        path, settings.partner_id, settings.get_password("api_key")
    )
    
    redirect_url = f"{get_url()}/api/method/ecommerce_sync.ecommerce_sync.api_gateway.auth_callback"
    
    auth_url = (
        f"https://partner.shopeemobile.com{path}?"
        f"partner_id={settings.partner_id}&timestamp={timestamp}&sign={sign}&redirect={redirect_url}"
    )
    return auth_url

@frappe.whitelist(allow_guest=True)
def auth_callback(code=None, shop_id=None, **kwargs):
    """Receiver for Shopee OAuth redirect."""
    if not code or not shop_id:
        return "Authorization failed: Missing code or shop_id."
    return exchange_code_for_token(code, shop_id)

def exchange_code_for_token(code, shop_id):
    """Exchanges Auth Code for permanent tokens."""
    settings = frappe.get_doc("Marketplace Settings")
    path = "/api/v2/auth/token/get"
    
    sign, timestamp = generate_shopee_sign(
        path, settings.partner_id, settings.get_password("api_key")
    )
    
    url = f"https://partner.shopeemobile.com{path}?partner_id={settings.partner_id}&timestamp={timestamp}&sign={sign}"
    payload = {"code": code, "shop_id": int(shop_id), "partner_id": int(settings.partner_id)}

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
    """Gets Store Name for UI display."""
    path = "/api/v2/shop/get_shop_info"
    access_token = settings.get_password("access_token")
    
    sign, timestamp = generate_shopee_sign(
        path, settings.partner_id, settings.get_password("api_key"), 
        access_token, settings.shop_id
    )

    params = {
        "partner_id": settings.partner_id, "timestamp": timestamp, "sign": sign,
        "access_token": access_token, "shop_id": settings.shop_id
    }

    try:
        res = requests.get(f"https://partner.shopeemobile.com{path}", params=params)
        return res.json().get("response")
    except Exception:
        return None

def refresh_market_token():
    """Handles token refresh using logic in utils.py."""
    settings = frappe.get_doc("Marketplace Settings")
    timestamp = int(time.time())
    saved_expiry = int(settings.expiry_time or 0)

    if not saved_expiry or timestamp > (saved_expiry - 300):
        path = "/api/v2/auth/access_token/get"
        
        # Pass shop_id but NOT access_token to trigger refresh-specific signature logic
        sign, timestamp = generate_shopee_sign(
            path, settings.partner_id, settings.get_password("api_key"), 
            shop_id=settings.shop_id
        )
        
        url = f"https://partner.shopeemobile.com{path}?partner_id={settings.partner_id}&timestamp={timestamp}&sign={sign}"
        payload = {
            "refresh_token": settings.get_password("refresh_token"),
            "partner_id": int(settings.partner_id),
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
    """Fetches full details for a batch of Order SNs."""
    settings = frappe.get_doc("Marketplace Settings")
    path = "/api/v2/order/get_order_detail"
    access_token = settings.get_password("access_token")
    
    sign, timestamp = generate_shopee_sign(
        path, settings.partner_id, settings.get_password("api_key"), 
        access_token, settings.shop_id
    )
    
    params = {
        "partner_id": settings.partner_id, "timestamp": timestamp, "sign": sign,
        "access_token": access_token, "shop_id": settings.shop_id,
        "order_sn_list": ",".join(order_ids),
        "response_optional_fields": "item_list"
    }

    response = requests.get(f"https://partner.shopeemobile.com{path}", params=params)
    return response.json().get("response", {}).get("order_list", [])

@frappe.whitelist()
def sync_orders_background():
    """Background task to fetch and create orders."""
    if not refresh_market_token():
        create_sync_log("Shopee", "Failed", 0, "Token refresh failed.")
        return

    settings = frappe.get_doc("Marketplace Settings")
    path = "/api/v2/order/get_order_list"
    access_token = settings.get_password("access_token")
    
    sign, timestamp = generate_shopee_sign(
        path, settings.partner_id, settings.get_password("api_key"), 
        access_token, settings.shop_id
    )
    
    params = {
        "partner_id": settings.partner_id, "timestamp": timestamp, "sign": sign,
        "access_token": access_token, "shop_id": settings.shop_id,
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
            create_sync_log("Shopee", "Success", 0, "No new orders found.")
            return

        order_ids = [o['order_sn'] for o in order_list]
        details = get_order_details(order_ids)
        
        success_count = 0
        for order_data in details:
            so_name = create_sales_order_from_market(order_data, "Shopee")
            if so_name:
                success_count += 1

        status = "Success" if success_count == len(order_ids) else "Partial"
        create_sync_log("Shopee", status, success_count, f"Created {success_count} / {len(order_ids)} orders.", raw_data=res_data)

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Shopee Sync Loop Failed")
        create_sync_log("Shopee", "Failed", 0, f"Error: {str(e)}")

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

    if skipped_items:
        frappe.log_error(title="Unmapped SKU", message=f"Order {order_id} skipped: {', '.join(skipped_items)}")
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
            # so.submit() # Experimental: Keep as Draft
            frappe.db.commit() 
            return so.name
        except Exception:
            frappe.log_error(frappe.get_traceback(), _("Sales Order Creation Failed"))
            return None
    return None

@frappe.whitelist()
def test_shopee_connection():
    """Rapid test to see if current tokens work."""
    if not refresh_market_token():
        return {"status": "error", "message": "Auth tokens are invalid and cannot be refreshed."}
    
    settings = frappe.get_doc("Marketplace Settings")
    info = fetch_shop_info(settings)
    
    if info:
        return {"status": "success", "message": f"Connected to {info.get('shop_name')}"}
    return {"status": "error", "message": "Could not fetch shop info."}