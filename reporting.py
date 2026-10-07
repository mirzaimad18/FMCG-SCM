"""
Reporting helpers for the Parle-G Supply Chain Engine.
======================================================
1. run_with_trace()   - runs the pipeline and records time + log lines.
2. validate_pipeline()- plain Python checks that prove the pipeline output is correct.
3. generate_report()  - asks Gemini (Vertex AI) to write a report from the real results.
4. review_checks()    - asks Gemini to explain the validation results.
5. check_orders()     - asks Gemini to audit order allocation, priority tiers & unfulfilled orders.
6. check_transfers()  - asks Gemini to audit transfers, volume risks & guardrail threshold compliance.
7. check_warehouses() - asks Gemini to audit warehouse inventory balances, depletion & restocking needs.

The LLM never makes supply chain decisions. The 4 agents decide (rule-based).
The LLM only EXPLAINS the facts it is given. If Gemini is not available,
a simple template report is used instead, so the dashboard never breaks.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

PROJECT = os.getenv("GCP_PROJECT", "velochain-509610")
LOCATION = os.getenv("GCP_LOCATION", "us-central1")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
APPROVAL_LIMIT = 50_000


# ------------------------------------------------------------ 1. Run + trace
class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines: List[str] = []

    def emit(self, record):
        self.lines.append(f"[{record.levelname}] {record.name}: {record.getMessage()}")


def run_with_trace(run_fn) -> Tuple[Any, float, List[str]]:
    """Runs run_fn() (your run_pipeline) and returns (result, seconds, log_lines)."""
    capture = _LogCapture()
    root = logging.getLogger()
    root.addHandler(capture)
    start = time.time()
    try:
        result = run_fn()
    finally:
        root.removeHandler(capture)
    return result, round(time.time() - start, 1), capture.lines


# ------------------------------------------------------------- 2. Validation
def validate_pipeline(result, warehouses: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Returns a list of checks. Each check is PASS or FAIL with details."""
    s, orders, transfers = result.stats, result.orders, result.transfers
    checks: List[Dict[str, str]] = []

    def add(name: str, ok: bool, detail: str = ""):
        checks.append({"Check": name, "Result": "✅ PASS" if ok else "❌ FAIL", "Details": detail})

    total = s.get("total_orders", 0)
    add("Every order has a final outcome",
        all(o.get("final_routing_outcome") for o in orders), f"{len(orders)} orders checked")
    add("Order count matches the summary", len(orders) == total, f"{len(orders)} vs {total}")

    parts = s.get("fulfilled_directly", 0) + s.get("transfers_created", 0) + s.get("unfulfillable", 0)
    add("Fulfilled + transfers + unfulfillable = total orders", parts == total, f"{parts} vs {total}")

    tiers = [o.get("priority_tier", 99) for o in orders]
    add("Orders are sorted by priority tier", tiers == sorted(tiers), f"tiers: {tiers}")

    add("No transfer has zero or negative quantity",
        all(t.get("quantity", 0) > 0 for t in transfers), f"{len(transfers)} transfers checked")
    add("No transfer sends stock to the same warehouse",
        all(t.get("from_warehouse") != t.get("to_warehouse") for t in transfers))

    rule_ok = all(
        (t.get("quantity", 0) >= APPROVAL_LIMIT) == (t.get("status") != "auto_approved")
        for t in transfers
    )
    add(f"Guardrail rule: {APPROVAL_LIMIT:,}+ units need manager approval", rule_ok)

    add("Transfer count matches the summary",
        len(transfers) == s.get("transfers_created", 0), f"{len(transfers)} vs {s.get('transfers_created', 0)}")
    add("Each transfer belongs to exactly one order",
        sum(1 for o in orders if o.get("transfer_id")) == len(transfers))

    negative = [w.get("warehouse_id") for w in warehouses
                for q in w.get("stock_per_sku", {}).values() if q < 0]
    add("No warehouse has negative stock", not negative, f"problem warehouses: {negative}" if negative else "")
    return checks


