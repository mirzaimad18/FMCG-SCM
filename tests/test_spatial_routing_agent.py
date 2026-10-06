"""
Unit tests for the Parle-G Spatial Routing Agent.
=================================================
Tests cover:
1. An order with an exact region match and sufficient stock
   (fulfilled_directly, stock deducted, incoming_orders_count up).
2. An order whose region has no matching warehouse at all, even by
   zone (no_warehouse_for_region, None warehouse).
3. An order where the warehouse exists but stock is insufficient
   (insufficient_stock, warehouse recorded, stock NOT deducted).
4. An order whose SKU does not exist in that warehouse's stock_per_sku
   (treated as 0, insufficient_stock).
5. Preservation of existing fields (such as priority_tier and
   priority_score from Agent 1).
6. Zone-based fallback: a region that isn't an exact regions_served
   match, but shares a zone with a warehouse, still routes correctly
   (this is the "nearest warehouse" approximation).
7. incoming_orders_count increments correctly after a direct fulfillment.

Every call passes sync_to_firestore=False, since the real agent writes
to live Firestore by default - tests must not touch the live database.
"""

import pytest
from agents.spatial_routing_agent import route_orders


@pytest.fixture
def sample_warehouses():
    """Returns a fresh sample warehouse list for test isolation."""
    return [
        {
            "warehouse_id": "WH-WEST-01",
            "zone": "West",
            "regions_served": ["West Zone A"],
            "stock_per_sku": {
                "PARLE-G-250G": 1000,
                "PARLE-G-100G": 2000,
            },
            "incoming_orders_count": 0,
            "outgoing_orders_count": 0,
        },
        {
            "warehouse_id": "WH-NORTH-01",
            "zone": "North",
            "regions_served": ["North Zone A"],
            "stock_per_sku": {
                "PARLE-G-250G": 50,
                "PARLE-G-100G": 300,
            },
            "incoming_orders_count": 0,
            "outgoing_orders_count": 0,
        },
    ]


def test_order_exact_region_sufficient_stock(sample_warehouses):
    """
    Test Case 1: Exact region match and sufficient stock.
    - Status is 'fulfilled_directly'.
    - assigned_warehouse_id is recorded.
    - quantity_requested is deducted from warehouse's stock_per_sku.
    - incoming_orders_count goes up by 1.
    """
    order = {
        "store_id": "STORE-W1",
        "region": "West Zone A",
        "sku": "PARLE-G-250G",
        "quantity_requested": 200,
    }

    initial_stock = sample_warehouses[0]["stock_per_sku"]["PARLE-G-250G"]
    assert initial_stock == 1000

    results = route_orders(
        orders=[order], warehouses=sample_warehouses, sync_to_firestore=False
    )

    routed = results[0]
    assert routed["routing_status"] == "fulfilled_directly"
    assert routed["assigned_warehouse_id"] == "WH-WEST-01"

    updated_stock = sample_warehouses[0]["stock_per_sku"]["PARLE-G-250G"]
    assert updated_stock == 800

    assert sample_warehouses[0]["incoming_orders_count"] == 1


def test_order_region_no_warehouse_at_all(sample_warehouses):
    """
    Test Case 2: Order's region doesn't match any warehouse's
    regions_served OR zone.
    - Status is 'no_warehouse_for_region'.
    - assigned_warehouse_id is None.
    - Processing stops cleanly without crashing.
    """
    order = {
        "store_id": "STORE-E1",
        "region": "East Zone A",  # No warehouse has zone "East"
        "sku": "PARLE-G-250G",
        "quantity_requested": 100,
    }

    results = route_orders(
        orders=[order], warehouses=sample_warehouses, sync_to_firestore=False
    )

    routed = results[0]
    assert routed["routing_status"] == "no_warehouse_for_region"
    assert routed["assigned_warehouse_id"] is None


