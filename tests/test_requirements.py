import csv
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from src.generator import generate_dataset
from src.matcher import evaluate_invoices


ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "data" / "generated"
GROUND_TRUTH_COLUMNS = {
    "ground_truth",
    "issue_type",
    "defect_subtype",
    "expected_decision",
    "expected_route",
    "paired_invoice_id",
    "secondary_issue_types",
    "injection_rule",
}


def _ground_truth_rows():
    with (GENERATED / "ground_truth.csv").open(
        encoding="utf-8", newline=""
    ) as source:
        return list(csv.DictReader(source))


def _file_checksums(directory):
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }


def test_full_scale_counts_and_issue_mix():
    rules = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8"))
    with sqlite3.connect(GENERATED / "dataset.sqlite") as connection:
        tables = (
            "vendor",
            "purchase_order",
            "purchase_order_line",
            "goods_receipt",
            "invoice",
        )
        actual = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }
    assert actual == {
        "vendor": 300,
        "purchase_order": 1500,
        "purchase_order_line": 2200,
        "goods_receipt": 1800,
        "invoice": 2000,
    }

    labels = _ground_truth_rows()
    assert len(labels) == 2000
    counts = {}
    for label in labels:
        counts[label["issue_type"]] = counts.get(label["issue_type"], 0) + 1
    for issue_type, expected_share in rules["invoice_mix"].items():
        actual_share = counts.get(issue_type, 0) / len(labels)
        assert abs(actual_share - expected_share) <= 0.01


def test_all_required_edge_cases_are_grounded():
    subtypes = {row["defect_subtype"] for row in _ground_truth_rows()}
    assert {
        "DUPLICATE_NUMBER",
        "TOTAL_DIFFERS_FROM_LINE_SUM",
        "DATE_BEFORE_PO",
        "PARTIAL_DELIVERY",
        "CLOSED_VENDOR",
        "TAX_MISMATCH",
        "INVOICE_EXCEEDS_PO_QTY",
        "SAME_INVOICE_TWICE",
    } <= subtypes


def test_input_and_derived_csvs_do_not_expose_ground_truth_columns():
    for folder in (GENERATED / "input", GENERATED / "derived"):
        for path in folder.glob("*.csv"):
            with path.open(encoding="utf-8", newline="") as source:
                header = {column.strip().lower() for column in next(csv.reader(source))}
            assert not any(
                column in GROUND_TRUTH_COLUMNS or "ground_truth" in column
                for column in header
            ), path


def test_matcher_does_not_read_ground_truth_labels():
    source = (ROOT / "src" / "matcher.py").read_text(encoding="utf-8")
    assert all(
        label not in source
        for label in ("ground_truth", "issue_type", "expected_decision")
    )


def test_duplicate_and_closed_vendor_invoices_cannot_be_payment_ready():
    labels = _ground_truth_rows()
    invoice_ids = [
        row["invoice_id"]
        for row in labels
        if row["defect_subtype"] in {"DUPLICATE_NUMBER", "SAME_INVOICE_TWICE", "CLOSED_VENDOR"}
    ]
    with sqlite3.connect(GENERATED / "dataset.sqlite") as connection:
        for invoice_id in invoice_ids:
            result = connection.execute(
                "SELECT decision, payment_ready FROM invoice_match_result "
                "WHERE invoice_id=?",
                (invoice_id,),
            ).fetchone()
            payment_count = connection.execute(
                "SELECT COUNT(*) FROM payment WHERE invoice_id=?", (invoice_id,)
            ).fetchone()[0]
            assert result is not None
            assert result[0] in {"DUPLICATE", "CLOSED_VENDOR"}
            assert result[1] == 0
            assert payment_count == 0


def test_every_payment_has_approved_approval_for_matched_invoice():
    with sqlite3.connect(GENERATED / "dataset.sqlite") as connection:
        payment_count = connection.execute("SELECT COUNT(*) FROM payment").fetchone()[0]
        valid_count = connection.execute(
            "SELECT COUNT(*) FROM payment p "
            "JOIN invoice_match_result m ON m.invoice_id=p.invoice_id "
            "JOIN approval a ON a.approval_id=p.approval_id "
            "JOIN approval_event e ON e.approval_id=a.approval_id "
            "WHERE m.decision='MATCHED' AND e.event_type='approved'"
        ).fetchone()[0]
    assert payment_count == 1400
    assert valid_count == payment_count


