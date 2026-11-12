"""Data-integrity and generated-decision evaluation."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


CENT = Decimal("0.01")
ROOT = Path(__file__).resolve().parents[1]
PRIMARY_KEYS = {
    "vendor": "vendor_id",
    "purchase_order": "po_id",
    "purchase_order_line": "po_line_id",
    "goods_receipt": "grn_id",
    "goods_receipt_line": "grn_line_id",
    "invoice": "invoice_id",
    "invoice_line": "invoice_line_id",
    "approval": "approval_id",
    "payment": "payment_id",
    "invoice_match_result": "invoice_id",
}
ALLOWED_TRANSITIONS = {
    ("received", "extracted"),
    ("extracted", "matched"),
    ("matched", "exception"),
    ("matched", "blocked"),
    ("matched", "approval_pending"),
    ("approval_pending", "approved"),
    ("approved", "payment_ready"),
}


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def validate_database(
    connection: sqlite3.Connection,
    ground_truth: list[dict[str, Any]],
    expected_counts: dict[str, int],
) -> dict[str, Any]:
    """Validate constraints and compare the engine with independent labels."""
    rules = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8"))
    approval_limit = Decimal(str(rules["approval"]["level1_limit_inr"]))
    payment_delay = int(rules["approval"]["payment_delay_days"])
    errors: list[str] = []
    truth_by_invoice = {row["invoice_id"]: row for row in ground_truth}
    invoice_rows = connection.execute("SELECT * FROM invoice").fetchall()
    invoice_ids = {row["invoice_id"] for row in invoice_rows}
    if len(truth_by_invoice) != len(ground_truth):
        errors.append("Ground-truth invoice IDs are not unique.")
    if set(truth_by_invoice) != invoice_ids:
        errors.append("Ground truth does not contain exactly one row per invoice.")

    for table, expected in expected_counts.items():
        actual = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if actual != expected:
            errors.append(f"{table} row count is {actual}; expected {expected}.")

    for table, primary_key in PRIMARY_KEYS.items():
        row = connection.execute(
            f"SELECT COUNT(*) AS n, COUNT(DISTINCT {primary_key}) AS distinct_n FROM {table}"
        ).fetchone()
        if row["n"] != row["distinct_n"]:
            errors.append(f"{table}.{primary_key} is not unique.")
    fk_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        errors.append(f"Foreign-key check found {len(fk_errors)} violation(s).")
    if "ground_truth" in {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }:
        errors.append("Ground truth leaked into the input database.")

    for row in connection.execute(
        "WITH line_receipts AS ("
        "SELECT pl.po_id,pl.quantity,"
        "COALESCE(SUM(CASE WHEN gr.status='posted' THEN grl.quantity_received ELSE 0 END),0) "
        "AS received FROM purchase_order_line pl "
        "LEFT JOIN goods_receipt_line grl ON grl.po_line_id=pl.po_line_id "
        "LEFT JOIN goods_receipt gr ON gr.grn_id=grl.grn_id "
        "GROUP BY pl.po_line_id) "
        "SELECT po.po_id,po.status,SUM(line_receipts.quantity) AS ordered,"
        "SUM(line_receipts.received) AS received,"
        "SUM(CASE WHEN line_receipts.received < line_receipts.quantity THEN 1 ELSE 0 END) "
        "AS unreceived_lines "
        "FROM purchase_order po JOIN line_receipts ON line_receipts.po_id=po.po_id "
        "GROUP BY po.po_id,po.status"
    ):
        received = int(row["received"])
        status = row["status"]
        if status == "open" and received != 0:
            errors.append(f"PO {row['po_id']} is open but has posted receipts.")
        elif status == "partially_received" and not (
            received > 0 and int(row["unreceived_lines"]) > 0
        ):
            errors.append(f"PO {row['po_id']} has an inconsistent partial-receipt status.")
        elif status in {"received", "closed"} and int(row["unreceived_lines"]) > 0:
            errors.append(f"PO {row['po_id']} is {status} before receiving its ordered quantity.")

    for po in connection.execute("SELECT po_id,total_amount FROM purchase_order"):
        line_total = sum(
            (
                _money(row[0])
                for row in connection.execute(
                    "SELECT line_total FROM purchase_order_line WHERE po_id=?",
                    (po["po_id"],),
                )
            ),
            Decimal(0),
        )
        if _money(po["total_amount"]) != line_total:
            errors.append(f"PO {po['po_id']} total does not reconcile to its lines.")

    wrong_receipt_joins = connection.execute(
        "SELECT COUNT(*) FROM goods_receipt_line grl "
        "JOIN goods_receipt gr ON gr.grn_id=grl.grn_id "
        "JOIN purchase_order_line pl ON pl.po_line_id=grl.po_line_id "
        "WHERE gr.po_id<>pl.po_id"
    ).fetchone()[0]
    if wrong_receipt_joins:
        errors.append(f"{wrong_receipt_joins} receipt line(s) reference a line from another PO.")
    invalid_vendor_dates = connection.execute(
        "SELECT COUNT(*) FROM purchase_order po JOIN vendor v ON v.vendor_id=po.vendor_id "
        "WHERE po.po_date<v.effective_from OR "
        "(v.effective_to IS NOT NULL AND po.po_date>v.effective_to)"
    ).fetchone()[0]
    if invalid_vendor_dates:
        errors.append(f"{invalid_vendor_dates} PO date(s) fall outside vendor effective dates.")
    invalid_receipt_dates = connection.execute(
        "SELECT COUNT(*) FROM goods_receipt gr JOIN purchase_order po ON po.po_id=gr.po_id "
        "WHERE gr.receipt_date<po.po_date"
    ).fetchone()[0]
    if invalid_receipt_dates:
        errors.append(f"{invalid_receipt_dates} receipt(s) are dated before their PO.")
    wrong_invoice_joins = connection.execute(
        "SELECT COUNT(*) FROM invoice i "
        "JOIN purchase_order po ON po.po_id=i.po_id "
        "WHERE i.vendor_id<>po.vendor_id"
    ).fetchone()[0]
    if wrong_invoice_joins:
        errors.append(f"{wrong_invoice_joins} invoice(s) use a PO from another vendor.")
    wrong_invoice_lines = connection.execute(
        "SELECT COUNT(*) FROM invoice_line il "
        "JOIN invoice i ON i.invoice_id=il.invoice_id "
        "JOIN purchase_order_line pl ON pl.po_line_id=il.po_line_id "
        "WHERE i.po_id IS NULL OR i.po_id<>pl.po_id"
    ).fetchone()[0]
    if wrong_invoice_lines:
        errors.append(f"{wrong_invoice_lines} invoice line(s) reference an incompatible PO line.")

    tax_rates = {
        code: Decimal(str(rate))
        for code, rate in rules["tax_rates"].items()
    }
    for row in connection.execute("SELECT * FROM purchase_order_line"):
        expected_net = _money(Decimal(row["quantity"]) * _money(row["unit_price"]))
        expected_tax = _money(expected_net * tax_rates[row["tax_code"]])
        if (
            _money(row["line_net"]) != expected_net
            or _money(row["tax_amount"]) != expected_tax
            or _money(row["line_total"]) != expected_net + expected_tax
        ):
            errors.append(f"PO line {row['po_line_id']} does not reconcile.")

    for line in connection.execute("SELECT * FROM invoice_line"):
        expected_net = _money(Decimal(line["quantity"]) * _money(line["unit_price"]))
        expected_tax = _money(expected_net * tax_rates[line["tax_code"]])
        if (
            _money(line["line_net"]) != expected_net
            or _money(line["tax_amount"]) != expected_tax
            or _money(line["line_total"]) != expected_net + expected_tax
        ):
            errors.append(f"Invoice line {line['invoice_line_id']} does not reconcile.")

    for invoice in invoice_rows:
        invoice_id = invoice["invoice_id"]
        truth = truth_by_invoice.get(invoice_id, {})
        subtype = truth.get("defect_subtype")
        lines = connection.execute(
            "SELECT * FROM invoice_line WHERE invoice_id=? ORDER BY line_number", (invoice_id,)
        ).fetchall()
        line_net = sum((_money(line["line_net"]) for line in lines), Decimal(0))
        line_tax = sum((_money(line["tax_amount"]) for line in lines), Decimal(0))
        arithmetic_ok = (
            _money(invoice["net_amount"]) == line_net
            and _money(invoice["tax_amount"]) == line_tax
            and _money(invoice["total_amount"])
            == _money(invoice["net_amount"]) + _money(invoice["tax_amount"])
        )
        if not arithmetic_ok and subtype != "TOTAL_DIFFERS_FROM_LINE_SUM":
            errors.append(f"Invoice {invoice_id} has an unlabelled arithmetic defect.")
        if arithmetic_ok and subtype == "TOTAL_DIFFERS_FROM_LINE_SUM":
            errors.append(f"Invoice {invoice_id} is labelled arithmetic-defective but reconciles.")
        if date.fromisoformat(invoice["received_date"]) < date.fromisoformat(invoice["invoice_date"]):
            errors.append(f"Invoice {invoice_id} was received before its invoice date.")
        if invoice["po_id"]:
            po_date = connection.execute(
                "SELECT po_date FROM purchase_order WHERE po_id=?", (invoice["po_id"],)
            ).fetchone()[0]
            before_po = date.fromisoformat(invoice["invoice_date"]) < date.fromisoformat(po_date)
            if before_po != (subtype == "DATE_BEFORE_PO"):
                errors.append(f"Invoice {invoice_id} has an unlabelled or incorrect PO date anomaly.")

    events_by_invoice = _events_by_invoice(connection)
    if set(events_by_invoice) != invoice_ids:
        errors.append("There must be lifecycle history for every invoice.")
    for invoice_id, event_rows in events_by_invoice.items():
        previous_status: str | None = None
        expected_seq = 1
        for event in event_rows:
            if event["event_seq"] != expected_seq:
                errors.append(f"Invoice {invoice_id} lifecycle sequence is not contiguous.")
            if event["from_status"] != previous_status:
                errors.append(f"Invoice {invoice_id} lifecycle history is discontinuous.")
            if previous_status is not None and (previous_status, event["to_status"]) not in ALLOWED_TRANSITIONS:
                errors.append(f"Invoice {invoice_id} has an invalid lifecycle transition.")
            previous_status = event["to_status"]
            expected_seq += 1

    result_by_invoice = {
        row["invoice_id"]: row
        for row in connection.execute("SELECT * FROM invoice_match_result")
    }
    if set(result_by_invoice) != invoice_ids:
        errors.append("There must be exactly one match result per invoice.")
    invoice_by_id = {row["invoice_id"]: row for row in invoice_rows}
    decision_correct = 0
    route_correct = 0
    duplicate_pair_correct = 0
    approval_policy_correct = 0
    payment_policy_correct = 0
    confusion: dict[str, dict[str, int]] = {}
    issue_counts = Counter(row["issue_type"] for row in ground_truth)
    observed_decisions = Counter()
    for invoice_id, truth in truth_by_invoice.items():
        result = result_by_invoice.get(invoice_id)
        if result is None:
            continue
        expected_decision = truth["expected_decision"]
        observed_decision = result["decision"]
        observed_decisions[observed_decision] += 1
        confusion.setdefault(expected_decision, {})[observed_decision] = (
            confusion.setdefault(expected_decision, {}).get(observed_decision, 0) + 1
        )
        decision_correct += observed_decision == expected_decision
        route_correct += result["route"] == truth["expected_route"]
        if bool(result["is_duplicate"]) != (truth["issue_type"] == "duplicate"):
            errors.append(f"Invoice {invoice_id} has an incorrect duplicate flag.")
        expected_pair = truth["paired_invoice_id"]
        if result["duplicate_of_invoice_id"] != expected_pair:
            errors.append(f"Invoice {invoice_id} has an incorrect duplicate pair.")
        if truth["issue_type"] == "duplicate":
            duplicate_pair_correct += result["duplicate_of_invoice_id"] == expected_pair
        expected_status = (
            "blocked"
            if result["blocked"]
            else "exception"
            if result["decision"] != "MATCHED"
            else "payment_ready"
        )
        invoice_events = events_by_invoice.get(invoice_id, [])
        if not invoice_events or invoice_events[-1]["to_status"] != expected_status:
            errors.append(f"Invoice {invoice_id} has an incorrect terminal lifecycle status.")

        if result["decision"] != "MATCHED":
            continue
        invoice = invoice_by_id[invoice_id]
        expected_levels = ["L1"]
        if _money(invoice["total_amount"]) > approval_limit:
            expected_levels.append("L2")
        approvals = connection.execute(
            "SELECT * FROM approval WHERE invoice_id=? ORDER BY approval_level",
            (invoice_id,),
        ).fetchall()
        actual_levels = [approval["approval_level"] for approval in approvals]
        approval_is_correct = actual_levels == expected_levels
        expected_request_date = (
            date.fromisoformat(invoice["received_date"]) + timedelta(days=1)
        )
        for approval in approvals:
            approval_is_correct = approval_is_correct and (
                date.fromisoformat(approval["requested_date"]) == expected_request_date
            )
            approval_events = connection.execute(
                "SELECT * FROM approval_event WHERE approval_id=? ORDER BY event_seq",
                (approval["approval_id"],),
            ).fetchall()
            approval_is_correct = approval_is_correct and (
                [event["event_type"] for event in approval_events] == ["requested", "approved"]
                and [event["event_seq"] for event in approval_events] == [1, 2]
                and all(
                    event["event_date"] == expected_request_date.isoformat()
                    for event in approval_events
                )
            )
        if not approval_is_correct:
            errors.append(f"Invoice {invoice_id} does not meet the approval-level policy.")
        approval_policy_correct += approval_is_correct

        payment = connection.execute(
            "SELECT * FROM payment WHERE invoice_id=?", (invoice_id,)
        ).fetchone()
        payment_is_correct = payment is not None
        if payment is not None:
            expected_approval = next(
                (approval for approval in approvals if approval["approval_level"] == expected_levels[-1]),
                None,
            )
            expected_scheduled_date = (
                date.fromisoformat(expected_approval["requested_date"])
                + timedelta(days=payment_delay)
                if expected_approval
                else None
            )
            payment_events = connection.execute(
                "SELECT * FROM payment_event WHERE payment_id=? ORDER BY event_seq",
                (payment["payment_id"],),
            ).fetchall()
            payment_is_correct = (
                expected_approval is not None
                and payment["approval_id"] == expected_approval["approval_id"]
                and _money(payment["amount"]) == _money(invoice["total_amount"])
                and expected_scheduled_date is not None
                and date.fromisoformat(payment["scheduled_date"]) == expected_scheduled_date
                and [event["event_type"] for event in payment_events] == ["scheduled"]
                and [event["event_seq"] for event in payment_events] == [1]
                and all(
                    event["event_date"] == expected_scheduled_date.isoformat()
                    for event in payment_events
                )
            )
        if not payment_is_correct:
            errors.append(f"Invoice {invoice_id} does not meet the payment policy.")
        payment_policy_correct += payment_is_correct

    total_invoices = len(invoice_ids)
    matched_count = sum(row["decision"] == "MATCHED" for row in result_by_invoice.values())
    duplicate_truth = {row["invoice_id"] for row in ground_truth if row["issue_type"] == "duplicate"}
    duplicate_detected = {
        invoice_id for invoice_id, row in result_by_invoice.items() if row["is_duplicate"]
    }
    duplicate_tp = len(duplicate_truth & duplicate_detected)
    duplicate_precision = duplicate_tp / len(duplicate_detected) if duplicate_detected else 1.0
    duplicate_recall = duplicate_tp / len(duplicate_truth) if duplicate_truth else 1.0

    blocked_ready = connection.execute(
        "SELECT COUNT(*) FROM invoice_match_result WHERE blocked=1 AND payment_ready=1"
    ).fetchone()[0]
    if blocked_ready:
        errors.append(f"{blocked_ready} blocked invoice(s) are marked payment-ready.")
    ineligible_workflow = connection.execute(
        "SELECT COUNT(*) FROM invoice_match_result m "
        "WHERE m.decision<>'MATCHED' AND ("
        "EXISTS (SELECT 1 FROM approval a WHERE a.invoice_id=m.invoice_id) OR "
        "EXISTS (SELECT 1 FROM payment p WHERE p.invoice_id=m.invoice_id))"
    ).fetchone()[0]
    if ineligible_workflow:
        errors.append(f"{ineligible_workflow} ineligible invoice(s) have approval/payment records.")
    unmatched_workflow = connection.execute(
        "SELECT COUNT(*) FROM invoice_match_result m WHERE m.decision='MATCHED' AND "
        "(m.payment_ready<>1 OR NOT EXISTS "
        "(SELECT 1 FROM approval a WHERE a.invoice_id=m.invoice_id) OR NOT EXISTS "
        "(SELECT 1 FROM payment p WHERE p.invoice_id=m.invoice_id))"
    ).fetchone()[0]
    if unmatched_workflow:
        errors.append(f"{unmatched_workflow} matched invoice(s) are missing approval/payment readiness.")
    unapproved_payments = connection.execute(
        "SELECT COUNT(*) FROM payment p "
        "JOIN approval a ON a.approval_id=p.approval_id "
        "WHERE a.invoice_id<>p.invoice_id OR NOT EXISTS "
        "(SELECT 1 FROM approval_event ae WHERE ae.approval_id=a.approval_id "
        "AND ae.event_type='approved')"
    ).fetchone()[0]
    if unapproved_payments:
        errors.append(f"{unapproved_payments} payment(s) do not have a matching approved approval.")

    payment_ready_count = connection.execute(
        "SELECT COUNT(*) FROM invoice_match_result WHERE payment_ready=1"
    ).fetchone()[0]
    return {
        "passed": not errors,
        "errors": errors,
        "counts": {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "vendor",
                "purchase_order",
                "purchase_order_line",
                "goods_receipt",
                "goods_receipt_line",
                "invoice",
                "invoice_line",
                "approval",
                "payment",
            )
        },
        "metrics": {
            "decision_accuracy": decision_correct / total_invoices if total_invoices else 1.0,
            "route_accuracy": route_correct / total_invoices if total_invoices else 1.0,
            "approval_policy_accuracy": (
                approval_policy_correct / matched_count if matched_count else 1.0
            ),
            "payment_policy_accuracy": (
                payment_policy_correct / matched_count if matched_count else 1.0
            ),
            "duplicate_precision": duplicate_precision,
            "duplicate_recall": duplicate_recall,
            "duplicate_true_positives": duplicate_tp,
            "duplicate_pair_accuracy": (
                duplicate_pair_correct / len(duplicate_truth) if duplicate_truth else 1.0
            ),
            "issue_distribution": dict(sorted(issue_counts.items())),
            "decision_distribution": dict(sorted(observed_decisions.items())),
            "decision_confusion_matrix": confusion,
            "payment_ready_count": payment_ready_count,
            "payment_ready_rate": payment_ready_count / total_invoices if total_invoices else 0.0,
            "blocked_payment_ready_count": blocked_ready,
        },
    }


def _events_by_invoice(connection: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    events: dict[str, list[sqlite3.Row]] = {}
    for event in connection.execute(
        "SELECT * FROM invoice_status_event ORDER BY invoice_id,event_seq"
    ):
        events.setdefault(event["invoice_id"], []).append(event)
    return events
