"""Seed ~18 months of realistic trading data into an ERPNext site.

NoteGo is modelled as a B2B distributor of IT hardware, office furniture,
stationery and IT services selling to customers across four Indian regions.
The script creates masters (items, customers with addresses, suppliers,
employees, sales persons), then simulates day by day from START to END:
purchase orders and invoices that replenish stock, sales orders and their
invoices, customer and supplier payments (some late, some outstanding), and
monthly expense journal entries. Documents are inserted in date order so stock
valuation never needs reposting.

Run from the bench directory (the company must already exist, e.g. from the setup wizard,
and Selling / Buying Settings need a default price list):
    env/bin/python apps/erp_agent/scripts/seed_sample_data.py

Settings come from environment variables, so several differently named companies can be
generated (useful for fine-tuning data); the defaults reproduce the original demo dataset:
    ERP_SEED_SITE=erpnext.localhost ERP_SEED_COMPANY=NoteGo ERP_SEED_ABBR=N
    ERP_SEED_START=2025-04-01 ERP_SEED_END=2026-10-08 ERP_SEED_RANDOM=42

Deterministic for a given ERP_SEED_RANDOM. Refuses to run if sales invoices exist.
"""

import math
import os
import random
import sys
import time
from collections import defaultdict
from datetime import date, timedelta

def find_bench(start):
    """The bench is the first parent directory holding both apps/ and sites/."""
    path = os.path.abspath(start)
    while path != os.path.dirname(path):
        if os.path.isdir(os.path.join(path, "sites")) and os.path.isdir(os.path.join(path, "apps")):
            return path
        path = os.path.dirname(path)
    sys.exit("Run this from inside a frappe-bench directory.")


BENCH = find_bench(os.getcwd())
os.chdir(os.path.join(BENCH, "sites"))

import frappe  # noqa: E402

SITE = os.environ.get("ERP_SEED_SITE", "erpnext.localhost")
COMPANY = os.environ.get("ERP_SEED_COMPANY", "NoteGo")
ABBR = os.environ.get("ERP_SEED_ABBR", "N")
WAREHOUSE = f"Stores - {ABBR}"
BANK = f"HDFC Current Account - {ABBR}"
START = date.fromisoformat(os.environ.get("ERP_SEED_START", "2025-04-01"))
END = date.fromisoformat(os.environ.get("ERP_SEED_END", "2026-10-08"))

random.seed(int(os.environ.get("ERP_SEED_RANDOM", "42")))

# ---------------------------------------------------------------- catalogue

# group: (code prefix, qty range per order line, group demand weight, items)
CATALOG = {
    "Laptops & Computers": ("LAP", (1, 12), 1.6, [
        ("Dell Latitude 5440 Laptop", 62000),
        ("HP ProBook 450 G10 Laptop", 58000),
        ("Lenovo ThinkPad E14 Laptop", 55000),
        ("Apple MacBook Air M3", 98000),
        ("Dell OptiPlex 7010 Desktop", 48000),
        ("HP 24-inch FHD Monitor", 11000),
        ("Dell 27-inch 4K Monitor", 26000),
        ("Logitech MK270 Keyboard Mouse Combo", 1500),
        ("USB-C Laptop Docking Station", 9000),
    ]),
    "Mobile & Accessories": ("MOB", (2, 30), 2.0, [
        ("Samsung Galaxy A55", 33000),
        ("Apple iPhone 16", 72000),
        ("OnePlus Nord CE4", 22000),
        ("USB-C Fast Charger 65W", 1800),
        ("Power Bank 20000mAh", 1600),
        ("Bluetooth Headset", 2200),
        ("Rugged Phone Case", 400),
    ]),
    "Office Furniture": ("FUR", (1, 15), 1.0, [
        ("Ergonomic Office Chair", 9500),
        ("Executive Desk 6ft", 18000),
        ("Conference Table 10-Seater", 42000),
        ("Steel Filing Cabinet", 8500),
        ("Visitor Chair", 3200),
        ("Height Adjustable Desk", 28000),
    ]),
    "Stationery": ("STN", (10, 200), 3.0, [
        ("A4 Copier Paper Ream", 280),
        ("Ballpoint Pens Box of 50", 250),
        ("Spiral Notebook A5", 60),
        ("Whiteboard Markers Set", 180),
        ("Sticky Notes Pack", 90),
        ("Heavy Duty Stapler", 350),
        ("Box File", 120),
    ]),
    "Networking": ("NET", (1, 10), 0.8, [
        ("TP-Link 24-Port Switch", 9000),
        ("Cisco Catalyst 9200 Switch", 145000),
        ("WiFi 6 Access Point", 7500),
        ("Cat6 Cable Box 305m", 6500),
        ("Firewall Appliance", 65000),
        ("Network Rack 12U", 11000),
    ]),
    "Printers & Supplies": ("PRN", (1, 25), 1.2, [
        ("HP LaserJet Pro Printer", 17000),
        ("Canon Multifunction Printer", 24000),
        ("Black Toner Cartridge", 3800),
        ("Color Ink Cartridge Set", 2600),
        ("Label Printer", 8500),
    ]),
}
SERVICES = [
    ("SRV-001", "Annual Maintenance Contract", 25000),
    ("SRV-002", "On-site Installation", 3500),
    ("SRV-003", "IT Support Pack 10 Hours", 8000),
]
SERVICE_WEIGHT = 0.6

