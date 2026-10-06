"""
Parle-G Agentic AI Supply Chain Engine - Dashboard
==================================================
A simple web page that runs your existing pipeline and shows the results.
It does NOT change any agent. Start it with:

    streamlit run app.py
"""

import pandas as pd
import streamlit as st

from pipeline import run_pipeline
from agents.spatial_routing_agent import load_warehouses

st.set_page_config(page_title="Parle-G Supply Chain Engine", layout="wide")

st.title("Parle-G Agentic AI Supply Chain Engine")
st.caption(
    "Smart order prioritization and dynamic stock rebalancing  |  "
    "4 agents: Order Intake, Spatial Routing, Rebalancing, Guardrail"
)

OUTCOME_ICONS = {
    "fulfilled directly": "🟢 fulfilled directly",
    "transfer auto-approved": "🟡 transfer auto-approved",
    "transfer pending manager approval": "🟠 pending manager approval",
    "no warehouse for region": "🔴 no warehouse for region",
    "no surplus available": "🔴 no surplus available",
}


def warehouses_table():
    """Reads the current warehouses from Firestore and makes a table."""
    rows = []
    for w in load_warehouses():
        stock = ", ".join(f"{sku}: {qty:,}" for sku, qty in w.get("stock_per_sku", {}).items())
        rows.append(
            {
                "Warehouse": w.get("warehouse_id"),
                "Zone": w.get("zone"),
                "Stock": stock,
                "Incoming orders": w.get("incoming_orders_count", 0),
                "Outgoing orders": w.get("outgoing_orders_count", 0),
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- Sidebar
with st.sidebar:
    st.header("Controls")
    run_clicked = st.button("▶ Run pipeline", type="primary", use_container_width=True)
    st.info(
        "Each run moves real stock in Firestore, so the numbers can change "
        "from one run to the next. This is normal."
    )

# ----------------------------------------------------------- Run pipeline
if run_clicked:
    with st.spinner("Running all 4 agents... this can take about a minute."):
        try:
            st.session_state["result"] = run_pipeline()
        except Exception as e:
            st.error(f"Pipeline failed: {e}")

result = st.session_state.get("result")

if result is None:
    st.write("Click **Run pipeline** in the sidebar to start.")
else:
    stats = result.stats

    # ---- Summary boxes
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total orders", stats.get("total_orders", 0))
    c2.metric("Fulfilled directly", stats.get("fulfilled_directly", 0))
    c3.metric("Transfers created", stats.get("transfers_created", 0))
    c4.metric("Pending manager approval", stats.get("pending_manager_approval", 0))
    c5.metric("Unfulfillable", stats.get("unfulfillable", 0))

    # ---- Orders table
    st.subheader("Orders (sorted by priority)")
    orders_df = pd.DataFrame(result.orders)
    wanted = [
        "store_id", "customer_type", "priority_tier", "region", "sku",
        "quantity_requested", "assigned_warehouse", "final_routing_outcome",
    ]
    orders_df = orders_df[[c for c in wanted if c in orders_df.columns]].copy()
    if "final_routing_outcome" in orders_df.columns:
        orders_df["final_routing_outcome"] = orders_df["final_routing_outcome"].map(
            lambda x: OUTCOME_ICONS.get(x, x)
        )
    st.dataframe(orders_df, use_container_width=True, hide_index=True)

    # ---- Transfers table
    st.subheader("Inter-warehouse transfers (Guardrail decision)")
    if result.transfers:
        st.dataframe(pd.DataFrame(result.transfers), use_container_width=True, hide_index=True)
        st.caption("Transfers of 50,000 units or more need manager sign-off.")
    else:
        st.write("No transfers were needed in this run.")

# ------------------------------------------------------ Warehouses (live)
st.subheader("Warehouses (live from Firestore)")
if st.button("🔄 Refresh warehouses"):
    pass  # clicking the button reloads the page, which reloads the table below
try:
    st.dataframe(warehouses_table(), use_container_width=True, hide_index=True)
except Exception as e:
    st.warning(f"Could not load warehouses: {e}")