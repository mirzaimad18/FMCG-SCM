"""
Unit tests for the FMCG Supply Chain Guardrail Agent.
===========================================
Tests cover:
1. Transfer just under 50,000 units (e.g., 49,999) -> auto_approved.
2. Transfer just over 50,000 units (e.g., 50,001) -> pending_manager_approval.
3. Transfer of EXACTLY 50,000 units -> pending_manager_approval (strict boundary check).
4. approve_transfer() successfully changing pending_manager_approval to manager_approved.
5. approve_transfer() handling non-existent transfer_id (returns False).
6. approve_transfer() rejecting approval on already auto-approved transfer.
7. Handling empty transfer lists.
8. End-to-end integration: Output from Rebalancing Agent evaluated by Guardrail Agent.
"""

import pytest
from agents.guardrail_agent import (
    evaluate_transfers,
    approve_transfer,
    main,
    run_local_test,
    STATUS_AUTO_APPROVED,
    STATUS_PENDING_APPROVAL,
    STATUS_MANAGER_APPROVED,
)
from agents.rebalancing_agent import rebalance_orders


def test_transfer_just_under_threshold_auto_approved():
    """
    Test Case 1: Transfer with quantity 49,999 (< 50,000).
    Must be 'auto_approved' immediately without human intervention.
    """
    transfers = [
        {
            "transfer_id": "TR-0001",
            "from_warehouse": "WH-WEST-01",
            "to_warehouse": "WH-NORTH-01",
            "sku": "FMCG-250G",
            "quantity": 49999,
            "status": "created",
        }
    ]

    result = evaluate_transfers(transfers, sync_to_firestore=False)

    assert len(result) == 1
    assert result[0]["status"] == STATUS_AUTO_APPROVED


def test_transfer_just_over_threshold_pending_approval():
    """
    Test Case 2: Transfer with quantity 50,001 (> 50,000).
    Must be set to 'pending_manager_approval'.
    """
    transfers = [
        {
            "transfer_id": "TR-0002",
            "from_warehouse": "WH-WEST-01",
            "to_warehouse": "WH-NORTH-01",
            "sku": "FMCG-250G",
            "quantity": 50001,
            "status": "created",
        }
    ]

    result = evaluate_transfers(transfers, sync_to_firestore=False)

    assert len(result) == 1
    assert result[0]["status"] == STATUS_PENDING_APPROVAL


def test_transfer_exactly_at_threshold_pending_approval():
    """
    Test Case 3: Transfer with quantity EXACTLY 50,000 units.
    Critical boundary check: 50,000 itself requires manager approval, NOT auto-approved.
    """
    transfers = [
        {
            "transfer_id": "TR-0003",
            "from_warehouse": "WH-WEST-01",
            "to_warehouse": "WH-NORTH-01",
            "sku": "FMCG-250G",
            "quantity": 50000,
            "status": "created",
        }
    ]

    result = evaluate_transfers(transfers, sync_to_firestore=False)

    assert len(result) == 1
    assert result[0]["status"] == STATUS_PENDING_APPROVAL


def test_approve_transfer_changes_status_to_manager_approved():
    """
    Test Case 4: approve_transfer function correctly transitions
    a 'pending_manager_approval' transfer to 'manager_approved'.
    """
    transfers = [
        {
            "transfer_id": "TR-0004",
            "from_warehouse": "WH-WEST-01",
            "to_warehouse": "WH-NORTH-01",
            "sku": "FMCG-800G",
            "quantity": 60000,
            "status": STATUS_PENDING_APPROVAL,
        }
    ]

    success = approve_transfer("TR-0004", transfers, sync_to_firestore=False)

    assert success is True
    assert transfers[0]["status"] == STATUS_MANAGER_APPROVED


def test_approve_transfer_non_existent_id():
    """
    Test Case 5: Calling approve_transfer on an ID that does not exist
    returns False and does not alter the list.
    """
    transfers = [
        {
            "transfer_id": "TR-0001",
            "quantity": 60000,
            "status": STATUS_PENDING_APPROVAL,
        }
    ]

    success = approve_transfer("TR-9999", transfers, sync_to_firestore=False)

    assert success is False
    assert transfers[0]["status"] == STATUS_PENDING_APPROVAL


def test_approve_transfer_already_auto_approved():
    """
    Test Case 6: Attempting to call approve_transfer on a transfer
    that is already 'auto_approved' should return False and remain 'auto_approved'.
    """
    transfers = [
        {
            "transfer_id": "TR-0001",
            "quantity": 1000,
            "status": STATUS_AUTO_APPROVED,
        }
    ]

    success = approve_transfer("TR-0001", transfers, sync_to_firestore=False)

    assert success is False
    assert transfers[0]["status"] == STATUS_AUTO_APPROVED


