import frappe

from erp_agent import sql_tool

# Field types that only shape the form layout and hold no data
LAYOUT_FIELDTYPES = {"Section Break", "Column Break", "Tab Break", "HTML", "Button", "Fold", "Heading"}
# Field types whose `options` is useful to the model (target DocType or allowed values)
OPTIONS_FIELDTYPES = {"Link", "Select", "Table", "Table MultiSelect", "Dynamic Link"}


def describe_doctype(doctype):
    """
    Describe the structure of a given doctype.

    :param doctype: The name of the doctype to describe.
    :return: A dictionary containing the doctype's metadata.
    """
    fields = frappe.get_meta(doctype).fields
    # One compact line per field ("fieldname: Type -> options (Label)") instead of a dict:
    # repeating dict keys for ~150 fields costs thousands of tokens per call.
    described = []
    for field in fields:
        if field.fieldtype in LAYOUT_FIELDTYPES or field.hidden:
            continue
        line = f"{field.fieldname}: {field.fieldtype}"
        if field.fieldtype in OPTIONS_FIELDTYPES and field.options:
            options = field.options.replace("\n", "|")
            line += f" -> {options}"
        if field.label and field.label.lower().replace(" ", "_") != field.fieldname:
            line += f" ({field.label})"
        described.append(line)
    return {"doctype": doctype, "fields": described}

def get_list(doctype, filters=None, fields=None, limit_page_length=20, limit_start=0, group_by=None, order_by=None):
    """
    Get a list of documents for a given doctype.

    :param doctype: The name of the doctype to fetch documents from.
    :param filters: A dictionary of filters to apply to the query.
    :param fields: A list of fields to retrieve for each document.
    :param limit_page_length: The maximum number of documents to return.
    :param limit_start: The starting index for pagination.
    :return: A list of documents matching the criteria.
    """
    if filters is None:
        filters = {}
    if fields is None:
        fields = ["*"]  # Fetch all fields by default
    limit_page_length = min(int(limit_page_length or 20), 100)

    return frappe.get_list(
        doctype,
        filters=filters,
        fields=fields,
        limit_page_length=limit_page_length,
        limit_start=limit_start,
        group_by=group_by,
        order_by=order_by
    )

def find_doctypes(search):
    """
    Find doctypes matching a search term.

    :param search: The search term to match against doctype names.
    :return: A list of matching doctypes.
    """
    return frappe.get_all(
        "DocType",
        filters={"name": ["like", f"%{search}%"], "istable": 0},
        pluck="name",
        order_by="name asc",
        limit=15,
    )

CHART_TYPES = {"bar", "line", "donut"}


