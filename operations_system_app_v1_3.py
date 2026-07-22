"""
Cake Album Operations Platform v1.1 TEST
SQLite-based test release connected to cake_album_operations_v1_1_TEST.db.

Core v1.1 rule:
No cake moves forward without acceptance by the receiving stage.
If rejected, it returns to the sender with issue logging, correction, and resubmission.
"""

import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, date, time as dtime

import pandas as pd
from operations_system_app_sqlite import render_admin_dashboard
import streamlit as st

APP_DIR = Path(__file__).parent
DATABASE_FILE = APP_DIR / "cake_album_operations_v1_1_TEST.db"
REFERENCE_IMAGE_DIR = APP_DIR / "cake_reference_images"
REFERENCE_IMAGE_DIR.mkdir(exist_ok=True)

st.set_page_config(
    page_title="Cake Album | Operations v1.1 TEST",
    page_icon="🎂",
    layout="wide",
    initial_sidebar_state="expanded",
)

DEPARTMENT_PASSCODES = {
    "Customer Care": st.secrets["operations_passcodes"]["customer_care"],
    "Production Planning": st.secrets["operations_passcodes"]["production_planning"],
    "Baking": st.secrets["operations_passcodes"]["baking"],
    "Filling & Piling": st.secrets["operations_passcodes"]["piling"],
    "Coating / Covering": st.secrets["operations_passcodes"]["covering"],
    "Decoration": st.secrets["operations_passcodes"]["decoration"],
    "Studio / Final QC": st.secrets["operations_passcodes"]["studio_quality"],
    "Packaging": st.secrets["operations_passcodes"]["packaging"],
    "Dispatch / Driver": st.secrets["operations_passcodes"]["dispatch_driver"],
    "Finance": st.secrets["operations_passcodes"]["finance"],
    "Follow-up / Complaints": st.secrets["operations_passcodes"]["follow_up"],
    "Procurement": st.secrets["operations_passcodes"]["procurement"],
    "Owner / Admin": st.secrets["operations_passcodes"]["owner_admin"],
}

FALLBACK_BAKERS = ["Billy", "Uncle Joe", "Ronnie", "Martin", "Andre"]
FALLBACK_PILERS = ["Zakia", "Eriya", "Lawrence", "Bobi", "Angel", "Desmond", "Zaitun", "Aisha"]
FALLBACK_COVERERS = ["Zakia", "Eriya", "Lawrence", "Bobi", "Angel", "Desmond", "Zaitun", "Aisha"]
FALLBACK_DECORATORS = ["Zakia", "Eriya", "Lawrence", "Bobi", "Angel", "Desmond", "Zaitun", "Aisha"]
FALLBACK_DRIVERS = ["Cyrus", "Company Driver", "Isaac", "Other"]

