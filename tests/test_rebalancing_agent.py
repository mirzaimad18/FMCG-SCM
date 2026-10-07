"""
Unit tests for the Parle-G Rebalancing Agent (new version).
===========================================================
Rules covered:
1. Only orders with routing_status == "insufficient_stock" are processed;
   all others pass through unchanged.
2. Shortage = quantity_requested - stock at the assigned warehouse.
3. The OTHER warehouse with the MOST stock of the SKU is chosen.
4. No surplus anywhere -> "no_surplus_available", no transfer created.
5. Transfer quantity = min(shortage, surplus available).
6. Stock is updated in both warehouses.
7. Several orders: transfer IDs go TR-0001, TR-0002, ...
8. Original order fields are kept.
9. NEW: outgoing_orders_count / incoming_orders_count go up on transfers.
10. NEW: sync_to_firestore=False never writes to Firestore.
11. End-to-end: route_orders -> rebalance_orders.

Every call uses sync_to_firestore=False so tests never touch the real database.
"""

import pytest

from agents import rebalancing_agent
from agents.rebalancing_agent import rebalance_orders
from agents.spatial_routing_agent import route_orders


@pytest.fixture
def sample_warehouses():
    """Fresh warehouses for every test (new zone / regions_served schema)."""
    return [
        {
            "warehouse_id": "WH-LKO-01",
            "zone": "Lucknow",
            "regions_served": ["Lucknow Central", "Lucknow North"],
            "stock_per_sku": {
                "PARLE-G-250G": 1000,
                "PARLE-G-100G": 3000,
            },
            "incoming_orders_count": 0,
            "outgoing_orders_count": 0,
        },
        {
            "warehouse_id": "WH-KNP-01",
            "zone": "Kanpur",
            "regions_served": ["Kanpur Central", "Kanpur East"],
            "stock_per_sku": {
                "PARLE-G-250G": 50,
                "PARLE-G-100G": 200,
            },
            "incoming_orders_count": 0,
            "outgoing_orders_count": 0,
        },
        {
            "warehouse_id": "WH-DEL-01",
            "zone": "Delhi",
            "regions_served": ["Delhi South", "Delhi West"],
            "stock_per_sku": {
                "PARLE-G-250G": 500,
                "PARLE-G-800G": 100,
            },
            "incoming_orders_count": 0,
            "outgoing_orders_count": 0,
        },
    ]


def get_wh(warehouses, warehouse_id):
    """Small helper: find a warehouse by its ID."""
    return next(w for w in warehouses if w["warehouse_id"] == warehouse_id)


def make_order(**overrides):
    """Builds an 'insufficient_stock' order for Kanpur. Override any field."""
    order = {
        "store_id": "STORE-K1",
        "sku": "PARLE-G-250G",
        "quantity_requested": 150,
        "assigned_warehouse_id": "WH-KNP-01",
        "routing_status": "insufficient_stock",
    }
    order.update(overrides)
    return order


# ---------------------------------------------------------------- Rule 1
def test_rule_1_unaffected_orders_passed_through_unchanged(sample_warehouses):
    orders = [
        {
            "store_id": "STORE-01",
            "routing_status": "fulfilled_directly",
            "assigned_warehouse_id": "WH-LKO-01",
            "sku": "PARLE-G-250G",
            "quantity_requested": 100,
        },
        {
            "store_id": "STORE-02",
            "routing_status": "no_warehouse_for_region",
            "assigned_warehouse_id": None,
            "sku": "PARLE-G-250G",
            "quantity_requested": 100,
        },
    ]

    result = rebalance_orders(orders, sample_warehouses, sync_to_firestore=False)

    assert len(result.orders) == 2
    assert len(result.transfers) == 0
    for order in result.orders:
        assert "rebalancing_status" not in order
        assert "transfer_id" not in order


# ------------------------------------------------------------ Rule 2 + 5
def test_rule_2_and_5_shortage_and_transfer_creation(sample_warehouses):
    """KNP has 50, needs 150 -> shortage 100. LKO (1000) sends 100."""
    result = rebalance_orders(
        [make_order()], sample_warehouses, sync_to_firestore=False
    )

    assert len(result.orders) == 1
    assert len(result.transfers) == 1

    order = result.orders[0]
    assert order["rebalancing_status"] == "transfer_created"
    assert order["transfer_id"] == "TR-0001"

    transfer = result.transfers[0]
    assert transfer["transfer_id"] == "TR-0001"
    assert transfer["from_warehouse"] == "WH-LKO-01"
    assert transfer["to_warehouse"] == "WH-KNP-01"
    assert transfer["sku"] == "PARLE-G-250G"
    assert transfer["quantity"] == 100
    assert transfer["status"] == "created"


