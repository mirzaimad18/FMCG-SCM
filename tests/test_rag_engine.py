"""
Unit tests for RAG engine vector search and historical incident diagnosis.
"""

from rag_engine import (
    load_historical_incidents,
    diagnose_pipeline_issues,
    search_historical_incidents,
    generate_rag_solution,
)


def test_load_historical_incidents():
    incidents = load_historical_incidents()
    assert len(incidents) >= 10
    ids = [inc["incident_id"] for inc in incidents]
    assert "INC-001" in ids
    assert "INC-002" in ids
    assert "INC-003" in ids


def test_diagnose_pipeline_issues_unserviced_region():
    orders = [
        {"store_id": "S1", "region": "Kanpur East", "final_routing_outcome": "fulfilled directly"},
        {"store_id": "S10", "region": "Mumbai Central", "final_routing_outcome": "no warehouse for region"},
    ]
    transfers = []
    stats = {"total_orders": 2, "unfulfillable": 1}
    issues = diagnose_pipeline_issues(orders, transfers, stats)
    assert any("Mumbai Central" in i["title"] for i in issues)
    assert any(i["type"] == "unserviced_region" for i in issues)


def test_diagnose_pipeline_issues_pending_manager():
    orders = []
    transfers = [
        {"transfer_id": "TR-1", "quantity": 60000, "status": "pending_manager_approval"}
    ]
    stats = {"transfers_created": 1, "pending_manager_approval": 1}
    issues = diagnose_pipeline_issues(orders, transfers, stats)
    assert any(i["type"] == "pending_manager_approval" for i in issues)


def test_search_historical_incidents():
    # Search for an unserviced region issue
    results = search_historical_incidents("Order rejected because no warehouse serves Mumbai Central", top_k=2)
    assert len(results) >= 1
    top_inc = results[0]["incident"]
    # Should match INC-001
    assert top_inc["incident_id"] == "INC-001"
    assert results[0]["score"] > 0


def test_generate_rag_solution_template():
    results = search_historical_incidents("Warehouse stock shortage", top_k=1)
    # Test generation (either Gemini or template fallback returns valid markdown)
    sol_text, sol_src = generate_rag_solution("Stock exhausted for PG-100G", results)
    assert len(sol_text) > 50
    assert "INC-" in sol_text or "RAG" in sol_src
