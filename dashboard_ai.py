"""
FMCG Supply Chain Dashboard with LLM (Gemini on Vertex AI)
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
    check_orders, check_transfers, check_warehouses,
)
from rag_engine import (
    diagnose_pipeline_issues,
    search_historical_incidents,
    generate_rag_solution,
)
from pdf_export import export_report_to_pdf

st.set_page_config(page_title="FMCG Supply Chain + AI", page_icon="📦", layout="wide")

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


def pdf_download_button(report_text, title, file_name, key, summary_stats=None):
    pdf_bytes = export_report_to_pdf(report_text, title=title, summary_stats=summary_stats)
    st.download_button(
        "⬇ Download PDF",
        data=pdf_bytes,
        file_name=file_name,
        mime="application/pdf",
        key=key,
        width="stretch",
    )


st.title("📦 FMCG Supply Chain Management System")
st.caption("Order Intake → Spatial Routing → Rebalancing → Guardrail  |  Reports & Audits by Gemini on Vertex AI")

# ----------------------------------------------------------------- Pipeline Execution state
result = st.session_state.get("result")
before = st.session_state.get("before")
checks = st.session_state.get("checks", [])
try:
    live = st.session_state.get("after") or load_warehouses()
except Exception as e:
    live = []
    st.warning(f"Could not load warehouses: {e}")

# ----------------------------------------------------------------- Sidebar
with st.sidebar:
    st.header("Controls")
    run_clicked = st.button("▶ Run pipeline", type="primary", width="stretch")
    st.info("Each run moves real stock in Firestore, runs all 4 agents, and automatically performs all Gemini AI audits, QA review, executive report, and RAG resolution.")
    if result is not None:
        if st.button("🔄 Refresh all AI audits & reports", width="stretch"):
            with st.spinner("Refreshing all Gemini AI audits and reports..."):
                st.session_state["orders_ai_check"] = check_orders(result.orders, result.stats)
                st.session_state["transfers_ai_check"] = check_transfers(result.transfers, result.stats)
                st.session_state["warehouses_ai_check"] = check_warehouses(live, before, result.stats)
                facts = build_facts(result, before, live, checks, st.session_state.get("seconds"))
                st.session_state["report"] = generate_report(facts)
                st.session_state["review"] = review_checks(facts)
            st.success("All AI audits & reports refreshed!")

if run_clicked:
    status_box = st.status("🚀 Running Pipeline, AI Audits, RAG & Executive Report...", expanded=True)
    try:
        with status_box:
            st.write("📦 Step 1/5: Loading warehouses & executing multi-agent fulfillment pipeline...")
            before = load_warehouses()
            result, seconds, logs = run_with_trace(run_pipeline)
            after = load_warehouses()
            live = after
            checks = validate_pipeline(result, after)

            st.write("📋 Step 2/5: Auditing orders, transfers, and warehouses with Gemini AI...")
            orders_ai_check = check_orders(result.orders, result.stats)
            transfers_ai_check = check_transfers(result.transfers, result.stats)
            warehouses_ai_check = check_warehouses(live, before, result.stats)

            st.write("🔍 Step 3/5: Diagnosing pipeline issues & running RAG Vector Search...")
            detected_issues = diagnose_pipeline_issues(result.orders, result.transfers, result.stats)
            primary_issue = detected_issues[0] if detected_issues else None
            if primary_issue:
                rag_evidence = search_historical_incidents(primary_issue["query"], top_k=3)
                rag_issue_ctx = f"{primary_issue['title']}: {primary_issue['context']}"
                st.write("🧠 Step 4/5: Formulating RAG Incident Resolution Plan with Gemini...")
                rag_solution = generate_rag_solution(rag_issue_ctx, rag_evidence, result.stats)
            else:
                rag_evidence = []
                rag_issue_ctx = "Healthy pipeline execution"
                rag_solution = ("✅ All customer orders fulfilled successfully. No operational failures detected in this cycle.", "RAG Engine")

            st.write("📝 Step 5/5: Generating Gemini Executive Report & QA Review...")
            facts = build_facts(result, before, live, checks, seconds)
            if rag_evidence:
                facts["historical_incident_resolutions"] = [
                    f"Precedent {m['incident']['incident_id']} - {m['incident']['title']} (Relevance: {m['score']:.1%}): Root Cause: {m['incident']['root_cause']} -> Verified Resolution: {m['incident']['resolution']} (Preventative Action: {m['incident']['preventative_actions']})"
                    for m in rag_evidence
                ]
            if rag_solution and isinstance(rag_solution, tuple):
                facts["rag_playbook_plan_summary"] = rag_solution[0][:600]
            report = generate_report(facts)
            review = review_checks(facts)

            st.session_state.update(
                before=before,
                result=result,
                after=after,
                seconds=seconds,
                logs=logs,
                checks=checks,
                orders_ai_check=orders_ai_check,
                transfers_ai_check=transfers_ai_check,
                warehouses_ai_check=warehouses_ai_check,
                rag_solution=rag_solution,
                rag_evidence=rag_evidence,
                rag_issue_ctx=rag_issue_ctx,
                report=report,
                review=review,
            )
            status_box.update(label="✅ Pipeline executed & all AI audits, RAG, and reports completed!", state="complete", expanded=False)
    except Exception as e:
        status_box.update(label=f"❌ Pipeline failed: {e}", state="error")
        st.error(f"Pipeline failed: {e}")

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

        st.markdown("---")
        st.subheader("🤖 Gemini AI Orders Audit")
        st.caption("Automated audit of order fulfillment outcomes, priority fairness, regional shortages, and unserviced stores.")

        if "orders_ai_check" in st.session_state:
            text, source = st.session_state["orders_ai_check"]
            st.markdown(text)
            st.caption(f"Audit source: {source}")
            pdf_download_button(
                text, "Orders Fulfillment Audit", "fmcg_orders_audit.pdf",
                "dl_orders_audit_pdf", result.stats,
            )

        with st.expander("🔄 Re-run Orders Audit"):
            if st.button("Re-audit Orders with Gemini", key="btn_check_orders"):
                with st.spinner("Gemini is auditing customer orders..."):
                    st.session_state["orders_ai_check"] = check_orders(result.orders, result.stats)
                st.rerun()

# ---- Transfers
with t_tr:
    if result is None:
        st.write("Run the pipeline to see transfers.")
    elif not result.transfers:
        st.info("No transfers were needed in this run.")
        st.markdown("---")
        st.subheader("🤖 Gemini AI Transfers & Guardrails Audit")
        if "transfers_ai_check" in st.session_state:
            text, source = st.session_state["transfers_ai_check"]
            st.markdown(text)
            st.caption(f"Audit source: {source}")
            pdf_download_button(
                text, "Transfers and Guardrails Audit", "fmcg_transfers_audit.pdf",
                "dl_transfers_audit_pdf", result.stats,
            )
    else:
        tr = pd.DataFrame(result.transfers)
        a, b = st.columns(2)
        a.metric("Transfers created", len(tr))
        b.metric("Total units moved", f"{tr['quantity'].sum():,}")
        st.dataframe(tr, width="stretch", hide_index=True)

        st.markdown("---")
        st.subheader("🤖 Gemini AI Transfers & Guardrails Audit")
        st.caption("Automated audit of the 50,000 unit approval guardrail, volume flow, and route safety.")

        if "transfers_ai_check" in st.session_state:
            text, source = st.session_state["transfers_ai_check"]
            st.markdown(text)
            st.caption(f"Audit source: {source}")
            pdf_download_button(
                text, "Transfers and Guardrails Audit", "fmcg_transfers_audit.pdf",
                "dl_transfers_audit_pdf", result.stats,
            )

        with st.expander("🔄 Re-run Transfers Audit"):
            if st.button("Re-audit Transfers with Gemini", key="btn_check_transfers"):
                with st.spinner("Gemini is auditing transfers & guardrails..."):
                    st.session_state["transfers_ai_check"] = check_transfers(result.transfers, result.stats)
                st.rerun()

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

        st.markdown("---")
        st.subheader("🤖 Gemini AI Warehouse Inventory Audit")
        st.caption("Automated audit of warehouse inventory balances, stock depletions, load imbalances, and replenishment priorities.")

        if "warehouses_ai_check" in st.session_state:
            text, source = st.session_state["warehouses_ai_check"]
            st.markdown(text)
            st.caption(f"Audit source: {source}")
            pdf_download_button(
                text, "Warehouse Inventory Audit", "fmcg_warehouse_audit.pdf",
                "dl_warehouse_audit_pdf", result.stats,
            )

        with st.expander("🔄 Re-run Warehouses Audit"):
            if st.button("Re-audit Warehouses with Gemini", key="btn_check_warehouses"):
                with st.spinner("Gemini is auditing warehouse inventory & network health..."):
                    st.session_state["warehouses_ai_check"] = check_warehouses(live, before, result.stats if result else None)
                st.rerun()

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

        st.subheader("🤖 Gemini QA Review")
        if "review" in st.session_state:
            text, source = st.session_state["review"]
            st.markdown(text)
            st.caption(f"Written by: {source}")
            pdf_download_button(
                text, "Pipeline QA Review", "fmcg_pipeline_qa_review.pdf",
                "dl_pipeline_qa_pdf", result.stats,
            )
        else:
            if st.button("🤖 Ask Gemini to review this run", key="btn_review_checks"):
                with st.spinner("Asking Gemini..."):
                    facts = build_facts(result, before, live, checks, st.session_state["seconds"])
                    st.session_state["review"] = review_checks(facts)
                st.rerun()

        with st.expander("Execution log"):
            st.code("\n".join(st.session_state["logs"]) or "No log lines captured.")

# ---- AI Report & RAG
with t_ai:
    if result is None:
        st.write("Run the pipeline first, then view reports and historical incident solutions.")
    else:
        st.caption("Executive reporting and RAG-powered historical incident resolution powered by Gemini on Vertex AI.")

        rep_subtabs = st.tabs(["📝 Executive Report", "🧠 Historical Incidents RAG Playbook"])
        t_exec_rep, t_rag_rep = rep_subtabs

        # Subtab 1: Executive Report
        with t_exec_rep:
            st.write("Gemini executive report synthesized from real pipeline execution results.")
            if "report" in st.session_state:
                text, source = st.session_state["report"]
                st.markdown(text)
                st.caption(f"Written by: {source}")
                md_col, pdf_col = st.columns(2)
                with md_col:
                    st.download_button(
                        "⬇ Download report (.md)",
                        text,
                        file_name="fmcg_pipeline_report.md",
                        key="dl_report",
                        width="stretch",
                    )
                with pdf_col:
                    pdf_download_button(
                        text,
                        "Executive Supply Chain Report",
                        "fmcg_pipeline_report.pdf",
                        "dl_report_pdf",
                        result.stats,
                    )
            else:
                st.info("Run the pipeline in the sidebar to generate the executive report automatically.")

            with st.expander("🔄 Re-generate Executive Report"):
                if st.button("Re-generate report with Gemini", key="btn_gen_exec_rep"):
                    with st.spinner("Writing report with Gemini..."):
                        facts = build_facts(result, before, live, checks, st.session_state.get("seconds"))
                        st.session_state["report"] = generate_report(facts)
                    st.rerun()

        # Subtab 2: RAG Incident Playbook
        with t_rag_rep:
            st.subheader("🧠 RAG Incident Resolution Playbook")
            st.caption(
                "When an order cannot be fulfilled or an operational bottleneck occurs, "
                "our **RAG Engine** uses vector similarity search across a curated database of **Historical Supply Chain Incidents** "
                "to retrieve proven solutions and synthesize an immediate remediation plan."
            )

            # Display automated RAG solution if present
            if "rag_solution" in st.session_state and "rag_evidence" in st.session_state:
                st.markdown("---")
                st.subheader("📚 1. Vector Search Results (Matching Historical Incidents)")
                st.caption("Top precedents retrieved using semantic vector similarity.")

                for idx, item in enumerate(st.session_state["rag_evidence"], 1):
                    inc = item["incident"]
                    score = item["score"]
                    src = item.get("source", "Vector Index")
                    badge = "🟢 High Match" if score > 0.6 else "🟡 Moderate Match"
                    with st.expander(f"#{idx} [{inc.get('incident_id')}] {inc.get('title')} — {badge} ({score:.1%})", expanded=(idx == 1)):
                        c1, c2 = st.columns([1, 2])
                        with c1:
                            st.markdown(f"**Incident ID:** `{inc.get('incident_id')}`")
                            st.markdown(f"**Category:** {inc.get('category')}")
                            st.markdown(f"**Similarity Score:** `{score:.4f}`")
                            st.caption(f"Index: {src}")
                        with c2:
                            st.markdown(f"**Symptoms:** {inc.get('symptoms')}")
                            st.markdown(f"**Root Cause:** {inc.get('root_cause')}")
                        st.markdown(f"**Verified Historical Resolution:**\n> {inc.get('resolution')}")
                        st.markdown(f"**Preventative Actions:**\n> {inc.get('preventative_actions')}")

                st.markdown("---")
                st.subheader("💡 2. Gemini Grounded Remediation Plan (RAG Synthesis)")
                sol_text, sol_src = st.session_state["rag_solution"]
                st.markdown(sol_text)
                st.caption(f"Solution Source: {sol_src}")
                md_col, pdf_col = st.columns(2)
                with md_col:
                    st.download_button(
                        "⬇ Download RAG remediation (.md)",
                        sol_text,
                        file_name="rag_incident_remediation.md",
                        key="dl_rag_sol",
                        width="stretch",
                    )
                with pdf_col:
                    pdf_download_button(
                        sol_text,
                        "RAG Incident Remediation",
                        "rag_incident_remediation.pdf",
                        "dl_rag_sol_pdf",
                        result.stats,
                    )

            # Allow user to explore other failure modes or custom queries
            st.markdown("---")
            with st.expander("🔍 Query Another Detected Issue or Custom Failure Mode"):
                detected = diagnose_pipeline_issues(result.orders, result.transfers, result.stats)
                options = [d["title"] for d in detected]
                options.append("🔍 Custom Incident / Error Query")

                selected_label = st.selectbox(
                    "Select an issue or query to solve with RAG:",
                    options,
                    key="rag_selected_issue",
                )

                if selected_label == "🔍 Custom Incident / Error Query":
                    query_text = st.text_input(
                        "Enter issue description, error code, or root cause:",
                        value="Order rejected because no warehouse serves regional territory",
                        key="rag_custom_query",
                    )
                    issue_ctx = query_text
                else:
                    match_issue = next(d for d in detected if d["title"] == selected_label)
                    query_text = match_issue["query"]
                    issue_ctx = f"{match_issue['title']}: {match_issue['context']}"
                    st.info(f"**Context:** {match_issue['context']}")

                if st.button("🚀 Search & Solve with RAG", key="btn_run_rag", type="primary"):
                    with st.spinner("Executing vector search over historical incidents & generating Gemini remediation plan..."):
                        retrieved = search_historical_incidents(query_text, top_k=3)
                        sol_text, sol_src = generate_rag_solution(issue_ctx, retrieved, result.stats)
                        st.session_state["rag_solution"] = (sol_text, sol_src)
                        st.session_state["rag_evidence"] = retrieved
                        st.session_state["rag_issue_ctx"] = issue_ctx
                    st.rerun()