# ----------------------------------------------------------- 3. Facts + LLM
def build_facts(result, before=None, after=None, checks=None, seconds=None) -> Dict[str, Any]:
    cols = ("store_id", "customer_type", "priority_tier", "region", "sku",
            "quantity_requested", "assigned_warehouse", "final_routing_outcome")
    facts: Dict[str, Any] = {
        "summary": result.stats,
        "orders": [{k: o.get(k) for k in cols} for o in result.orders],
        "transfers": result.transfers,
        "approval_limit_units": APPROVAL_LIMIT,
    }
    if seconds is not None:
        facts["run_seconds"] = seconds
    if before and after:
        old = {(w["warehouse_id"], k): v for w in before for k, v in w.get("stock_per_sku", {}).items()}
        change = {}
        for w in after:
            for k, v in w.get("stock_per_sku", {}).items():
                diff = v - old.get((w["warehouse_id"], k), v)
                if diff:
                    change[f"{w['warehouse_id']} / {k}"] = diff
        facts["stock_change"] = change
    if checks:
        facts["validation"] = [{"check": c["Check"], "result": c["Result"]} for c in checks]
    return facts


def _gemini(prompt: str) -> str:
    from google import genai  # imported here so the dashboard still opens without it

    client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    response = client.models.generate_content(model=MODEL, contents=prompt)
    return response.text


def _template_report(facts: Dict[str, Any]) -> str:
    s = facts["summary"]
    lines = [
        "## Pipeline report (template)",
        f"- Orders processed: **{s.get('total_orders', 0)}**",
        f"- Fulfilled directly: **{s.get('fulfilled_directly', 0)}**",
        f"- Transfers created: **{s.get('transfers_created', 0)}** "
        f"(auto-approved: {s.get('auto_approved', 0)}, pending approval: {s.get('pending_manager_approval', 0)})",
        f"- Unfulfillable orders: **{s.get('unfulfillable', 0)}** "
        f"(no warehouse for region: {s.get('no_warehouse_for_region', 0)}, "
        f"no surplus: {s.get('no_surplus_available', 0)})",
    ]
    for t in facts.get("transfers", []):
        lines.append(f"- {t['transfer_id']}: {t['quantity']:,} x {t['sku']} from {t['from_warehouse']} "
                     f"to {t['to_warehouse']} ({t['status']})")
    
    rag_resolutions = facts.get("historical_incident_resolutions", [])
    if rag_resolutions:
        lines.append("\n## 🧠 RAG Historical Incidents Playbook Summary")
        for res in rag_resolutions:
            lines.append(f"- {res}")
    return "\n".join(lines)


def generate_report(facts: Dict[str, Any]) -> Tuple[str, str]:
    """Returns (report_text, source). source says whether Gemini or the template wrote it."""
    prompt = (
        "You are a supply chain analyst for Parle-G. Write an executive report in simple, professional English "
        "using ONLY the facts below. Do not invent numbers or reasons.\n"
        "Sections:\n"
        "1) 📊 Summary of Pipeline Execution\n"
        "2) 🎯 Key Decisions (Routing allocations & stock transfers)\n"
        "3) ⚠️ Problems, Risks & Unfulfilled Orders (Identify any unfulfilled orders and root causes)\n"
        "4) 💡 Recommended Next Actions for Operations\n"
        "5) 🧠 RAG Historical Incidents Playbook Summary (Summarize the matching historical incidents and verified playbooks from facts['historical_incident_resolutions'] to resolve current unfulfilled orders or operational bottlenecks).\n\n"
        "Use short bullet points with status badges (🟢 / 🟡 / 🔴).\n\n"
        f"FACTS (JSON):\n{json.dumps(facts, indent=2, default=str)}"
    )
    try:
        return _gemini(prompt), f"Gemini ({MODEL})"
    except Exception as e:
        return _template_report(facts), f"Template (Gemini unavailable: {e})"


