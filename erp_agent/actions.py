"""Draft ERPNext documents on the user's behalf, but only create them after they confirm.

draft_document (a tool the model calls) builds the document and runs ERPNext's own validation
and pricing inside a savepoint that is always rolled back, so nothing is written. The result is
a preview the UI shows with Confirm / Cancel. confirm_action (called by the user from the UI,
never by the model) rebuilds and inserts it as a draft (docstatus 0). Nothing is ever submitted.
"""

import re

import frappe
from frappe.utils import add_days, today

ACTION_TTL_SECONDS = 24 * 60 * 60
PREVIEW_ITEM_LIMIT = 20

# DocType: party field (resolved by name), whether it has an items table
ACTION_DOCTYPES = {
    "Sales Order": {"party": ("customer", "Customer", "customer_name"), "items": True},
    "Quotation": {"party": ("party_name", "Customer", "customer_name"), "items": True},
    "Purchase Order": {"party": ("supplier", "Supplier", "supplier_name"), "items": True},
    "Material Request": {"party": None, "items": True},
    "ToDo": {"party": None, "items": False},
}


def draft_document(doctype, values):
    """Validate a proposed document and stage it for the user's confirmation."""
    doc = build_doc(doctype, values)
    preview = validate_and_preview(doc)

    action_id = frappe.generate_hash(length=12)
    frappe.cache.set_value(
        cache_key(action_id),
        {"user": frappe.session.user, "doctype": doctype, "values": values, "preview": preview},
        expires_in_sec=ACTION_TTL_SECONDS,
    )
    return {
        "action_id": action_id,
        "status": "awaiting user confirmation",
        "preview": preview,
        "note": "Shown to the user as a card with Create draft / Cancel buttons. It is NOT created yet; "
        "tell the user to review the card and click Create draft.",
    }


def confirm_action(action_id):
    """Create the staged document as a draft. Called from the UI by the user, not by the model."""
    staged = frappe.cache.get_value(cache_key(action_id))
    if not staged:
        frappe.throw("This proposal has expired or was already handled. Ask the agent again.")
    if staged["user"] != frappe.session.user:
        raise frappe.PermissionError

    doc = build_doc(staged["doctype"], staged["values"])
    doc.insert()  # permission checks and validation run again, as the confirming user
    frappe.cache.delete_value(cache_key(action_id))
    return {"doctype": doc.doctype, "name": doc.name}


def cancel_action(action_id):
    staged = frappe.cache.get_value(cache_key(action_id))
    if staged and staged["user"] != frappe.session.user:
        raise frappe.PermissionError
    frappe.cache.delete_value(cache_key(action_id))


def cache_key(action_id):
    return f"erp_agent:action:{action_id}"


def build_doc(doctype, values):
    if doctype not in ACTION_DOCTYPES:
        raise ValueError(f"Can only draft: {', '.join(ACTION_DOCTYPES)}")
    if not frappe.has_permission(doctype, "create"):
        raise frappe.PermissionError(f"You are not allowed to create {doctype}")

    spec = ACTION_DOCTYPES[doctype]
    values = dict(values or {})
    items = values.pop("items", None) or []
    values.pop("docstatus", None)  # drafts only

    if spec["party"]:
        fieldname, party_doctype, title_field = spec["party"]
        values[fieldname] = resolve_name(party_doctype, values.get(fieldname), title_field)

    doc = frappe.new_doc(doctype)
    doc.update(values)
    apply_defaults(doc)

    if spec["items"]:
        if not items:
            raise ValueError(f"{doctype} needs at least one item: items=[{{item_code, qty}}]")
        for row in items:
            row = dict(row)
            row["item_code"] = resolve_name("Item", row.get("item_code") or row.get("item_name"), "item_name")
            row.pop("item_name", None)
            row.setdefault("qty", 1)
            apply_item_defaults(doc, row)
            doc.append("items", row)

    # What the desk form fills in on the client: price list, currency, item rates, warehouses
    if hasattr(doc, "set_missing_values"):
        doc.set_missing_values()
    return doc


def apply_defaults(doc):
    company = frappe.defaults.get_user_default("Company") or frappe.defaults.get_global_default("company")
    if doc.meta.has_field("company") and not doc.get("company"):
        doc.company = company
    if doc.meta.has_field("transaction_date") and not doc.get("transaction_date"):
        doc.transaction_date = today()
    if doc.doctype == "Sales Order" and not doc.get("delivery_date"):
        doc.delivery_date = add_days(today(), 7)
    if doc.doctype in ("Purchase Order", "Material Request") and not doc.get("schedule_date"):
        doc.schedule_date = add_days(today(), 7)
    # The party lookup in v16 does not fall back to the settings' default price list
    if doc.meta.has_field("selling_price_list") and not doc.get("selling_price_list"):
        doc.selling_price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")
    if doc.meta.has_field("buying_price_list") and not doc.get("buying_price_list"):
        doc.buying_price_list = frappe.db.get_single_value("Buying Settings", "buying_price_list")
    if doc.doctype == "Quotation":
        doc.quotation_to = "Customer"
    if doc.doctype == "Material Request" and not doc.get("material_request_type"):
        doc.material_request_type = "Purchase"
    if doc.doctype == "ToDo" and not doc.get("allocated_to"):
        doc.allocated_to = frappe.session.user