CSS = """
<style>
:root{
  --ca-purple:#5B2C6F; --ca-purple-dark:#2F123B; --ca-red:#E1261C;
  --ca-cream:#FFF4E6; --ca-blush:#FBE7EA; --ca-ink:#231F20;
  --ca-muted:#62546B; --ca-border:#DFC5E5; --ca-input:#FFF9F1;
}
.stApp{background:linear-gradient(135deg,#FFF1E2 0%,#FBE2EA 45%,#F2DCF8 100%); color:var(--ca-ink)}
.block-container{padding-top:1.2rem; padding-bottom:2rem; max-width:1420px;}
h1,h2,h3,h4{color:var(--ca-purple-dark)!important; font-family:Georgia,'Times New Roman',serif;}
.stMarkdown,.stMarkdown p,label,[data-testid='stWidgetLabel']{color:var(--ca-ink)!important;}
[data-testid='stSidebar']{background:linear-gradient(180deg,#2F123B,#4B1F5C 55%,#6A2C7E);}
[data-testid='stSidebar'] *{color:#FFF7FB!important;}
[data-testid='stSidebar'] input,[data-testid='stSidebar'] div[data-baseweb='select']>div{background:#FFF6EA!important;color:#231F20!important;}
.ca-header{padding:22px 26px;margin-bottom:20px;border-radius:24px;background:linear-gradient(135deg,#FFF6EA,#FBE7EA,#F1E1F7);border:1px solid rgba(91,44,111,.22);border-bottom:6px solid var(--ca-red);box-shadow:0 16px 36px rgba(47,18,59,.14)}
.ca-header h1{margin:0;font-size:2.05rem}.ca-header p{margin:6px 0 0;color:var(--ca-muted)!important;font-weight:750;font-style:italic}
.ca-card{background:linear-gradient(180deg,#FFF6EA,#F7E5F8);border:1px solid var(--ca-border);border-left:8px solid var(--ca-purple);border-radius:20px;padding:18px 22px;margin:12px 0 18px;box-shadow:0 12px 28px rgba(47,18,59,.10);line-height:1.65}.ca-card *{color:var(--ca-ink)!important}.ca-card b{color:var(--ca-purple-dark)!important}
.ca-kpi{background:linear-gradient(180deg,#FFF6EA,#F7E5F8);border:1px solid var(--ca-border);border-radius:20px;padding:18px;box-shadow:0 12px 28px rgba(47,18,59,.10)}.ca-kpi .label{font-size:.78rem;text-transform:uppercase;font-weight:950;color:var(--ca-muted)!important}.ca-kpi .value{font-size:1.9rem;font-weight:950;color:var(--ca-purple-dark)!important}.ca-kpi .note{font-size:.85rem;color:var(--ca-muted)!important;font-weight:700}
input,textarea,div[data-baseweb='select']>div{background:var(--ca-input)!important;color:var(--ca-ink)!important;border-color:#CBAFD5!important;border-radius:11px!important}
.stButton>button,.stFormSubmitButton>button{border-radius:13px;font-weight:900;min-height:42px;background:linear-gradient(135deg,var(--ca-purple),var(--ca-purple-dark));color:#fff!important;border:0;box-shadow:0 10px 20px rgba(47,18,59,.18)}
.stTabs [data-baseweb='tab-list']{gap:8px;background:rgba(91,44,111,.11);padding:9px;border-radius:18px;border:1px solid var(--ca-border)}
.stTabs [data-baseweb='tab']{background:linear-gradient(180deg,#FFF6EA,#F8E5F3);border-radius:13px;font-weight:900;color:var(--ca-purple-dark)!important;border:1px solid rgba(91,44,111,.16)}
.stTabs [aria-selected='true']{background:linear-gradient(135deg,var(--ca-purple),var(--ca-purple-dark))!important;color:#fff!important}.stTabs [aria-selected='true'] *{color:#fff!important}
[data-testid='stDataFrame']{background:#FFF9F1;border-radius:18px;border:1px solid var(--ca-border);box-shadow:0 10px 26px rgba(47,18,59,.10);overflow:hidden}[data-testid='stDataFrame'] *{color:#231F20!important}
@media print{[data-testid='stSidebar'],header,footer,.stButton{display:none!important}.block-container{padding:0!important}.print-note{box-shadow:none!important;border:2px solid #111!important;background:#fff!important;color:#000!important}.print-note *{color:#000!important}}
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def page_header(title: str, subtitle: str = ""):
    st.markdown(f"<div class='ca-header'><h1>{title}</h1><p>{subtitle}</p></div>", unsafe_allow_html=True)


# -----------------------------
# Database helpers
# -----------------------------

def connect():
    if not DATABASE_FILE.exists():
        st.error(f"Database not found: {DATABASE_FILE}. Put cake_album_operations_v1_1_TEST.db next to this app file.")
        st.stop()
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def db_columns(table: str):
    with connect() as conn:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


@st.cache_data(show_spinner=False)
def load_orders_cached(db_mtime: float):
    with sqlite3.connect(DATABASE_FILE) as conn:
        return pd.read_sql_query("SELECT * FROM orders", conn)


def load_orders():
    mtime = DATABASE_FILE.stat().st_mtime if DATABASE_FILE.exists() else 0
    return load_orders_cached(mtime)


def refresh_data():
    load_orders_cached.clear()


def audit_log(order_id: str | None, action_type: str, stage: str, details: str, performed_by: str):
    with connect() as conn:
        conn.execute(
            """INSERT INTO audit_logs(order_id, action_type, stage, action_details, performed_by, performed_at)
               VALUES(?,?,?,?,?,?)""",
            (order_id, action_type, stage, details, performed_by or "System", now_iso()),
        )
        conn.commit()


def update_order(order_id: str, updates: dict, updated_by: str = "System", action_type: str = "Order Update", stage: str = "") -> bool:
    updates = dict(updates)
    updates.setdefault("last_updated_at", now_iso())
    updates.setdefault("last_updated_by", updated_by)
    allowed = set(db_columns("orders"))
    updates = {k: v for k, v in updates.items() if k in allowed}
    if not updates:
        return False
    clause = ", ".join([f"{k} = ?" for k in updates.keys()])
    values = list(updates.values()) + [order_id]
    with connect() as conn:
        cur = conn.execute(f"UPDATE orders SET {clause} WHERE order_id = ?", values)
        conn.execute(
            """INSERT INTO audit_logs(order_id, action_type, stage, action_details, performed_by, performed_at)
               VALUES(?,?,?,?,?,?)""",
            (order_id, action_type, stage, str(updates), updated_by or "System", now_iso()),
        )
        conn.commit()
        ok = cur.rowcount > 0
    refresh_data()
    return ok


def insert_order(order: dict):
    allowed = set(db_columns("orders"))
    order = {k: v for k, v in order.items() if k in allowed}
    cols = list(order.keys())
    placeholders = ",".join(["?"] * len(cols))
    with connect() as conn:
        conn.execute(f"INSERT INTO orders({','.join(cols)}) VALUES({placeholders})", [order[c] for c in cols])
        conn.execute(
            """INSERT INTO audit_logs(order_id, action_type, stage, action_details, performed_by, performed_at)
               VALUES(?,?,?,?,?,?)""",
            (order.get("order_id"), "Order Created", "Customer Care", "New order created", order.get("last_updated_by") or "Customer Care", now_iso()),
        )
        conn.commit()
    refresh_data()


def insert_stage_check(order_id, from_stage, to_stage, checked_by, check_status, issue_category="", issue_description="", responsible_department="", responsible_person=""):
    with connect() as conn:
        conn.execute(
            """INSERT INTO stage_quality_checks(
                order_id, from_stage, to_stage, check_type, checked_by, check_status,
                issue_category, issue_description, responsible_department, responsible_person,
                checked_at, returned_at, resolution_status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                order_id, from_stage, to_stage, f"{from_stage} to {to_stage}", checked_by,
                check_status, issue_category, issue_description, responsible_department,
                responsible_person, now_iso(), now_iso() if check_status == "Rejected" else None,
                "Correction Required" if check_status == "Rejected" else "Not Required",
            ),
        )
        conn.execute(
            """INSERT INTO audit_logs(order_id, action_type, stage, action_details, performed_by, performed_at)
               VALUES(?,?,?,?,?,?)""",
            (order_id, "Stage QC " + check_status, to_stage, issue_description or f"{from_stage} accepted by {to_stage}", checked_by, now_iso()),
        )
        conn.commit()
    refresh_data()


def create_material_requirement(order_id, requested_by, item_name, qty, unit):
    with connect() as conn:
        conn.execute(
            """INSERT INTO order_material_requirements(order_id, requested_by, item_name, quantity_required, unit, requirement_status, requested_at)
               VALUES(?,?,?,?,?,?,?)""",
            (order_id, requested_by, item_name, qty, unit, "Submitted", now_iso()),
        )
        conn.commit()


def load_table(table):
    with sqlite3.connect(DATABASE_FILE) as conn:
        return pd.read_sql_query(f"SELECT * FROM {table}", conn)


# -----------------------------
# General helpers
# -----------------------------

def generate_order_id():
    return f"CA-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"


def fmt_ugx(v):
    try:
        return f"UGX {float(v):,.0f}"
    except Exception:
        return "UGX 0"


def disp(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v) == "nan" or v == "":
        return "—"
    return v


def col(df, name):
    return df[name] if name in df.columns else pd.Series([""] * len(df), index=df.index)


def filter_orders(df, statuses):
    if df.empty or "workflow_status" not in df.columns:
        return df.iloc[0:0]
    return df[df["workflow_status"].isin(statuses)].copy()


def order_label(row):
    return f"{row.get('order_id')} · {row.get('customer_name')} · Due {disp(row.get('due_date'))}"


def select_order(df, key, label="Select an order"):
    if df.empty:
        st.success("Nothing in this queue right now.")
        return None
    d = df.copy()
    d["_label"] = d.apply(order_label, axis=1)
    choice = st.selectbox(label, d["_label"].tolist(), key=key)
    return d[d["_label"] == choice].iloc[0]