# ------------------------------------------------------------ Rule 3
def test_rule_3_selects_warehouse_with_most_stock(sample_warehouses):
    """LKO has 1000 and DEL has 500 of 250G. LKO must be chosen."""
    result = rebalance_orders(
        [make_order(quantity_requested=200)],
        sample_warehouses,
        sync_to_firestore=False,
    )

    assert len(result.transfers) == 1
    assert result.transfers[0]["from_warehouse"] == "WH-LKO-01"


# ------------------------------------------------------------ Rule 4
def test_rule_4_no_surplus_available(sample_warehouses):
    """No other warehouse has any 800G -> no transfer."""
    get_wh(sample_warehouses, "WH-DEL-01")["stock_per_sku"]["PARLE-G-800G"] = 0
    order = make_order(sku="PARLE-G-800G", quantity_requested=50)

    result = rebalance_orders([order], sample_warehouses, sync_to_firestore=False)

    assert len(result.transfers) == 0
    assert result.orders[0]["rebalancing_status"] == "no_surplus_available"
    assert "transfer_id" not in result.orders[0]


def test_unknown_assigned_warehouse_is_marked_no_surplus(sample_warehouses):
    """If the assigned warehouse ID does not exist, nothing crashes."""
    order = make_order(assigned_warehouse_id="WH-DOES-NOT-EXIST")

    result = rebalance_orders([order], sample_warehouses, sync_to_firestore=False)

    assert len(result.transfers) == 0
    assert result.orders[0]["rebalancing_status"] == "no_surplus_available"


# ------------------------------------------------------------ Rule 5
def test_rule_5_partial_surplus_capped_at_available_stock(sample_warehouses):
    """KNP has no 800G, needs 150. Only DEL has 800G (100). Transfer 100."""
    order = make_order(sku="PARLE-G-800G", quantity_requested=150)

    result = rebalance_orders([order], sample_warehouses, sync_to_firestore=False)

    assert len(result.transfers) == 1
    transfer = result.transfers[0]
    assert transfer["quantity"] == 100
    assert transfer["from_warehouse"] == "WH-DEL-01"
    assert transfer["to_warehouse"] == "WH-KNP-01"


# ------------------------------------------------------------ Rule 6
def test_rule_6_inventory_levels_updated(sample_warehouses):
    lko = get_wh(sample_warehouses, "WH-LKO-01")
    knp = get_wh(sample_warehouses, "WH-KNP-01")
    assert lko["stock_per_sku"]["PARLE-G-250G"] == 1000
    assert knp["stock_per_sku"]["PARLE-G-250G"] == 50

    rebalance_orders([make_order()], sample_warehouses, sync_to_firestore=False)

    assert lko["stock_per_sku"]["PARLE-G-250G"] == 900   # 1000 - 100
    assert knp["stock_per_sku"]["PARLE-G-250G"] == 150   # 50 + 100


# ------------------------------------------------------------ Rule 7
def test_sequential_orders_update_stock_and_increment_ids(sample_warehouses):
    orders = [
        # KNP has 50, needs 100 -> 50 comes from LKO (1000 -> 950)
        make_order(store_id="STORE-01", quantity_requested=100),
        # DEL has no 100G, needs 500 -> 500 comes from LKO (3000 -> 2500)
        make_order(
            store_id="STORE-02",
            sku="PARLE-G-100G",
            quantity_requested=500,
            assigned_warehouse_id="WH-DEL-01",
        ),
    ]

    result = rebalance_orders(orders, sample_warehouses, sync_to_firestore=False)

    assert len(result.transfers) == 2
    assert result.transfers[0]["transfer_id"] == "TR-0001"
    assert result.transfers[1]["transfer_id"] == "TR-0002"
    assert result.orders[0]["transfer_id"] == "TR-0001"
    assert result.orders[1]["transfer_id"] == "TR-0002"

    lko = get_wh(sample_warehouses, "WH-LKO-01")
    assert lko["stock_per_sku"]["PARLE-G-250G"] == 950
    assert lko["stock_per_sku"]["PARLE-G-100G"] == 2500


# ------------------------------------------------------------ Rule 8
def test_preserves_order_metadata(sample_warehouses):
    order = make_order(
        store_id="STORE-GOV",
        region="Kanpur Central",
        customer_type="Government",
        quantity_requested=100,
        priority_tier=1,
        priority_score=1.0,
    )

    result = rebalance_orders([order], sample_warehouses, sync_to_firestore=False)
    updated = result.orders[0]

    assert updated["store_id"] == "STORE-GOV"
    assert updated["region"] == "Kanpur Central"
    assert updated["customer_type"] == "Government"
    assert updated["priority_tier"] == 1
    assert updated["priority_score"] == 1.0
    assert updated["routing_status"] == "insufficient_stock"
    assert updated["rebalancing_status"] == "transfer_created"