SUPPLIERS = {
    "Laptops & Computers": [("Redington India", "Distributor"), ("Ingram Micro India", "Distributor")],
    "Mobile & Accessories": [("Supertron Electronics", "Distributor"), ("Mobile Hub Wholesale", "Local")],
    "Office Furniture": [("Godrej Interio Supplies", "Hardware"), ("Featherlite Furniture Co", "Local")],
    "Stationery": [("Navneet Office Supplies", "Local"), ("Camlin Traders", "Distributor"),
                   ("Classmate Wholesale", "Local")],
    "Networking": [("Netlink Systems", "Electrical"), ("Cisco Channel Partners", "Distributor")],
    "Printers & Supplies": [("PrintTech Distributors", "Distributor"), ("Toner World", "Local")],
}

REGIONS = {
    "North India": [("Delhi", "Delhi"), ("Lucknow", "Uttar Pradesh"), ("Chandigarh", "Chandigarh"),
                    ("Jaipur", "Rajasthan")],
    "South India": [("Bengaluru", "Karnataka"), ("Chennai", "Tamil Nadu"), ("Hyderabad", "Telangana"),
                    ("Kochi", "Kerala")],
    "East India": [("Kolkata", "West Bengal"), ("Bhubaneswar", "Odisha"), ("Patna", "Bihar"),
                   ("Guwahati", "Assam")],
    "West India": [("Mumbai", "Maharashtra"), ("Pune", "Maharashtra"), ("Ahmedabad", "Gujarat"),
                   ("Surat", "Gujarat")],
}

CUSTOMER_PREFIXES = [
    "Apex", "Bharat", "Crescent", "Delta", "Everest", "Fusion", "Galaxy", "Horizon", "Indus", "Jupiter",
    "Kaveri", "Lotus", "Meridian", "Nova", "Orion", "Pinnacle", "Quantum", "Redwood", "Sapphire",
    "Trident", "Unity", "Vertex", "Zenith", "Saffron", "Ganga", "Himalaya", "Coral", "Summit", "Banyan",
    "Peacock", "Aravali", "Narmada", "Konark", "Sahyadri", "Vistara",
]
CUSTOMER_SUFFIXES = [
    "Traders", "Enterprises", "Retail", "Solutions", "Industries", "Technologies", "Distributors",
    "Pvt Ltd", "Agencies", "Infotech", "Hospitals", "Academy",
]
GOVT_CUSTOMERS = [
    "Municipal Corporation of Pune", "Kerala State IT Mission", "Bihar Education Department",
    "Delhi Metro Rail Office", "Odisha Police Headquarters", "Gujarat Water Board",
]
NONPROFIT_CUSTOMERS = [
    "Akshaya Patra Foundation Office", "Teach For India Chapter", "Smile Foundation", "Goonj Relief Centre",
]
INDIVIDUAL_CUSTOMERS = [
    "Rahul Mehta", "Priya Nair", "Arjun Reddy", "Sneha Kulkarni", "Vikram Singh", "Ananya Das",
    "Karthik Iyer",
]

