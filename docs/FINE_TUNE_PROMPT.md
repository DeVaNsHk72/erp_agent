# Fine-tune a small, fast, ERPNext-native model for erp_agent

**Brief for Claude running on a GPU machine.** Read it end to end before doing anything, then work
through the phases in order. You start with no context; everything you need is in this file and in
this repository.

---

## 0. For the human: how to start

On the GPU machine, after cloning this repo, give Claude this prompt:

> Read `docs/FINE_TUNE_PROMPT.md` in this repository and execute it phase by phase. Start with
> Phase 0 and report the hardware check and your model choice before installing anything large.
> Stop and ask me at every "Stop and ask" point.

---

## 1. Mission

`erp_agent` is a Frappe app that answers questions about ERPNext data and drafts documents, by
letting an LLM call tools (`get_list`, `run_sql`, `draft_document`, ...). Today it runs on
`openai/gpt-oss-120b` through Groq. That is accurate but **rate-limited and paid per token**: the
free tier allows 8,000 tokens per minute and 200,000 per day, and one question costs 3,000-7,000.

Build a **small open model, fine-tuned for this agent and ERPNext**, that is:

| Goal | What it means here |
| --- | --- |
| **Accurate** | Writes the correct tool call in one step; matches the current model on the benchmark; never leaks data across permissions; never claims a draft was created |
| **Fast** | One LLM call in well under a second on the target GPU |
| **Cheap** | Self-hosted, so the cost per 1,000 questions is a fraction of a hosted 120B model |
| **ERPNext-native** | Knows Frappe v16 query rules and ERPNext conventions, but **reads company-specific facts from the prompt** instead of memorising one company |

"Never makes mistakes" is not achievable by any model. Get as close as possible with three things,
all in this brief: verified training data, hard evaluation gates, and the agent's existing safety
net (tool errors are fed back to the model, permissions are enforced in code, writes need a human
click).

---

## 2. Ground rules (non-negotiable)

1. **Work on a branch.** `git checkout -b finetune` in this repo. Put all fine-tuning code under
   `finetune/` at the repo root. Do not change `erp_agent/` behaviour without asking (see §12).
2. **Large files stay out of git:** datasets, checkpoints, merged models and logs go under
   `~/erp-finetune/` and in `.gitignore`. Commit code, configs and the final report only.
3. **Commits use Conventional Commits** (`feat(finetune): ...`, `test(finetune): ...`) and carry
   **no AI or Claude attribution** of any kind: no `Co-Authored-By`, no "Generated with" lines.
4. **Do not push** to any remote, and do not upload any dataset or model anywhere (including the
   Hugging Face Hub), without asking first.
5. **Secrets** (API keys, DB passwords) only ever come from environment variables or
   `site_config.json`, never in code, commits, logs or datasets.
6. **No real business data.** Only the synthetic companies produced by `scripts/seed_sample_data.py`.
7. **Never train on the benchmark.** The questions in `erp_agent/eval.py` (`CASES`) and anything
   generated for the held-out company are test-only. Prove it with a decontamination check (§7.5).
8. **Report honestly.** Every number in your final report must come from a run you actually did.
   If a gate fails, say which, by how much, and what you tried.

---

## 3. The product you are fine-tuning for

### 3.1 Architecture

```
Desk side panel (Vue, frontend/)  ->  erp_agent.api.ask  ->  erp_agent.agent.run_agent
                                                               |  loop, max 8 steps:
                                                               |  LLM(messages, tools) -> tool_calls?
                                                               |    yes: run_tool -> append result -> repeat
                                                               |    no:  final answer (Markdown)
```

Read these files first, in this order:

| File | What to learn from it |
| --- | --- |
| `erp_agent/agent.py` | `run_agent` (the loop and **exact message format**), `system_prompt()`, `fiscal_year_note()`, the 12,000-character tool-result truncation |
| `erp_agent/tools.py` | `TOOL_SCHEMAS`, `get_tool_schemas()` (tools differ per user), `run_tool`, and the tool implementations |
| `erp_agent/data_map.py` | The DocType and field map injected into the system prompt, including Select options and the company-suffix hint |
| `erp_agent/sql_tool.py` | `run_sql` (System Managers only) and its validator |
| `erp_agent/actions.py` | `draft_document`: proposals only, the user confirms in the UI |
| `erp_agent/llm.py` | OpenAI-compatible client: the model is just `base_url` + `model` in **Agents Settings** |
| `erp_agent/eval.py` | The benchmark: 28 questions graded against values computed live with SQL |
| `scripts/seed_sample_data.py` | Generates a realistic company with 18 months of transactions |

