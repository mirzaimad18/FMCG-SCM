"""
Reporting helpers for the Parle-G Supply Chain Engine.
======================================================
1. run_with_trace()   - runs the pipeline and records time + log lines.
2. validate_pipeline()- plain Python checks that prove the pipeline output is correct.
3. generate_report()  - asks Gemini (Vertex AI) to write a report from the real results.
4. review_checks()    - asks Gemini to explain the validation results.

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
    return "\n".join(lines)


def generate_report(facts: Dict[str, Any]) -> Tuple[str, str]:
    """Returns (report_text, source). source says whether Gemini or the template wrote it."""
    prompt = (
        "You are a supply chain analyst for Parle-G. Write a short report in simple English "
        "using ONLY the facts below. Do not invent numbers or reasons.\n"
        "Sections: 1) Summary, 2) Key decisions (routing and transfers), "
        "3) Problems and risks, 4) Recommended next actions.\n"
        "Use short bullet points. Mention any order that could not be fulfilled and why.\n\n"
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