FIRST_NAMES_M = ["Aarav", "Rohan", "Siddharth", "Aditya", "Manish", "Nikhil", "Rajesh", "Suresh",
                 "Varun", "Amit", "Harsh", "Kunal", "Deepak", "Yash"]
FIRST_NAMES_F = ["Isha", "Kavya", "Meera", "Neha", "Pooja", "Riya", "Shreya", "Tanvi", "Divya",
                 "Anjali", "Swati", "Nandini"]
LAST_NAMES = ["Sharma", "Verma", "Gupta", "Iyer", "Menon", "Patel", "Shah", "Reddy", "Rao", "Joshi",
              "Banerjee", "Chatterjee", "Kapoor", "Malhotra", "Pillai", "Desai", "Naidu", "Mishra"]

# department: (headcount, designations)
DEPARTMENTS = {
    "Sales": (8, ["Head of Marketing and Sales", "Sales Representative"]),
    "Operations": (5, ["Manager", "Associate"]),
    "Accounts": (3, ["Finance Manager", "Accountant"]),
    "Purchase": (3, ["Manager", "Associate"]),
    "Customer Service": (4, ["Manager", "Customer Service Representative"]),
    "Management": (2, ["Chief Executive Officer", "Chief Operating Officer"]),
    "Human Resources": (2, ["HR Manager", "Administrative Officer"]),
    "Marketing": (2, ["Marketing Manager", "Marketing Specialist"]),
}

# account: (monthly base amount, randomness)
MONTHLY_EXPENSES = {
    "Office Rent": (180000, 0.0),
    "Utility Expenses": (42000, 0.25),
    "Telephone Expenses": (14000, 0.15),
    "Marketing Expenses": (110000, 0.5),
    "Travel Expenses": (55000, 0.4),
    "Office Maintenance Expenses": (22000, 0.3),
    "Print and Stationery": (8000, 0.3),
}
OPENING_CAPITAL = 50_000_000


# ---------------------------------------------------------------- helpers

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure(doctype, name, data):
    if frappe.db.exists(doctype, name):
        return name
    doc = frappe.get_doc({"doctype": doctype, **data})
    doc.insert()
    return doc.name


def poisson(lam):
    threshold, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= random.random()
        if p <= threshold:
            return k
        k += 1


def progress(d):
    return (d - START).days / (END - START).days


def season(d):
    return {10: 1.4, 11: 1.3, 3: 1.35, 12: 1.1, 1: 0.9, 5: 0.85, 6: 0.8}.get(d.month, 1.0)


def order_rate(d):
    weekday = 0.2 if d.weekday() == 6 else 1.0
    return 1.15 * (0.8 + 0.6 * progress(d)) * season(d) * weekday


def money(x):
    return round(x / 10) * 10 if x >= 100 else round(x, 2)


# ---------------------------------------------------------------- masters