### 3.2 The exact conversation format

The model sees OpenAI chat-completions messages plus `tools`:

1. `system`: `system_prompt(context)`: company, today, fiscal years, rules, and the data map.
2. Up to 6 earlier `user` / `assistant` text turns (history).
3. `user`: the question.
4. The model replies **either** with `tool_calls`
   (`[{"id", "type": "function", "function": {"name", "arguments": "<JSON string>"}}]`)
   **or** with a final Markdown answer.
5. Each tool result comes back as `{"role": "tool", "tool_call_id": ..., "content": "<JSON>"}`.
   Errors come back as `{"error": "..."}`, and the model is expected to correct itself.

**Never hand-write prompts or tool schemas for training data.** Generate them by calling the real
`system_prompt()` and `get_tool_schemas()` inside a bench session, so training input matches
production byte for byte. If the agent's prompt changes later, regenerate the data.

### 3.3 What a correct tool call looks like

```json
{"name": "get_list", "arguments": {
  "doctype": "Sales Invoice",
  "fields": ["customer", {"SUM": "base_net_total", "as": "revenue"}],
  "filters": [["docstatus", "=", 1], ["is_return", "=", 0],
              ["posting_date", "between", ["2026-04-01", "2027-03-31"]]],
  "group_by": "customer", "order_by": "revenue desc", "limit_page_length": 5}}
```

---

## 4. ERPNext v16 facts the model must get right

Each of these has already caused a real failure in this project. The model must learn the **rules**,
and read the **values** (names, years, suffixes) from the prompt.

1. **Aggregates in `get_list` are dicts, never strings:** `{"SUM": "grand_total", "as": "total"}`,
   `{"COUNT": "*", "as": "n"}`. Strings like `"sum(grand_total)"` are rejected by Frappe v16.
   Allowed: COUNT, SUM, AVG, MAX, MIN, ABS, EXTRACT, LOCATE, TIMESTAMP, IFNULL, CONCAT, NOW, NULLIF,
   MONTHNAME, QUARTER, MONTH, YEAR. `order_by` may use the alias.
2. **Child tables** (for example invoice line items) are queried through the parent, with backticks:
   `` "`tabSales Invoice Item`.item_name" ``, `` {"SUM": "`tabSales Invoice Item`.qty", "as": "units"} ``.
3. **Submitted documents only:** `docstatus = 1`. Revenue also needs `is_return = 0`.
4. **Status values are specific:** "open sales orders" means
   `status not in (Completed, Closed, Cancelled)`. The allowed values are listed in the data map
   (`status(Draft|To Deliver and Bill|...)`). Counting all submitted orders was a real bug.
5. **Company suffix:** departments, warehouses, accounts and cost centers are named
   `"<name> - <ABBR>"` (`"Sales - N"`). Filtering on `"Sales"` returned 0, which was a real bug.
6. **Fiscal years** come from the prompt ("This fiscal year is 2026-2027 (2026-04-01 to
   2027-03-31)"). Picking the previous year was a real bug.
7. **Stock tables (`Bin`, `Stock Ledger Entry`) only store `item_code`.** Resolve a named item to
   its code via `Item.item_name` first. Answering 0 for a named item was a real bug.
8. **No join needed for region or segment:** Sales Invoice already has `territory` and
   `customer_group`.
9. **Permissions:** tools run as the asking user. On `PermissionError`, say the user lacks access,
   with **no numbers**. A Sales User cannot read Sales Invoice at all in v16. `run_sql` is only
   offered to System Managers; never assume it exists.
10. **Drafts:** `draft_document` only proposes a document. The answer must never say it was created;
    the user clicks "Create draft".
11. **Money format:** write `₹` followed by the raw number with no commas (`₹34423819.35`). The UI
    applies Indian digit grouping. Never convert to lakh or crore yourself.
12. **Unknown entities:** if a customer or item does not exist, say so. Never invent a number.
    Ambiguous names (three customers called "Vistara ...") lead to a question back to the user.

---

## 5. Targets and acceptance gates

Measure the **teacher** (current production model) on the same sets first (Phase 0). Gates are
relative to it.

| Gate | Requirement | Type |
| --- | --- | --- |
| Benchmark accuracy (`erp_agent.eval`, NoteGo site) | ≥ teacher − 2 cases | hard |
| Held-out company accuracy (§7.4) | ≥ teacher − 5 percentage points | hard |
| Permission leaks (`permissions` area, and any answer with a forbidden number) | **0** | hard |
| Drafts created or claimed without confirmation (`actions` area) | **0** | hard |
| Invalid tool-call JSON or unknown tool names | < 1% of calls | hard |
| LLM calls per question | ≤ teacher's average | soft |
| Latency per LLM call on the target GPU, 4 concurrent requests | p50 ≤ 400 ms, p95 ≤ 1.5 s | soft |
| Cost per 1,000 questions | Report against the teacher's current hosted price (look it up; don't guess) | report |

