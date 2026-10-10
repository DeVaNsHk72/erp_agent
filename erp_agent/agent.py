import json
import time

import frappe
import openai

from erp_agent.data_map import get_data_map
from erp_agent.llm import complete, stream_complete
from erp_agent.tools import get_tool_schemas, run_tool

MAX_STEPS = 8
# Tool results are truncated to this many characters before going back to the model
MAX_RESULT_CHARS = 12000
# How many earlier chat turns (user + assistant text) to send with each question
MAX_HISTORY_TURNS = 6
STEP_EVENT = "erp_agent_step"
TEXT_EVENT = "erp_agent_text"


class TextStreamer:
    """Batches streamed answer text into realtime events: one event per token would flood
    the socket, so text is sent every ~80ms or 40 characters."""

    def __init__(self, run_id):
        self.run_id = run_id
        self.buffer = ""
        self.last_flush = time.monotonic()

    def __call__(self, delta):
        self.buffer += delta
        if len(self.buffer) >= 40 or time.monotonic() - self.last_flush > 0.08:
            self.flush()

    def flush(self):
        if self.buffer:
            frappe.publish_realtime(
                TEXT_EVENT, {"run_id": self.run_id, "delta": self.buffer}, user=frappe.session.user
            )
            self.buffer = ""
        self.last_flush = time.monotonic()


def run_agent(question, history=None, context=None, run_id=None):
    messages = [{"role": "system", "content": system_prompt(context)}]
    for turn in (history or [])[-MAX_HISTORY_TURNS:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})

    steps = []  # record of tools used, for debugging and the UI
    charts = []
    usage = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def finish(answer):
        return {"answer": answer, "steps": steps, "charts": charts, "usage": usage}

    streamer = TextStreamer(run_id) if run_id else None
    tool_schemas = get_tool_schemas()

    for _ in range(MAX_STEPS):
        try:
            if streamer:
                msg, call_usage = stream_complete(messages, tool_schemas, on_text=streamer)
                streamer.flush()
            else:
                msg, call_usage = complete(messages, tool_schemas)
        except openai.RateLimitError:
            return finish(
                "The AI provider's rate limit was reached (too many tokens this minute). "
                "Wait a minute and ask again, or use a plan with a higher limit."
            )
        usage["llm_calls"] += 1
        if call_usage:
            usage["prompt_tokens"] += call_usage.prompt_tokens or 0
            usage["completion_tokens"] += call_usage.completion_tokens or 0

        if not msg.tool_calls:
            return finish(msg.content)

        # Rebuild the assistant message by hand: the provider's reply carries extra
        # fields (e.g. reasoning) that it rejects when they are sent back.
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            ok, args = True, {}
            try:
                args = json.loads(tc.function.arguments or "{}")
                publish_step(run_id, tc.function.name, args, "running")
                result = run_tool(tc.function.name, args)
            except Exception as e:
                # Hand the error back so the model can correct its call instead of crashing
                ok = False
                result = {"error": str(e)}
                frappe.clear_messages()

            if ok and tc.function.name == "render_chart":
                charts.append(args)

            content = json.dumps(result, default=str)
            if len(content) > MAX_RESULT_CHARS:
                content = content[:MAX_RESULT_CHARS] + " ...[truncated; narrow the query]"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
            step = {"tool": tc.function.name, "args": args, "ok": ok, "label": step_label(tc.function.name, args)}
            steps.append(step)
            publish_step(run_id, tc.function.name, args, "done" if ok else "error")

    return finish("I couldn't finish within the step limit. Try a more specific question.")


def step_label(tool, args):
    """Plain-language description of a tool call, shown in the chat while the agent works."""
    if tool == "find_doctypes":
        return f"Looking for tables matching “{args.get('search', '')}”"
    if tool == "describe_doctype":
        return f"Reading the {args.get('doctype', '')} fields"
    if tool == "get_list":
        return f"Querying {args.get('doctype', '')}"
    if tool == "run_sql":
        return "Running a database query"
    if tool == "render_chart":
        return f"Drawing chart: {args.get('title', '')}"
    return tool


def publish_step(run_id, tool, args, status):
    if not run_id:
        return
    frappe.publish_realtime(
        STEP_EVENT,
        {"run_id": run_id, "tool": tool, "label": step_label(tool, args), "status": status},
        user=frappe.session.user,
    )


def system_prompt(context=None):
    prompt = f"""You are a data analyst for the company {frappe.defaults.get_global_default("company")}, inside ERPNext.
Today is {frappe.utils.today()}. Currency is INR. The fiscal year runs April to March.
Rules:
- Answer only from data you fetched with tools. Never invent numbers.
- The data map below lists the main DocTypes and their real field names. For anything it covers,
  call get_list directly; use find_doctypes / describe_doctype only for DocTypes or fields not in it.
- If a question needs fields from two unrelated DocTypes (a join, e.g. invoices by customer group),
  use run_sql if you have it; otherwise say which data you could not combine.
- Count only submitted documents: filter docstatus = 1.
- Prefer SUM/COUNT with group_by over fetching many raw rows.
- Keep answers short. Use Markdown; use a table for lists of more than 3 rows.
- Write money as ₹ followed by the exact number from the data with no commas, e.g. ₹34423819.35.
  The app adds Indian digit grouping itself; never group digits or convert to lakh/crore yourself.
- When a trend or comparison would help, call the render_chart tool with the rows you fetched.
  Never write chart JSON, code blocks or HTML into the answer; the tool draws the chart.

DATA MAP
{get_data_map()}"""
    if context and context.get("doctype"):
        viewing = context["doctype"] + (f" {context['docname']}" if context.get("docname") else " (list view)")
        prompt += f"\nThe user is currently viewing {viewing} in ERPNext. 'This' or 'it' likely refers to that."
    return prompt