def order_card(row, extra=None):
    size = ""
    if disp(row.get("cake_size_value")) != "—" or disp(row.get("cake_shape")) != "—":
        val = disp(row.get("cake_size_value"))
        shape = disp(row.get("cake_shape"))
        size = f"<b>Size:</b> {val}'' {shape}<br>"
    html = ["<div class='ca-card'>"]
    html.append(f"<b style='font-size:1.1rem'>{disp(row.get('customer_name'))}</b> · {disp(row.get('order_id'))}<br><br>")
    html.append(f"<b>Flavours:</b> {disp(row.get('flavours'))}<br>{size}")
    html.append(f"<b>Layers:</b> {disp(row.get('number_of_layers'))}<br>")
    html.append(f"<b>Design:</b> {disp(row.get('design_description'))}<br>")
    html.append(f"<b>Due:</b> {disp(row.get('due_date'))} | <b>Time:</b> {disp(row.get('expected_time'))}<br>")
    html.append(f"<b>Location:</b> {disp(row.get('location'))}<br>")
    if extra:
        for label, value in extra:
            html.append(f"<b>{label}:</b> {disp(value)}<br>")
    if disp(row.get("reference_image_path")) != "—":
        html.append(f"<br><b>Reference Image:</b> {disp(row.get('reference_image_path'))}")
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)
    path = row.get("reference_image_path")
    if path and isinstance(path, str) and Path(path).exists():
        st.image(path, caption="Customer reference image", width=320)


def table(df, columns):
    if df.empty:
        st.info("No records to show.")
        return
    cols = [c for c in columns if c in df.columns]
    st.dataframe(df[cols], hide_index=True, use_container_width=True)


def kpi(label, value, note=""):
    st.markdown(f"<div class='ca-kpi'><div class='label'>{label}</div><div class='value'>{value}</div><div class='note'>{note}</div></div>", unsafe_allow_html=True)


def staff_lists():
    return FALLBACK_BAKERS, FALLBACK_PILERS, FALLBACK_COVERERS, FALLBACK_DECORATORS, FALLBACK_DRIVERS


# -----------------------------
# Pages
# -----------------------------

def render_customer_care():
    page_header("📋 Customer Care", "Create orders with structured cake size, layers, and reference image.")
    df = load_orders()
    c1, c2, c3, c4 = st.columns(4)
    with c1: kpi("Total Orders", f"{len(df):,}")
    with c2: kpi("Awaiting Deposit", int((col(df,"workflow_status") == "Awaiting Deposit").sum()))
    with c3: kpi("In Production", int(col(df,"workflow_status").isin(["Production Planned","Baking","Piling","Covering","Decorating"]).sum()))
    with c4: kpi("Complaints Open", len(load_table("complaints").query("complaint_status != 'Closed'") if not load_table("complaints").empty else []))

    with st.form("new_order_form", clear_on_submit=True):
        st.markdown("### Customer Details")
        a,b = st.columns(2)
        customer_name = a.text_input("Customer Name *")
        customer_number = b.text_input("Customer Phone *")
        st.markdown("### Cake Order Details")
        a,b,c,d = st.columns(4)
        flavours = a.text_input("Flavours *")
        size_value = b.number_input("Cake Size", min_value=0.0, step=0.5, value=8.0)
        shape = c.selectbox("Cake Shape", ["Round", "Rectangle", "Square", "Heart", "Custom"])
        layers = d.number_input("Number of Layers", min_value=1, step=1, value=1)
        design = st.text_area("Description of Design *")
        img = st.file_uploader("Customer Reference Image", type=["jpg","jpeg","png"])
        a,b = st.columns(2)
        due_date = a.date_input("Due Date")
        expected_time = b.time_input("Expected Time", value=dtime(12,0))
        st.markdown("### Payment and Delivery")
        a,b,c = st.columns(3)
        price = a.number_input("Price (UGX) *", min_value=0, step=5000)
        deposit = b.number_input("Deposit (UGX)", min_value=0, step=5000)
        payment_method = c.selectbox("Payment Method", ["Mobile Money", "Cash", "Bank Transfer", "Other"])
        location = st.text_input("Delivery / Pickup Location")
        a,b,c = st.columns(3)
        order_channel = a.selectbox("Order Channel", ["Loyal Client", "Referral", "New Client", "Gift", "Cake Album", "Other"])
        priority = b.selectbox("Priority", ["Normal", "High", "Critical", "Low"])
        created_by = c.text_input("Order Entered By *")
        submitted = st.form_submit_button("Create New Order", use_container_width=True)

    if submitted:
        missing = [name for name,val in [("Customer Name",customer_name),("Customer Phone",customer_number),("Flavours",flavours),("Design",design),("Entered By",created_by)] if not str(val).strip()]
        if price <= 0: missing.append("Price")
        if deposit > price: st.error("Deposit cannot be greater than price."); return
        if missing: st.error("Please complete: " + ", ".join(missing)); return
        order_id = generate_order_id()
        image_path = ""
        if img is not None:
            suffix = Path(img.name).suffix.lower()
            target = REFERENCE_IMAGE_DIR / f"{order_id}{suffix}"
            target.write_bytes(img.getbuffer())
            image_path = str(target)
        balance = max(price - deposit, 0)
        insert_order({
            "order_id": order_id, "customer_name": customer_name.strip(), "customer_number": customer_number.strip(),
            "flavours": flavours.strip(), "design_description": design.strip(), "due_date": str(due_date), "expected_time": str(expected_time),
            "price_ugx": price, "deposit": deposit, "balance": balance, "payment_method": payment_method, "location": location.strip(),
            "order_channel": order_channel, "workflow_status": "Awaiting Deposit", "current_owner": "Finance", "next_action": "Confirm deposit received",
            "priority": priority, "balance_to_collect": balance, "balance_collection_status": "Pending" if balance > 0 else "Not Required",
            "finance_confirmation_status": "Pending" if balance > 0 else "Not Required", "delivery_status": "Not Started", "follow_up_status": "Pending",
            "issue_flag": "No", "cake_size_value": size_value, "cake_size_unit": "Inches", "cake_shape": shape, "number_of_layers": layers,
            "reference_image_path": image_path, "order_created_at": now_iso(), "last_updated_at": now_iso(), "last_updated_by": created_by.strip(),
        })
        st.success(f"Order {order_id} created and sent to Finance.")
        st.rerun()

    st.markdown("### Recent Orders")
    table(load_orders().tail(25).iloc[::-1], ["order_id","customer_name","due_date","expected_time","cake_size_value","cake_shape","number_of_layers","balance","workflow_status","current_owner"])


