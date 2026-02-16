frappe.ui.form.on('Marketplace Settings', {
    refresh: function(frm) {
        frm.add_custom_button(__('Get Authorization URL'), function() {
            frappe.call({
                method: "ecommerce_sync.ecommerce_sync.api_gateway.initiate_auth",
                callback: function(r) {
                    if (r.message) {
                        // Opens the Shopee/Lazada login in a new tab
                        window.open(r.message, '_blank');
                    }
                }
            });
        }).addClass('btn-primary');
    }
});