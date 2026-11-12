"""Deterministic synthetic invoice / PO / receipt data generation and export."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import sqlite3
import zipfile
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from src.matcher import evaluate_invoices
from src.intake import extract_invoice_document, format_invoice_document
from src.validation import validate_database


ROOT = Path(__file__).resolve().parents[1]
INPUT_TABLES = [
    "vendor",
    "purchase_order",
    "purchase_order_line",
    "goods_receipt",
    "goods_receipt_line",
    "invoice",
    "invoice_line",
    "invoice_extraction",
    "approval",
    "payment",
    "invoice_status_event",
    "approval_event",
    "payment_event",
]
DERIVED_TABLES = ["invoice_match_result", "invoice_line_po_line_match"]
ISSUE_RULES = {
    "NONE": ("clean", "MATCHED", "approver"),
    "BOUNDARY_CLEAN": ("clean", "MATCHED", "approver"),
    "PRICE_OUTSIDE_TOLERANCE": ("price_variance", "PRICE_VARIANCE", "operations_analyst"),
    "PARTIAL_DELIVERY": ("quantity_mismatch", "QUANTITY_MISMATCH", "operations_analyst"),
    "INVOICE_EXCEEDS_PO_QTY": ("quantity_mismatch", "QUANTITY_MISMATCH", "operations_analyst"),
    "DUPLICATE_NUMBER": ("duplicate", "DUPLICATE", "blocked"),
    "SAME_INVOICE_TWICE": ("duplicate", "DUPLICATE", "blocked"),
    "NO_RECEIPT": ("missing_receipt", "MISSING_RECEIPT", "requester_hold"),
    "TAX_MISMATCH": ("tax_vendor_error", "TAX_MISMATCH", "operations_analyst"),
    "CLOSED_VENDOR": ("tax_vendor_error", "CLOSED_VENDOR", "blocked"),
    "TOTAL_DIFFERS_FROM_LINE_SUM": (
        "tax_vendor_error",
        "ARITHMETIC_ANOMALY",
        "operations_analyst",
    ),
    "DATE_BEFORE_PO": ("tax_vendor_error", "DATE_ANOMALY", "operations_analyst"),
}
COMPANY_PREFIXES = ("Aster", "Bluebird", "Cedar", "Delta", "Evergreen", "Frontier")
COMPANY_SUFFIXES = ("Components", "Industrial Supply", "Trading", "Materials", "Systems")
DESCRIPTIONS = ("fasteners", "safety equipment", "packaging", "electrical parts", "bearings")
TAX_CODES = ("T0", "T5", "T12", "T18")
CENT = Decimal("0.01")


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _counts_for_scale(rules: dict[str, Any], scale: float) -> dict[str, int]:
    if not math.isfinite(scale) or scale < 0.01 or scale > 10:
        raise ValueError("scale must be between 0.01 and 10.")
    counts = {
        key: max(1, int(round(value * scale)))
        for key, value in rules["counts"].items()
    }
    counts["purchase_order_lines"] = min(
        counts["purchase_order_lines"],
        counts["purchase_orders"] * 8,
    )
    counts["purchase_order_lines"] = max(
        counts["purchase_order_lines"],
        counts["purchase_orders"],
    )
    counts["goods_receipts"] = max(
        counts["goods_receipts"],
        counts["purchase_orders"] - max(0, int(round(rules["receipt_mix"]["no_receipt_purchase_orders"] * scale))),
    )
    return counts


def _apportion(total: int, weights: dict[str, float]) -> dict[str, int]:
    exact = {key: total * weight for key, weight in weights.items()}
    result = {key: math.floor(value) for key, value in exact.items()}
    remaining = total - sum(result.values())
    order = sorted(weights, key=lambda key: (exact[key] - result[key], key), reverse=True)
    for key in order[:remaining]:
        result[key] += 1
    return result


def _po_line_counts(
    purchase_order_count: int,
    total_line_count: int,
    distribution: dict[str, int],
    rng: random.Random,
) -> list[int]:
    if not purchase_order_count <= total_line_count <= purchase_order_count * 8:
        raise ValueError("PO line count must allow 1-8 lines per purchase order.")
    weights_total = sum(distribution.values())
    weights = {
        int(line_count): count / weights_total
        for line_count, count in distribution.items()
    }
    apportioned = _apportion(purchase_order_count, weights)
    line_counts = [
        line_count
        for line_count, count in apportioned.items()
        for _ in range(count)
    ]
    current_total = sum(line_counts)
    while current_total < total_line_count:
        candidates = [index for index, value in enumerate(line_counts) if value < 8]
        index = rng.choice(candidates)
        line_counts[index] += 1
        current_total += 1
    while current_total > total_line_count:
        candidates = [index for index, value in enumerate(line_counts) if value > 1]
        index = rng.choice(candidates)
        line_counts[index] -= 1
        current_total -= 1
    rng.shuffle(line_counts)
    return line_counts


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript((ROOT / "src" / "schema.sql").read_text(encoding="utf-8"))
    return connection


def _line_amount(quantity: int, unit_price: Decimal, tax_code: str, tax_rates: dict[str, Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    net = _money(quantity * unit_price)
    tax = _money(net * tax_rates[tax_code])
    return net, tax, net + tax


def _invoice_date(rng: random.Random, po_date: date | None, subtype: str) -> tuple[date, date]:
    if po_date is None:
        invoice_date = date(2026, 4, 1) + timedelta(days=rng.randint(0, 150))
    elif subtype == "DATE_BEFORE_PO":
        invoice_date = po_date - timedelta(days=1)
    else:
        invoice_date = po_date + timedelta(days=rng.randint(1, 45))
    received_date = invoice_date + timedelta(days=rng.randint(1, 5))
    return invoice_date, received_date


def _label(invoice_id: str, subtype: str, paired_invoice_id: str | None, secondary: str | None, rule: str) -> dict[str, Any]:
    issue_type, decision, route = ISSUE_RULES[subtype]
    return {
        "invoice_id": invoice_id,
        "issue_type": issue_type,
        "defect_subtype": subtype,
        "secondary_issue_types": secondary,
        "expected_decision": decision,
        "expected_route": route,
        "paired_invoice_id": paired_invoice_id,
        "injection_rule": rule,
    }


def _make_invoice_spec(
    rng: random.Random,
    subtype: str,
    po: dict[str, Any] | None,
    po_lines: dict[str, list[dict[str, Any]]],
    tax_rates: dict[str, Decimal],
    vendor_id: str | None = None,
) -> dict[str, Any]:
    selected_vendor = vendor_id or (po["vendor_id"] if po else None)
    if selected_vendor is None:
        raise ValueError("An invoice spec needs a vendor.")
    invoice_po_lines = po_lines[po["po_id"]] if po else []
    if invoice_po_lines:
        invoice_lines = []
        for source in invoice_po_lines:
            quantity = int(source["quantity"])
            unit_price = _money(source["unit_price"])
            tax_code = source["tax_code"]
            if source["line_number"] == 1:
                if subtype == "PRICE_OUTSIDE_TOLERANCE":
                    unit_price = _money(unit_price * Decimal("1.05"))
                elif subtype == "BOUNDARY_CLEAN":
                    unit_price = _money(unit_price * Decimal("1.02"))
                elif subtype == "INVOICE_EXCEEDS_PO_QTY":
                    quantity += max(1, math.ceil(quantity * 0.05))
                elif subtype == "TAX_MISMATCH":
                    tax_code = next(code for code in TAX_CODES if code != tax_code)
            net, tax, total = _line_amount(quantity, unit_price, tax_code, tax_rates)
            invoice_lines.append(
                {
                    "po_line_id": source["po_line_id"],
                    "sku": source["sku"],
                    "description": source["description"],
                    "quantity": quantity,
                    "unit_price": float(unit_price),
                    "tax_code": tax_code,
                    "line_net": float(net),
                    "tax_amount": float(tax),
                    "line_total": float(total),
                }
            )
    else:
        unit_price = Decimal(rng.randint(100, 10000))
        tax_code = rng.choice(TAX_CODES)
        net, tax, total = _line_amount(3, unit_price, tax_code, tax_rates)
        invoice_lines = [
            {
                "po_line_id": None,
                "sku": f"SKU-{rng.randint(1, 120):04d}",
                "description": rng.choice(DESCRIPTIONS),
                "quantity": 3,
                "unit_price": float(unit_price),
                "tax_code": tax_code,
                "line_net": float(net),
                "tax_amount": float(tax),
                "line_total": float(total),
            }
        ]

    invoice_date, received_date = _invoice_date(
        rng,
        date.fromisoformat(po["po_date"]) if po else None,
        subtype,
    )
    net_amount = _money(sum((Decimal(str(line["line_net"])) for line in invoice_lines), Decimal(0)))
    tax_amount = _money(sum((Decimal(str(line["tax_amount"])) for line in invoice_lines), Decimal(0)))
    total_amount = net_amount + tax_amount
    if subtype == "TOTAL_DIFFERS_FROM_LINE_SUM":
        total_amount += Decimal("25.00")
    return {
        "vendor_id": selected_vendor,
        "po_id": po["po_id"] if po else None,
        "invoice_date": invoice_date,
        "received_date": received_date,
        "vendor_invoice_number": None,
        "currency": "INR",
        "net_amount": float(net_amount),
        "tax_amount": float(tax_amount),
        "total_amount": float(total_amount),
        "source_channel": rng.choice(("email", "portal", "edi")),
        "lines": invoice_lines,
        "subtype": subtype,
        "paired_spec": None,
        "secondary_issue_types": None,
        "injection_rule": f"subtype:{subtype}",
    }


def _build_grounded_specs(
    rng: random.Random,
    mix_counts: dict[str, int],
    tax_rates: dict[str, Decimal],
    po_lines: dict[str, list[dict[str, Any]]],
    active_pos: list[dict[str, Any]],
    closed_pos: list[dict[str, Any]],
    no_receipt_pos: list[dict[str, Any]],
    partial_pos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []

    def choose(pool: list[dict[str, Any]], purpose: str) -> dict[str, Any]:
        if not pool:
            raise ValueError(f"No suitable PO is available for {purpose}.")
        return rng.choice(pool)

    clean_subtypes = _apportion(mix_counts["clean"], {"NONE": 0.99, "BOUNDARY_CLEAN": 0.01})
    for subtype, count in clean_subtypes.items():
        for _ in range(count):
            po = choose(active_pos, "clean invoice")
            specs.append(_make_invoice_spec(rng, subtype, po, po_lines, tax_rates))

    for _ in range(mix_counts["price_variance"]):
        po = choose(active_pos, "price variance")
        specs.append(_make_invoice_spec(rng, "PRICE_OUTSIDE_TOLERANCE", po, po_lines, tax_rates))

    quantity_subtypes = _apportion(
        mix_counts["quantity_mismatch"],
        {"PARTIAL_DELIVERY": 0.5, "INVOICE_EXCEEDS_PO_QTY": 0.5},
    )
    for _ in range(quantity_subtypes["PARTIAL_DELIVERY"]):
        po = choose(partial_pos, "partial delivery")
        specs.append(_make_invoice_spec(rng, "PARTIAL_DELIVERY", po, po_lines, tax_rates))
    for _ in range(quantity_subtypes["INVOICE_EXCEEDS_PO_QTY"]):
        po = choose(active_pos, "invoice quantity over PO")
        specs.append(_make_invoice_spec(rng, "INVOICE_EXCEEDS_PO_QTY", po, po_lines, tax_rates))

    missing_count = mix_counts["missing_receipt"]
    no_po_count = max(0, int(round(missing_count * 0.15)))
    for _ in range(missing_count - no_po_count):
        po = choose(no_receipt_pos, "missing receipt")
        specs.append(_make_invoice_spec(rng, "NO_RECEIPT", po, po_lines, tax_rates))
    active_vendors = sorted({po["vendor_id"] for po in active_pos})
    for _ in range(no_po_count):
        vendor_id = rng.choice(active_vendors)
        specs.append(_make_invoice_spec(rng, "NO_RECEIPT", None, po_lines, tax_rates, vendor_id))

    tax_subtypes = _apportion(
        mix_counts["tax_vendor_error"],
        {
            "TAX_MISMATCH": 0.50,
            "CLOSED_VENDOR": 0.25,
            "TOTAL_DIFFERS_FROM_LINE_SUM": 0.125,
            "DATE_BEFORE_PO": 0.125,
        },
    )
    for subtype, count in tax_subtypes.items():
        for _ in range(count):
            pool = closed_pos if subtype == "CLOSED_VENDOR" else active_pos
            po = choose(pool, subtype)
            specs.append(_make_invoice_spec(rng, subtype, po, po_lines, tax_rates))

    duplicate_count = mix_counts["duplicate"]
    rng.shuffle(specs)
    clean_templates = [spec for spec in specs if spec["subtype"] in {"NONE", "BOUNDARY_CLEAN"}]
    if duplicate_count > len(clean_templates):
        raise ValueError("Scale is too small to provide clean duplicate source invoices.")
    for i in range(duplicate_count):
        source = rng.choice(clean_templates)
        duplicate = {
            **source,
            "lines": [dict(line) for line in source["lines"]],
            "invoice_date": source["invoice_date"],
            "received_date": source["received_date"] + timedelta(days=1),
            "subtype": "SAME_INVOICE_TWICE" if i % 2 == 0 else "DUPLICATE_NUMBER",
            "paired_spec": source,
            "secondary_issue_types": "PRICE_OUTSIDE_TOLERANCE" if i % 2 else None,
            "injection_rule": "resubmission:identical" if i % 2 == 0 else "resubmission:number_collision",
        }
        if i % 2 == 1 and duplicate["lines"]:
            line = duplicate["lines"][0]
            price = _money(Decimal(str(line["unit_price"])) * Decimal("1.05"))
            net, tax, total = _line_amount(
                int(line["quantity"]),
                price,
                line["tax_code"],
                tax_rates,
            )
            line.update(
                unit_price=float(price),
                line_net=float(net),
                tax_amount=float(tax),
                line_total=float(total),
            )
            duplicate["net_amount"] = float(
                _money(sum((Decimal(str(item["line_net"])) for item in duplicate["lines"]), Decimal(0)))
            )
            duplicate["tax_amount"] = float(
                _money(sum((Decimal(str(item["tax_amount"])) for item in duplicate["lines"]), Decimal(0)))
            )
            duplicate["total_amount"] = float(
                _money(duplicate["net_amount"]) + _money(duplicate["tax_amount"])
            )
        specs.append(duplicate)

    if len(specs) != sum(mix_counts.values()):
        raise RuntimeError(f"Generated {len(specs)} invoice specs for {sum(mix_counts.values())} labels.")
    return specs


def _insert_generated_records(
    connection: sqlite3.Connection,
    rng: random.Random,
    counts: dict[str, int],
    rules: dict[str, Any],
    scale: float,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    vendor_count = counts["vendors"]
    po_count = counts["purchase_orders"]
    line_count = counts["purchase_order_lines"]
    receipt_count = counts["goods_receipts"]
    invoice_count = counts["invoices"]
    mix_counts = _apportion(invoice_count, rules["invoice_mix"])
    tax_rates = {key: Decimal(str(value)) for key, value in rules["tax_rates"].items()}
    year = int(rules["year"])

    vendors: list[dict[str, Any]] = []
    for index in range(1, vendor_count + 1):
        status = "closed" if index % 20 == 1 else ("on_hold" if index % 17 == 0 else "active")
        vendor = {
            "vendor_id": f"VND-{index:05d}",
            "vendor_code": f"{index:05d}",
            "vendor_name": f"{COMPANY_PREFIXES[(index - 1) % len(COMPANY_PREFIXES)]} "
            f"{COMPANY_SUFFIXES[(index - 1) % len(COMPANY_SUFFIXES)]} {index:03d}",
            "status": status,
            "payment_terms_days": (15, 30, 45, 60)[(index - 1) % 4],
            "effective_from": date(year, 1, 1).isoformat(),
            "effective_to": date(year, 6, 30).isoformat() if status == "closed" else None,
        }
        vendors.append(vendor)
    connection.executemany(
        "INSERT INTO vendor VALUES (:vendor_id,:vendor_code,:vendor_name,:status,"
        ":payment_terms_days,:effective_from,:effective_to)",
        vendors,
    )
    vendor_by_id = {row["vendor_id"]: row for row in vendors}

    pos: list[dict[str, Any]] = []
    for index in range(1, po_count + 1):
        vendor = vendors[(index - 1) % vendor_count]
        po_id = f"PO-{year}-{index:06d}"
        date_window = 180 if vendor["status"] == "closed" else 240
        po_date = date(year, 1, 1) + timedelta(days=rng.randint(0, date_window))
        pos.append(
            {
                "po_id": po_id,
                "vendor_id": vendor["vendor_id"],
                "po_date": po_date.isoformat(),
                "status": "open",
                "currency": "INR",
                "requester_id": f"REQ-{(index - 1) % 250 + 1:03d}",
                "total_amount": 0.0,
            }
        )
    closed_pos = [po for po in pos if vendor_by_id[po["vendor_id"]]["status"] == "closed"]
    active_pos = [po for po in pos if vendor_by_id[po["vendor_id"]]["status"] == "active"]
    if not active_pos:
        raise ValueError("At least one active vendor is needed.")
    no_receipt_count = min(
        int(round(rules["receipt_mix"]["no_receipt_purchase_orders"] * scale)),
        max(0, po_count - 1),
    )
    no_receipt_pos = rng.sample(active_pos, min(no_receipt_count, len(active_pos)))
    no_receipt_ids = {po["po_id"] for po in no_receipt_pos}
    remaining_pos = [po for po in active_pos if po["po_id"] not in no_receipt_ids]
    partial_count = _apportion(mix_counts["quantity_mismatch"], {"PARTIAL_DELIVERY": 0.5, "INVOICE_EXCEEDS_PO_QTY": 0.5})["PARTIAL_DELIVERY"]
    if partial_count > len(remaining_pos):
        raise ValueError("Not enough received POs for partial-delivery invoices at this scale.")
    partial_pos = rng.sample(remaining_pos, partial_count)
    partial_ids = {po["po_id"] for po in partial_pos}
    matchable_active_pos = [
        po for po in active_pos
        if po["po_id"] not in no_receipt_ids and po["po_id"] not in partial_ids
    ]
    two_receipt_count = max(0, receipt_count - (po_count - no_receipt_count))
    two_receipt_count = min(two_receipt_count, po_count - no_receipt_count)
    receipt_candidates = [po for po in pos if po["po_id"] not in no_receipt_ids]
    two_receipt_pos = rng.sample(receipt_candidates, two_receipt_count)
    two_receipt_ids = {po["po_id"] for po in two_receipt_pos}
    over_receipt_candidates = [
        po for po in active_pos
        if po["po_id"] not in no_receipt_ids and po["po_id"] not in partial_ids
    ]
    over_receipt_ids = {
        po["po_id"]
        for po in rng.sample(
            over_receipt_candidates,
            min(len(over_receipt_candidates), max(1, int(round(po_count * 0.03)))),
        )
    }

    po_lines: dict[str, list[dict[str, Any]]] = {}
    po_line_counts = _po_line_counts(
        po_count,
        line_count,
        rules["po_line_count_distribution"],
        rng,
    )
    line_generation = rules["line_generation"]
    for po, count in zip(pos, po_line_counts):
        lines = []
        for line_number in range(1, count + 1):
            sku_number = rng.randint(1, 120)
            quantity = rng.randint(
                int(line_generation["quantity_min"]),
                int(line_generation["quantity_max"]),
            )
            if po["po_id"] in partial_ids and line_number == 1:
                quantity = max(quantity, 3)
            unit_price = Decimal(
                rng.randint(
                    int(line_generation["unit_price_steps_min"]),
                    int(line_generation["unit_price_steps_max"]),
                )
                * int(line_generation["unit_price_step_inr"])
            )
            tax_code = rng.choice(TAX_CODES)
            net, tax, total = _line_amount(quantity, unit_price, tax_code, tax_rates)
            lines.append(
                {
                    "po_line_id": f"{po['po_id']}-L{line_number:02d}",
                    "po_id": po["po_id"],
                    "line_number": line_number,
                    "sku": f"SKU-{sku_number:04d}",
                    "description": DESCRIPTIONS[(sku_number - 1) % len(DESCRIPTIONS)],
                    "quantity": quantity,
                    "unit_price": float(unit_price),
                    "tax_code": tax_code,
                    "line_net": float(net),
                    "tax_amount": float(tax),
                    "line_total": float(total),
                }
            )
        po_lines[po["po_id"]] = lines
        po["total_amount"] = float(_money(sum((Decimal(str(line["line_total"])) for line in lines), Decimal(0))))

    planned_receipts: list[dict[str, Any]] = []
    grn_counter = 0
    for po in pos:
        if po["po_id"] in no_receipt_ids:
            continue
        number_of_receipts = 2 if po["po_id"] in two_receipt_ids else 1
        receipt_parts: list[dict[str, Any]] = []
        for part in range(number_of_receipts):
            grn_counter += 1
            grn_id = f"GRN-{year}-{grn_counter:06d}"
            receipt_date = date.fromisoformat(po["po_date"]) + timedelta(days=part + 1)
            receipt_parts.append(
                {
                    "grn_id": grn_id,
                    "po_id": po["po_id"],
                    "receipt_date": receipt_date.isoformat(),
                    "status": "posted",
                    "lines": [],
                }
            )
        for line in po_lines[po["po_id"]]:
            total_received = int(line["quantity"])
            if po["po_id"] in partial_ids and line["line_number"] == 1:
                total_received = max(2, int(line["quantity"] * 0.7))
            elif po["po_id"] in over_receipt_ids and line["line_number"] == 1:
                total_received += max(1, int(line["quantity"] * 0.05))
            if number_of_receipts == 1:
                allocations = [total_received]
            elif total_received == 1:
                allocations = [1, 1]
            else:
                first = max(1, total_received // 2)
                allocations = [first, total_received - first]
            for receipt, quantity_received in zip(receipt_parts, allocations):
                receipt["lines"].append(
                    {
                        "grn_line_id": f"{receipt['grn_id']}-L{line['line_number']:02d}",
                        "grn_id": receipt["grn_id"],
                        "line_number": line["line_number"],
                        "po_line_id": line["po_line_id"],
                        "quantity_received": quantity_received,
                    }
                )
        planned_receipts.extend(receipt_parts)

    if len(planned_receipts) != receipt_count:
        raise RuntimeError(f"Generated {len(planned_receipts)} receipts; expected {receipt_count}.")
    for po in pos:
        received_by_line = {
            line["po_line_id"]: sum(
                item["quantity_received"]
                for receipt in planned_receipts
                for item in receipt["lines"]
                if item["po_line_id"] == line["po_line_id"]
            )
            for line in po_lines[po["po_id"]]
        }
        received_any = sum(received_by_line.values())
        any_unreceived_line = any(
            received_by_line[line["po_line_id"]] < int(line["quantity"])
            for line in po_lines[po["po_id"]]
        )
        if received_any == 0:
            po["status"] = "open"
        elif any_unreceived_line:
            po["status"] = "partially_received"
        else:
            po["status"] = "closed" if int(po["po_id"][-2:]) % 17 == 0 else "received"

    connection.executemany(
        "INSERT INTO purchase_order VALUES "
        "(:po_id,:vendor_id,:po_date,:status,:currency,:requester_id,:total_amount)",
        pos,
    )
    connection.executemany(
        "INSERT INTO purchase_order_line VALUES "
        "(:po_line_id,:po_id,:line_number,:sku,:description,:quantity,:unit_price,"
        ":tax_code,:line_net,:tax_amount,:line_total)",
        [line for lines in po_lines.values() for line in lines],
    )
    connection.executemany(
        "INSERT INTO goods_receipt (grn_id,po_id,receipt_date,status) VALUES "
        "(:grn_id,:po_id,:receipt_date,:status)",
        [{key: value for key, value in receipt.items() if key != "lines"} for receipt in planned_receipts],
    )
    connection.executemany(
        "INSERT INTO goods_receipt_line VALUES "
        "(:grn_line_id,:grn_id,:line_number,:po_line_id,:quantity_received)",
        [line for receipt in planned_receipts for line in receipt["lines"]],
    )

    mix_counts = _apportion(invoice_count, rules["invoice_mix"])
    specs = _build_grounded_specs(
        rng,
        mix_counts,
        tax_rates,
        po_lines,
        matchable_active_pos,
        closed_pos,
        no_receipt_pos,
        partial_pos,
    )
    rng.shuffle(specs)
    invoice_number_counters: Counter[str] = Counter()
    for invoice_index, spec in enumerate(specs, 1):
        spec["invoice_id"] = (
            f"INV-{vendor_by_id[spec['vendor_id']]['vendor_code']}-{year}-{invoice_index:06d}"
        )
    for spec in specs:
        if spec["paired_spec"] is not None:
            continue
        vendor_code = vendor_by_id[spec["vendor_id"]]["vendor_code"]
        invoice_number_counters[vendor_code] += 1
        spec["vendor_invoice_number"] = f"BILL-{vendor_code}-{invoice_number_counters[vendor_code]:06d}"
    # A stable map lets labels and resubmissions refer to the correct immutable ID.
    for spec in specs:
        paired = spec["paired_spec"]
        if paired is not None:
            spec["vendor_invoice_number"] = paired["vendor_invoice_number"]

    invoice_rows = []
    invoice_line_rows = []
    extraction_rows = []
    document_rows: list[dict[str, str]] = []
    truth_rows = []
    for spec in specs:
        invoice_id = spec["invoice_id"]
        source_invoice = {
            "vendor_id": spec["vendor_id"],
            "po_id": spec["po_id"],
            "vendor_invoice_number": spec["vendor_invoice_number"],
            "invoice_date": spec["invoice_date"].isoformat(),
            "received_date": spec["received_date"].isoformat(),
            "currency": spec["currency"],
            "source_channel": spec["source_channel"],
            "net_amount": spec["net_amount"],
            "tax_amount": spec["tax_amount"],
            "total_amount": spec["total_amount"],
        }
        source_document = format_invoice_document(source_invoice, spec["lines"])
        extracted = extract_invoice_document(source_document, invoice_id)
        document_name = f"invoices/{invoice_id}.txt"
        document_bytes = source_document.encode("utf-8")
        document_rows.append(
            {
                "document_name": document_name,
                "document_sha256": hashlib.sha256(document_bytes).hexdigest(),
                "content": source_document,
            }
        )
        invoice_rows.append(
            {
                "invoice_id": invoice_id,
                "vendor_id": extracted["vendor_id"],
                "po_id": extracted["po_id"],
                "vendor_invoice_number": extracted["vendor_invoice_number"],
                "invoice_date": extracted["invoice_date"],
                "received_date": extracted["received_date"],
                "currency": extracted["currency"],
                "net_amount": float(Decimal(extracted["net_amount"])),
                "tax_amount": float(Decimal(extracted["tax_amount"])),
                "total_amount": float(Decimal(extracted["total_amount"])),
                "source_channel": extracted["source_channel"],
            }
        )
        extraction_rows.append(
            {
                "extraction_id": f"EXT-{invoice_id}",
                "invoice_id": invoice_id,
                "document_name": document_name,
                "document_sha256": document_rows[-1]["document_sha256"],
                "extraction_status": "succeeded",
                "extracted_at": extracted["received_date"],
                "extracted_fields_json": json.dumps(
                    extracted,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
        for line in extracted["lines"]:
            line_number = line["line_number"]
            net, tax, total = _line_amount(
                line["quantity"],
                Decimal(line["unit_price"]),
                line["tax_code"],
                tax_rates,
            )
            invoice_line_rows.append(
                {
                    "po_line_id": line["po_line_id"],
                    "sku": line["sku"],
                    "description": line["description"],
                    "quantity": line["quantity"],
                    "unit_price": float(Decimal(line["unit_price"])),
                    "tax_code": line["tax_code"],
                    "line_net": float(net),
                    "tax_amount": float(tax),
                    "line_total": float(total),
                    "invoice_line_id": f"INVL-{invoice_id}-{line_number:02d}",
                    "invoice_id": invoice_id,
                    "line_number": line_number,
                }
            )
        paired_invoice_id = (
            spec["paired_spec"]["invoice_id"] if spec["paired_spec"] is not None else None
        )
        truth_rows.append(
            _label(
                invoice_id,
                spec["subtype"],
                paired_invoice_id,
                spec["secondary_issue_types"],
                spec["injection_rule"],
            )
        )

    connection.executemany(
        "INSERT INTO invoice VALUES "
        "(:invoice_id,:vendor_id,:po_id,:vendor_invoice_number,:invoice_date,:received_date,"
        ":currency,:net_amount,:tax_amount,:total_amount,:source_channel)",
        invoice_rows,
    )
    connection.executemany(
        "INSERT INTO invoice_line VALUES "
        "(:invoice_line_id,:invoice_id,:line_number,:po_line_id,:sku,:description,:quantity,"
        ":unit_price,:tax_code,:line_net,:tax_amount,:line_total)",
        invoice_line_rows,
    )
    connection.executemany(
        "INSERT INTO invoice_extraction VALUES "
        "(:extraction_id,:invoice_id,:document_name,:document_sha256,"
        ":extraction_status,:extracted_at,:extracted_fields_json)",
        extraction_rows,
    )
    return truth_rows, document_rows


def _csv_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)


def _table_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def _ground_truth_sql(rows: list[dict[str, Any]]) -> str:
    truth_connection = sqlite3.connect(":memory:")
    truth_connection.executescript(
        (ROOT / "src" / "schema_ground_truth.sql").read_text(encoding="utf-8")
    )
    truth_connection.executemany(
        "INSERT INTO ground_truth VALUES "
        "(:invoice_id,:issue_type,:defect_subtype,:secondary_issue_types,"
        ":expected_decision,:expected_route,:paired_invoice_id,:injection_rule)",
        rows,
    )
    dump = "\n".join(truth_connection.iterdump()) + "\n"
    truth_connection.close()
    return dump


def _export_dataset(
    connection: sqlite3.Connection,
    truth_rows: list[dict[str, Any]],
    document_rows: list[dict[str, str]],
    output_dir: Path,
    summary: dict[str, Any],
    seed: int,
    scale: float,
    counts: dict[str, int],
    rules: dict[str, Any],
) -> None:
    input_dir = output_dir / "input"
    derived_dir = output_dir / "derived"
    input_dir.mkdir(parents=True, exist_ok=True)
    derived_dir.mkdir(parents=True, exist_ok=True)
    input_json: dict[str, list[dict[str, Any]]] = {}
    derived_json: dict[str, list[dict[str, Any]]] = {}
    for table in INPUT_TABLES:
        rows = _table_rows(connection, table)
        _csv_rows(input_dir / f"{table}.csv", rows)
        input_json[table] = rows
    for table in DERIVED_TABLES:
        rows = _table_rows(connection, table)
        _csv_rows(derived_dir / f"{table}.csv", rows)
        derived_json[table] = rows

    _csv_rows(output_dir / "ground_truth.csv", truth_rows)
    (output_dir / "ground_truth.json").write_text(
        json.dumps(truth_rows, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "input.json").write_text(
        json.dumps(input_json, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "derived.json").write_text(
        json.dumps(derived_json, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "seed.sql").write_text(
        "\n".join(connection.iterdump()) + "\n",
        encoding="utf-8",
    )
    (output_dir / "ground_truth.sql").write_text(
        _ground_truth_sql(truth_rows),
        encoding="utf-8",
    )
    documents_dir = output_dir / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        documents_dir / "invoice_documents.zip",
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for document in sorted(document_rows, key=lambda row: row["document_name"]):
            entry = zipfile.ZipInfo(document["document_name"], date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, document["content"].encode("utf-8"))
    (output_dir / "validation.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "source_manifest.json").write_text(
        json.dumps(
            {
                "provenance": "Entirely synthetic; generated locally by this project.",
                "external_sources": [],
                "license": "MIT (see ../../LICENSE)",
                "generator": "src.generator",
                "seed": seed,
                "scale": scale,
                "rules": {
                    "invoice_mix": rules["invoice_mix"],
                    "tolerances": rules["tolerances"],
                    "tax_rates": rules["tax_rates"],
                    "line_generation": rules["line_generation"],
                    "po_line_count_distribution": rules["po_line_count_distribution"],
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    database_path = output_dir / "dataset.sqlite"
    if database_path.exists():
        database_path.unlink()
    database = sqlite3.connect(database_path)
    connection.backup(database)
    database.close()
    files = sorted(
        path for path in output_dir.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    )
    checksums = {
        path.relative_to(output_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "seed": seed,
                "scale": scale,
                "row_counts": counts,
                "table_row_counts": summary["counts"],
                "checksums_sha256": checksums,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def generate_dataset(
    output_dir: Path,
    *,
    scale: float = 1.0,
    seed: int | None = None,
) -> dict[str, Any]:
    """Generate, evaluate, validate and export a fully reproducible dataset."""
    rules = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8"))
    actual_seed = int(rules["seed"] if seed is None else seed)
    counts = _counts_for_scale(rules, scale)
    rng = random.Random(actual_seed)
    connection = _connect()
    try:
        truth_rows, document_rows = _insert_generated_records(
            connection,
            rng,
            counts,
            rules,
            scale,
        )
        evaluate_invoices(connection)
        expected_table_counts = {
            "vendor": counts["vendors"],
            "purchase_order": counts["purchase_orders"],
            "purchase_order_line": counts["purchase_order_lines"],
            "goods_receipt": counts["goods_receipts"],
            "invoice": counts["invoices"],
            "invoice_extraction": counts["invoices"],
        }
        summary = validate_database(connection, truth_rows, expected_table_counts)
        if not summary["passed"]:
            raise RuntimeError("Generated dataset failed validation: " + "; ".join(summary["errors"]))
        connection.commit()
        output_dir.mkdir(parents=True, exist_ok=True)
        _export_dataset(
            connection,
            truth_rows,
            document_rows,
            output_dir,
            summary,
            actual_seed,
            scale,
            counts,
            rules,
        )
        return summary
    finally:
        connection.close()