def render_finance():
    page_header("💰 Finance", "Confirm deposits and delivery balances.")
    df = load_orders()
    t1,t2 = st.tabs(["Confirm Deposits", "Confirm Delivery Balances"])
    with t1:
        row = select_order(filter_orders(df,["Awaiting Deposit"]), "finance_deposit")
        if row is not None:
            order_card(row, [("Deposit", fmt_ugx(row.get("deposit"))), ("Balance", fmt_ugx(row.get("balance")))])
            by = st.text_input("Confirmed by", value="Finance", key="fin_by1")
            if st.button("✅ Confirm Deposit", use_container_width=True):
                update_order(row.order_id, {"workflow_status":"Deposit Confirmed", "current_owner":"Production Planning", "next_action":"Assign Baker, Piler, Coverer and Decorator"}, by, "Deposit Confirmed", "Finance")
                st.success("Deposit confirmed."); st.rerun()
    with t2:
        row = select_order(filter_orders(df,["Finance Payment Confirmation Pending"]), "finance_bal")
        if row is not None:
            order_card(row, [("Balance", fmt_ugx(row.get("balance_to_collect"))), ("Driver", row.get("driver_assigned"))])
            by = st.text_input("Confirmed by", value="Finance", key="fin_by2")
            if st.button("✅ Confirm Balance Received", use_container_width=True):
                update_order(row.order_id, {"finance_confirmation_status":"Confirmed", "payment_confirmed_at":now_iso(), "balance_to_collect":0, "workflow_status":"Payment Confirmed", "current_owner":"Driver", "next_action":"Complete delivery handover"}, by, "Balance Confirmed", "Finance")
                st.success("Balance confirmed."); st.rerun()


def render_production_planning():
    page_header("🏭 Production Planning", "Assign Baker, Piler, Coverer and Decorator.")
    df = load_orders()
    row = select_order(filter_orders(df,["Deposit Confirmed"]), "pp_order")
    if row is not None:
        order_card(row)
        bakers,pilers,coverers,decorators,_ = staff_lists()
        a,b,c,d,e = st.columns(5)
        baker = a.selectbox("Baker", bakers)
        piler = b.selectbox("Piler", pilers)
        coverer = c.selectbox("Coverer", coverers)
        decorator = d.selectbox("Decorator", decorators)
        by = e.text_input("Updated by", value="Production Manager")
        if st.button("Assign Full Production Team", use_container_width=True):
            update_order(row.order_id, {
                "baker_assigned":baker, "piler_assigned":piler, "coverer_assigned":coverer, "decorator_assigned":decorator,
                "workflow_status":"Production Planned", "current_owner":"Baking", "next_action":"Start baking",
                "production_planned_at":now_iso(), "baking_status":"Not Started", "decoration_status":"Not Started",
            }, by, "Production Team Assigned", "Production Planning")
            st.success("Production team assigned."); st.rerun()
    st.markdown("### Production Pipeline")
    table(filter_orders(df,["Production Planned","Baking","Baking Check","Piling Incoming","Piling","Piling Check","Covering Incoming","Covering","Covering Check","Decorating Incoming","Decorating","Studio Check"]),
          ["order_id","customer_name","due_date","expected_time","baker_assigned","piler_assigned","coverer_assigned","decorator_assigned","workflow_status","next_action"])


def issue_form(prefix, from_stage, to_stage, sender_status, sender_owner, row, default_person):
    with st.expander("❌ Reject / Return to Sender"):
        cat = st.selectbox("Issue category", ["Quality Issue", "Wrong Colour", "Wrong Size", "Wrong Flavour", "Height Issue", "Leaning", "Cracked", "Underbaked", "Overbaked", "Spelling/Design Issue", "Other"], key=f"{prefix}_cat")
        desc = st.text_area("Issue description", key=f"{prefix}_desc")
        resp_dept = st.text_input("Responsible department", value=from_stage, key=f"{prefix}_dept")
        resp_person = st.text_input("Responsible person", value=default_person or "", key=f"{prefix}_person")
        by = st.text_input("Rejected by", value=to_stage, key=f"{prefix}_by")
        if st.button("Log Issue and Return", key=f"{prefix}_reject", use_container_width=True):
            insert_stage_check(row.order_id, from_stage, to_stage, by, "Rejected", cat, desc, resp_dept, resp_person)
            update_order(row.order_id, {"workflow_status":sender_status, "current_owner":sender_owner, "next_action":"Correct issue and resubmit", "issue_flag":"Yes", "issue_notes":desc}, by, "Stage Rejected", to_stage)
            st.warning("Issue logged and returned to sender."); st.rerun()


def render_baking():
    page_header("🍰 Baking", "Bake layers, submit for baking check, and correct rejected cakes.")
    df = load_orders()
    t1,t2,t3 = st.tabs(["Assigned", "In Progress", "Correction Required"])
    with t1:
        row = select_order(filter_orders(df,["Production Planned"]), "bake_assigned")
        if row is not None:
            order_card(row, [("Baker", row.get("baker_assigned"))])
            by = st.text_input("Updated by", value=disp(row.get("baker_assigned")), key="bake_by1")
            if st.button("▶️ Start Baking", use_container_width=True):
                update_order(row.order_id, {"workflow_status":"Baking", "current_owner":"Baking", "next_action":"Submit for baking check", "baking_started_at":now_iso(), "baking_status":"In Progress"}, by, "Baking Started", "Baking")
                st.rerun()
    with t2:
        row = select_order(filter_orders(df,["Baking"]), "bake_prog")
        if row is not None:
            order_card(row)
            by = st.text_input("Checked/Submitted by", value=disp(row.get("baker_assigned")), key="bake_by2")
            a,b = st.columns(2)
            if a.button("✅ Baking Check Passed → Send to Piling", use_container_width=True):
                insert_stage_check(row.order_id,"Baking","Piling",by,"Passed")
                update_order(row.order_id, {"workflow_status":"Piling Incoming", "current_owner":"Filling / Piling", "next_action":"Piler to accept cake", "baking_completed_at":now_iso(), "baking_status":"Complete"}, by, "Baking Passed", "Baking")
                st.rerun()
            with b:
                issue_form("bake", "Baking", "Baking Check", "Baking Correction Required", "Baking", row, row.get("baker_assigned"))
    with t3:
        row = select_order(filter_orders(df,["Baking Correction Required"]), "bake_corr")
        if row is not None:
            order_card(row, [("Issue", row.get("issue_notes"))])
            by = st.text_input("Corrected by", value=disp(row.get("baker_assigned")), key="bake_by3")
            if st.button("🔁 Correction Complete — Resubmit Baking Check", use_container_width=True):
                update_order(row.order_id, {"workflow_status":"Baking", "current_owner":"Baking", "next_action":"Resubmitted for baking check"}, by, "Baking Correction Complete", "Baking")
                st.rerun()


