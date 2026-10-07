"""
Parle-G Supply Chain Dashboard with LLM (Gemini on Vertex AI)
=============================================================
Tabs: Overview, Orders, Transfers, Warehouses, Pipeline Execution, AI Report.
Does NOT change any agent. Start it with:

    streamlit run dashboard_ai.py
"""

import pandas as pd
import streamlit as st

from pipeline import run_pipeline
from agents.spatial_routing_agent import load_warehouses
from reporting import (
    build_facts, generate_report, review_checks, run_with_trace, validate_pipeline,
)

st.set_page_config(page_title="Parle-G Supply Chain + AI", page_icon="📦", layout="wide")

ICONS = {
    "fulfilled directly": "🟢 fulfilled directly",
    "transfer auto-approved": "🟡 transfer auto-approved",
    "transfer pending manager approval": "🟠 pending manager approval",
    "no warehouse for region": "🔴 no warehouse for region",
    "no surplus available": "🔴 no surplus available",
}


def stock_df(warehouses):
    rows = [{"Warehouse": w.get("warehouse_id"), "SKU": k, "Stock": v}
            for w in warehouses for k, v in w.get("stock_per_sku", {}).items()]
    return pd.DataFrame(rows, columns=["Warehouse", "SKU", "Stock"])


def counts_df(warehouses):
    rows = [{"Warehouse": w.get("warehouse_id"), "Incoming": w.get("incoming_orders_count", 0),
             "Outgoing": w.get("outgoing_orders_count", 0)} for w in warehouses]
    return pd.DataFrame(rows, columns=["Warehouse", "Incoming", "Outgoing"]).set_index("Warehouse")


st.title("📦 Parle-G Agentic AI Supply Chain Engine")
st.caption("Order Intake → Spatial Routing → Rebalancing → Guardrail  |  Reports by Gemini on Vertex AI")

# ----------------------------------------------------------------- Sidebar
with st.sidebar:
    st.header("Controls")
    run_clicked = st.button("▶ Run pipeline", type="primary", width="stretch")
    st.info("Each run moves real stock in Firestore, so numbers change between runs.")

if run_clicked:
    with st.spinner("Running all 4 agents... this can take about a minute."):
        try:
            before = load_warehouses()
            result, seconds, logs = run_with_trace(run_pipeline)
            after = load_warehouses()
            checks = validate_pipeline(result, after)
            for key in ("report", "review"):
                st.session_state.pop(key, None)
            st.session_state.update(before=before, result=result, after=after,
                                    seconds=seconds, logs=logs, checks=checks)
        except Exception as e:
            st.error(f"Pipeline failed: {e}")

result = st.session_state.get("result")
before = st.session_state.get("before")
checks = st.session_state.get("checks", [])
try:
    live = st.session_state.get("after") or load_warehouses()
except Exception as e:
    live = []
    st.warning(f"Could not load warehouses: {e}")

orders_df = pd.DataFrame(result.orders) if result else pd.DataFrame()

tabs = st.tabs(["📊 Overview", "📋 Orders", "🔁 Transfers", "🏭 Warehouses",
                "⚙️ Pipeline Execution", "🤖 AI Report"])
t_over, t_orders, t_tr, t_wh, t_exec, t_ai = tabs

# ---- Overview
with t_over:
    if result is None:
        st.write("Click **Run pipeline** in the sidebar to start.")
    else:
        s = result.stats
        total = s.get("total_orders", 0)
        rate = (s.get("fulfilled_directly", 0) + s.get("transfers_created", 0)) / total * 100 if total else 0
        cols = st.columns(6)
        for c, (label, val) in zip(cols, [
            ("Total orders", total), ("Fulfilled directly", s.get("fulfilled_directly", 0)),
            ("Transfers", s.get("transfers_created", 0)), ("Pending approval", s.get("pending_manager_approval", 0)),
            ("Unfulfillable", s.get("unfulfillable", 0)), ("Fulfilment rate", f"{rate:.0f}%")]):
            c.metric(label, val)
        l, r = st.columns(2)
        with l:
            st.subheader("Orders by outcome")
            st.bar_chart(orders_df["final_routing_outcome"].value_counts())
        with r:
            st.subheader("Orders by priority tier")
            st.bar_chart(orders_df["priority_tier"].value_counts().sort_index())

