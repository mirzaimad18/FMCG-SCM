"""
Unit tests for reporting and AI check auditing functions.
"""

from reporting import (
    build_facts,
    validate_pipeline,
    _template_report,
    _template_orders_check,
    _template_transfers_check,
    _template_warehouses_check,
)
from pipeline import PipelineResult


def test_template_orders_check():
    orders = [
        {
            "store_id": "S1",
            "customer_type": "Government",
            "priority_tier": 1,
            "region": "Kanpur East",
            "sku": "PG-100G",
            "quantity_requested": 500,
            "assigned_warehouse": "WH-KNP-01",
            "final_routing_outcome": "fulfilled directly",
        },
        {
            "store_id": "S10",
            "customer_type": "Regular",
            "priority_tier": 4,
            "region": "Mumbai Central",
            "sku": "PG-100G",
            "quantity_requested": 1000,
            "assigned_warehouse": None,
            "final_routing_outcome": "no warehouse for region",
        },
    ]
    report = _template_orders_check(orders, {"total_orders": 2})
    assert "Total Orders Evaluated" in report
    assert "Tier 1" in report
    assert "no warehouse for region" in report


def test_template_transfers_check():
    transfers = [
        {
            "transfer_id": "TR-1",
            "from_warehouse": "WH-KNP-01",
            "to_warehouse": "WH-KNP-02",
            "sku": "PG-100G",
            "quantity": 60000,
            "status": "pending_manager_approval",
        },
        {
            "transfer_id": "TR-2",
            "from_warehouse": "WH-KNP-02",
            "to_warehouse": "WH-KNP-01",
            "sku": "PG-200G",
            "quantity": 10000,
            "status": "auto_approved",
        },
    ]
    report = _template_transfers_check(transfers)
    assert "Total Transfer Requests" in report
    assert "60,000" in report
    assert "PENDING MANAGER" in report
    assert "AUTO-APPROVED" in report


def test_template_warehouses_check():
    warehouses = [
        {
            "warehouse_id": "WH-KNP-01",
            "zone": "Kanpur",
            "stock_per_sku": {"PG-100G": 10000, "PG-200G": 5000},
            "incoming_orders_count": 2,
            "outgoing_orders_count": 1,
        }
    ]
    before = [
        {
            "warehouse_id": "WH-KNP-01",
            "zone": "Kanpur",
            "stock_per_sku": {"PG-100G": 12000, "PG-200G": 5000},
            "incoming_orders_count": 0,
            "outgoing_orders_count": 0,
        }
    ]
    report = _template_warehouses_check(warehouses, before=before)
    assert "Active Distribution Centers" in report
    assert "WH-KNP-01" in report
    assert "Decreased by 2,000 units" in report
