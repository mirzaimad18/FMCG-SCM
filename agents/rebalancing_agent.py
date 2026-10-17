"""
Rebalancing Agent for the FMCG Supply Chain Management System.
===================================================
Agent #3 in the Agentic AI Supply Chain Engine. Connects to the live
Firestore database "velochaindb001" in project velochain-509610.

Handles orders the Spatial Routing Agent marked "insufficient_stock".
Finds a warehouse with surplus of the needed SKU and creates a
transfer request. Also tracks each warehouse's outgoing/incoming order
counts so storage activity can be monitored.

Rules (deterministic, no AI model needed):
  1. Only orders with routing_status == "insufficient_stock" are touched.
  2. Shortage = quantity_requested - stock currently at the assigned
     warehouse.
  3. Look at every OTHER warehouse for surplus of that SKU. Pick the
     one with the MOST stock of it.
  4. If no warehouse has any stock of that SKU at all, mark the order
     "no_surplus_available" - a known, accepted edge case.
  5. If found, transfer the smaller of (shortage, surplus available),
     update both warehouses' stock and order counts, and record the
     transfer. The Guardrail Agent (Agent #4) decides afterward whether
     each transfer needs manager approval.
"""

import logging
from typing import Any, Dict, List, NamedTuple, Optional

from google.cloud import firestore

logger = logging.getLogger("rebalancing_agent")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

FIRESTORE_DATABASE = "velochaindb001"


class RebalanceResult(NamedTuple):
    """
    Supports both tuple unpacking (orders, transfers = rebalance_orders(...))
    and attribute access (result.orders, result.transfers).
    """
    orders: List[Dict[str, Any]]
    transfers: List[Dict[str, Any]]


def find_surplus_warehouse(
    sku: str, assigned_warehouse_id: str, warehouses: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Returns the warehouse (other than the assigned one) with the most stock of sku, or None."""
    best_warehouse = None
    best_stock = 0
    for warehouse in warehouses:
        if warehouse["warehouse_id"] == assigned_warehouse_id:
            continue
        stock = warehouse.get("stock_per_sku", {}).get(sku, 0)
        if stock > best_stock:
            best_stock = stock
            best_warehouse = warehouse
    return best_warehouse


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


def write_transfer_to_firestore(
    transfer: Dict[str, Any], database: str = FIRESTORE_DATABASE
) -> None:
    try:
        db = firestore.Client(database=database)
        db.collection("transfers").document(transfer["transfer_id"]).set(transfer)
    except Exception as e:
        logger.warning(
            f"Could not write transfer '{transfer.get('transfer_id')}' to Firestore: {e}"
        )


def rebalance_orders(
    orders: List[Dict[str, Any]],
    warehouses: List[Dict[str, Any]],
    sync_to_firestore: bool = True,
) -> RebalanceResult:
    """
    Applies rebalancing logic to every order flagged "insufficient_stock".
    Returns a RebalanceResult(orders=..., transfers=...).
    """
    transfers: List[Dict[str, Any]] = []
    counter = 1

    for order in orders:
        if order.get("routing_status") != "insufficient_stock":
            continue

        assigned_id = order.get("assigned_warehouse_id")
        sku = order.get("sku")

        assigned_warehouse = next(
            (w for w in warehouses if w["warehouse_id"] == assigned_id), None
        )
        if assigned_warehouse is None:
            order["rebalancing_status"] = "no_surplus_available"
            continue

        current_stock = assigned_warehouse.get("stock_per_sku", {}).get(sku, 0)
        shortage = order.get("quantity_requested", 0) - current_stock
        if shortage <= 0:
            # An earlier transfer in this run already topped up this warehouse,
            # so this order can now be filled from its own stock.
            assigned_warehouse["stock_per_sku"][sku] = current_stock - order.get("quantity_requested", 0)
            assigned_warehouse["incoming_orders_count"] = (
                assigned_warehouse.get("incoming_orders_count", 0) + 1
            )
            order["routing_status"] = "fulfilled_directly"
            if sync_to_firestore:
                write_warehouse_to_firestore(assigned_warehouse)
            continue
        surplus_warehouse = find_surplus_warehouse(sku, assigned_id, warehouses)

        if surplus_warehouse is None:
            order["rebalancing_status"] = "no_surplus_available"
            logger.warning(
                f"No surplus warehouse found for SKU '{sku}' (order {order.get('store_id')})."
            )
            continue

        surplus_available = surplus_warehouse["stock_per_sku"][sku]
        transfer_qty = min(shortage, surplus_available)

        # Move the stock
        surplus_warehouse["stock_per_sku"][sku] = surplus_available - transfer_qty
        assigned_warehouse["stock_per_sku"][sku] = current_stock + transfer_qty

        # Track incoming/outgoing order activity for both warehouses
        surplus_warehouse["outgoing_orders_count"] = (
            surplus_warehouse.get("outgoing_orders_count", 0) + 1
        )
        assigned_warehouse["incoming_orders_count"] = (
            assigned_warehouse.get("incoming_orders_count", 0) + 1
        )

        transfer_id = f"TR-{counter:04d}"
        counter += 1

        transfer = {
            "transfer_id": transfer_id,
            "from_warehouse": surplus_warehouse["warehouse_id"],
            "to_warehouse": assigned_id,
            "sku": sku,
            "quantity": transfer_qty,
            "status": "created",
        }

        order["rebalancing_status"] = "transfer_created"
        order["transfer_id"] = transfer_id
        transfers.append(transfer)

        if sync_to_firestore:
            write_warehouse_to_firestore(surplus_warehouse)
            write_warehouse_to_firestore(assigned_warehouse)
            write_transfer_to_firestore(transfer)

    return RebalanceResult(orders=orders, transfers=transfers)


if __name__ == "__main__":
    demo_warehouses = [
        {
            "warehouse_id": "WH-A",
            "stock_per_sku": {"PG-100G": 0},
            "incoming_orders_count": 0,
            "outgoing_orders_count": 0,
        },
        {
            "warehouse_id": "WH-B",
            "stock_per_sku": {"PG-100G": 500},
            "incoming_orders_count": 0,
            "outgoing_orders_count": 0,
        },
    ]
    demo_orders = [
        {
            "store_id": "DEMO1",
            "sku": "PG-100G",
            "quantity_requested": 200,
            "assigned_warehouse_id": "WH-A",
            "routing_status": "insufficient_stock",
        }
    ]
    result = rebalance_orders(demo_orders, demo_warehouses, sync_to_firestore=False)
    print(result.orders)
    print(result.transfers)