def render_piling():
    page_header("🎂 Filling / Piling", "Accept baked cakes, pile to correct height, and send to Covering.")
    df = load_orders()
    t1,t2,t3 = st.tabs(["Incoming from Baking", "In Progress", "Correction Required"])
    with t1:
        row = select_order(filter_orders(df,["Piling Incoming"]), "pile_in")
        if row is not None:
            order_card(row, [("Baker", row.get("baker_assigned")), ("Piler", row.get("piler_assigned"))])
            by = st.text_input("Checked by", value=disp(row.get("piler_assigned")), key="pile_by1")
            a,b = st.columns(2)
            if a.button("✅ Accept for Piling", use_container_width=True):
                insert_stage_check(row.order_id,"Baking","Piling",by,"Passed")
                update_order(row.order_id, {"workflow_status":"Piling", "current_owner":"Filling / Piling", "next_action":"Pile and submit to Covering"}, by, "Piling Accepted", "Piling")
                st.rerun()
            with b:
                issue_form("pile_in", "Baking", "Piling", "Baking Correction Required", "Baking", row, row.get("baker_assigned"))
    with t2:
        row = select_order(filter_orders(df,["Piling"]), "pile_prog")
        if row is not None:
            order_card(row)
            by = st.text_input("Updated by", value=disp(row.get("piler_assigned")), key="pile_by2")
            if st.button("✅ Piling Complete → Send to Covering Check", use_container_width=True):
                update_order(row.order_id, {"workflow_status":"Covering Incoming", "current_owner":"Coating / Covering", "next_action":"Coverer to check piling and accept"}, by, "Piling Submitted", "Piling")
                st.rerun()
    with t3:
        row = select_order(filter_orders(df,["Piling Correction Required"]), "pile_corr")
        if row is not None:
            order_card(row, [("Issue", row.get("issue_notes"))])
            by = st.text_input("Corrected by", value=disp(row.get("piler_assigned")), key="pile_by3")
            if st.button("🔁 Correction Complete — Resubmit to Covering", use_container_width=True):
                update_order(row.order_id, {"workflow_status":"Covering Incoming", "current_owner":"Coating / Covering", "next_action":"Resubmitted for covering acceptance"}, by, "Piling Correction Complete", "Piling")
                st.rerun()


def render_covering():
    page_header("🧁 Coating / Covering", "Check piling/height, cover cake, then send to Decoration.")
    df = load_orders()
    t1,t2,t3 = st.tabs(["Incoming from Piling", "In Progress", "Correction Required"])
    with t1:
        row = select_order(filter_orders(df,["Covering Incoming"]), "cov_in")
        if row is not None:
            order_card(row, [("Piler", row.get("piler_assigned")), ("Coverer", row.get("coverer_assigned"))])
            by = st.text_input("Checked by", value=disp(row.get("coverer_assigned")), key="cov_by1")
            a,b = st.columns(2)
            if a.button("✅ Piling Accepted → Start Covering", use_container_width=True):
                insert_stage_check(row.order_id,"Piling","Covering",by,"Passed")
                update_order(row.order_id, {"workflow_status":"Covering", "current_owner":"Coating / Covering", "next_action":"Cover and submit to Decoration"}, by, "Piling Accepted by Coverer", "Covering")
                st.rerun()
            with b:
                issue_form("cov_in", "Piling", "Covering", "Piling Correction Required", "Filling / Piling", row, row.get("piler_assigned"))
    with t2:
        row = select_order(filter_orders(df,["Covering"]), "cov_prog")
        if row is not None:
            order_card(row)
            by = st.text_input("Updated by", value=disp(row.get("coverer_assigned")), key="cov_by2")
            if st.button("✅ Covering Complete → Send to Decorator Check", use_container_width=True):
                update_order(row.order_id, {"workflow_status":"Decorating Incoming", "current_owner":"Decoration", "next_action":"Decorator to check covering and accept"}, by, "Covering Submitted", "Covering")
                st.rerun()
    with t3:
        row = select_order(filter_orders(df,["Covering Correction Required"]), "cov_corr")
        if row is not None:
            order_card(row, [("Issue", row.get("issue_notes"))])
            by = st.text_input("Corrected by", value=disp(row.get("coverer_assigned")), key="cov_by3")
            if st.button("🔁 Correction Complete — Resubmit to Decoration", use_container_width=True):
                update_order(row.order_id, {"workflow_status":"Decorating Incoming", "current_owner":"Decoration", "next_action":"Resubmitted for decorator acceptance"}, by, "Covering Correction Complete", "Covering")
                st.rerun()


def render_decoration():
    page_header("🎨 Decoration", "Accept covering, plan materials, decorate, and send to Studio.")
    df = load_orders()
    t1,t2,t3,t4 = st.tabs(["Incoming from Covering", "Materials Planning", "Decorating", "Correction Required"])
    with t1:
        row = select_order(filter_orders(df,["Decorating Incoming"]), "deco_in")
        if row is not None:
            order_card(row, [("Coverer", row.get("coverer_assigned")), ("Decorator", row.get("decorator_assigned"))])
            by = st.text_input("Checked by", value=disp(row.get("decorator_assigned")), key="deco_by1")
            a,b = st.columns(2)
            if a.button("✅ Covering Accepted → Start Decorating", use_container_width=True):
                insert_stage_check(row.order_id,"Covering","Decoration",by,"Passed")
                update_order(row.order_id, {"workflow_status":"Decorating", "current_owner":"Decoration", "next_action":"Decorate and submit to Studio", "decorating_started_at":now_iso(), "decoration_status":"In Progress"}, by, "Covering Accepted by Decorator", "Decoration")
                st.rerun()
            with b:
                issue_form("deco_in", "Covering", "Decoration", "Covering Correction Required", "Coating / Covering", row, row.get("coverer_assigned"))
    with t2:
        row = select_order(df[df["decorator_assigned"].notna()] if "decorator_assigned" in df.columns else df.iloc[0:0], "mat_order")
        if row is not None:
            order_card(row)
            st.markdown("### Add Material Requirement")
            a,b,c,d = st.columns(4)
            item = a.text_input("Item")
            qty = b.number_input("Quantity", min_value=0.0, step=1.0)
            unit = c.text_input("Unit", value="pcs")
            by = d.text_input("Requested by", value=disp(row.get("decorator_assigned")))
            if st.button("Submit Material Requirement", use_container_width=True):
                if item and qty > 0:
                    create_material_requirement(row.order_id, by, item, qty, unit)
                    audit_log(row.order_id, "Material Requirement Submitted", "Decoration", f"{item} x {qty} {unit}", by)
                    st.success("Sent to Procurement.")
                else:
                    st.error("Enter item and quantity.")
            req = load_table("order_material_requirements")
            table(req[req["order_id"] == row.order_id] if not req.empty else req, ["item_name","quantity_required","unit","requirement_status","requested_by","requested_at"])
    with t3:
        row = select_order(filter_orders(df,["Decorating"]), "deco_prog")
        if row is not None:
            order_card(row)
            by = st.text_input("Updated by", value=disp(row.get("decorator_assigned")), key="deco_by3")
            if st.button("✅ Decoration Complete → Send to Studio / Final QC", use_container_width=True):
                update_order(row.order_id, {"workflow_status":"Studio Check", "current_owner":"Studio / Final QC", "next_action":"Final quality check", "decorating_completed_at":now_iso(), "decoration_status":"Complete"}, by, "Decoration Submitted", "Decoration")
                st.rerun()
    with t4:
        row = select_order(filter_orders(df,["Decoration Correction Required"]), "deco_corr")
        if row is not None:
            order_card(row, [("Issue", row.get("issue_notes"))])
            by = st.text_input("Corrected by", value=disp(row.get("decorator_assigned")), key="deco_by4")
            if st.button("🔁 Correction Complete — Resubmit to Studio", use_container_width=True):
                update_order(row.order_id, {"workflow_status":"Studio Check", "current_owner":"Studio / Final QC", "next_action":"Resubmitted for Studio check"}, by, "Decoration Correction Complete", "Decoration")
                st.rerun()


