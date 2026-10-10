"""A compact map of the DocTypes people ask about most, sent in the system prompt.

With it the model can usually go straight to get_list instead of spending round
trips (and tokens) on find_doctypes and describe_doctype. Field lists are checked
against live metadata, so a renamed or missing field is dropped rather than
taught to the model.
"""

import frappe

# DocType: (key fields, {child table field: key fields of the child DocType})
DATA_MAP = {
    "Sales Invoice": (
        "customer customer_name posting_date due_date territory customer_group grand_total "
        "base_net_total outstanding_amount status is_return",
        {"items": "item_code item_name item_group qty rate amount base_net_amount warehouse",
         "sales_team": "sales_person allocated_percentage allocated_amount"},
    ),
    "Sales Order": (
        "customer transaction_date delivery_date territory grand_total base_net_total status "
        "per_delivered per_billed",
        {"items": "item_code item_name qty delivered_qty rate amount"},
    ),
    "Purchase Invoice": (
        "supplier posting_date due_date grand_total base_net_total outstanding_amount status is_return",
        {"items": "item_code item_name item_group qty rate amount"},
    ),
    "Purchase Order": (
        "supplier transaction_date schedule_date grand_total status per_received per_billed",
        {"items": "item_code item_name qty received_qty rate amount"},
    ),
    "Payment Entry": ("payment_type party_type party posting_date paid_amount mode_of_payment", {}),
    "Customer": ("customer_name customer_group territory customer_type", {}),
    "Supplier": ("supplier_name supplier_group", {}),
    "Item": (
        "item_code item_name item_group stock_uom is_stock_item valuation_rate standard_rate",
        {"reorder_levels": "warehouse warehouse_reorder_level warehouse_reorder_qty"},
    ),
    "Bin": ("item_code warehouse actual_qty projected_qty reserved_qty ordered_qty stock_value", {}),
    "Stock Ledger Entry": (
        "item_code warehouse posting_date actual_qty qty_after_transaction stock_value_difference "
        "voucher_type is_cancelled",
        {},
    ),
    "GL Entry": ("account posting_date debit credit party_type party voucher_type is_cancelled", {}),
    "Account": ("account_name root_type account_type parent_account is_group", {}),
    "Employee": ("employee_name department designation date_of_joining status gender", {}),
}

HINTS = """\
- Revenue: SUM(Sales Invoice.base_net_total), docstatus=1, is_return=0. Region = territory (on the invoice).
- Open sales / purchase orders: docstatus=1 and status not in (Completed, Closed, Cancelled).
- Overdue receivables: Sales Invoice outstanding_amount > 0 and due_date < today, docstatus=1.
- Units sold per item: query Sales Invoice with `tabSales Invoice Item`.item_name / .qty (see get_list).
- Sales person performance: Sales Invoice with `tabSales Team`.sales_person / .allocated_amount.
- Stock on hand: Bin.actual_qty (per item and warehouse). Below reorder level: compare with Item reorder_levels.
- Expenses / P&L: first get_list Account names with root_type 'Expense' (or 'Income'), then SUM
  GL Entry debit (or credit) with account in that list, is_cancelled=0."""


def _existing(doctype, fields):
    """Field names that really exist; Select fields carry their allowed values, e.g.
    status(Draft|To Bill|Completed|...), so the model filters on real values."""
    meta = frappe.get_meta(doctype)
    names = []
    for fieldname in fields.split():
        field = meta.get_field(fieldname)
        if not field:
            continue
        if field.fieldtype == "Select" and field.options:
            options = [o for o in field.options.split("\n") if o.strip()]
            names.append(f"{fieldname}({'|'.join(options)})")
        else:
            names.append(fieldname)
    return names


def naming_hint():
    abbr = frappe.db.get_value("Company", frappe.defaults.get_global_default("company"), "abbr")
    if not abbr:
        return ""
    return (
        f"\n- Departments, warehouses, accounts and cost centers end with the company suffix ' - {abbr}' "
        f"(e.g. 'Sales - {abbr}', 'Stores - {abbr}', 'Salary - {abbr}'); filter with that full name "
        f"or with like, e.g. department like 'Sales%'."
    )


def build_data_map():
    lines = []
    for doctype, (fields, children) in DATA_MAP.items():
        if not frappe.db.exists("DocType", doctype):
            continue
        line = f"{doctype}: {', '.join(_existing(doctype, fields))}"
        meta = frappe.get_meta(doctype)
        for table_field, child_fields in children.items():
            child_doctype = meta.get_field(table_field).options if meta.has_field(table_field) else None
            if child_doctype:
                line += f"\n  child {table_field} -> {child_doctype}: {', '.join(_existing(child_doctype, child_fields))}"
        lines.append(line)
    return "\n".join(lines) + "\nCommon questions:\n" + HINTS + naming_hint()


def get_data_map():
    """Cached per site; metadata rarely changes and `bench migrate` clears the cache."""
    return frappe.cache.get_value("erp_agent:data_map", generator=build_data_map)
