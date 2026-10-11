### Erp Agent

Ask questions about your ERPNext data in plain language, from a panel docked inside desk.
The agent looks up the right DocTypes, queries live data with the asking user's permissions,
and answers with text, tables and charts.

- **Side panel in desk**: resizable, pushes desk content aside instead of covering it, `Cmd/Ctrl + I` to toggle
- **Live progress**: each step ("Querying Sales Invoice") and the answer text stream in as they happen
- **Permission-aware tools**: `get_list` runs as the logged-in user, so answers only use data they can see
- **Read-only SQL** for joins across DocTypes, limited to System Managers (see below)
- **Saved chats** per user, with follow-up questions and page context ("this invoice")
- **Drafts with confirmation**: "create a sales order for Vistara Traders, 20 laptops" shows a preview card
  (validated and priced by ERPNext); nothing is saved until you click Create draft, and never submitted
- **Daily digest** at 08:00 for System Managers: sales, overdue invoices, low stock, late orders
- **Any OpenAI-compatible model**: Groq, OpenAI, Gemini, Ollama and others

### Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch version-16
bench install-app erp_agent
bench build --app erp_agent
```

`bench build` also builds the chat frontend in `frontend/` (Vue 3 + frappe-ui).

### Setup

1. Open **Agents Settings** in desk and fill in:
   - **Base URL**: e.g. `https://api.groq.com/openai/v1`
   - **Model**: a model with tool calling, e.g. `openai/gpt-oss-120b`
   - **API key**: stored encrypted
2. Optional, for the read-only SQL tool: create a MariaDB user that can only `SELECT` from the
   site's `tab*` tables (not `__Auth`). Needs a MariaDB admin login once:

   ```bash
   bench --site $SITE execute erp_agent.sql_tool.create_readonly_user \
       --kwargs "{'admin_user': '...', 'admin_password': '...'}"
   ```

   Re-run it after installing apps that add tables.
3. The daily digest is on by default (Agents Settings → *Send a daily digest*). Send one now with
   `bench --site $SITE execute erp_agent.digest.send_daily_digest`.

### Permissions

The agent never sees more than the person asking. `get_list`, `describe_doctype` and `find_doctypes`
run as the logged-in user, so ERPNext roles and User Permissions apply (a Sales User limited to one
territory only gets that territory's numbers). Drafts are created as that user too. Raw SQL is only
offered to System Managers. `/agent` is limited to desk (System) users, and chats are private to
their owner.

### How it works

| File | Role |
| --- | --- |
| `erp_agent/agent.py` | Tool-calling loop: ask the model, run the tools it requests, repeat until it answers |
| `erp_agent/tools.py` | `find_doctypes`, `describe_doctype`, `get_list`, `render_chart` and their schemas |
| `erp_agent/data_map.py` | Compact map of common DocTypes and fields, checked against live metadata, so most questions need one query |
| `erp_agent/actions.py` | Draft documents: validate and price in a rolled-back savepoint, create only on confirm |
| `erp_agent/digest.py` | The daily digest, built from SQL |
| `erp_agent/sql_tool.py` | Read-only SQL: role check, query validation, SELECT-only database user, time and row limits |
| `erp_agent/llm.py` | OpenAI-compatible client with streaming |
| `erp_agent/api.py` | Whitelisted endpoints: `ask`, `list_chats`, `get_chat`, `delete_chat`, `confirm_action`, `cancel_action` |
| `erp_agent/public/js/agent_panel.bundle.js` | The desk side panel |
| `frontend/` | The chat app served at `/agent` |

### Benchmark

`erp_agent/eval.py` asks 28 questions (sales, receivables, stock, HR, follow-ups, page context, an
unknown customer, drafting documents, and questions as a territory-restricted user) and grades the answers against values computed live from the database:

```bash
bench --site $SITE execute erp_agent.eval.run
bench --site $SITE execute erp_agent.eval.run --kwargs "{'areas': ['stock', 'hr'], 'pause': 5}"
```

Results are written to `logs/erp_agent_eval-<timestamp>.json`. The expected values assume the
demo dataset this was developed against; adjust the cases for your own data.

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/erp_agent
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