# ---- Orders
with t_orders:
    if orders_df.empty:
        st.write("Run the pipeline to see orders.")
    else:
        types = st.multiselect("Customer type", sorted(orders_df["customer_type"].unique()),
                               default=sorted(orders_df["customer_type"].unique()))
        cols = ["store_id", "customer_type", "priority_tier", "region", "sku",
                "quantity_requested", "assigned_warehouse", "final_routing_outcome"]
        shown = orders_df[orders_df["customer_type"].isin(types)][[c for c in cols if c in orders_df]].copy()
        shown["final_routing_outcome"] = shown["final_routing_outcome"].map(lambda x: ICONS.get(x, x))
        st.dataframe(shown, width="stretch", hide_index=True)

# ---- Transfers
with t_tr:
    if result is None:
        st.write("Run the pipeline to see transfers.")
    elif not result.transfers:
        st.info("No transfers were needed in this run.")
    else:
        tr = pd.DataFrame(result.transfers)
        a, b = st.columns(2)
        a.metric("Transfers created", len(tr))
        b.metric("Total units moved", f"{tr['quantity'].sum():,}")
        st.dataframe(tr, width="stretch", hide_index=True)

# ---- Warehouses
with t_wh:
    sdf = stock_df(live)
    if sdf.empty:
        st.write("No warehouse data found.")
    else:
        l, r = st.columns(2)
        with l:
            st.subheader("Stock per warehouse")
            st.bar_chart(sdf.pivot_table(index="Warehouse", columns="SKU", values="Stock", fill_value=0))
        with r:
            st.subheader("Incoming / outgoing orders")
            st.bar_chart(counts_df(live))
        if before:
            st.subheader("Stock before vs after the last run")
            cmp = stock_df(before).merge(sdf, on=["Warehouse", "SKU"], how="outer",
                                         suffixes=(" before", " after")).fillna(0)
            cmp["Change"] = cmp["Stock after"] - cmp["Stock before"]
            st.dataframe(cmp, width="stretch", hide_index=True)

# ---- Pipeline Execution
with t_exec:
    if result is None:
        st.write("Run the pipeline to see how it executed.")
    else:
        s = result.stats
        passed = sum(1 for c in checks if "PASS" in c["Result"])
        a, b, c = st.columns(3)
        a.metric("Run time", f"{st.session_state['seconds']} s")
        b.metric("Checks passed", f"{passed} / {len(checks)}")
        c.metric("Log lines", len(st.session_state["logs"]))

        if passed == len(checks):
            st.success("✅ Pipeline verdict: all checks passed. The run looks correct.")
        else:
            st.error("❌ Pipeline verdict: some checks failed. See the table below.")

        st.subheader("Agent steps")
        auto, pend = s.get("auto_approved", 0), s.get("pending_manager_approval", 0)
        steps = [
            ("1. Order Intake", f"{s.get('total_orders', 0)} orders ranked by priority"),
            ("2. Spatial Routing", f"{s.get('fulfilled_directly', 0)} fulfilled directly, "
                                   f"{s.get('no_warehouse_for_region', 0)} with no warehouse"),
            ("3. Rebalancing", f"{s.get('transfers_created', 0)} transfers created"),
            ("4. Guardrail", f"{auto} auto-approved, {pend} pending manager"),
        ]
        for col, (title, text) in zip(st.columns(4), steps):
            col.success(f"**{title}**\n\n{text}")

        st.subheader("Validation checks")
        st.dataframe(pd.DataFrame(checks), width="stretch", hide_index=True)

        if st.button("🤖 Ask Gemini to review this run"):
            with st.spinner("Asking Gemini..."):
                facts = build_facts(result, before, live, checks, st.session_state["seconds"])
                st.session_state["review"] = review_checks(facts)
        if "review" in st.session_state:
            text, source = st.session_state["review"]
            st.markdown(text)
            st.caption(f"Written by: {source}")

        with st.expander("Execution log"):
            st.code("\n".join(st.session_state["logs"]) or "No log lines captured.")

# ---- AI Report
with t_ai:
    if result is None:
        st.write("Run the pipeline first, then generate a report.")
    else:
        st.write("Gemini writes the report from the real pipeline results. "
                 "It explains the decisions. It does not make them.")
        if st.button("📝 Generate report with Gemini", type="primary"):
            with st.spinner("Writing report..."):
                facts = build_facts(result, before, live, checks, st.session_state["seconds"])
                st.session_state["report"] = generate_report(facts)
        if "report" in st.session_state:
            text, source = st.session_state["report"]
            st.markdown(text)
            st.caption(f"Written by: {source}")
            st.download_button("⬇ Download report (.md)", text, file_name="parle_g_pipeline_report.md")