import frappe
from frappe.sessions import get_csrf_token

no_cache = 1


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/agent"
        raise frappe.Redirect

    # Read by the boot script in agent.html: frappe-ui needs the CSRF token for
    # POST calls and the site name for the realtime socket.
    context.boot = {"csrf_token": get_csrf_token(), "site_name": frappe.local.site}