def test_entity_ids_are_unique_and_follow_documented_formats():
    formats = {
        "vendor": ("vendor_id", r"VND-\d{5}"),
        "purchase_order": ("po_id", r"PO-2026-\d{6}"),
        "purchase_order_line": ("po_line_id", r"PO-2026-\d{6}-L\d{2}"),
        "goods_receipt": ("grn_id", r"GRN-2026-\d{6}"),
        "goods_receipt_line": ("grn_line_id", r"GRN-2026-\d{6}-L\d{2}"),
        "invoice": ("invoice_id", r"INV-\d{5}-2026-\d{6}"),
        "invoice_line": ("invoice_line_id", r"INVL-INV-\d{5}-2026-\d{6}-\d{2}"),
        "invoice_extraction": ("extraction_id", r"EXT-INV-\d{5}-2026-\d{6}"),
        "approval": ("approval_id", r"APR-2026-\d{6}"),
        "payment": ("payment_id", r"PAY-2026-\d{6}"),
    }
    with sqlite3.connect(GENERATED / "dataset.sqlite") as connection:
        for table, (column, pattern) in formats.items():
            values = [
                row[0]
                for row in connection.execute(f"SELECT {column} FROM {table}")
            ]
            assert len(values) == len(set(values)), table
            assert all(re.fullmatch(pattern, value) for value in values), table
        for table in ("invoice_status_event", "approval_event", "payment_event"):
            values = [
                row[0] for row in connection.execute(f"SELECT event_id FROM {table}")
            ]
            assert len(values) == len(set(values)), table
        for table, column in (
            ("invoice_match_result", "invoice_id"),
            ("invoice_line_po_line_match", "match_id"),
        ):
            values = [row[0] for row in connection.execute(f"SELECT {column} FROM {table}")]
            assert len(values) == len(set(values)), table


def test_same_seed_repeats_and_different_seed_changes_output(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    different = tmp_path / "different"
    generate_dataset(first, scale=0.05, seed=20261022)
    generate_dataset(second, scale=0.05, seed=20261022)
    generate_dataset(different, scale=0.05, seed=20261023)

    first_checksums = _file_checksums(first)
    assert first_checksums == _file_checksums(second)
    assert first_checksums != _file_checksums(different)


def _decision_for_invoice_price(price):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript((ROOT / "src" / "schema.sql").read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO vendor VALUES (?,?,?,?,?,?,?)",
        ("VND-00001", "00001", "Synthetic Vendor", "active", 30, "2026-01-01", None),
    )
    connection.execute(
        "INSERT INTO purchase_order VALUES (?,?,?,?,?,?,?)",
        ("PO-2026-000001", "VND-00001", "2026-01-01", "received", "INR", "REQ-001", 1000),
    )
    connection.execute(
        "INSERT INTO purchase_order_line VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("PO-2026-000001-L01", "PO-2026-000001", 1, "SKU-0001", "Test item", 10, 100, "T0", 1000, 0, 1000),
    )
    connection.execute(
        "INSERT INTO goods_receipt VALUES (?,?,?,?)",
        ("GRN-2026-000001", "PO-2026-000001", "2026-01-02", "posted"),
    )
    connection.execute(
        "INSERT INTO goods_receipt_line VALUES (?,?,?,?,?)",
        ("GRN-2026-000001-L01", "GRN-2026-000001", 1, "PO-2026-000001-L01", 10),
    )
    invoice_id = "INV-00001-2026-000001"
    line_net = price * 10
    connection.execute(
        "INSERT INTO invoice VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (invoice_id, "VND-00001", "PO-2026-000001", "V-1", "2026-01-02", "2026-01-03", "INR", line_net, 0, line_net, "email"),
    )
    connection.execute(
        "INSERT INTO invoice_line VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("INVL-" + invoice_id + "-01", invoice_id, 1, "PO-2026-000001-L01", "SKU-0001", "Test item", 10, price, "T0", line_net, 0, line_net),
    )
    evaluate_invoices(connection)
    decision = connection.execute(
        "SELECT decision FROM invoice_match_result WHERE invoice_id=?", (invoice_id,)
    ).fetchone()[0]
    connection.close()
    return decision


def test_price_tolerance_boundary_is_inclusive():
    assert _decision_for_invoice_price(102.00) == "MATCHED"
    assert _decision_for_invoice_price(102.01) == "PRICE_VARIANCE"