def render_chart(title, chart_type, data, x, y):
    """
    Validate a chart for the chat UI. The agent collects the arguments and the
    frontend draws them; nothing is rendered on the server.

    :param title: Chart title.
    :param chart_type: One of bar, line, donut.
    :param data: Rows to plot, e.g. [{"month": "2026-01", "revenue": 120000}].
    :param x: Key in each row for the x axis (or donut category).
    :param y: Keys in each row to plot as values.
    :return: Confirmation for the model.
    """
    if chart_type not in CHART_TYPES:
        raise ValueError(f"chart_type must be one of {sorted(CHART_TYPES)}")
    if not data or not isinstance(data, list):
        raise ValueError("data must be a non-empty list of rows")
    if len(data) > 60:
        raise ValueError("Too many rows for a chart; aggregate to 60 rows or fewer")
    missing = [key for key in [x, *y] if key not in data[0]]
    if missing:
        raise ValueError(f"Keys {missing} are not in the data rows")
    return {"rendered": True, "note": "The chart is shown to the user; do not repeat all its numbers."}


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "find_doctypes",
            "description": (
                "Search ERPNext DocType (table) names. Use this first when you don't know "
                "which DocType holds the data, e.g. search 'invoice' to find 'Sales Invoice'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": "A word to search for, e.g. 'invoice', 'customer', 'stock'.",
                    }
                },
                "required": ["search"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_doctype",
            "description": (
                "List the fields (columns) of an ERPNext DocType with their types. "
                "Always call this before get_list so you use real field names, never guessed ones. "
                "Fields of type Table point to a child DocType (in options), e.g. items -> 'Sales Invoice Item'; "
                "describe that child DocType to see its fields."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doctype": {
                        "type": "string",
                        "description": "Exact DocType name, e.g. 'Sales Invoice'.",
                    }
                },
                "required": ["doctype"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_list",
            "description": (
                "Fetch rows, totals or counts from an ERPNext DocType. Results respect the "
                "current user's permissions. "
                "For totals and counts, use dict fields like {\"SUM\": \"grand_total\", \"as\": \"total\"} "
                "or {\"COUNT\": \"*\", \"as\": \"n\"}; never write SQL functions as strings like "
                "'sum(grand_total)'. Allowed functions: COUNT, SUM, AVG, MAX, MIN, YEAR, MONTH, QUARTER. "
                "For submitted (final) documents add the filter [\"docstatus\", \"=\", 1]. "
                "Child tables (e.g. invoice line items) cannot be queried on their own: query the parent DocType "
                "and write child columns as `tab<Child DocType>`.<field> with backticks, e.g. for units sold per item: "
                "doctype 'Sales Invoice', fields [\"`tabSales Invoice Item`.item_name\", "
                "{\"SUM\": \"`tabSales Invoice Item`.qty\", \"as\": \"units\"}], "
                "group_by \"`tabSales Invoice Item`.item_name\"."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doctype": {
                        "type": "string",
                        "description": "Exact DocType name, e.g. 'Sales Invoice'.",
                    },
                    "fields": {
                        "type": "array",
                        "items": {"anyOf": [{"type": "string"}, {"type": "object"}]},
                        "description": (
                            "Columns to return: field names as strings, or aggregate dicts. "
                            "Example: [\"customer\", {\"SUM\": \"grand_total\", \"as\": \"total\"}]"
                        ),
                    },
                    "filters": {
                        "type": "array",
                        "items": {"type": "array"},
                        "description": (
                            "List of [field, operator, value] conditions, all must match. "
                            "Operators: =, !=, >, <, >=, <=, like, in, not in, between. "
                            "Example: [[\"docstatus\", \"=\", 1], "
                            "[\"posting_date\", \"between\", [\"2026-01-01\", \"2026-03-31\"]]]"
                        ),
                    },
                    "group_by": {
                        "type": "string",
                        "description": "Field to group rows by when using SUM/COUNT, e.g. 'customer'.",
                    },
                    "order_by": {
                        "type": "string",
                        "description": "Sort order, e.g. 'total desc' or 'posting_date asc'.",
                    },
                    "limit_page_length": {
                        "type": "integer",
                        "description": "Maximum rows to return. Default 20; keep it small.",
                    },
                },
                "required": ["doctype", "fields"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_chart",
            "description": (
                "Show a chart to the user under your answer. Use it for trends over time (line), "
                "comparisons across categories (bar), or shares of a whole (donut). "
                "Pass rows you already fetched with get_list; aggregate first, at most 60 rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short chart title."},
                    "chart_type": {"type": "string", "enum": ["bar", "line", "donut"]},
                    "data": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Rows, e.g. [{\"month\": \"2026-01\", \"revenue\": 120000}]",
                    },
                    "x": {"type": "string", "description": "Row key for the x axis or donut category."},
                    "y": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Row keys to plot as values. Donut uses only the first.",
                    },
                },
                "required": ["title", "chart_type", "data", "x", "y"],
            },
        },
    },
]

def get_tool_schemas():
    """Tools for the current user: run_sql only for users allowed to bypass per-document permissions."""
    if sql_tool.can_use_sql() and sql_tool.is_configured():
        return [*TOOL_SCHEMAS, sql_tool.RUN_SQL_SCHEMA]
    return TOOL_SCHEMAS


def run_tool(name, args):
    """
    Run a tool function by name with the given arguments.

    :param name: The name of the tool function to run.
    :param args: A dictionary of arguments to pass to the tool function.
    :return: The result of the tool function.
    """
    tools = {
        "find_doctypes": find_doctypes,
        "describe_doctype": describe_doctype,
        "get_list": get_list,
        "render_chart": render_chart,
        "run_sql": sql_tool.run_sql,
    }
    if name not in tools:
        raise ValueError(f"Tool '{name}' not found.")
    return tools[name](**args)