If the smallest model misses a hard gate, try the next size up (§6, Phase 1) before relaxing
anything. **Never relax a hard gate without asking.**

---

## 6. Phases

Each phase says what to do, what it produces, and when it is done. Finish one before starting the next.

### Phase 0: Environment, hardware and baseline

1. `nvidia-smi`. Record the GPU model, VRAM, CUDA driver, disk free and RAM.
   **Stop and ask** if VRAM < 16 GB or free disk < 150 GB.
2. Install a bare-metal ERPNext v16 bench (no Docker) with MariaDB 11.8, Redis, Node 24 and the
   Python version Frappe v16 requires. Then `bench get-app` this repo (branch `version-16`) and
   `bench install-app erp_agent`. Known gotchas from the original setup:
   - After `bench new-site`, check that ERPNext's `after_install` ran: `Contact` must have the custom
     field `is_billing_contact`. If it is missing (it fails silently when Redis is down), run
     `bench --site <site> execute erpnext.setup.install.after_install`.
   - Complete the setup wizard (programmatically is fine). Set **Selling Settings → default price list
     = Standard Selling** and **Buying Settings → Standard Buying**; v16 leaves them empty.
   - Restart `bench start` after every `get-app` or `new-app`.
   - `bench build --app erp_agent` also builds the Vue frontend.
3. Create the reference site and data:
   `env/bin/python apps/erp_agent/scripts/seed_sample_data.py` (defaults reproduce company "NoteGo",
   abbreviation "N"). Then run `erp_agent.sql_tool.create_readonly_user` (see README) and create the
   restricted test user exactly as `eval.py` expects (`sales.west@notego.test`, role Sales User,
   User Permission Territory = West India).
4. **Baseline the teacher:** point Agents Settings at the teacher (§8) and run
   `bench --site <site> execute erp_agent.eval.run`. Save the JSON from `logs/`. This is the bar.

**Done when:** the benchmark runs end to end against the teacher, and you have recorded its score,
average LLM calls and latency.

### Phase 1: Choose the base model

Pick the **current** best open instruct model with native tool calling and a permissive license
(Apache-2.0 or MIT) in the size class the GPU allows. Check Hugging Face model cards; don't rely on
memory. Prefer the Qwen family (strong tool calling, JSON and Hindi).

| VRAM | Train | Start with |
| --- | --- | --- |
| 16-23 GB | QLoRA 4-bit | ~3-4B |
| 24-47 GB | QLoRA 4-bit | ~7-8B |
| ≥ 48 GB | LoRA bf16 | ~7-8B; ~14B only if 8B misses gates |

Run the **untuned** candidate on the benchmark (served as in Phase 7). That's your "before" number.
Record the choice and the reasons in `finetune/REPORT.md`.

### Phase 2: Trace exporter

Write `finetune/export_context.py`, run with `bench --site <site> execute`. For a given user and
optional desk context, it returns the exact `system_prompt()` and `get_tool_schemas()`. Every
training example's system message and tools come from this.

**Done when:** an exported prompt for Administrator byte-matches what `run_agent` sends. Verify by
logging the request from a real `run_agent` call.

### Phase 3: Varied synthetic companies

One company teaches the model to memorise `"Sales - N"` and one fiscal year. Create **at least 5
training companies plus 1 held-out test company**, each on its own site, using the seed script's
environment variables (`ERP_SEED_SITE`, `ERP_SEED_COMPANY`, `ERP_SEED_ABBR`, `ERP_SEED_START`,
`ERP_SEED_END`, `ERP_SEED_RANDOM`). Vary them deliberately:

- abbreviations (2-4 letters), company names, and date ranges (so "today", "last quarter" and the
  fiscal year differ)
- product catalogues: extend the seed script with a `--catalog` option or environment variable
  (pharma, FMCG, auto parts, textiles...) rather than only the default IT distributor
- at least two companies with **custom fields** on Sales Invoice or Customer (e.g. `custom_channel`
  as a Select), so the model learns to use fields it finds in the data map
- the held-out company must differ from all training companies in abbreviation, catalogue, fiscal
  dates and at least one custom field

Commit changes to the seed script on the `finetune` branch. Keep the original defaults working:
NoteGo must still reproduce exactly.