def review_checks(facts: Dict[str, Any]) -> Tuple[str, str]:
    """Asks Gemini to explain whether the pipeline run looks correct."""
    prompt = (
        "You are a QA engineer. Below are the results of automatic checks on one run of a "
        "4-agent supply chain pipeline. In simple English, say whether the run looks correct, "
        "explain any FAIL in one or two sentences, and suggest what to look at next. "
        "Use ONLY the facts given. Keep it under 150 words.\n\n"
        f"FACTS (JSON):\n{json.dumps(facts, indent=2, default=str)}"
    )
    try:
        return _gemini(prompt), f"Gemini ({MODEL})"
    except Exception as e:
        failed = [c for c in facts.get("validation", []) if "FAIL" in c["result"]]
        text = "All checks passed." if not failed else "Failed checks: " + ", ".join(c["check"] for c in failed)
        return text, f"Template (Gemini unavailable: {e})"


# ---------------------------------------------------- 4. Domain AI Audits
def _template_orders_check(orders: List[Dict[str, Any]], stats: Optional[Dict[str, Any]] = None) -> str:
    total = len(orders)
    fulfilled = sum(1 for o in orders if o.get("final_routing_outcome") == "fulfilled directly")
    transfers = sum(1 for o in orders if "transfer" in str(o.get("final_routing_outcome", "")))
    unfulfilled = [o for o in orders if o.get("final_routing_outcome") in ("no warehouse for region", "no surplus available")]
    
    lines = [
        "### 📋 Order Fulfillment Audit (Deterministic Analysis)",
        f"- **Total Orders Evaluated**: {total}",
        f"- **Directly Fulfilled**: {fulfilled} ({(fulfilled/total*100):.1f}% direct fulfillment rate)" if total else "- **Directly Fulfilled**: 0",
        f"- **Fulfillment via Transfers**: {transfers}",
        f"- **Unfulfilled / Bottlenecks**: {len(unfulfilled)} orders",
        "",
        "#### Priority & Service Level Breakdown:",
    ]
    tiers = {}
    for o in orders:
        t = o.get("priority_tier", "Unknown")
        tiers[t] = tiers.get(t, 0) + 1
    for t in sorted(tiers.keys()):
        lines.append(f"- **Tier {t}**: {tiers[t]} orders processed")
    
    if unfulfilled:
        lines.append("\n#### ⚠️ Unfulfilled Order Details:")
        for u in unfulfilled:
            lines.append(f"- **Store {u.get('store_id')}** ({u.get('region')}) - {u.get('quantity_requested')} units of {u.get('sku')}: *{u.get('final_routing_outcome')}*")
    else:
        lines.append("\n✅ All customer orders successfully scheduled for fulfillment.")
    return "\n".join(lines)


