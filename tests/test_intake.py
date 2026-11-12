import zipfile

import pytest

from src.intake import extract_invoice_document, extract_invoice_source, format_invoice_document


INVOICE_ID = "INV-00001-2026-000001"


def sample_document():
    invoice = {
        "vendor_id": "VND-00001",
        "po_id": "PO-2026-000001",
        "vendor_invoice_number": "BILL-00001-000001",
        "invoice_date": "2026-02-01",
        "received_date": "2026-02-02",
        "currency": "INR",
        "source_channel": "email",
        "net_amount": 100,
        "tax_amount": 5,
        "total_amount": 105,
    }
    lines = [
        {
            "po_line_id": "PO-2026-000001-L01",
            "sku": "SKU-0001",
            "description": "fasteners | zinc-coated",
            "quantity": 2,
            "unit_price": 50,
            "tax_code": "T5",
        }
    ]
    return format_invoice_document(invoice, lines)


def test_extracts_invoice_header_and_quoted_line_fields():
    extracted = extract_invoice_document(sample_document(), INVOICE_ID)

    assert extracted["vendor_id"] == "VND-00001"
    assert extracted["po_id"] == "PO-2026-000001"
    assert extracted["total_amount"] == "105.00"
    assert extracted["lines"] == [
        {
            "line_number": 1,
            "po_line_id": "PO-2026-000001-L01",
            "sku": "SKU-0001",
            "description": "fasteners | zinc-coated",
            "quantity": 2,
            "unit_price": "50.00",
            "tax_code": "T5",
        }
    ]


def test_file_and_zip_intake_use_filename_as_immutable_invoice_id(tmp_path):
    invoice_path = tmp_path / f"{INVOICE_ID}.txt"
    invoice_path.write_text(sample_document(), encoding="utf-8")
    assert extract_invoice_source(invoice_path)[0]["invoice_id"] == INVOICE_ID

    archive_path = tmp_path / "invoices.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(invoice_path, arcname=f"invoices/{invoice_path.name}")
    assert extract_invoice_source(archive_path)[0]["invoice_id"] == INVOICE_ID


def test_rejects_malformed_or_unsupported_invoice_fields():
    malformed = sample_document().replace("Currency: INR", "Currency: USD")
    with pytest.raises(ValueError, match="unsupported currency"):
        extract_invoice_document(malformed, INVOICE_ID)


def test_rejects_invoice_id_vendor_mismatch():
    with pytest.raises(ValueError, match="vendor does not match"):
        extract_invoice_document(sample_document(), "INV-00002-2026-000001")


def test_extracts_invoice_without_optional_po_reference():
    document = sample_document().replace(
        "Purchase Order ID: PO-2026-000001",
        "Purchase Order ID: NONE",
    )

    extracted = extract_invoice_document(document, INVOICE_ID)

    assert extracted["po_id"] is None