### Phase 4: Data generation

All four sources produce examples in the format of §7.1 and are **verified by execution** (§7.3).

| Source | How | Target share |
| --- | --- | --- |
| **A. Template synthetic** | Question templates × entities and periods read from each site (top-N, totals, trends, counts, overdue, stock, payments, HR, by-region and by-group). **Your code** writes the correct tool call for each; run it; keep it if it matches an independent SQL ground truth | ~40% |
| **B. Verified teacher distillation** | Run the teacher through the real `run_agent` on thousands of varied questions; keep only full traces whose final answer passes the same grader as `eval.py` (numbers within 0.5%, names present) | ~30% |
| **C. Behaviour cases** | Permission refusals (run as restricted users), unknown entity → "not found", ambiguous name → ask, drafts (proposal and wording, never "created"), no `run_sql` for non-admins, error recovery (a wrong call → `{"error": ...}` → corrected call) | ~20% |
| **D. Multi-turn and context** | Follow-ups that depend on history ("how much do *they* owe?"), and desk context ("this invoice"), using `system_prompt(context)` | ~10% |

Rephrase questions with the teacher into casual English, terse operator style ("overdue amt?"),
and **Hinglish** ("kal kitna becha?"), keeping the ground-truth tool call unchanged. Target about
**6,000 training examples**, 500 validation (training companies) and 400 test (held-out company
only).

Teacher access: Groq's free tier (200k tokens/day) is far too small for this. **Stop and ask** for
a paid key, or serve the teacher locally if VRAM allows (an 80 GB GPU can run gpt-oss-120b).

### Phase 5: Dataset assembly and checks

Write `finetune/build_dataset.py`. It merges the sources, deduplicates (exact and near-duplicate
questions), balances areas and companies, splits by **company** (held-out company only in test),
and writes `train.jsonl`, `val.jsonl`, `test.jsonl` plus `stats.md` (counts per source, area,
company and language, token-length histogram). It also runs the decontamination check (§7.5).

**Done when:** all checks pass and `stats.md` shows the target mix.

### Phase 6: Training

| Setting | Start value |
| --- | --- |
| Library | Unsloth (or TRL SFTTrainer + PEFT) |
| Method | QLoRA 4-bit (or LoRA bf16 on ≥ 48 GB) |
| LoRA | r=16, alpha=32, dropout 0.05, all attention and MLP projections |
| Learning rate | 2e-4, cosine, 3% warmup |
| Epochs | 2-3, keep the best checkpoint by **validation tool-call exact match**, not loss |
| Max sequence length | 8192 (the system prompt with data map is ~3-4k tokens) |
| Loss | **Assistant turns only** (tool calls and final answers); mask system, user and tool messages |
| Chat template | The model's own tool-calling template, the same one vLLM uses when serving |

Log training and validation curves to `~/erp-finetune/runs/` and summarise them in the report.
Save the LoRA adapter and a merged model.

### Phase 7: Serving

Serve with **vLLM's OpenAI-compatible server**, with tool calling enabled
(`--enable-auto-tool-choice` and the tool-call parser the model card specifies, e.g. `hermes` for
Qwen). Then:

1. Point **Agents Settings** at it: `base_url = http://<gpu-host>:<port>/v1`, `model` = the served
   name, any non-empty API key. **No code change in erp_agent should be needed.** If one is,
   stop and ask.
2. Try cheaper serving: an AWQ 4-bit or FP8 quantised version. Keep the cheapest one that still
   passes every hard gate.
3. Measure p50/p95 latency and throughput at concurrency 1, 4 and 16, and the cost per 1,000
   questions from the GPU's hourly price.

### Phase 8: Evaluation

Run all of these for the teacher, the untuned base and the fine-tuned model (and each quantisation):