def render_studio_qc():
    page_header("🔍 Studio / Final QC", "Final release check before Packaging.")
    df = load_orders()
    row = select_order(filter_orders(df,["Studio Check"]), "studio_check")
    if row is not None:
        order_card(row, [("Decorator", row.get("decorator_assigned"))])
        by = st.text_input("Checked by", value="Studio / Final QC")
        a,b = st.columns(2)
        if a.button("✅ Final QC Passed → Ready for Packaging", use_container_width=True):
            insert_stage_check(row.order_id,"Decoration","Studio / Final QC",by,"Passed")
            update_order(row.order_id, {"workflow_status":"Ready for Packaging", "current_owner":"Packaging", "next_action":"Pack and print delivery note", "qc_status":"Approved", "qc_completed_at":now_iso()}, by, "Final QC Passed", "Studio")
            st.rerun()
        with b:
            issue_form("studio", "Decoration", "Studio / Final QC", "Decoration Correction Required", "Decoration", row, row.get("decorator_assigned"))


def render_procurement():
    page_header("🧾 Procurement", "Issue decorator materials and raise requisitions.")
    req = load_table("order_material_requirements")
    if req.empty:
        st.info("No material requirements submitted yet.")
        return
    table(req, ["id","order_id","item_name","quantity_required","unit","requirement_status","requested_by","requested_at"])
    rid = st.number_input("Requirement ID", min_value=1, step=1)
    status = st.selectbox("Procurement action", ["Issued", "Partially Issued", "Out of Stock", "Requisition Required"])
    issued_qty = st.number_input("Quantity issued", min_value=0.0, step=1.0)
    by = st.text_input("Updated by", value="Procurement")
    notes = st.text_input("Notes")
    if st.button("Update Requirement", use_container_width=True):
        with connect() as conn:
            conn.execute("UPDATE order_material_requirements SET requirement_status=? WHERE id=?", (status, rid))
            conn.execute("INSERT INTO material_issues(requirement_id, quantity_issued, issued_by, issued_to, issue_status, issued_at, notes) VALUES(?,?,?,?,?,?,?)", (rid, issued_qty, by, "Decoration", status, now_iso(), notes))
            if status == "Requisition Required":
                row = req[req["id"] == rid].iloc[0]
                conn.execute("INSERT INTO procurement_requisitions(requirement_id, order_id, item_name, quantity_required, requisition_status, requested_at, updated_by) VALUES(?,?,?,?,?,?,?)", (rid, row.order_id, row.item_name, row.quantity_required, "Pending Procurement", now_iso(), by))
            conn.commit()
        st.success("Procurement updated."); st.rerun()


def delivery_note_html(row):
    return f"""
    <div class='print-note' style='max-width:420px;padding:24px;border:2px solid #231F20;border-radius:10px;background:#fff;color:#000;font-family:Arial,sans-serif;'>
      <h2 style='text-align:center;margin:0 0 12px;color:#000!important;'>CAKE ALBUM DELIVERY</h2>
      <hr>
      <p><b>ORDER:</b> {disp(row.get('order_id'))}</p>
      <p><b>CUSTOMER:</b> {disp(row.get('customer_name'))}</p>
      <p><b>TEL:</b> {disp(row.get('customer_number'))}</p>
      <p><b>DELIVER TO:</b><br>{disp(row.get('location'))}</p>
      <p><b>DELIVERY TIME:</b> {disp(row.get('expected_time'))}</p>
      <p><b>BALANCE:</b> {fmt_ugx(row.get('balance_to_collect') or row.get('balance'))}</p>
      <p><b>PAYMENT:</b> {disp(row.get('payment_method'))}</p>
      <hr>
      <p style='text-align:center;font-weight:bold;'>BAKING YOUR IDEAS TO LIFE</p>
    </div>
    """


def render_packaging():
    page_header("📦 Packaging", "Pack cake, print simple delivery note, and send to Dispatch.")
    df = load_orders()
    t1,t2,t3 = st.tabs(["Ready for Packaging", "Packaging", "Delivery Note"])
    with t1:
        row = select_order(filter_orders(df,["Ready for Packaging"]), "pack_ready")
        if row is not None:
            order_card(row)
            by = st.text_input("Updated by", value="Packaging", key="pack_by1")
            if st.button("▶️ Start Packaging", use_container_width=True):
                update_order(row.order_id, {"workflow_status":"Packaging", "current_owner":"Packaging", "next_action":"Print delivery note and complete packaging", "packaging_status":"In Progress"}, by, "Packaging Started", "Packaging")
                st.rerun()
    with t2:
        row = select_order(filter_orders(df,["Packaging"]), "pack_prog")
        if row is not None:
            order_card(row)
            st.markdown("### Delivery Note Preview")
            st.markdown(delivery_note_html(row), unsafe_allow_html=True)
            st.info("Use your browser print option. On Windows: Ctrl + P. Print this simple note and pin it to the cake box.")
            by = st.text_input("Updated by", value="Packaging", key="pack_by2")
            if st.button("✅ Packaging Complete → Ready for Dispatch", use_container_width=True):
                update_order(row.order_id, {"workflow_status":"Ready for Dispatch", "current_owner":"Dispatch", "next_action":"Assign to delivery run", "packaging_status":"Complete", "packaging_completed_at":now_iso()}, by, "Packaging Complete", "Packaging")
                st.rerun()
    with t3:
        row = select_order(df, "pack_note", "Select any order for delivery note")
        if row is not None:
            st.markdown(delivery_note_html(row), unsafe_allow_html=True)