def setup_masters():
    # Every April-March fiscal year the simulated period touches
    first = START.year if START.month >= 4 else START.year - 1
    last = END.year if END.month >= 4 else END.year - 1
    for year in range(first, last + 1):
        name = f"{year}-{year + 1}"
        ensure("Fiscal Year", name, {
            "year": name, "year_start_date": f"{year}-04-01", "year_end_date": f"{year + 1}-03-31",
        })
    ensure("Account", BANK, {
        "account_name": "HDFC Current Account", "parent_account": f"Bank Accounts - {ABBR}",
        "account_type": "Bank", "company": COMPANY, "account_currency": "INR",
    })

    frappe.db.set_value("Territory", "India", "is_group", 1)
    for region in REGIONS:
        ensure("Territory", region, {"territory_name": region, "parent_territory": "India"})

    for group in [*CATALOG, "IT Services"]:
        ensure("Item Group", group, {"item_group_name": group, "parent_item_group": "All Item Groups"})

    items = {}  # code -> dict(name, group, cost, price, qty range, weight, stock)
    for group, (prefix, qty_range, gweight, rows) in CATALOG.items():
        for i, (item_name, cost) in enumerate(rows, 1):
            code = f"{prefix}-{i:03d}"
            price = money(cost * random.uniform(1.18, 1.42))
            hi = qty_range[1]
            ensure("Item", code, {
                "item_code": code, "item_name": item_name, "item_group": group, "stock_uom": "Nos",
                "is_stock_item": 1, "valuation_rate": cost, "standard_rate": price,
                "description": item_name,
                "item_defaults": [{"company": COMPANY, "default_warehouse": WAREHOUSE}],
                "reorder_levels": [{
                    "warehouse": WAREHOUSE, "warehouse_reorder_level": hi * 2,
                    "warehouse_reorder_qty": hi * 4, "material_request_type": "Purchase",
                }],
            })
            items[code] = {
                "name": item_name, "group": group, "cost": cost, "price": price, "qty": qty_range,
                "weight": gweight * random.uniform(0.4, 1.6) / len(rows), "stock": True,
            }
    for code, item_name, price in SERVICES:
        ensure("Item", code, {
            "item_code": code, "item_name": item_name, "item_group": "IT Services", "stock_uom": "Nos",
            "is_stock_item": 0, "standard_rate": price, "description": item_name,
            "item_defaults": [{"company": COMPANY, "income_account": f"Service - {ABBR}"}],
        })
        items[code] = {
            "name": item_name, "group": "IT Services", "cost": 0, "price": price, "qty": (1, 3),
            "weight": SERVICE_WEIGHT / len(SERVICES), "stock": False,
        }

    suppliers = defaultdict(list)  # item group -> supplier names
    for group, rows in SUPPLIERS.items():
        for supplier_name, supplier_group in rows:
            ensure("Supplier", supplier_name, {
                "supplier_name": supplier_name, "supplier_group": supplier_group,
                "supplier_type": "Company", "country": "India",
            })
            suppliers[group].append(supplier_name)

    employees = setup_employees()

    root_sp = frappe.get_all("Sales Person", filters={"is_group": 1}, pluck="name", order_by="lft")[0]
    sales_emps = [e for e in employees if e["department"] == f"Sales - {ABBR}"]
    sales_persons = defaultdict(list)  # region -> sales person names
    regions = list(REGIONS)
    for i, emp in enumerate(sales_emps):
        ensure("Sales Person", emp["employee_name"], {
            "sales_person_name": emp["employee_name"], "parent_sales_person": root_sp,
            "employee": emp["name"], "is_group": 0, "enabled": 1,
        })
        sales_persons[regions[i % len(regions)]].append(emp["employee_name"])

    customers = setup_customers()
    return items, suppliers, sales_persons, customers


def setup_employees():
    employees = []
    used = set()
    for dept, (count, designations) in DEPARTMENTS.items():
        for i in range(count):
            female = random.random() < 0.42
            while True:
                first = random.choice(FIRST_NAMES_F if female else FIRST_NAMES_M)
                last = random.choice(LAST_NAMES)
                if (first, last) not in used:
                    used.add((first, last))
                    break
            full = f"{first} {last}"
            existing = frappe.db.get_value("Employee", {"employee_name": full}, "name")
            if not existing:
                joining = date(2019, 1, 1) + timedelta(days=random.randint(0, 2300))
                if dept == "Sales" and i >= 6:  # two sales hires during the simulated period
                    joining = START + timedelta(days=random.randint(60, 300))
                doc = frappe.get_doc({
                    "doctype": "Employee", "first_name": first, "last_name": last,
                    "gender": "Female" if female else "Male",
                    "date_of_birth": date(1975, 1, 1) + timedelta(days=random.randint(0, 9500)),
                    "date_of_joining": joining, "company": COMPANY, "status": "Active",
                    "department": f"{dept} - {ABBR}",
                    "designation": designations[0] if i == 0 else designations[-1],
                    "cell_number": f"9{random.randint(100000000, 999999999)}",
                })
                doc.insert()
                existing = doc.name
            employees.append({"name": existing, "employee_name": full, "department": f"{dept} - {ABBR}"})
    return employees


