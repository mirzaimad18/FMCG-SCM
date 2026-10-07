
"""
Parle-G Agentic AI Supply Chain Engine - Dashboard (tabbed version)
===================================================================
Runs your existing pipeline and shows the results with charts.
It does NOT change any agent. Start it with:

    streamlit run dashboard.py
"""

import pandas as pd
import streamlit as st

from pipeline import run_pipeline
from agents.spatial_routing_agent import load_warehouses

st.set_page_config(page_title="Parle-G Supply Chain Dashboard", page_icon="📦", layout="wide")

ICONS = {
    "fulfilled directly": "🟢 fulfilled directly",
    "transfer auto-approved": "🟡 transfer auto-approved",
    "transfer pending manager approval": "🟠 pending manager approval",
    "no warehouse for region": "🔴 no warehouse for region",
    "no surplus available": "🔴 no surplus available",
}


def stock_df(warehouses):
    """One row per warehouse and SKU: how many units are in stock."""
    rows = []
    for w in warehouses:
        for sku, qty in w.get("stock_per_sku", {}).items():
            rows.append({"Warehouse": w.get("warehouse_id"), "SKU": sku, "Stock": qty})
    return pd.DataFrame(rows, columns=["Warehouse", "SKU", "Stock"])


def counts_df(warehouses):
    """Incoming and outgoing order counts for each warehouse."""
    rows = [
        {
            "Warehouse": w.get("warehouse_id"),
            "Incoming": w.get("incoming_orders_count", 0),
            "Outgoing": w.get("outgoing_orders_count", 0),
        }
        for w in warehouses
    ]
    return pd.DataFrame(rows, columns=["Warehouse", "Incoming", "Outgoing"]).set_index("Warehouse")


# ------------------------------------------------------------------ Header
st.title("📦 Parle-G Agentic AI Supply Chain Engine")
st.caption(
    "Order Intake → Spatial Routing → Rebalancing → Guardrail  |  "
    "Data: Firestore (velochaindb001)"
)

# ----------------------------------------------------------------- Sidebar
with st.sidebar:
    st.header("Controls")
    run_clicked = st.button("▶ Run pipeline", type="primary", width="stretch")
    st.info(
        "Each run moves real stock in Firestore, so numbers can change "
        "from one run to the next. This is normal."
    )

if run_clicked:
    with st.spinner("Running all 4 agents... this can take about a minute."):
        try:
            before = load_warehouses()
            result = run_pipeline()
            after = load_warehouses()
            st.session_state.update(before=before, result=result, after=after)
        except Exception as e:
            st.error(f"Pipeline failed: {e}")

result = st.session_state.get("result")
before = st.session_state.get("before")

try:
    live = st.session_state.get("after") or load_warehouses()
except Exception as e:
    live = []
    st.warning(f"Could not load warehouses: {e}")

# -------------------------------------------------------------------- Tabs
tab_over, tab_orders, tab_transfers, tab_wh = st.tabs(
    ["📊 Overview", "📋 Orders", "🔁 Transfers", "🏭 Warehouses"]
)

orders_df = pd.DataFrame(result.orders) if result else pd.DataFrame()

# ---- Overview
with tab_over:
    if result is None:
        st.write("Click **Run pipeline** in the sidebar to start.")
    else:
        s = result.stats
        total = s.get("total_orders", 0)
        rate = (s.get("fulfilled_directly", 0) + s.get("transfers_created", 0)) / total * 100 if total else 0

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Total orders", total)
        c2.metric("Fulfilled directly", s.get("fulfilled_directly", 0))
        c3.metric("Transfers", s.get("transfers_created", 0))
        c4.metric("Pending approval", s.get("pending_manager_approval", 0))
        c5.metric("Unfulfillable", s.get("unfulfillable", 0))
        c6.metric("Fulfilment rate", f"{rate:.0f}%")

        left, right = st.columns(2)
        with left:
            st.subheader("Orders by outcome")
            if "final_routing_outcome" in orders_df:
                st.bar_chart(orders_df["final_routing_outcome"].value_counts())
        with right:
            st.subheader("Orders by priority tier")
            if "priority_tier" in orders_df:
                st.bar_chart(orders_df["priority_tier"].value_counts().sort_index())

# ---- Orders
with tab_orders:
    if orders_df.empty:
        st.write("Run the pipeline to see orders.")
    else:
        f1, f2 = st.columns(2)
        types = f1.multiselect(
            "Customer type", sorted(orders_df["customer_type"].unique()),
            default=sorted(orders_df["customer_type"].unique()),
        )
        outcomes = f2.multiselect(
            "Outcome", sorted(orders_df["final_routing_outcome"].unique()),
            default=sorted(orders_df["final_routing_outcome"].unique()),
        )
        shown = orders_df[
            orders_df["customer_type"].isin(types)
            & orders_df["final_routing_outcome"].isin(outcomes)
        ]
        cols = [
            "store_id", "customer_type", "priority_tier", "region", "sku",
            "quantity_requested", "assigned_warehouse", "final_routing_outcome",
        ]
        shown = shown[[c for c in cols if c in shown.columns]].copy()
        shown["final_routing_outcome"] = shown["final_routing_outcome"].map(lambda x: ICONS.get(x, x))
        st.dataframe(shown, width="stretch", hide_index=True)

# ---- Transfers
with tab_transfers:
    if result is None:
        st.write("Run the pipeline to see transfers.")
    elif not result.transfers:
        st.info("No transfers were needed in this run.")
    else:
        tr = pd.DataFrame(result.transfers)
        m1, m2 = st.columns(2)
        m1.metric("Transfers created", len(tr))
        m2.metric("Total units moved", f"{tr['quantity'].sum():,}")
        st.dataframe(tr, width="stretch", hide_index=True)
        st.caption("Guardrail rule: transfers of 50,000 units or more need manager approval.")

# ---- Warehouses
with tab_wh:
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
            cmp = stock_df(before).merge(sdf, on=["Warehouse", "SKU"], how="outer", suffixes=(" before", " after")).fillna(0)
            cmp["Change"] = cmp["Stock after"] - cmp["Stock before"]
            st.dataframe(cmp, width="stretch", hide_index=True)