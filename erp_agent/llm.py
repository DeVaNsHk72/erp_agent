from types import SimpleNamespace

import frappe
from openai import OpenAI


def get_client():
    """Build a client from Agents Settings on every call, so settings changes apply immediately."""
    settings = frappe.get_single("Agents Settings")
    # Free tiers rate-limit per minute; the client waits (honouring Retry-After) and retries
    client = OpenAI(base_url=settings.base_url, api_key=settings.get_password("api_key"), max_retries=5)
    return client, settings.model


def chat(messages):
    client, model = get_client()
    response = client.chat.completions.create(model=model, messages=messages)
    return response.choices[0].message.content


def stream_complete(messages, tools=None, on_text=None):
    """Like complete(), but streams the reply and calls on_text(delta) for each piece of text.

    Returns an object shaped like the non-streaming message (`.content`, `.tool_calls` with
    `.id` and `.function.name/.arguments`) so callers can treat both the same, plus usage.
    """
    client, model = get_client()
    kwargs = {"model": model, "messages": messages, "stream": True}
    if tools:
        kwargs["tools"] = tools
    stream = client.chat.completions.create(**kwargs)

    content, calls, usage = [], {}, None
    for chunk in stream:
        # Providers report token usage on the final chunk, in different places
        usage = chunk.usage or getattr(getattr(chunk, "x_groq", None), "usage", None) or usage
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            content.append(delta.content)
            if on_text:
                on_text(delta.content)
        # Tool calls arrive in fragments keyed by index; the arguments JSON is split across chunks
        for tc in delta.tool_calls or []:
            call = calls.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
            if tc.id:
                call["id"] = tc.id
            if tc.function and tc.function.name:
                call["name"] += tc.function.name
            if tc.function and tc.function.arguments:
                call["arguments"] += tc.function.arguments

    tool_calls = [
        SimpleNamespace(id=c["id"], function=SimpleNamespace(name=c["name"], arguments=c["arguments"]))
        for _, c in sorted(calls.items())
    ]
    message = SimpleNamespace(content="".join(content) or None, tool_calls=tool_calls or None)
    return message, usage


def complete(messages, tools=None):
    """Return the whole assistant message (tool requests live in `tool_calls`) and token usage."""
    client, model = get_client()
    kwargs = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message, response.usage
