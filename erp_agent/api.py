import json

import frappe

from erp_agent.agent import run_agent

CHAT_LIST_LIMIT = 50
TITLE_LENGTH = 80


@frappe.whitelist()
def ask(question, chat=None, context=None, run_id=None):
    """Answer a question about ERPNext data and save the exchange to an Agent Chat.

    chat: name of an existing Agent Chat to continue; a new one is created when empty
    context: what the user is looking at in desk, e.g. {"doctype": "Sales Invoice", "docname": "..."}
    run_id: client-generated id used to tag live step and text events for this question
    """
    doc = get_own_chat(chat) if chat else frappe.new_doc("Agent Chat")
    messages = load_messages(doc)
    history = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("content")]

    result = run_agent(
        question,
        history=history,
        context=frappe.parse_json(context) if context else None,
        run_id=run_id,
    )

    messages.append({"role": "user", "content": question})
    messages.append({
        "role": "assistant",
        "content": result["answer"],
        "steps": [{"label": s["label"], "ok": s["ok"]} for s in result["steps"]],
        "charts": result["charts"],
    })
    doc.title = doc.title or question[:TITLE_LENGTH]
    doc.messages = json.dumps(messages, default=str)
    doc.save()

    return {**result, "chat": doc.name, "title": doc.title}


@frappe.whitelist()
def list_chats():
    return frappe.get_all(
        "Agent Chat",
        filters={"owner": frappe.session.user},
        fields=["name", "title", "modified"],
        order_by="modified desc",
        limit=CHAT_LIST_LIMIT,
    )


@frappe.whitelist()
def get_chat(chat):
    doc = get_own_chat(chat)
    return {"name": doc.name, "title": doc.title, "messages": load_messages(doc)}


@frappe.whitelist(methods=["POST"])
def delete_chat(chat):
    get_own_chat(chat)
    frappe.delete_doc("Agent Chat", chat)


def get_own_chat(chat):
    """Chats are private: only the person who started one can read or continue it."""
    doc = frappe.get_doc("Agent Chat", chat)
    if doc.owner != frappe.session.user:
        raise frappe.PermissionError
    return doc


def load_messages(doc):
    return frappe.parse_json(doc.messages) or [] if doc.messages else []