def test_order_warehouse_exists_insufficient_stock(sample_warehouses):
    """
    Test Case 3: Warehouse exists, but available stock is insufficient.
    - Status is 'insufficient_stock'.
    - assigned_warehouse_id IS recorded anyway (for downstream rebalancing).
    - Stock is NOT deducted.
    - incoming_orders_count does NOT increase.
    """
    order = {
        "store_id": "STORE-N1",
        "region": "North Zone A",
        "sku": "PARLE-G-250G",
        "quantity_requested": 200,  # WH-NORTH-01 only has 50 units
    }

    results = route_orders(
        orders=[order], warehouses=sample_warehouses, sync_to_firestore=False
    )

    routed = results[0]
    assert routed["routing_status"] == "insufficient_stock"
    assert routed["assigned_warehouse_id"] == "WH-NORTH-01"

    remaining_stock = sample_warehouses[1]["stock_per_sku"]["PARLE-G-250G"]
    assert remaining_stock == 50
    assert sample_warehouses[1]["incoming_orders_count"] == 0


def test_order_sku_not_in_warehouse_stock(sample_warehouses):
    """
    Test Case 4: Warehouse exists, but requested SKU does not exist in
    stock_per_sku at all.
    - Missing SKU is treated as 0 stock.
    - Status is 'insufficient_stock'.
    - assigned_warehouse_id is recorded.
    """
    order = {
        "store_id": "STORE-W2",
        "region": "West Zone A",
        "sku": "PARLE-G-800G",  # WH-WEST-01 does not carry 800G
        "quantity_requested": 50,
    }

    assert "PARLE-G-800G" not in sample_warehouses[0]["stock_per_sku"]

    results = route_orders(
        orders=[order], warehouses=sample_warehouses, sync_to_firestore=False
    )

    routed = results[0]
    assert routed["routing_status"] == "insufficient_stock"
    assert routed["assigned_warehouse_id"] == "WH-WEST-01"


def test_preserves_order_intake_priority_fields(sample_warehouses):
    """
    Test Case 5: Verifies that prior metadata from order_intake_agent
    (priority_tier, priority_score) is preserved through spatial routing.
    """
    order_with_priority = {
        "store_id": "STORE-W1",
        "region": "West Zone A",
        "customer_type": "Government",
        "sku": "PARLE-G-250G",
        "quantity_requested": 100,
        "priority_tier": 1,
        "priority_score": 1.0,
    }

    results = route_orders(
        orders=[order_with_priority],
        warehouses=sample_warehouses,
        sync_to_firestore=False,
    )

    routed = results[0]
    assert routed["priority_tier"] == 1
    assert routed["priority_score"] == 1.0
    assert routed["routing_status"] == "fulfilled_directly"
    assert routed["assigned_warehouse_id"] == "WH-WEST-01"


def test_zone_fallback_when_no_exact_region_match(sample_warehouses):
    """
    Test Case 6: Region isn't an exact match in any warehouse's
    regions_served, but shares a zone with a warehouse. This is the
    "nearest warehouse" approximation - same city/zone counts as near
    enough, without real distance math.
    """
    order = {
        "store_id": "STORE-W3",
        "region": "West Zone B",  # not in regions_served, but zone "West" exists
        "sku": "PARLE-G-250G",
        "quantity_requested": 100,
    }

    results = route_orders(
        orders=[order], warehouses=sample_warehouses, sync_to_firestore=False
    )

    routed = results[0]
    assert routed["routing_status"] == "fulfilled_directly"
    assert routed["assigned_warehouse_id"] == "WH-WEST-01"  # matched via zone, not exact region


def test_incoming_orders_count_increments_across_multiple_orders(sample_warehouses):
    """
    Test Case 7: incoming_orders_count increases once per order
    successfully fulfilled directly from that warehouse.
    """
    orders = [
        {
            "store_id": "STORE-A",
            "region": "West Zone A",
            "sku": "PARLE-G-100G",
            "quantity_requested": 100,
        },
        {
            "store_id": "STORE-B",
            "region": "West Zone A",
            "sku": "PARLE-G-100G",
            "quantity_requested": 100,
        },
    ]

    route_orders(orders=orders, warehouses=sample_warehouses, sync_to_firestore=False)

    assert sample_warehouses[0]["incoming_orders_count"] == 2