def render_dispatch():
    page_header("🚚 Dispatch", "Create delivery runs with multiple cakes and stop sequence.")
    df = load_orders()
    _,_,_,_,drivers = staff_lists()
    ready = filter_orders(df,["Ready for Dispatch"])
    if ready.empty:
        st.info("No cakes ready for dispatch.")
    else:
        st.markdown("### Create Delivery Run")
        driver = st.selectbox("Driver", drivers)
        by = st.text_input("Created by", value="Dispatch")
        selected = st.multiselect("Select orders for this run", ready.apply(order_label, axis=1).tolist())
        sequence = []
        if selected:
            st.markdown("### Stop sequence")
            for i, label in enumerate(selected, start=1):
                st.write(f"Stop {i}: {label}")
                sequence.append(label.split(" · ")[0])
        if st.button("Create Delivery Run", use_container_width=True) and sequence:
            run_id = f"RUN-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{driver[:3].upper()}"
            with connect() as conn:
                conn.execute("INSERT INTO delivery_runs(run_id, driver_name, run_status, created_at, created_by) VALUES(?,?,?,?,?)", (run_id, driver, "Planned", now_iso(), by))
                for stop, oid in enumerate(sequence, start=1):
                    conn.execute("INSERT INTO delivery_run_orders(run_id, order_id, stop_sequence, delivery_status) VALUES(?,?,?,?)", (run_id, oid, stop, "Planned"))
                    conn.execute("UPDATE orders SET workflow_status=?, current_owner=?, next_action=?, driver_assigned=?, last_updated_at=?, last_updated_by=? WHERE order_id=?", ("Delivery Run Assigned", "Driver", "Start delivery run", driver, now_iso(), by, oid))
                    conn.execute("INSERT INTO audit_logs(order_id, action_type, stage, action_details, performed_by, performed_at) VALUES(?,?,?,?,?,?)", (oid, "Assigned to Delivery Run", "Dispatch", run_id, by, now_iso()))
                conn.commit()
            refresh_data(); st.success(f"Delivery run {run_id} created."); st.rerun()
    st.markdown("### Delivery Runs")
    runs = load_table("delivery_runs")
    table(runs, ["run_id","driver_name","run_status","run_started_at","run_completed_at","created_at","created_by"])


def render_driver():
    page_header("🚗 Driver", "Simple delivery flow: Start Run → Arrived → Payment/Delivered → Next Stop.")
    runs = load_table("delivery_runs")
    if runs.empty:
        st.info("No delivery runs yet.")
        return
    run_label = st.selectbox("Select Delivery Run", runs["run_id"].tolist())
    run = runs[runs["run_id"] == run_label].iloc[0]
    dro = load_table("delivery_run_orders")
    orders = load_orders()
    stops = dro[dro["run_id"] == run_label].sort_values("stop_sequence")
    merged = stops.merge(orders, on="order_id", how="left")
    st.markdown(f"### Driver: {run.driver_name} | Status: {run.run_status}")
    if run.run_status == "Planned":
        if st.button("🚗 Start Delivery Run", use_container_width=True):
            with connect() as conn:
                conn.execute("UPDATE delivery_runs SET run_status='In Progress', run_started_at=? WHERE run_id=?", (now_iso(), run_label))
                conn.commit()
            st.rerun()
    table(merged, ["stop_sequence","order_id","customer_name","customer_number","location","expected_time","balance_to_collect","delivery_status"])
    active = merged[merged["delivery_status"].isin(["Planned","Arrived","Finance Pending","Payment Confirmed"])]
    if active.empty:
        st.success("All stops completed.")
        if st.button("✅ Complete Delivery Run", use_container_width=True):
            with connect() as conn:
                conn.execute("UPDATE delivery_runs SET run_status='Completed', run_completed_at=? WHERE run_id=?", (now_iso(), run_label))
                conn.commit()
            st.rerun()
        return
    current = active.iloc[0]
    st.markdown("### Current Stop")
    order_card(current, [("Stop", current.stop_sequence), ("Balance", fmt_ugx(current.balance_to_collect))])
    a,b,c = st.columns(3)
    if a.button("📍 Arrived at Destination", use_container_width=True):
        with connect() as conn:
            conn.execute("UPDATE delivery_run_orders SET arrival_time=?, delivery_status='Arrived' WHERE id=?", (now_iso(), int(current.id)))
            conn.execute("UPDATE orders SET delivery_status='Arrived', workflow_status='Arrived at Destination', current_owner='Driver', next_action='Collect balance or complete handover' WHERE order_id=?", (current.order_id,))
            conn.commit()
        refresh_data(); st.rerun()
    balance = float(current.balance_to_collect or 0)
    if b.button("💰 Request Finance Confirmation" if balance > 0 else "No Balance Needed", disabled=balance <= 0, use_container_width=True):
        with connect() as conn:
            conn.execute("UPDATE delivery_run_orders SET finance_confirmation_requested_at=?, delivery_status='Finance Pending' WHERE id=?", (now_iso(), int(current.id)))
            conn.execute("UPDATE orders SET workflow_status='Finance Payment Confirmation Pending', current_owner='Finance', next_action='Confirm delivery balance received' WHERE order_id=?", (current.order_id,))
            conn.commit()
        refresh_data(); st.rerun()
    can_deliver = balance <= 0 or current.workflow_status == "Payment Confirmed" or current.delivery_status == "Payment Confirmed"
    if c.button("✅ Delivered / Next Stop", disabled=not can_deliver, use_container_width=True):
        with connect() as conn:
            conn.execute("UPDATE delivery_run_orders SET delivery_completed_at=?, delivery_status='Delivered' WHERE id=?", (now_iso(), int(current.id)))
            conn.execute("UPDATE orders SET workflow_status='Follow-up Pending', current_owner='Follow-up / Complaints', next_action='Customer follow-up', delivery_status='Delivered', delivered_at=? WHERE order_id=?", (now_iso(), current.order_id))
            conn.commit()
        refresh_data(); st.rerun()