def check_orders(orders: List[Dict[str, Any]], stats: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """Asks Gemini to audit orders, priority fairness, regional bottlenecks, and fulfillment gaps."""
    cols = ("store_id", "customer_type", "priority_tier", "region", "sku",
            "quantity_requested", "assigned_warehouse", "final_routing_outcome")
    payload = {
        "summary": stats or {},
        "total_orders": len(orders),
        "orders": [{k: o.get(k) for k in cols} for o in orders],
    }
    prompt = (
        "You are a Senior Supply Chain Operations Auditor for Parle-G.\n"
        "Analyze the following customer order execution and allocation results.\n"
        "Provide a concise, professional audit with the following sections:\n"
        "1. 🎯 **Fulfillment & SLA Health**: Overall order fulfillment rate, direct allocations vs rebalanced transfers.\n"
        "2. 🎖️ **Priority Tier Compliance**: Did high-priority tiers (Tier 1 Government/Key Accounts) get serviced properly over lower tiers?\n"
        "3. ⚠️ **Unfulfilled Orders & Regional Gaps**: Identify any unfulfilled orders (e.g. no regional warehouse or stock exhaustion) and the root cause.\n"
        "4. 💡 **Tactical Operations Recommendations**: 2-3 concrete next actions for dispatch and regional sales.\n\n"
        "Format cleanly with markdown headers, concise bullet points, and status badges (🟢 / 🟡 / 🔴).\n"
        f"DATA (JSON):\n{json.dumps(payload, indent=2, default=str)}"
    )
    try:
        return _gemini(prompt), f"Gemini ({MODEL})"
    except Exception as e:
        return _template_orders_check(orders, stats), f"Template (Gemini unavailable: {e})"


def _template_transfers_check(transfers: List[Dict[str, Any]], stats: Optional[Dict[str, Any]] = None) -> str:
    if not transfers:
        return (
            "### 🔁 Inter-Warehouse Transfers Audit\n"
            "🟢 **No Transfers Required**: All orders were fulfilled directly from local regional inventory, "
            "or marked unfulfillable due to lack of network stock."
        )
    total_units = sum(t.get("quantity", 0) for t in transfers)
    auto = sum(1 for t in transfers if t.get("status") == "auto_approved")
    pending = sum(1 for t in transfers if t.get("status") == "pending_manager_approval")
    
    lines = [
        "### 🔁 Inter-Warehouse Transfers & Guardrails Audit (Deterministic Analysis)",
        f"- **Total Transfer Requests**: {len(transfers)}",
        f"- **Total Volume Moved**: {total_units:,} units",
        f"- **Auto-Approved (< {APPROVAL_LIMIT:,} units)**: {auto} transfers",
        f"- **Pending Manager Approval (>= {APPROVAL_LIMIT:,} units)**: {pending} transfers",
        "",
        "#### Guardrail & Route Verification:",
    ]
    for t in transfers:
        qty = t.get("quantity", 0)
        badge = "🟡 PENDING MANAGER" if qty >= APPROVAL_LIMIT else "🟢 AUTO-APPROVED"
        lines.append(f"- Transfer **{t.get('transfer_id')}**: {qty:,} units of {t.get('sku')} from `{t.get('from_warehouse')}` ➔ `{t.get('to_warehouse')}` [{badge}]")
    
    if pending > 0:
        lines.append(f"\n⚠️ **Action Required**: {pending} high-volume transfer(s) exceed {APPROVAL_LIMIT:,} units and require human-in-the-loop sign-off before fleet dispatch.")
    else:
        lines.append("\n✅ All transfers are within standard thresholds and auto-approved.")
    return "\n".join(lines)


def check_transfers(transfers: List[Dict[str, Any]], stats: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """Asks Gemini to inspect transfers, guardrail compliance (50,000 threshold), and logistics risk."""
    payload = {
        "approval_limit_units": APPROVAL_LIMIT,
        "summary": stats or {},
        "transfers": transfers,
    }
    prompt = (
        "You are a Logistics Risk & Guardrail Compliance Auditor for Parle-G.\n"
        "Review the inter-warehouse stock transfer requests generated by the Rebalancing Agent and evaluated by the Guardrail Agent.\n"
        "Provide a concise, professional audit with the following sections:\n"
        f"1. 🛡️ **Guardrail Policy Compliance**: Verify strict adherence to the {APPROVAL_LIMIT:,} units rule (transfers >= {APPROVAL_LIMIT:,} marked 'pending_manager_approval', < {APPROVAL_LIMIT:,} auto-approved).\n"
        "2. 🚚 **Route & Flow Feasibility**: Analyze origin-destination pairs, volume concentration, and freight logistics feasibility.\n"
        "3. ⚠️ **Logistics Risks & Backlog**: Highlight donor warehouse stock exhaustion risks or manager approval bottlenecks.\n"
        "4. 💡 **Fleet & Logistics Recommendations**: 2-3 specific operational guidelines for transport scheduling.\n\n"
        "Format cleanly with markdown headers, concise bullet points, and status badges (🟢 / 🟡 / 🔴).\n"
        f"DATA (JSON):\n{json.dumps(payload, indent=2, default=str)}"
    )
    try:
        return _gemini(prompt), f"Gemini ({MODEL})"
    except Exception as e:
        return _template_transfers_check(transfers, stats), f"Template (Gemini unavailable: {e})"


def _template_warehouses_check(
    warehouses: List[Dict[str, Any]],
    before: Optional[List[Dict[str, Any]]] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> str:
    total_wh = len(warehouses)
    totals_by_sku: Dict[str, int] = {}
    for w in warehouses:
        for sku, count in w.get("stock_per_sku", {}).items():
            totals_by_sku[sku] = totals_by_sku.get(sku, 0) + count
            
    lines = [
        "### 🏭 Warehouse Network & Inventory Audit (Deterministic Analysis)",
        f"- **Active Distribution Centers**: {total_wh}",
        f"- **Network Stock by SKU**: " + ", ".join(f"**{sku}**: {cnt:,} units" for sku, cnt in totals_by_sku.items()),
        "",
        "#### Warehouse Stock & Dispatch Workload:",
    ]
    for w in warehouses:
        wid = w.get("warehouse_id")
        zone = w.get("zone", "N/A")
        inc = w.get("incoming_orders_count", 0)
        outg = w.get("outgoing_orders_count", 0)
        stock_str = ", ".join(f"{k}: {v:,}" for k, v in w.get("stock_per_sku", {}).items())
        lines.append(f"- **{wid}** ({zone}): Stock [{stock_str}] | Incoming: {inc} | Outgoing: {outg}")
    
    if before:
        old_map = {(w["warehouse_id"], k): v for w in before for k, v in w.get("stock_per_sku", {}).items()}
        depleted = []
        for w in warehouses:
            wid = w.get("warehouse_id")
            for k, v in w.get("stock_per_sku", {}).items():
                old_val = old_map.get((wid, k), v)
                diff = v - old_val
                if diff < 0:
                    depleted.append((wid, k, abs(diff), v))
        if depleted:
            lines.append("\n#### 📉 Notable Stock Depletions in This Run:")
            for wid, k, diff, remaining in depleted:
                lines.append(f"- **{wid}** / {k}: Decreased by {diff:,} units (Remaining: {remaining:,})")
    
    lines.append("\n💡 **Replenishment Advice**: Trigger factory production shipments to warehouses experiencing high outgoing transfers.")
    return "\n".join(lines)


def check_warehouses(
    warehouses: List[Dict[str, Any]],
    before: Optional[List[Dict[str, Any]]] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Asks Gemini to inspect warehouse stock levels, depletion rates, and replenishment priorities."""
    stock_change = {}
    if before:
        old_map = {(w["warehouse_id"], k): v for w in before for k, v in w.get("stock_per_sku", {}).items()}
        for w in warehouses:
            wid = w.get("warehouse_id")
            for k, v in w.get("stock_per_sku", {}).items():
                diff = v - old_map.get((wid, k), v)
                if diff != 0:
                    stock_change[f"{wid} / {k}"] = diff

    payload = {
        "summary": stats or {},
        "warehouses": [
            {
                "warehouse_id": w.get("warehouse_id"),
                "zone": w.get("zone"),
                "regions_served": w.get("regions_served"),
                "stock_per_sku": w.get("stock_per_sku"),
                "incoming_orders_count": w.get("incoming_orders_count", 0),
                "outgoing_orders_count": w.get("outgoing_orders_count", 0),
            }
            for w in warehouses
        ],
        "stock_changes_this_run": stock_change,
    }
    prompt = (
        "You are an Inventory Operations & Network Optimization Director for Parle-G.\n"
        "Review the warehouse stock levels, order workload, and stock movements below.\n"
        "Provide a concise, professional audit with the following sections:\n"
        "1. 📦 **Stock Health & SKU Reserves**: Evaluate stock availability per warehouse (PG-100G, PG-200G). Flag any depleted or critically low nodes.\n"
        "2. ⚖️ **Network Load & Workload Imbalance**: Analyze incoming fulfillment vs outgoing rebalancing strain across hubs. Which centers are under heavy pressure?\n"
        "3. 📉 **Stock Depletion Analysis**: Assess the impact of stock deductions from this fulfillment cycle.\n"
        "4. 🏭 **Factory Replenishment Priorities**: 2-3 specific recommendations for Parle-G production plants (which warehouses and SKUs need prioritized replenishment).\n\n"
        "Format cleanly with markdown headers, concise bullet points, and status badges (🟢 / 🟡 / 🔴).\n"
        f"DATA (JSON):\n{json.dumps(payload, indent=2, default=str)}"
    )
    try:
        return _gemini(prompt), f"Gemini ({MODEL})"
    except Exception as e:
        return _template_warehouses_check(warehouses, before, stats), f"Template (Gemini unavailable: {e})"