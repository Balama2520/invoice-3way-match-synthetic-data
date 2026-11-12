import csv
import hashlib
import json
import sqlite3
import zipfile

import pytest

from src.generator import generate_dataset
from src.intake import extract_invoice_source


def test_starter_scale_generates_expected_volume_and_separate_labels(tmp_path):
    output = tmp_path / "run"
    summary = generate_dataset(output, scale=0.05)

    assert summary["passed"]
    assert summary["counts"]["vendor"] == 15
    assert summary["counts"]["purchase_order"] == 75
    assert summary["counts"]["purchase_order_line"] == 110
    assert summary["counts"]["goods_receipt"] == 90
    assert summary["counts"]["invoice"] == 100
    assert summary["counts"]["invoice_extraction"] == 100
    assert summary["metrics"]["decision_accuracy"] == 1.0
    assert summary["metrics"]["extraction_field_accuracy"] == 1.0
    assert summary["metrics"]["route_accuracy"] == 1.0
    assert summary["metrics"]["duplicate_precision"] == 1.0
    assert summary["metrics"]["duplicate_recall"] == 1.0
    assert summary["metrics"]["duplicate_pair_accuracy"] == 1.0
    assert summary["metrics"]["blocked_payment_ready_count"] == 0

    connection = sqlite3.connect(output / "dataset.sqlite")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ground_truth" not in tables
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    document_hashes = dict(
        connection.execute(
            "SELECT document_name,document_sha256 FROM invoice_extraction"
        ).fetchall()
    )
    connection.close()
    assert (output / "ground_truth.csv").is_file()
    assert (output / "ground_truth.json").is_file()
    assert (output / "ground_truth.sql").is_file()
    dashboard = (output / "dashboard.html").read_text(encoding="utf-8")
    assert "Exceptions to review" in dashboard
    assert "Source SHA-256" in dashboard
    assert "Duplicate invoice submission" in dashboard
    assert "defect_subtype" not in dashboard
    assert "paired_invoice_id" not in dashboard
    documents = extract_invoice_source(output / "documents" / "invoice_documents.zip")
    assert len(documents) == 100
    with zipfile.ZipFile(output / "documents" / "invoice_documents.zip") as archive:
        assert all(
            hashlib.sha256(archive.read(name)).hexdigest() == digest
            for name, digest in document_hashes.items()
        )

    labels = list(csv.DictReader((output / "ground_truth.csv").open(encoding="utf-8")))
    counts = {}
    for label in labels:
        counts[label["issue_type"]] = counts.get(label["issue_type"], 0) + 1
    assert counts == {
        "clean": 70,
        "duplicate": 5,
        "missing_receipt": 4,
        "price_variance": 10,
        "quantity_mismatch": 8,
        "tax_vendor_error": 3,
    }


def test_same_seed_has_identical_export_checksums(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate_dataset(first, scale=0.05, seed=1234)
    generate_dataset(second, scale=0.05, seed=1234)

    def checksums(directory):
        return {
            path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*")
            if path.is_file()
        }

    expected = checksums(first)
    assert expected == checksums(second)
    generate_dataset(first, scale=0.05, seed=1234)
    assert expected == checksums(first)


def test_all_documented_anomalies_are_present_at_full_scale(tmp_path):
    output = tmp_path / "full"
    summary = generate_dataset(output, seed=20261022)
    assert summary["counts"]["vendor"] == 300
    assert summary["counts"]["purchase_order"] == 1500
    assert summary["counts"]["purchase_order_line"] == 2200
    assert summary["counts"]["goods_receipt"] == 1800
    assert summary["counts"]["invoice"] == 2000
    assert summary["counts"]["invoice_extraction"] == 2000
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["table_row_counts"]["invoice_extraction"] == 2000
    assert summary["metrics"]["decision_accuracy"] == 1.0
    assert summary["metrics"]["extraction_field_accuracy"] == 1.0
    assert summary["metrics"]["route_accuracy"] == 1.0
    assert summary["metrics"]["duplicate_precision"] == 1.0
    assert summary["metrics"]["duplicate_recall"] == 1.0
    assert summary["metrics"]["duplicate_pair_accuracy"] == 1.0
    assert summary["metrics"]["issue_distribution"] == {
        "clean": 1400,
        "duplicate": 100,
        "missing_receipt": 80,
        "price_variance": 200,
        "quantity_mismatch": 160,
        "tax_vendor_error": 60,
    }
    labels = json.loads((output / "ground_truth.json").read_text(encoding="utf-8"))
    subtypes = {row["defect_subtype"] for row in labels}
    assert {
        "BOUNDARY_CLEAN",
        "PRICE_OUTSIDE_TOLERANCE",
        "PARTIAL_DELIVERY",
        "INVOICE_EXCEEDS_PO_QTY",
        "DUPLICATE_NUMBER",
        "SAME_INVOICE_TWICE",
        "NO_RECEIPT",
        "TAX_MISMATCH",
        "CLOSED_VENDOR",
        "TOTAL_DIFFERS_FROM_LINE_SUM",
        "DATE_BEFORE_PO",
    } <= subtypes
    assert len(labels) == 2000
    connection = sqlite3.connect(output / "dataset.sqlite")
    document_hashes = dict(
        connection.execute(
            "SELECT document_name,document_sha256 FROM invoice_extraction"
        ).fetchall()
    )
    line_counts = {
        row[0]
        for row in connection.execute(
            "SELECT COUNT(*) FROM purchase_order_line GROUP BY po_id"
        )
    }
    assert line_counts == set(range(1, 9))
    partial_receipts = connection.execute(
        "SELECT COUNT(*) FROM ("
        "SELECT pl.po_line_id FROM purchase_order_line pl "
        "JOIN goods_receipt_line grl ON grl.po_line_id=pl.po_line_id "
        "JOIN goods_receipt gr ON gr.grn_id=grl.grn_id AND gr.status='posted' "
        "GROUP BY pl.po_line_id,pl.quantity "
        "HAVING SUM(grl.quantity_received)>0 AND SUM(grl.quantity_received)<pl.quantity)"
    ).fetchone()[0]
    over_receipts = connection.execute(
        "SELECT COUNT(*) FROM ("
        "SELECT pl.po_line_id FROM purchase_order_line pl "
        "JOIN goods_receipt_line grl ON grl.po_line_id=pl.po_line_id "
        "JOIN goods_receipt gr ON gr.grn_id=grl.grn_id AND gr.status='posted' "
        "GROUP BY pl.po_line_id,pl.quantity "
        "HAVING SUM(grl.quantity_received)>pl.quantity)"
    ).fetchone()[0]
    connection.close()
    assert partial_receipts > 0
    assert over_receipts > 0
    with zipfile.ZipFile(output / "documents" / "invoice_documents.zip") as archive:
        assert len(archive.namelist()) == 2000
        assert all(
            hashlib.sha256(archive.read(name)).hexdigest() == digest
            for name, digest in document_hashes.items()
        )


@pytest.mark.parametrize("scale", [0, -0.5, 10.1])
def test_invalid_scale_is_rejected(tmp_path, scale):
    with pytest.raises(ValueError, match="scale must be between"):
        generate_dataset(tmp_path / "invalid", scale=scale)