def apply_item_defaults(doc, row):
    # No standard buying prices exist for most items; start from what we paid last time
    if doc.doctype == "Purchase Order" and not row.get("rate"):
        last, valuation = frappe.db.get_value("Item", row["item_code"], ["last_purchase_rate", "valuation_rate"])
        row["rate"] = last or valuation or 0
    if doc.doctype == "Sales Order":
        row.setdefault("delivery_date", doc.delivery_date)
    if doc.doctype in ("Purchase Order", "Material Request"):
        row.setdefault("schedule_date", doc.schedule_date)


def resolve_name(doctype, value, title_field):
    """Accept either the record ID or its display name, e.g. 'LAP-001' or 'Dell Latitude 5440 Laptop'."""
    if not value:
        raise ValueError(f"A {doctype} is required")
    if frappe.db.exists(doctype, value):
        return value

    # People say "Box Files" or "dell laptops": try exact, then contains, then every word (singular)
    words = [singular(w) for w in re.findall(r"[\w-]+", value.lower())]
    attempts = [
        {title_field: value},
        {title_field: ["like", f"%{value}%"]},
        {title_field: ["like", f"%{' '.join(words)}%"]},
        [[doctype, title_field, "like", f"%{w}%"] for w in words],
    ]
    for filters in attempts:
        matches = frappe.get_all(doctype, filters=filters, fields=["name", title_field], limit=6)
        if len(matches) == 1:
            return matches[0].name
        if len(matches) > 1:
            options = ", ".join(f"{m.name} ({m.get(title_field)})" for m in matches[:5])
            raise ValueError(f"{value!r} matches several {doctype} records: {options}. Ask the user which one.")

    suggestions = frappe.get_all(
        doctype, filters={title_field: ["like", f"%{words[0] if words else value}%"]}, pluck=title_field, limit=5
    )
    hint = f" Similar: {', '.join(suggestions)}." if suggestions else ""
    raise ValueError(f"No {doctype} matches {value!r}.{hint}")


def singular(word):
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def validate_and_preview(doc):
    """Run ERPNext's insert (validation, pricing, totals) and roll it back, keeping only a summary."""
    frappe.db.savepoint("erp_agent_preview")
    try:
        doc.insert()
        return summarize(doc)
    finally:
        frappe.db.rollback(save_point="erp_agent_preview")
        frappe.clear_messages()


def summarize(doc):
    spec = ACTION_DOCTYPES[doc.doctype]
    preview = {"doctype": doc.doctype, "fields": {}}
    if spec["party"]:
        preview["fields"]["Party"] = doc.get(spec["party"][0])
    for fieldname, label in (
        ("transaction_date", "Date"),
        ("delivery_date", "Delivery"),
        ("schedule_date", "Required by"),
        ("description", "Description"),
        ("date", "Due"),
        ("allocated_to", "Assigned to"),
    ):
        if doc.meta.has_field(fieldname) and doc.get(fieldname):
            preview["fields"][label] = str(doc.get(fieldname))
    if spec["items"]:
        preview["items"] = [
            {"item": row.item_name or row.item_code, "qty": row.qty, "rate": row.get("rate"), "amount": row.get("amount")}
            for row in doc.items[:PREVIEW_ITEM_LIMIT]
        ]
        preview["total"] = doc.get("grand_total")
    return preview


DRAFT_DOCUMENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "draft_document",
        "description": (
            "Prepare a new ERPNext document for the user to confirm. Only when the user asks to create "
            "something. Nothing is saved until the user clicks Confirm, and it is saved as a draft, never "
            "submitted. Customers, suppliers and items may be given by name. Examples: "
            "Sales Order {\"customer\": \"Vistara Traders\", \"items\": [{\"item_code\": \"Dell Latitude 5440 Laptop\", \"qty\": 20}]}; "
            "Quotation {\"party_name\": \"...\", \"items\": [...]}; "
            "Purchase Order {\"supplier\": \"Redington India\", \"items\": [...]}; "
            "Material Request {\"items\": [{\"item_code\": \"...\", \"qty\": 50}]}; "
            "ToDo {\"description\": \"Call Gujarat Water Board about overdue invoices\", \"date\": \"YYYY-MM-DD\"}. "
            "Dates and company are filled in automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "doctype": {"type": "string", "enum": list(ACTION_DOCTYPES)},
                "values": {"type": "object", "description": "Field values, with items as a list of {item_code, qty}."},
            },
            "required": ["doctype", "values"],
        },
    },
}
