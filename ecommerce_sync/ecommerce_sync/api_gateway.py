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
        print(f"DEBUG: Mapping not found for SKU: {marketplace_sku}")
        frappe.log_error(title="Mapping Missing", message=f"SKU {marketplace_sku} on {platform}")
        return None
    return mapping

def get_or_create_customer(buyer_name):
    """
    Checks if customer exists by name; if not, creates a new one.
    Returns the Customer Name (ID).
    """
    if not buyer_name:
        buyer_name = "Marketplace Guest"

    # Check for existing customer by name
    customer = frappe.db.get_value("Customer", {"customer_name": buyer_name}, "name")
    
    if customer:
        return customer
    
    # Create new customer if not found
    try:
        new_cust = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": buyer_name,
            "customer_group": "All Customer Groups",
            "territory": "All Territories",
            "customer_type": "Individual"
        })
        new_cust.insert(ignore_permissions=True)
        print(f"DEBUG: Automatically created new Customer: {buyer_name}")
        return new_cust.name
    except Exception:
        frappe.log_error(frappe.get_traceback(), _("Automatic Customer Creation Failed"))
        return "Marketplace Customer"

@frappe.whitelist()
def create_sales_order_from_market(order_data, platform):
    """Converts marketplace payload into ERPNext Sales Order with Dynamic Customer and Warehouse."""
    
    # 1. Fetch the LATEST record from Marketplace Settings
    # This handles the case where the record ID is random (e.g., bb4lcqtuap)
    settings_name = frappe.db.get_value("Marketplace Settings", {}, "name", order_by="creation desc")
    
    if not settings_name:
        print("DEBUG: No records found in Marketplace Settings! Please create one in the Desk.")
        return None
        
    settings = frappe.get_doc("Marketplace Settings", settings_name)
    target_warehouse = settings.warehouse 
    
    if not target_warehouse:
        print(f"DEBUG: No Warehouse found in Settings record {settings_name}!")
        return None

    # 2. Get or Create Customer based on Buyer Name
    buyer_name = order_data.get('buyer_username')
    customer_id = get_or_create_customer(buyer_name)

    order_id = order_data.get('order_sn')
    
    # Check if order already exists
    if frappe.db.exists("Sales Order", {"market_order_id": order_id}):
        print(f"DEBUG: Order {order_id} already exists. Skipping.")
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
                "warehouse": target_warehouse, 
                "delivery_date": nowdate()
            })
        else:
            skipped_items.append(sku)

    if skipped_items:
        print(f"DEBUG: Order {order_id} failed because of unmapped SKUs: {skipped_items}")
        return None

    if items:
        try:
            so = frappe.get_doc({
                "doctype": "Sales Order",
                "naming_series": "SO-MKT-",
                "customer": customer_id, 
                "transaction_date": nowdate(),
                "items": items,
                "market_order_id": order_id,
                "order_type": "Sales"
            })
            so.insert(ignore_permissions=True)
            frappe.db.commit() 
            return so.name
        except Exception:
            print(f"DEBUG: Insertion failed for {order_id}. Check Error Logs.")
            frappe.log_error(frappe.get_traceback(), _("Sales Order Creation Failed"))
            return None
    return None

# --- MOCK TESTING SECTION ---

@frappe.whitelist()
def sync_mock_orders():
    """Mock sync with Dynamic Customer Testing."""
    print("\n--- Starting Mock Sync ---")
    
    unique_id = int(time.time())
    
    mock_order_details = [
        {
            "order_sn": f"MOCK-SHP-{unique_id}",
            "buyer_username": f"Buyer-{unique_id}", 
            "item_list": [
                {
                    "item_sku": "TEST-SKU-001", 
                    "model_quantity": 1,
                    "model_original_price": 499.00
                }
            ]
        }
    ]

    success_count = 0
    for order in mock_order_details:
        print(f"Attempting to create order for: {order['buyer_username']}")
        so_name = create_sales_order_from_market(order, "Shopee")
        if so_name:
            success_count += 1
            print(f"SUCCESS: Created Sales Order {so_name}")
    
    print(f"--- Mock Sync Finished. Created {success_count} orders ---\n")
    return {"status": "Mock Sync Finished", "created": success_count}