def setup_customers():
    names = set()
    commercial = []
    while len(commercial) < 48:
        n = f"{random.choice(CUSTOMER_PREFIXES)} {random.choice(CUSTOMER_SUFFIXES)}"
        if n not in names:
            names.add(n)
            commercial.append(n)
    specs = ([(n, "Commercial", "Company") for n in commercial]
             + [(n, "Government", "Company") for n in GOVT_CUSTOMERS]
             + [(n, "Non Profit", "Company") for n in NONPROFIT_CUSTOMERS]
             + [(n, "Individual", "Individual") for n in INDIVIDUAL_CUSTOMERS])

    customers = []
    for customer_name, group, ctype in specs:
        region = random.choice(list(REGIONS))
        city, state = random.choice(REGIONS[region])
        # government bodies sit in the city or state their name mentions
        for r, places in REGIONS.items():
            for c, s in places:
                if group == "Government" and (c in customer_name or s in customer_name):
                    region, city, state = r, c, s
        if not frappe.db.exists("Customer", customer_name):
            frappe.get_doc({
                "doctype": "Customer", "customer_name": customer_name, "customer_type": ctype,
                "customer_group": group, "territory": region,
            }).insert()
            frappe.get_doc({
                "doctype": "Address", "address_title": customer_name, "address_type": "Billing",
                "address_line1": f"{random.randint(1, 450)}, {random.choice(['MG Road', 'Station Road', 'Ring Road', 'Industrial Area', 'Tech Park', 'Civil Lines'])}",
                "city": city, "state": state, "country": "India",
                "pincode": str(random.randint(110001, 799999)), "is_primary_address": 1,
                "links": [{"link_doctype": "Customer", "link_name": customer_name}],
            }).insert()
        size = min(random.paretovariate(1.3), 12)
        if group == "Individual":
            size = 0.3
        customers.append({
            "name": customer_name, "group": group, "region": region, "weight": size,
            "credit_days": {"Government": 60, "Individual": 0}.get(group, random.choice([15, 30, 30, 45])),
            "bad_payer": random.random() < (0.35 if group == "Government" else 0.1),
            "discount": random.choice([0, 0, 2, 5, 7.5, 10]) if size > 2 else random.choice([0, 0, 0, 2]),
        })
    return customers


# ---------------------------------------------------------------- simulation

def simulate(items, suppliers, sales_persons, customers):
    """Plan every transaction in memory; return events sorted by date."""
    events = []  # (date, priority, kind, payload)
    stock = defaultdict(float)
    on_order = defaultdict(float)
    receipts = defaultdict(list)  # date -> [(code, qty)]
    stock_codes = [c for c, it in items.items() if it["stock"]]
    po_seq = 0

    def place_po(d, group, lines):
        nonlocal po_seq
        po_seq += 1
        lead = random.randint(3, 8)
        receive = d + timedelta(days=lead)
        supplier = random.choice(suppliers[group])
        inflation = 1 + 0.05 * progress(d)
        rows = [(code, qty, money(items[code]["cost"] * inflation * random.uniform(0.97, 1.03)))
                for code, qty in lines]
        events.append((d, 1, "po", {"id": po_seq, "supplier": supplier, "rows": rows, "schedule": receive}))
        if receive <= END:
            events.append((receive, 2, "pi", {"id": po_seq}))
            receipts[receive].extend(lines)
            pay = receive + timedelta(days=30 + random.randint(-6, 12))
            if pay <= END:
                events.append((pay, 5, "pay_pi", {"id": po_seq}))
        for code, qty in lines:
            on_order[code] += qty

    # opening purchases
    by_group = defaultdict(list)
    for code in stock_codes:
        by_group[items[code]["group"]].append((code, items[code]["qty"][1] * 4))
    for group, lines in by_group.items():
        place_po(START, group, lines)

    weights = [it["weight"] for it in items.values()]
    codes = list(items)
    cust_weights = [c["weight"] for c in customers]
    so_seq = 0

    d = START
    while d <= END:
        for code, qty in receipts.pop(d, []):
            stock[code] += qty
            on_order[code] -= qty

        if d >= START + timedelta(days=5):
            for _ in range(poisson(order_rate(d))):
                cust = random.choices(customers, cust_weights)[0]
                n_lines = random.choices([1, 2, 3, 4, 5], [30, 30, 20, 12, 8])[0]
                chosen = set(random.choices(codes, weights, k=n_lines))
                rows = []
                for code in chosen:
                    it = items[code]
                    lo, hi = it["qty"]
                    scale = 1.6 if cust["weight"] > 3 else (0.5 if cust["group"] == "Individual" else 1.0)
                    qty = max(1, round(random.randint(lo, hi) * scale))
                    if it["stock"]:
                        qty = min(qty, int(stock[code]))
                        if qty <= 0:
                            continue  # lost sale: out of stock
                        stock[code] -= qty
                    rows.append((code, qty, it["price"], cust["discount"]))
                if not rows:
                    continue
                so_seq += 1
                sp_pool = sales_persons[cust["region"]]
                events.append((d, 3, "so", {
                    "id": so_seq, "customer": cust["name"], "rows": rows,
                    "delivery": d + timedelta(days=random.randint(2, 10)),
                    "sales_person": random.choice(sp_pool) if sp_pool else None,
                }))
                bill = d + timedelta(days=random.choice([0, 0, 1, 2, 3, 5]))
                if bill > END or random.random() < 0.02:
                    continue  # still to deliver and bill
                due = bill + timedelta(days=cust["credit_days"])
                events.append((bill, 4, "si", {"id": so_seq, "due": due}))
                if cust["bad_payer"]:
                    pay = due + timedelta(days=random.randint(20, 140))
                else:
                    pay = due + timedelta(days=random.randint(-8, 12))
                pay = max(pay, bill + timedelta(days=1))
                if random.random() < 0.08:  # half now, half a month later
                    if pay <= END:
                        events.append((pay, 5, "pay_si", {"id": so_seq, "fraction": 0.5}))
                    rest = pay + timedelta(days=random.randint(20, 45))
                    if rest <= END:
                        events.append((rest, 5, "pay_si", {"id": so_seq, "fraction": None}))
                elif pay <= END:
                    events.append((pay, 5, "pay_si", {"id": so_seq, "fraction": None}))

        if d.weekday() == 0:  # Monday replenishment run
            needs = defaultdict(list)
            for code in stock_codes:
                hi = items[code]["qty"][1]
                if stock[code] + on_order[code] < hi * 2:
                    needs[items[code]["group"]].append((code, hi * 4))
            for group, lines in needs.items():
                place_po(d, group, lines)

        if d.day == 1:
            events.append((d, 0, "expenses", {}))
        d += timedelta(days=1)

    events.sort(key=lambda e: (e[0], e[1]))
    return events