def render_followup_complaints():
    page_header("📞 Follow-up / Complaints", "Capture satisfaction and open complaint cases when service fails.")
    df = load_orders()
    t1,t2 = st.tabs(["Customer Follow-up", "Complaint Cases"])
    with t1:
        row = select_order(filter_orders(df,["Follow-up Pending"]), "follow_order")
        if row is not None:
            order_card(row)
            satisfied = st.radio("Customer satisfied?", ["Yes", "No"])
            rating = st.slider("Rating", 1, 5, 5)
            comments = st.text_area("Customer comments")
            by = st.text_input("Followed up by", value="Customer Care")
            if satisfied == "Yes":
                if st.button("✅ Close Follow-up", use_container_width=True):
                    update_order(row.order_id, {"workflow_status":"Follow-up Done", "current_owner":"Customer Care", "next_action":"Closed", "follow_up_status":"Done", "follow_up_completed_at":now_iso()}, by, "Follow-up Closed", "Customer Care")
                    st.success("Order closed."); st.rerun()
            else:
                cat = st.selectbox("Complaint category", ["Cake Quality", "Wrong Design", "Wrong Flavour", "Late Delivery", "Damaged Cake", "Customer Service", "Payment Issue", "Driver Conduct", "Missing Accessories", "Other"])
                severity = st.selectbox("Severity", ["Low", "Medium", "High", "Critical"])
                resp = st.selectbox("Responsible department", ["Customer Care", "Baking", "Filling / Piling", "Coating / Covering", "Decoration", "Studio / Final QC", "Packaging", "Delivery", "Finance", "Procurement"])
                if st.button("🚨 Open Complaint Case", use_container_width=True):
                    cid = f"CMP-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:3].upper()}"
                    with connect() as conn:
                        conn.execute("INSERT INTO complaints(complaint_id, order_id, customer_name, complaint_category, complaint_details, severity, responsible_department, opened_at, complaint_status) VALUES(?,?,?,?,?,?,?,?,?)", (cid, row.order_id, row.customer_name, cat, comments, severity, resp, now_iso(), "Opened"))
                        conn.commit()
                    update_order(row.order_id, {"workflow_status":"Complaint Open", "current_owner":"Follow-up / Complaints", "next_action":"Resolve complaint", "follow_up_status":"Complaint Open"}, by, "Complaint Opened", "Follow-up")
                    st.warning(f"Complaint {cid} opened."); st.rerun()
    with t2:
        comp = load_table("complaints")
        table(comp, ["complaint_id","order_id","customer_name","complaint_category","severity","responsible_department","complaint_status","opened_at","resolved_at"])
        if not comp.empty:
            cid = st.selectbox("Select complaint", comp["complaint_id"].tolist())
            rowc = comp[comp["complaint_id"] == cid].iloc[0]
            action = st.text_area("Resolution action")
            by = st.text_input("Updated by", value="Customer Care", key="comp_by")
            if st.button("✅ Mark Complaint Resolved", use_container_width=True):
                with connect() as conn:
                    conn.execute("UPDATE complaints SET complaint_status='Closed', resolution_action=?, resolved_at=?, customer_confirmation='Confirmed' WHERE complaint_id=?", (action, now_iso(), cid))
                    conn.commit()
                update_order(rowc.order_id, {"workflow_status":"Follow-up Done", "current_owner":"Customer Care", "next_action":"Closed", "follow_up_status":"Complaint Resolved"}, by, "Complaint Resolved", "Follow-up")
                st.success("Complaint closed."); st.rerun()


def render_admin():
    page_header("👑 Owner / Admin Command Center", "v1.1 operational visibility.")
    df = load_orders()
    comp = load_table("complaints")
    qc = load_table("stage_quality_checks")
    runs = load_table("delivery_runs")
    c1,c2,c3,c4 = st.columns(4)
    with c1: kpi("Orders", f"{len(df):,}")
    with c2: kpi("Active", f"{int(~col(df,'workflow_status').isin(['Follow-up Done']).sum()):,}")
    with c3: kpi("Quality Issues", f"{int((qc['check_status']=='Rejected').sum()) if not qc.empty else 0:,}")
    with c4: kpi("Open Complaints", f"{int((comp['complaint_status']!='Closed').sum()) if not comp.empty else 0:,}")
    st.markdown("### Workflow Status")
    if "workflow_status" in df.columns:
        table(col(df,"workflow_status").value_counts().reset_index().rename(columns={"workflow_status":"count","index":"workflow_status"}), ["workflow_status","count"])
    st.markdown("### Quality Issues")
    table(qc.sort_values("checked_at", ascending=False).head(50) if not qc.empty else qc, ["order_id","from_stage","to_stage","check_status","issue_category","issue_description","responsible_person","checked_by","checked_at"])
    st.markdown("### Delivery Runs")
    table(runs, ["run_id","driver_name","run_status","run_started_at","run_completed_at","created_at"])
    st.markdown("### Complaints")
    table(comp, ["complaint_id","order_id","customer_name","complaint_category","severity","complaint_status","responsible_department","opened_at"])


PAGES = {
    "Owner / Admin": render_admin_dashboard,
    "Customer Care": render_customer_care,
    "Finance": render_finance,
    "Production Planning": render_production_planning,
    "Baking": render_baking,
    "Filling & Piling": render_piling,
    "Coating / Covering": render_covering,
    "Decoration": render_decoration,
    "Studio / Final QC": render_studio_qc,
    "Packaging": render_packaging,
    "Dispatch / Driver": render_dispatch,
    "Procurement": render_procurement,
    "Follow-up / Complaints": render_followup_complaints,
}

def render_login():
    st.markdown("<div style='text-align:center;padding-top:45px;'><h1>Cake Album</h1><p style='font-style:italic;'>Operations Platform v1.1 TEST</p></div>", unsafe_allow_html=True)
    with st.form("login_form"):
        dept = st.selectbox("Department", list(DEPARTMENT_PASSCODES.keys()))
        passcode = st.text_input("Passcode", type="password")
        submitted = st.form_submit_button("Enter", use_container_width=True)
    if submitted:
        if DEPARTMENT_PASSCODES.get(dept) == passcode:
            st.session_state.authenticated = True
            st.session_state.department = dept
            st.rerun()
        else:
            st.error("Incorrect passcode.")


def render_sidebar():
    st.sidebar.markdown("### Cake Album")
    st.sidebar.caption("v1.1 TEST release")
    st.sidebar.divider()
    st.sidebar.write(f"Signed in as: **{st.session_state.department}**")
    st.sidebar.caption(f"DB: {DATABASE_FILE.name}")
    if st.sidebar.button("Log out", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.department = None
        st.rerun()
    if st.session_state.department == "Owner / Admin":
        st.sidebar.divider()
        return st.sidebar.radio("Go to", list(PAGES.keys()))
    return st.session_state.department


def main():
    inject_css()
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
        st.session_state.department = None
    if not st.session_state.authenticated:
        render_login(); return
    page = render_sidebar()
    PAGES[page]()


if __name__ == "__main__":
    main()
