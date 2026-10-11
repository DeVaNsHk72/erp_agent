"""A daily business digest, saved as an Agent Chat for each System Manager.

Built from plain SQL rather than the model: a morning summary must never contain an invented
number, and it should not depend on (or spend) the LLM provider's quota. Money is written as
₹<raw number> so the chat UI applies Indian digit grouping, like it does for agent answers.

Scheduled at 08:00 by hooks.scheduler_events; to build one now:
    bench --site erpnext.localhost execute erp_agent.digest.send_daily_digest
"""

import json
from datetime import date, timedelta

import frappe

DIGEST_ROLE = "System Manager"
TOP_N = 3
CHART_DAYS = 14


def send_daily_digest():
    if not frappe.db.get_single_value("Agents Settings", "daily_digest"):
        return
    content, chart = build_digest(date.fromisoformat(frappe.utils.today()))
    title = f"Daily digest · {frappe.utils.formatdate(frappe.utils.today(), 'd MMM yyyy')}"
    for user in recipients():
        save_digest_chat(user, title, content, chart)


def recipients():
    """Enabled desk users with the System Manager role."""
    with_role = frappe.get_all("Has Role", filters={"role": DIGEST_ROLE, "parenttype": "User"}, pluck="parent")
    return frappe.get_all(
        "User", filters={"name": ["in", with_role], "enabled": 1, "user_type": "System User"}, pluck="name"
    )


def save_digest_chat(user, title, content, chart):
    message = {"role": "assistant", "content": content, "steps": [], "charts": [chart], "actions": []}
    chat = frappe.get_doc({"doctype": "Agent Chat", "title": title, "messages": json.dumps([message], default=str)})
    chat.insert(ignore_permissions=True)
    # Chats are private to their owner; the scheduler runs as Administrator, so hand it over
    frappe.db.set_value("Agent Chat", chat.name, "owner", user, update_modified=False)
    frappe.get_doc({
        "doctype": "Notification Log",
        "for_user": user,
        "type": "Alert",
        "subject": f"{title} is ready in ERP Agent",
        "email_content": "Open the ERP Agent panel (Cmd/Ctrl + I) to read it.",
        "document_type": "Agent Chat",
        "document_name": chat.name,
    }).insert(ignore_permissions=True)
    return chat.name


def build_digest(today):
    yesterday = today - timedelta(days=1)
    week_ago = yesterday - timedelta(days=7)
    # The month yesterday belongs to, so the digest on the 1st covers the whole previous month
    month_start = yesterday.replace(day=1)
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    prev_month_end = min(prev_month_start + (yesterday - month_start), month_start - timedelta(days=1))

    sold_yesterday = sales_between(yesterday, yesterday)
    sold_week_ago = sales_between(week_ago, week_ago)
    mtd = sales_between(month_start, yesterday)
    prev_mtd = sales_between(prev_month_start, prev_month_end)

    overdue_total, overdue_count = frappe.db.sql(
        "select coalesce(sum(outstanding_amount), 0), count(*) from `tabSales Invoice` "
        "where docstatus = 1 and outstanding_amount > 0 and due_date < %s",
        today,
    )[0]
    newly_overdue_total, newly_overdue_count = frappe.db.sql(
        "select coalesce(sum(outstanding_amount), 0), count(*) from `tabSales Invoice` "
        "where docstatus = 1 and outstanding_amount > 0 and due_date = %s",
        yesterday,
    )[0]
    top_debtors = frappe.db.sql(
        "select customer, sum(outstanding_amount) from `tabSales Invoice` "
        "where docstatus = 1 and outstanding_amount > 0 and due_date < %s "
        "group by customer order by 2 desc limit %s",
        (today, TOP_N),
    )
    low_stock = frappe.db.sql(
        "select i.item_name, b.actual_qty, r.warehouse_reorder_level from tabBin b "
        "join `tabItem Reorder` r on r.parent = b.item_code and r.warehouse = b.warehouse "
        "join tabItem i on i.name = b.item_code "
        "where b.actual_qty < r.warehouse_reorder_level order by b.actual_qty / r.warehouse_reorder_level limit 5"
    )
    late_orders = frappe.db.sql(
        "select count(*), coalesce(sum(base_net_total), 0) from `tabSales Order` where docstatus = 1 "
        "and status in ('To Deliver and Bill', 'To Deliver') and delivery_date < %s",
        today,
    )[0]

    lines = [f"**Good morning — here's {frappe.defaults.get_global_default('company')} as of {frappe.utils.formatdate(yesterday, 'd MMM')}.**", ""]
    lines.append("**Sales**")
    lines.append(
        f"- Yesterday: ₹{sold_yesterday[0]:.2f} from {plural(sold_yesterday[1], 'invoice')} "
        f"({change(sold_yesterday[0], sold_week_ago[0])} vs the same day last week)"
    )
    lines.append(
        f"- {frappe.utils.formatdate(month_start, 'MMMM')} so far: ₹{mtd[0]:.2f} ({change(mtd[0], prev_mtd[0])} vs the same days last month)"
    )
    lines.append("")
    lines.append("**Money owed to us**")
    lines.append(f"- Overdue: ₹{overdue_total:.2f} across {plural(overdue_count, 'invoice')}")
    if newly_overdue_count:
        lines.append(f"- Newly overdue since yesterday: ₹{newly_overdue_total:.2f} ({plural(newly_overdue_count, 'invoice')})")
    if top_debtors:
        lines.append("- Biggest overdue: " + "; ".join(f"{name} ₹{amount:.2f}" for name, amount in top_debtors))
    lines.append("")
    lines.append("**Needs attention**")
    if low_stock:
        lines.append(
            "- Below reorder level: "
            + "; ".join(f"{name} ({qty:g} left, reorder at {level:g})" for name, qty, level in low_stock)
        )
    else:
        lines.append("- No items below their reorder level")
    if late_orders[0]:
        lines.append(f"- {plural(late_orders[0], 'sales order')} past their delivery date (₹{late_orders[1]:.2f})")
    else:
        lines.append("- No sales orders are past their delivery date")
    lines.append("")
    lines.append("_Built from live ERPNext data. Ask a follow-up question below for details._")

    return "\n".join(lines), daily_sales_chart(today)


def sales_between(start, end):
    total, count = frappe.db.sql(
        "select coalesce(sum(base_net_total), 0), count(*) from `tabSales Invoice` "
        "where docstatus = 1 and is_return = 0 and posting_date between %s and %s",
        (start, end),
    )[0]
    return float(total), count


def plural(count, noun):
    return f"{count} {noun}{'' if count == 1 else 's'}"


def change(current, previous):
    if not previous:
        return "no sales to compare" if not current else "new"
    pct = (current - previous) / previous * 100
    return f"{'up' if pct >= 0 else 'down'} {abs(pct):.0f}%"


def daily_sales_chart(today):
    start = today - timedelta(days=CHART_DAYS)
    totals = dict(
        frappe.db.sql(
            "select posting_date, sum(base_net_total) from `tabSales Invoice` where docstatus = 1 "
            "and is_return = 0 and posting_date >= %s and posting_date < %s group by posting_date",
            (start, today),
        )
    )
    data = [
        {"day": (start + timedelta(days=i)).strftime("%d %b"), "revenue": float(totals.get(start + timedelta(days=i), 0))}
        for i in range(CHART_DAYS)
    ]
    return {"title": f"Sales, last {CHART_DAYS} days", "chart_type": "bar", "data": data, "x": "day", "y": ["revenue"]}
