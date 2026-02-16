app_name = "ecommerce_sync"
app_title = "Ecommerce Sync"
app_publisher = "Shan Marion Silveo"
app_description = "Ecommerce Integration on ERPNEXT"
app_email = "shan.silveo@gmail.com"
app_license = "mit"

# Include JS in DocType views
# Ensure this file exists at: ecommerce_sync/public/js/marketplace_settings.js
doctype_js = {
    "Marketplace Settings": "public/js/marketplace_settings.js"
}

# Scheduled Tasks
# ----------------
scheduler_events = {
    "cron": {
        "*/15 * * * *": [
            "ecommerce_sync.ecommerce_sync.api_gateway.sync_orders_background"
        ]
    }
}

# Log Cleanup (The Professional Touch)
# ------------------------------------
# This automatically deletes logs older than 30 days to keep the DB fast.
default_log_clearing_doctypes = {
    "Marketplace Sync Log": 30
}

# Fixtures (Optional but recommended)
# -----------------------------------
# If you want your custom DocTypes to be part of the app install 
# for others, uncomment the line below.
# fixtures = ["Custom Field", "Property Setter"]