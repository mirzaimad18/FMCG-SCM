"""
Spatial Routing Agent for Parle-G Supply Chain Engine.
=======================================================
Agent #2 in the Agentic AI Supply Chain Engine. Connects to the live
Firestore database "velochaindb001" in project velochain-509610.

Matches each order to the warehouse that should fulfill it, and checks
stock availability.

Matching rules (no GPS, no distance math - deliberately simple):
  1. Try to find a warehouse whose "regions_served" list contains the
     order's exact region (e.g. "Kanpur East").
  2. If no exact match, fall back to any warehouse whose "zone" matches
     the order's zone - the city part of the region, e.g. "Kanpur East"
     -> zone "Kanpur". This is our stand-in for "nearest warehouse":
     same city counts as near enough, without real distance math.
  3. If still no match, the order is flagged "no_warehouse_for_region".

Once a warehouse is found:
  - Enough stock -> routing_status = "fulfilled_directly", stock is
    deducted, and the warehouse's incoming_orders_count goes up by 1.
  - Not enough stock -> routing_status = "insufficient_stock" (handled
    next by the Rebalancing Agent).

Every routed order is stamped with "assigned_warehouse_id".
"""

import logging
from typing import Any, Dict, List, Optional

from google.cloud import firestore

logger = logging.getLogger("spatial_routing_agent")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

FIRESTORE_DATABASE = "velochaindb001"


def load_warehouses(database: str = FIRESTORE_DATABASE) -> List[Dict[str, Any]]:
    """Loads all warehouse documents from Firestore into a plain list."""
    logger.info(f"Connecting to Firestore database '{database}' (collection: 'warehouses')...")
    db = firestore.Client(database=database)
    docs = db.collection("warehouses").stream()
    warehouses: List[Dict[str, Any]] = []
    for doc in docs:
        data = doc.to_dict()
        data["warehouse_id"] = doc.id
        warehouses.append(data)
    logger.info(f"Successfully loaded {len(warehouses)} warehouses from Firestore.")
    return warehouses


def extract_zone(region: str) -> str:
    """
    Turns a specific sub-region into its broader zone.
    Example: "Kanpur East" -> "Kanpur".
    Assumes the format is "<Zone> <Direction>". If there's no space,
    the whole region is treated as the zone.
    """
    if not region:
        return region
    parts = region.rsplit(" ", 1)
    if len(parts) == 2:
        return parts[0]
    return region


def find_warehouse_for_region(
    region: str, warehouses: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    Step 1: exact sub-region match (regions_served).
    Step 2: same-zone fallback (our "nearest warehouse" approximation).
    Returns the matching warehouse dict, or None if nothing matches.
    """
    for warehouse in warehouses:
        if region in warehouse.get("regions_served", []):
            return warehouse

    zone = extract_zone(region)
    for warehouse in warehouses:
        if warehouse.get("zone") == zone:
            return warehouse

    return None


def write_warehouse_to_firestore(
    warehouse: Dict[str, Any], database: str = FIRESTORE_DATABASE
) -> None:
    try:
        db = firestore.Client(database=database)
        db.collection("warehouses").document(warehouse["warehouse_id"]).set(warehouse)
    except Exception as e:
        logger.warning(
            f"Could not update warehouse '{warehouse.get('warehouse_id')}' in Firestore: {e}"
        )


def route_single_order(
    order: Dict[str, Any],
    warehouses: List[Dict[str, Any]],
    sync_to_firestore: bool,
) -> Dict[str, Any]:
    """Applies the routing rules to one order, updating it and the matched warehouse in place."""
    region = order.get("region")
    warehouse = find_warehouse_for_region(region, warehouses)

    if warehouse is None:
        order["routing_status"] = "no_warehouse_for_region"
        order["assigned_warehouse_id"] = None
        logger.warning(
            f"No warehouse found for region '{region}' (store {order.get('store_id')})."
        )
        return order

    order["assigned_warehouse_id"] = warehouse["warehouse_id"]

    sku = order.get("sku")
    requested_qty = order.get("quantity_requested", 0)
    available_stock = warehouse.get("stock_per_sku", {}).get(sku, 0)

    if available_stock >= requested_qty:
        warehouse["stock_per_sku"][sku] = available_stock - requested_qty
        warehouse["incoming_orders_count"] = warehouse.get("incoming_orders_count", 0) + 1
        order["routing_status"] = "fulfilled_directly"
    else:
        order["routing_status"] = "insufficient_stock"

    if sync_to_firestore:
        write_warehouse_to_firestore(warehouse)

    return order


def route_orders(
    orders: List[Dict[str, Any]],
    warehouses: List[Dict[str, Any]],
    sync_to_firestore: bool = True,
) -> List[Dict[str, Any]]:
    """
    Routes every order, ONE AT A TIME in the given list order, so
    earlier orders claim stock before later ones (this is why
    pipeline.py sorts orders by priority BEFORE calling this).
    Returns the same list with routing_status and assigned_warehouse_id
    set on each order.
    """
    for order in orders:
        route_single_order(order, warehouses, sync_to_firestore)
    return orders


if __name__ == "__main__":
    demo_warehouses = [
        {
            "warehouse_id": "WH-KNP-01",
            "zone": "Kanpur",
            "regions_served": ["Kanpur East", "Kanpur North"],
            "stock_per_sku": {"PG-100G": 100},
            "incoming_orders_count": 0,
            "outgoing_orders_count": 0,
        }
    ]
    demo_orders = [
        {
            "store_id": "DEMO1",
            "region": "Kanpur Central",  # not exact -> zone fallback
            "sku": "PG-100G",
            "quantity_requested": 50,
        }
    ]
    print(route_orders(demo_orders, demo_warehouses, sync_to_firestore=False))