# ------------------------------------------------- Rule 9: order counts
def test_order_counts_increase_on_transfer(sample_warehouses):
    """Sender gets outgoing +1. Receiver gets incoming +1."""
    rebalance_orders([make_order()], sample_warehouses, sync_to_firestore=False)

    lko = get_wh(sample_warehouses, "WH-LKO-01")   # sender
    knp = get_wh(sample_warehouses, "WH-KNP-01")   # receiver

    assert lko["outgoing_orders_count"] == 1
    assert lko["incoming_orders_count"] == 0
    assert knp["incoming_orders_count"] == 1
    assert knp["outgoing_orders_count"] == 0


def test_order_counts_add_up_across_multiple_transfers(sample_warehouses):
    orders = [
        make_order(store_id="STORE-01", quantity_requested=100),
        make_order(store_id="STORE-02", quantity_requested=120),
    ]

    rebalance_orders(orders, sample_warehouses, sync_to_firestore=False)

    # Second order: KNP now has 100 after the first transfer, so it still
    # needs 20 more, and LKO is again the warehouse with the most stock.
    assert get_wh(sample_warehouses, "WH-LKO-01")["outgoing_orders_count"] == 2
    assert get_wh(sample_warehouses, "WH-KNP-01")["incoming_orders_count"] == 2


def test_order_counts_unchanged_when_no_transfer(sample_warehouses):
    get_wh(sample_warehouses, "WH-DEL-01")["stock_per_sku"]["PARLE-G-800G"] = 0
    order = make_order(sku="PARLE-G-800G", quantity_requested=50)

    rebalance_orders([order], sample_warehouses, sync_to_firestore=False)

    for wh in sample_warehouses:
        assert wh["incoming_orders_count"] == 0
        assert wh["outgoing_orders_count"] == 0


# ------------------------------------------- Rule 10: no Firestore writes
def test_sync_false_does_not_write_to_firestore(sample_warehouses, monkeypatch):
    """With sync_to_firestore=False, the write functions must never run."""
    calls = []
    monkeypatch.setattr(
        rebalancing_agent, "write_warehouse_to_firestore",
        lambda *a, **k: calls.append("warehouse"),
    )
    monkeypatch.setattr(
        rebalancing_agent, "write_transfer_to_firestore",
        lambda *a, **k: calls.append("transfer"),
    )

    rebalance_orders([make_order()], sample_warehouses, sync_to_firestore=False)

    assert calls == []


def test_sync_true_writes_two_warehouses_and_one_transfer(
    sample_warehouses, monkeypatch
):
    """With sync_to_firestore=True, writes happen (faked here, not real)."""
    calls = []
    monkeypatch.setattr(
        rebalancing_agent, "write_warehouse_to_firestore",
        lambda *a, **k: calls.append("warehouse"),
    )
    monkeypatch.setattr(
        rebalancing_agent, "write_transfer_to_firestore",
        lambda *a, **k: calls.append("transfer"),
    )

    rebalance_orders([make_order()], sample_warehouses, sync_to_firestore=True)

    assert calls.count("warehouse") == 2
    assert calls.count("transfer") == 1


# ------------------------------------------------- Rule 11: end to end
def test_end_to_end_pipeline_integration(sample_warehouses):
    """Spatial Routing output goes straight into Rebalancing."""
    raw_orders = [
        {
            "store_id": "STORE-LKO",
            "region": "Lucknow Central",
            "sku": "PARLE-G-250G",
            "quantity_requested": 200,   # LKO has 1000 -> fulfilled directly
        },
        {
            "store_id": "STORE-KNP",
            "region": "Kanpur Central",
            "sku": "PARLE-G-250G",
            "quantity_requested": 200,   # KNP has 50 -> insufficient stock
        },
    ]

    routed = route_orders(raw_orders, sample_warehouses, sync_to_firestore=False)
    assert routed[0]["routing_status"] == "fulfilled_directly"
    assert routed[1]["routing_status"] == "insufficient_stock"

    result = rebalance_orders(routed, sample_warehouses, sync_to_firestore=False)

    assert "rebalancing_status" not in result.orders[0]
    assert result.orders[1]["rebalancing_status"] == "transfer_created"
    assert len(result.transfers) == 1
    assert result.transfers[0]["sku"] == "PARLE-G-250G"
    assert result.transfers[0]["to_warehouse"] == "WH-KNP-01"
def test_no_zero_quantity_transfer_when_earlier_transfer_covered_shortage(sample_warehouses):
       orders = [
           make_order(store_id="STORE-01", quantity_requested=150),
           make_order(store_id="STORE-02", quantity_requested=150),
       ]

       result = rebalance_orders(orders, sample_warehouses, sync_to_firestore=False)

       assert len(result.transfers) == 1
       assert result.transfers[0]["quantity"] > 0
       assert "transfer_id" not in result.orders[1]
       assert result.orders[1]["routing_status"] == "fulfilled_directly"
       assert get_wh(sample_warehouses, "WH-KNP-01")["stock_per_sku"]["PARLE-G-250G"] == 0