"""Read-only SQL for questions get_list cannot answer (joins across DocTypes).

Defence in depth, because SQL bypasses ERPNext's per-user permissions:
1. Only users with SQL_ROLE get the tool at all.
2. Queries are checked: one SELECT/WITH statement, no write or file keywords, no system tables.
3. They run as a separate MariaDB user that only has SELECT on the site's `tab*` tables
   (not __Auth, which holds password hashes), in a read-only transaction with a time limit.

One-time setup (needs a MariaDB admin login), from the bench directory:
    bench --site erpnext.localhost execute erp_agent.sql_tool.create_readonly_user \
        --kwargs "{'admin_user': '...', 'admin_password': '...'}"
"""

import re
import secrets
from decimal import Decimal

import frappe
import pymysql
from frappe.installer import update_site_config
from pymysql.cursors import DictCursor

SQL_ROLE = "System Manager"
MAX_ROWS = 200
TIMEOUT_SECONDS = 10
READONLY_USER = "erp_agent_ro"

FORBIDDEN_WORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|lock|unlock|call|"
    r"load_file|outfile|dumpfile|sleep|benchmark|handler|rename|kill|shutdown)\b",
    re.IGNORECASE,
)
FORBIDDEN_TABLES = re.compile(r"__auth|information_schema|performance_schema|\bmysql\.|\bsys\.", re.IGNORECASE)


# Keys of frappe's connection settings that pymysql.connect understands
CONNECTION_KEYS = {"host", "port", "unix_socket", "ssl", "database", "charset", "collation", "use_unicode", "local_infile"}


def connect(user, password, **extra):
    """Connect to the site database the same way frappe does, but as another MariaDB user."""
    settings = {k: v for k, v in frappe.db.get_connection_settings().items() if k in CONNECTION_KEYS}
    return pymysql.connect(**settings, user=user, password=password, **extra)


def can_use_sql(user=None):
    return SQL_ROLE in frappe.get_roles(user or frappe.session.user)


def is_configured():
    return bool(frappe.conf.get("erp_agent_ro_password"))


def validate_query(query):
    # Strip comments so keywords can't hide inside them
    sql = re.sub(r"/\*.*?\*/", " ", query, flags=re.DOTALL)
    sql = re.sub(r"(--|#)[^\n]*", " ", sql).strip().rstrip(";").strip()
    if ";" in sql:
        raise ValueError("Only one statement is allowed.")
    if not re.match(r"^(select|with)\b", sql, re.IGNORECASE):
        raise ValueError("Only SELECT (or WITH ... SELECT) queries are allowed.")
    if FORBIDDEN_WORDS.search(sql):
        raise ValueError(f"Keyword not allowed: {FORBIDDEN_WORDS.search(sql).group(0)}")
    if FORBIDDEN_TABLES.search(sql):
        raise ValueError("System tables are not allowed.")
    return sql


def run_sql(query):
    """Run one read-only SELECT and return up to MAX_ROWS rows."""
    if not can_use_sql():
        raise frappe.PermissionError(f"run_sql needs the {SQL_ROLE} role.")
    if not is_configured():
        raise ValueError("run_sql is not set up on this site (see erp_agent/sql_tool.py).")
    sql = validate_query(query)

    conn = connect(
        frappe.conf.get("erp_agent_ro_user", READONLY_USER),
        frappe.conf.get("erp_agent_ro_password"),
        cursorclass=DictCursor,
        read_timeout=TIMEOUT_SECONDS + 5,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET SESSION max_statement_time = {TIMEOUT_SECONDS}")
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute(sql)
            rows = cur.fetchmany(MAX_ROWS + 1)
    finally:
        conn.close()
    # MariaDB DECIMAL arrives as Decimal('257928471.250000000'); plain numbers read better
    rows = [{k: float(v) if isinstance(v, Decimal) else v for k, v in row.items()} for row in rows]
    return {"rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS}


def create_readonly_user(admin_user, admin_password):
    """Create (or refresh) the read-only MariaDB user and store its password in site_config.

    Re-run after installing apps that add tables, so the new tables are readable too.
    """
    db_name = frappe.conf.db_name
    password = frappe.conf.get("erp_agent_ro_password") or secrets.token_urlsafe(24)

    conn = connect(admin_user, admin_password)
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE USER IF NOT EXISTS %s@'localhost' IDENTIFIED BY %s", (READONLY_USER, password))
            cur.execute("ALTER USER %s@'localhost' IDENTIFIED BY %s", (READONLY_USER, password))
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name LIKE 'tab%%'",
                (db_name,),
            )
            tables = [row[0] for row in cur.fetchall()]
            for table in tables:
                cur.execute(f"GRANT SELECT ON `{db_name}`.`{table}` TO %s@'localhost'", (READONLY_USER,))
        conn.commit()
    finally:
        conn.close()

    update_site_config("erp_agent_ro_user", READONLY_USER)
    update_site_config("erp_agent_ro_password", password)
    print(f"{READONLY_USER}: SELECT granted on {len(tables)} tables of {db_name}")


RUN_SQL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": (
            "Run one read-only MariaDB SELECT for questions get_list cannot answer, mainly joins across "
            "DocTypes (e.g. revenue by customer group: Sales Invoice joined to Customer). Prefer get_list "
            "when one DocType is enough. Tables are named `tab<DocType>` in backticks, e.g. `tabSales Invoice`; "
            "child rows link to the parent through `parent`. Filter docstatus = 1 for transactions. "
            f"Returns at most {MAX_ROWS} rows; aggregate in SQL instead of fetching raw rows."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "A single SELECT statement."}},
            "required": ["query"],
        },
    },
}
