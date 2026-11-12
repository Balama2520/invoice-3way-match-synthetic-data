import sqlite3
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
CORE = ["vendor", "purchase_order", "purchase_order_line", "goods_receipt",
        "goods_receipt_line", "invoice", "invoice_line", "approval", "payment"]
EXTRA = ["invoice_status_event", "approval_event", "payment_event",
         "invoice_match_result", "invoice_line_po_line_match", "invoice_extraction"]
GROUND_TRUTH_COLUMNS = {"issue_type", "defect_subtype", "secondary_issue_types",
                        "expected_decision", "expected_route",
                        "paired_invoice_id", "injection_rule"}


def connect():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript((SRC / "schema.sql").read_text())
    return c


def table_names(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def add_vendor(c, n=1):
    c.execute("INSERT INTO vendor VALUES (?,?,?,?,?,?,?)",
              (f"VND-{n:05d}", f"{n:05d}", "Acme Supplies", "active", 30, "2026-01-01", None))


def test_all_tables_exist():
    assert set(CORE + EXTRA) <= table_names(connect())


def test_ground_truth_not_in_input_schema():
    c = connect()
    assert "ground_truth" not in table_names(c)
    for t in table_names(c):
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
        assert not (cols & GROUND_TRUTH_COLUMNS), t


def test_ground_truth_schema_loads_separately():
    c = sqlite3.connect(":memory:")
    c.executescript((SRC / "schema_ground_truth.sql").read_text())
    assert table_names(c) == {"ground_truth"}


def test_fk_rejects_unknown_vendor():
    c = connect()
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO purchase_order VALUES (?,?,?,?,?,?,?)",
                  ("PO-2026-000001", "VND-99999", "2026-01-05", "open", "INR", "REQ-001", 0))


def test_valid_vendor_then_po_inserts():
    c = connect()
    add_vendor(c)
    c.execute("INSERT INTO purchase_order VALUES (?,?,?,?,?,?,?)",
              ("PO-2026-000001", "VND-00001", "2026-01-05", "open", "INR", "REQ-001", 0))
    assert c.execute("SELECT COUNT(*) FROM purchase_order").fetchone()[0] == 1


def test_bad_vendor_status_rejected():
    c = connect()
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO vendor VALUES (?,?,?,?,?,?,?)",
                  ("VND-00001", "00001", "Acme", "deleted", 30, "2026-01-01", None))


def test_bad_id_format_rejected():
    c = connect()
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO vendor VALUES (?,?,?,?,?,?,?)",
                  ("V-1", "1", "Acme", "active", 30, "2026-01-01", None))


def test_blocked_invoice_cannot_be_payment_ready():
    c = connect()
    add_vendor(c)
    c.execute("INSERT INTO invoice VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              ("INV-00001-2026-000001", "VND-00001", None, "A-1", "2026-02-01",
               "2026-02-02", "INR", 100, 5, 105, "email"))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO invoice_match_result (invoice_id, decision, route, is_duplicate, "
            "duplicate_of_invoice_id, vendor_ok, date_ok, arithmetic_ok, receipt_found, "
            "price_ok, quantity_ok, tax_ok, blocked, payment_ready) "
            "VALUES ('INV-00001-2026-000001','DUPLICATE','blocked',1,NULL,1,1,1,1,1,1,1,1,1)")