1. `erp_agent.eval.run` on the NoteGo site (use `pause=0` locally; there's no rate limit).
2. The same grader on the **held-out company** test set.
3. Tool-call validity: share of calls that parse, name a real tool and pass its argument schema.
4. Average LLM calls per question.
5. The three hard-gate checks: permissions, drafts, invalid calls.

Put one table with all models side by side in the report.

### Phase 9 (optional, ask first): Router

If the fine-tuned model passes most gates but not all, propose a router in `erp_agent/llm.py`:
small model first, falling back to the teacher when a tool call fails twice, the JSON is invalid,
or the question is outside the trained areas. **Ask before changing erp_agent.**

---

## 7. Data specification

### 7.1 Format

One JSON object per line, in the same OpenAI format `run_agent` uses:

```json
{"messages": [
  {"role": "system", "content": "<exact system_prompt() for this company, user, date and context>"},
  {"role": "user", "content": "kitna paisa overdue hai abhi?"},
  {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function",
    "function": {"name": "get_list", "arguments": "{\"doctype\":\"Sales Invoice\",\"fields\":[{\"SUM\":\"outstanding_amount\",\"as\":\"overdue\"},{\"COUNT\":\"*\",\"as\":\"n\"}],\"filters\":[[\"docstatus\",\"=\",1],[\"outstanding_amount\",\">\",0],[\"due_date\",\"<\",\"2026-10-11\"]]}"}}]},
  {"role": "tool", "tool_call_id": "call_1", "content": "[{\"overdue\": 1678650.0, \"n\": 8}]"},
  {"role": "assistant", "content": "₹1678650 is overdue across 8 invoices."}
 ],
 "tools": ["<exact get_tool_schemas() for this user>"],
 "meta": {"company": "...", "source": "template|teacher|behaviour|multiturn", "area": "...", "lang": "en|hinglish", "verified": true}}
```

`meta` is for bookkeeping only. Strip it before training.

### 7.2 Mix

| Area | Share |
| --- | --- |
| Sales and revenue (incl. by region/group, trends with `render_chart`) | 20% |
| Receivables and payments | 15% |
| Stock and items | 15% |
| Orders (sales and purchase) and purchases | 15% |
| Finance and GL, HR | 10% |
| Drafts (`draft_document`) | 10% |
| Permissions, unknown entities, ambiguity, error recovery | 10% |
| Multi-turn and desk context | 5% |

Languages: about 70% English, 20% terse operator style, 10% Hinglish.

### 7.3 Verification (every example)

- The tool call is executed with `run_tool` **as the example's user** on that company's site.
- The final numbers are checked against an independent SQL query, with the `eval.py` grading logic
  (0.5% tolerance, names present, forbidden numbers absent).
- Drafts: the document count must be unchanged after `draft_document`, and the answer must not say
  "created".
- Anything unverified is dropped. Don't hand-fix generated examples.

### 7.4 Held-out company

A full site the model never sees during training: different abbreviation, catalogue, fiscal dates
and custom fields. Its only purpose is to answer: **does the model read the context, or did it
memorise the training companies?**

### 7.5 Decontamination

No training or validation question may match an `eval.py` question, or any held-out-company
question, by normalised exact match or 8-gram overlap ≥ 0.6. Write the check into
`build_dataset.py` and report how many examples it removed.

### 7.6 Never include

Real customer data, API keys or passwords, the benchmark questions, held-out-company examples
(outside test), and unverified teacher output.

---

## 8. Teacher and baseline configuration

- Production today: `base_url = https://api.groq.com/openai/v1`, `model = openai/gpt-oss-120b`.
- Set models in **Agents Settings** (Desk → Agents Settings: base URL, model, API key).
  `erp_agent.llm.get_client` reads it on every call, so no restart is needed.
- Keep API keys in the environment or Agents Settings, never in code.

---

## 9. Deliverables

In the `finetune/` folder on the `finetune` branch:

1. Code: `export_context.py`, the generators, `build_dataset.py`, the training script and config,
   `serve.sh`, and `evaluate.py` (a wrapper around `erp_agent.eval` plus the held-out set).
2. `finetune/REPORT.md` (template below).
3. Outside git, under `~/erp-finetune/`: datasets, adapter, merged and quantised models, run logs.

### REPORT.md template

```
# erp_agent fine-tune report
## Hardware and cost basis      (GPU, VRAM, $/hour used for cost)
## Base model chosen and why
## Data                         (counts per source, area, company, language; decontamination removals)
## Training                     (settings, curves summary, best checkpoint and why)
## Results                      (one table: teacher | base | fine-tuned | each quantisation:
                                  benchmark score, held-out score, permission leaks, draft violations,
                                  invalid calls %, calls/question, p50/p95 latency, $/1,000 questions)
## Gates                        (each hard gate: pass/fail with numbers)
## Failure analysis             (every failed case: question, expected, got, likely cause)
## How to switch erp_agent to this model   (exact Agents Settings values and serve command)
## Recommendation               (ship / ship with router / don't ship yet, and why)
```

---

## 10. Stop and ask (don't guess)

- VRAM < 16 GB, or free disk < 150 GB
- The teacher needs a paid API key, or would cost more than **USD 20**
- Any change to `erp_agent/` code, prompts or tool schemas
- Relaxing any hard gate
- Pushing to a remote, or uploading data or models anywhere
- Anything that would use real (non-synthetic) business data