# ---------------------------------------------------------------- execution

def run(events):
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice
    from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

    po_names, pi_names, so_names, si_names = {}, {}, {}, {}
    counts, failures = defaultdict(int), 0

    capital = frappe.get_doc({
        "doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": COMPANY,
        "posting_date": START, "user_remark": "Opening capital infusion",
        "accounts": [
            {"account": BANK, "debit_in_account_currency": OPENING_CAPITAL},
            {"account": f"Shareholders Funds - {ABBR}", "credit_in_account_currency": OPENING_CAPITAL},
        ],
    })
    capital.insert()
    capital.submit()
    frappe.db.commit()

    total = len(events)
    for n, (d, _, kind, p) in enumerate(events, 1):
        try:
            if kind == "expenses":
                payroll = 1_250_000 * (1 + 0.12 * progress(d))
                rows = [("Salary", payroll)] + [
                    (acc, base * random.uniform(1 - var, 1 + var) * (season(d) if acc == "Marketing Expenses" else 1))
                    for acc, (base, var) in MONTHLY_EXPENSES.items()
                ]
                accounts = [{"account": f"{acc} - {ABBR}", "debit_in_account_currency": money(amt),
                             "cost_center": f"Main - {ABBR}"} for acc, amt in rows]
                accounts.append({"account": BANK,
                                 "credit_in_account_currency": sum(a["debit_in_account_currency"] for a in accounts)})
                doc = frappe.get_doc({
                    "doctype": "Journal Entry", "voucher_type": "Bank Entry", "company": COMPANY,
                    "posting_date": d, "cheque_no": f"EXP-{d:%Y%m}", "cheque_date": d,
                    "user_remark": f"Monthly operating expenses {d:%B %Y}", "accounts": accounts,
                })
                doc.insert()
                doc.submit()

            elif kind == "po":
                doc = frappe.get_doc({
                    "doctype": "Purchase Order", "supplier": p["supplier"], "company": COMPANY,
                    "transaction_date": d, "schedule_date": p["schedule"], "set_warehouse": WAREHOUSE,
                    "items": [{"item_code": c, "qty": q, "rate": r, "schedule_date": p["schedule"],
                               "warehouse": WAREHOUSE} for c, q, r in p["rows"]],
                })
                doc.insert()
                doc.submit()
                po_names[p["id"]] = doc.name

            elif kind == "pi":
                doc = make_purchase_invoice(po_names[p["id"]])
                doc.set_posting_time = 1
                doc.posting_date = d
                doc.posting_time = "09:30:00"
                doc.bill_no = f"SUP-{p['id']:05d}"
                doc.bill_date = d
                doc.update_stock = 1
                doc.set_warehouse = WAREHOUSE
                for row in doc.items:
                    row.warehouse = WAREHOUSE
                doc.due_date = d + timedelta(days=30)
                doc.payment_schedule = []
                doc.insert()
                doc.submit()
                pi_names[p["id"]] = doc.name

            elif kind == "so":
                rows = []
                for code, qty, price, disc in p["rows"]:
                    rows.append({"item_code": code, "qty": qty, "price_list_rate": price,
                                 "discount_percentage": disc, "rate": money(price * (1 - disc / 100)),
                                 "delivery_date": p["delivery"], "warehouse": WAREHOUSE})
                data = {"doctype": "Sales Order", "customer": p["customer"], "company": COMPANY,
                        "transaction_date": d, "delivery_date": p["delivery"], "set_warehouse": WAREHOUSE,
                        "selling_price_list": "Standard Selling", "currency": "INR", "items": rows}
                if p["sales_person"]:
                    data["sales_team"] = [{"sales_person": p["sales_person"], "allocated_percentage": 100}]
                doc = frappe.get_doc(data)
                doc.insert()
                doc.submit()
                so_names[p["id"]] = doc.name

            elif kind == "si":
                doc = make_sales_invoice(so_names[p["id"]])
                doc.set_posting_time = 1
                doc.posting_date = d
                doc.posting_time = "15:00:00"
                doc.update_stock = 1
                doc.set_warehouse = WAREHOUSE
                for row in doc.items:
                    row.warehouse = WAREHOUSE
                doc.due_date = p["due"]
                doc.payment_schedule = []
                doc.insert()
                doc.submit()
                si_names[p["id"]] = doc.name

            elif kind in ("pay_si", "pay_pi"):
                dt, names = ("Sales Invoice", si_names) if kind == "pay_si" else ("Purchase Invoice", pi_names)
                if p["id"] not in names:
                    continue
                doc = get_payment_entry(dt, names[p["id"]])
                doc.posting_date = d
                doc.mode_of_payment = "Wire Transfer"
                doc.reference_no = f"UTR{random.randint(10**11, 10**12 - 1)}"
                doc.reference_date = d
                if kind == "pay_si":
                    doc.paid_to = BANK
                else:
                    doc.paid_from = BANK
                if p.get("fraction"):
                    amount = money(doc.references[0].allocated_amount * p["fraction"])
                    doc.paid_amount = doc.received_amount = amount
                    doc.references[0].allocated_amount = amount
                doc.insert()
                doc.submit()

            frappe.db.commit()
            counts[kind] += 1
        except Exception as e:
            frappe.db.rollback()
            failures += 1
            log(f"FAILED {kind} {p.get('id', '')} on {d}: {type(e).__name__}: {str(e)[:300]}")
            if failures > 25:
                log("Too many failures, stopping.")
                break
        if n % 200 == 0:
            log(f"{n}/{total} events done ({d})")

    return counts, failures


def main():
    frappe.init(site=SITE, sites_path=".")
    frappe.connect()
    frappe.set_user("Administrator")
    frappe.flags.mute_emails = True

    if frappe.db.count("Sales Invoice"):
        sys.exit("Sales invoices already exist; refusing to seed twice.")

    log("Creating masters")
    items, suppliers, sales_persons, customers = setup_masters()
    frappe.db.commit()
    log(f"Masters: {len(items)} items, {len(customers)} customers, "
        f"{sum(len(v) for v in suppliers.values())} suppliers")

    events = simulate(items, suppliers, sales_persons, customers)
    log(f"Planned {len(events)} transactions from {START} to {END}")

    counts, failures = run(events)
    log(f"Done. Created: {dict(counts)}. Failures: {failures}")
    frappe.destroy()


if __name__ == "__main__":
    main()