def test_empty_transfers_list():
    """
    Test Case 7: Evaluates behavior when an empty list is passed.
    """
    result = evaluate_transfers([], sync_to_firestore=False)
    assert result == []


def test_end_to_end_rebalancing_to_guardrail_integration():
    """
    Test Case 8: Full integration test from Rebalancing Agent -> Guardrail Agent.
    """
    # Warehouses with large surplus
    warehouses = [
        {
            "warehouse_id": "WH-WEST-01",
            "region_served": "West",
            "stock_per_sku": {
                "FMCG-250G": 100000,
                "FMCG-100G": 20000,
            },
        },
        {
            "warehouse_id": "WH-NORTH-01",
            "region_served": "North",
            "stock_per_sku": {
                "FMCG-250G": 0,
                "FMCG-100G": 0,
            },
        },
    ]

    # Two shortage orders: one small (1,000), one large (60,000)
    orders = [
        {
            "store_id": "STORE-01",
            "routing_status": "insufficient_stock",
            "assigned_warehouse_id": "WH-NORTH-01",
            "sku": "FMCG-100G",
            "quantity_requested": 1000,
        },
        {
            "store_id": "STORE-02",
            "routing_status": "insufficient_stock",
            "assigned_warehouse_id": "WH-NORTH-01",
            "sku": "FMCG-250G",
            "quantity_requested": 60000,
        },
    ]

    # Step 1: Rebalance inventory
    _, transfers = rebalance_orders(
        orders=orders,
        warehouses=warehouses,
        sync_to_firestore=False,
    )
    assert len(transfers) == 2

    # Step 2: Apply guardrails
    evaluated = evaluate_transfers(transfers, sync_to_firestore=False)
    assert len(evaluated) == 2

    # Small transfer should be auto_approved
    assert evaluated[0]["transfer_id"] == "TR-0001"
    assert evaluated[0]["quantity"] == 1000
    assert evaluated[0]["status"] == STATUS_AUTO_APPROVED

    # Large transfer (60,000 >= 50,000) should be pending_manager_approval
    assert evaluated[1]["transfer_id"] == "TR-0002"
    assert evaluated[1]["quantity"] == 60000
    assert evaluated[1]["status"] == STATUS_PENDING_APPROVAL

    # Manager approves the large transfer
    approved = approve_transfer("TR-0002", evaluated, sync_to_firestore=False)
    assert approved is True
    assert evaluated[1]["status"] == STATUS_MANAGER_APPROVED


def test_local_entrypoint_evaluates_and_approves_entered_transfer(monkeypatch, capsys):
    answers = iter(["LOCAL-TRANSFER-1", "50000", "y"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    result = run_local_test()

    assert result[0]["status"] == STATUS_MANAGER_APPROVED
    assert "no Firestore writes" in capsys.readouterr().out


def test_main_runs_threshold_boundary_checks_before_manual_test(monkeypatch, capsys):
    manual_test_called = False

    def fake_manual_test():
        nonlocal manual_test_called
        manual_test_called = True

    monkeypatch.setattr("agents.guardrail_agent.run_local_test", fake_manual_test)

    main()

    output = capsys.readouterr().out
    assert "approval limit: 50,000 units" in output
    assert (
        "Quantity:  49,999 units | Limit: 50,000 | "
        "Expected: auto_approved | Actual: auto_approved"
    ) in output
    assert (
        "Quantity:  50,000 units | Limit: 50,000 | "
        "Expected: pending_manager_approval | Actual: pending_manager_approval"
    ) in output
    assert (
        "Quantity:  50,001 units | Limit: 50,000 | "
        "Expected: pending_manager_approval | Actual: pending_manager_approval"
    ) in output
    assert manual_test_called


def test_failed_firestore_approval_does_not_claim_success(monkeypatch):
    transfers = [
        {
            "transfer_id": "TR-0005",
            "quantity": 60000,
            "status": STATUS_PENDING_APPROVAL,
        }
    ]

    def fail_update(transfer_id, status, database="velochaindb001"):
        raise RuntimeError(
            f"Firestore unavailable for {transfer_id}: {status} in {database}"
        )

    monkeypatch.setattr(
        "agents.guardrail_agent.update_transfer_status_in_firestore",
        fail_update,
    )

    with pytest.raises(RuntimeError, match="Firestore unavailable"):
        approve_transfer("TR-0005", transfers)

    assert transfers[0]["status"] == STATUS_PENDING_APPROVAL
