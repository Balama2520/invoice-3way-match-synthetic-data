"""Rule-based three-way invoice matching and workflow event creation."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CENT = Decimal("0.01")


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _insert_status_events(
    connection: sqlite3.Connection,
    invoice_id: str,
    received_date: date,
    decision: str,
    blocked: bool,
    scheduled_date: date | None,
) -> None:
    events: list[tuple[str | None, str, date, str | None]] = [
        (None, "received", received_date, None),
        ("received", "extracted", received_date, None),
        ("extracted", "matched", received_date, None),
    ]
    if blocked:
        events.append(("matched", "blocked", received_date, decision.lower()))
    elif decision != "MATCHED":
        events.append(("matched", "exception", received_date, decision.lower()))
    else:
        approval_date = received_date + timedelta(days=1)
        events.extend(
            [
                ("matched", "approval_pending", received_date, None),
                ("approval_pending", "approved", approval_date, None),
                ("approved", "payment_ready", scheduled_date or approval_date, None),
            ]
        )
    for seq, (from_status, to_status, event_date, reason) in enumerate(events, 1):
        connection.execute(
            "INSERT INTO invoice_status_event "
            "(invoice_id,event_seq,from_status,to_status,event_date,actor_role,reason_code) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                invoice_id,
                seq,
                from_status,
                to_status,
                event_date.isoformat(),
                "system",
                reason,
            ),
        )


def evaluate_invoices(connection: sqlite3.Connection) -> None:
    """Derive match decisions, bridge allocations, approvals, payments and events."""
    rules = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8"))
    tolerance = Decimal(str(rules["tolerances"]["price_variance_pct"]))
    money_tolerance = Decimal(str(rules["tolerances"]["money_inr"]))
    tax_rates = {key: Decimal(str(rate)) for key, rate in rules["tax_rates"].items()}
    level1_limit = Decimal(str(rules["approval"]["level1_limit_inr"]))
    payment_delay = int(rules["approval"]["payment_delay_days"])

    invoices = connection.execute(
        "SELECT i.*, v.status AS vendor_status, po.po_date AS purchase_order_date "
        "FROM invoice i JOIN vendor v ON v.vendor_id=i.vendor_id "
        "LEFT JOIN purchase_order po ON po.po_id=i.po_id "
        "ORDER BY i.received_date, i.invoice_id"
    ).fetchall()
    seen_invoice_numbers: dict[tuple[str, str], str] = {}
    approval_counter = 0
    payment_counter = 0

    for invoice in invoices:
        invoice_id = invoice["invoice_id"]
        duplicate_key = (invoice["vendor_id"], invoice["vendor_invoice_number"])
        duplicate_of = seen_invoice_numbers.get(duplicate_key)
        if duplicate_of is None:
            seen_invoice_numbers[duplicate_key] = invoice_id

        vendor_ok = invoice["vendor_status"] == "active"
        invoice_date = date.fromisoformat(invoice["invoice_date"])
        received_date = date.fromisoformat(invoice["received_date"])
        po_date = (
            date.fromisoformat(invoice["purchase_order_date"])
            if invoice["purchase_order_date"]
            else None
        )
        date_ok = received_date >= invoice_date and (
            po_date is None or invoice_date >= po_date
        )

        invoice_lines = connection.execute(
            "SELECT * FROM invoice_line WHERE invoice_id=? ORDER BY line_number",
            (invoice_id,),
        ).fetchall()
        line_net_total = sum((_money(row["line_net"]) for row in invoice_lines), Decimal(0))
        line_tax_total = sum((_money(row["tax_amount"]) for row in invoice_lines), Decimal(0))
        arithmetic_ok = (
            _money(invoice["net_amount"]) == line_net_total
            and _money(invoice["tax_amount"]) == line_tax_total
            and _money(invoice["total_amount"])
            == _money(invoice["net_amount"]) + _money(invoice["tax_amount"])
        )

        receipt_found = False
        price_ok = True
        quantity_ok = True
        tax_ok = True
        matched_line_data: list[tuple[sqlite3.Row, sqlite3.Row, int]] = []

        if invoice["po_id"] is None:
            receipt_found = False
            price_ok = quantity_ok = tax_ok = False
        else:
            receipt_found = connection.execute(
                "SELECT 1 FROM goods_receipt gr "
                "JOIN goods_receipt_line grl ON grl.grn_id=gr.grn_id "
                "WHERE gr.po_id=? AND gr.status='posted' LIMIT 1",
                (invoice["po_id"],),
            ).fetchone() is not None
            for line in invoice_lines:
                if line["po_line_id"] is None:
                    price_ok = quantity_ok = tax_ok = False
                    continue
                po_line = connection.execute(
                    "SELECT * FROM purchase_order_line WHERE po_line_id=?",
                    (line["po_line_id"],),
                ).fetchone()
                received = connection.execute(
                    "SELECT COALESCE(SUM(grl.quantity_received),0) "
                    "FROM goods_receipt gr "
                    "JOIN goods_receipt_line grl ON grl.grn_id=gr.grn_id "
                    "WHERE gr.po_id=? AND gr.status='posted' AND grl.po_line_id=?",
                    (invoice["po_id"], line["po_line_id"]),
                ).fetchone()[0]
                matched_line_data.append((line, po_line, received))
                po_price = _money(po_line["unit_price"])
                invoiced_price = _money(line["unit_price"])
                variance = abs(invoiced_price - po_price) / po_price
                price_ok = price_ok and variance <= tolerance
                quantity_ok = quantity_ok and (
                    int(line["quantity"]) == int(po_line["quantity"])
                    and int(line["quantity"]) <= int(received)
                )
                expected_tax = _money(
                    _money(line["line_net"]) * tax_rates[po_line["tax_code"]]
                )
                actual_tax = _money(line["tax_amount"])
                tax_ok = tax_ok and (
                    line["tax_code"] == po_line["tax_code"]
                    and abs(actual_tax - expected_tax) <= money_tolerance
                )

        for line, po_line, received in matched_line_data:
            po_price = _money(po_line["unit_price"])
            invoiced_price = _money(line["unit_price"])
            price_variance = (invoiced_price - po_price) / po_price
            expected_po_tax = _money(
                _money(line["line_net"]) * tax_rates[po_line["tax_code"]]
            )
            connection.execute(
                "INSERT INTO invoice_line_po_line_match "
                "(invoice_line_id,po_line_id,matched_quantity,price_variance_pct,"
                "quantity_variance,tax_variance) VALUES (?,?,?,?,?,?)",
                (
                    line["invoice_line_id"],
                    po_line["po_line_id"],
                    min(int(line["quantity"]), int(received)),
                    float(price_variance),
                    int(line["quantity"]) - int(received),
                    float(_money(line["tax_amount"]) - expected_po_tax),
                ),
            )

        is_duplicate = duplicate_of is not None
        if is_duplicate:
            decision = "DUPLICATE"
        elif not vendor_ok:
            decision = "CLOSED_VENDOR"
        elif not arithmetic_ok:
            decision = "ARITHMETIC_ANOMALY"
        elif not date_ok:
            decision = "DATE_ANOMALY"
        elif not receipt_found:
            decision = "MISSING_RECEIPT"
        elif not tax_ok:
            decision = "TAX_MISMATCH"
        elif not price_ok:
            decision = "PRICE_VARIANCE"
        elif not quantity_ok:
            decision = "QUANTITY_MISMATCH"
        else:
            decision = "MATCHED"

        route = {
            "MATCHED": "approver",
            "DUPLICATE": "blocked",
            "CLOSED_VENDOR": "blocked",
            "MISSING_RECEIPT": "requester_hold",
        }.get(decision, "operations_analyst")
        blocked = decision in {"DUPLICATE", "CLOSED_VENDOR"}
        payment_ready = decision == "MATCHED"
        connection.execute(
            "INSERT INTO invoice_match_result "
            "(invoice_id,decision,route,is_duplicate,duplicate_of_invoice_id,vendor_ok,"
            "date_ok,arithmetic_ok,receipt_found,price_ok,quantity_ok,tax_ok,blocked,"
            "payment_ready) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                invoice_id,
                decision,
                route,
                int(is_duplicate),
                duplicate_of,
                int(vendor_ok),
                int(date_ok),
                int(arithmetic_ok),
                int(receipt_found),
                int(price_ok),
                int(quantity_ok),
                int(tax_ok),
                int(blocked),
                int(payment_ready),
            ),
        )

        scheduled_date: date | None = None
        if payment_ready:
            approval_date = received_date + timedelta(days=1)
            levels = ["L1"]
            if _money(invoice["total_amount"]) > level1_limit:
                levels.append("L2")
            last_approval_id = ""
            for level in levels:
                approval_counter += 1
                approval_id = f"APR-{int(rules['year'])}-{approval_counter:06d}"
                last_approval_id = approval_id
                approver_id = f"APR-USER-{approval_counter:05d}"
                connection.execute(
                    "INSERT INTO approval "
                    "(approval_id,invoice_id,approval_level,approver_id,requested_date) "
                    "VALUES (?,?,?,?,?)",
                    (
                        approval_id,
                        invoice_id,
                        level,
                        approver_id,
                        approval_date.isoformat(),
                    ),
                )
                connection.executemany(
                    "INSERT INTO approval_event "
                    "(approval_id,event_seq,event_type,event_date,actor_id) "
                    "VALUES (?,?,?,?,?)",
                    [
                        (approval_id, 1, "requested", approval_date.isoformat(), "SYSTEM"),
                        (approval_id, 2, "approved", approval_date.isoformat(), approver_id),
                    ],
                )
            payment_counter += 1
            payment_id = f"PAY-{int(rules['year'])}-{payment_counter:06d}"
            scheduled_date = approval_date + timedelta(days=payment_delay)
            connection.execute(
                "INSERT INTO payment "
                "(payment_id,invoice_id,approval_id,amount,scheduled_date) VALUES (?,?,?,?,?)",
                (
                    payment_id,
                    invoice_id,
                    last_approval_id,
                    float(_money(invoice["total_amount"])),
                    scheduled_date.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO payment_event "
                "(payment_id,event_seq,event_type,event_date) VALUES (?,?,?,?)",
                (payment_id, 1, "scheduled", scheduled_date.isoformat()),
            )

        _insert_status_events(
            connection,
            invoice_id,
            received_date,
            decision,
            blocked,
            scheduled_date,
        )
