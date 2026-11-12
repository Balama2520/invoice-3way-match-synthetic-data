"""Deterministic text invoice formatting and field extraction.

This parser handles the project's documented plain-text invoice layout. It is
not an OCR engine; scanned documents must be OCR'd into this layout upstream.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


CENT = Decimal("0.01")
TAX_CODES = {"T0", "T5", "T12", "T18"}
SOURCE_CHANNELS = {"email", "portal", "edi"}
HEADER_FIELDS = (
    "Vendor ID",
    "Purchase Order ID",
    "Vendor Invoice Number",
    "Invoice Date",
    "Received Date",
    "Currency",
    "Source Channel",
    "Net Amount INR",
    "Tax Amount INR",
    "Total Amount INR",
)
INVOICE_ID_PATTERN = re.compile(r"INV-(\d{5})-2026-\d{6}\Z")
VENDOR_ID_PATTERN = re.compile(r"VND-\d{5}\Z")
PO_ID_PATTERN = re.compile(r"PO-2026-\d{6}\Z")
PO_LINE_ID_PATTERN = re.compile(r"PO-2026-\d{6}-L\d{2}\Z")
MAX_DOCUMENT_BYTES = 1_000_000
MAX_DOCUMENTS_PER_SOURCE = 20_000


def _money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Invalid INR amount: {value!r}.") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"INR amounts must be finite and non-negative: {value!r}.")
    return amount


def format_invoice_document(invoice: dict[str, Any], lines: list[dict[str, Any]]) -> str:
    """Render a generated invoice as a stable, human-readable source document."""
    header = {
        "Vendor ID": str(invoice["vendor_id"]),
        "Purchase Order ID": str(invoice["po_id"] or "NONE"),
        "Vendor Invoice Number": str(invoice["vendor_invoice_number"]),
        "Invoice Date": str(invoice["invoice_date"]),
        "Received Date": str(invoice["received_date"]),
        "Currency": str(invoice["currency"]),
        "Source Channel": str(invoice["source_channel"]),
        "Net Amount INR": format(_money(invoice["net_amount"]), ".2f"),
        "Tax Amount INR": format(_money(invoice["tax_amount"]), ".2f"),
        "Total Amount INR": format(_money(invoice["total_amount"]), ".2f"),
    }
    buffer = io.StringIO(newline="")
    for field in HEADER_FIELDS:
        buffer.write(f"{field}: {header[field]}\n")
    buffer.write("\nLine Items (pipe-delimited CSV):\n")
    writer = csv.writer(buffer, delimiter="|", lineterminator="\n")
    for line in lines:
        writer.writerow(
            [
                "ITEM",
                line["sku"],
                line["description"],
                line["quantity"],
                format(_money(line["unit_price"]), ".2f"),
                line["tax_code"],
                line.get("po_line_id") or "NONE",
            ]
        )
    return buffer.getvalue()


def extract_invoice_document(content: str, invoice_id: str) -> dict[str, Any]:
    """Extract and validate invoice fields from the project's text layout."""
    match = INVOICE_ID_PATTERN.fullmatch(invoice_id)
    if match is None:
        raise ValueError(f"{invoice_id!r} is not a valid invoice ID.")

    lines = content.splitlines()
    header: dict[str, str] = {}
    item_start: int | None = None
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if line == "Line Items (pipe-delimited CSV):":
            item_start = line_number
            break
        if not line:
            continue
        if ":" not in line:
            raise ValueError(f"{invoice_id}: malformed header on line {line_number}.")
        key, value = line.split(":", 1)
        key = key.strip()
        if key not in HEADER_FIELDS:
            raise ValueError(f"{invoice_id}: unknown header {key!r} on line {line_number}.")
        if key in header:
            raise ValueError(f"{invoice_id}: duplicate header {key!r}.")
        header[key] = value.strip()
    if item_start is None:
        raise ValueError(f"{invoice_id}: missing line-item section.")
    missing = sorted(set(HEADER_FIELDS) - set(header))
    if missing:
        raise ValueError(f"{invoice_id}: missing required fields: {', '.join(missing)}.")

    vendor_id = header["Vendor ID"]
    if VENDOR_ID_PATTERN.fullmatch(vendor_id) is None:
        raise ValueError(f"{invoice_id}: invalid vendor ID {vendor_id!r}.")
    if match.group(1) != vendor_id[4:]:
        raise ValueError(f"{invoice_id}: invoice ID vendor does not match {vendor_id}.")
    po_id = header["Purchase Order ID"]
    if po_id == "NONE":
        po_id = None
    elif PO_ID_PATTERN.fullmatch(po_id) is None:
        raise ValueError(f"{invoice_id}: invalid purchase order ID {po_id!r}.")
    if not header["Vendor Invoice Number"]:
        raise ValueError(f"{invoice_id}: vendor invoice number cannot be empty.")
    if header["Currency"] != "INR":
        raise ValueError(f"{invoice_id}: unsupported currency {header['Currency']!r}.")
    if header["Source Channel"] not in SOURCE_CHANNELS:
        raise ValueError(f"{invoice_id}: invalid source channel {header['Source Channel']!r}.")
    try:
        invoice_date = date.fromisoformat(header["Invoice Date"])
        received_date = date.fromisoformat(header["Received Date"])
    except ValueError as error:
        raise ValueError(f"{invoice_id}: dates must use valid YYYY-MM-DD values.") from error
    if received_date < invoice_date:
        raise ValueError(f"{invoice_id}: received date precedes invoice date.")

    extracted_lines: list[dict[str, Any]] = []
    item_section = lines[item_start:]
    reader = csv.reader(item_section, delimiter="|", strict=True)
    try:
        for line_number, row in enumerate(reader, item_start + 1):
            if not row or all(not value.strip() for value in row):
                continue
            if len(row) != 7 or row[0].strip() != "ITEM":
                raise ValueError(f"{invoice_id}: malformed line item on line {line_number}.")
            _, sku, description, quantity_text, price_text, tax_code, po_line_id = [
                value.strip() for value in row
            ]
            if not sku or not description:
                raise ValueError(f"{invoice_id}: line {line_number} needs SKU and description.")
            try:
                quantity = int(quantity_text)
            except ValueError as error:
                raise ValueError(f"{invoice_id}: invalid quantity on line {line_number}.") from error
            if quantity <= 0:
                raise ValueError(f"{invoice_id}: quantity must be positive on line {line_number}.")
            unit_price = _money(price_text)
            if unit_price == 0:
                raise ValueError(f"{invoice_id}: unit price must be positive on line {line_number}.")
            if tax_code not in TAX_CODES:
                raise ValueError(f"{invoice_id}: invalid tax code {tax_code!r} on line {line_number}.")
            if po_line_id == "NONE":
                po_line_id = None
            elif po_line_id and PO_LINE_ID_PATTERN.fullmatch(po_line_id) is None:
                raise ValueError(f"{invoice_id}: invalid PO-line reference on line {line_number}.")
            extracted_lines.append(
                {
                    "line_number": len(extracted_lines) + 1,
                    "po_line_id": po_line_id,
                    "sku": sku,
                    "description": description,
                    "quantity": quantity,
                    "unit_price": format(unit_price, ".2f"),
                    "tax_code": tax_code,
                }
            )
    except csv.Error as error:
        raise ValueError(f"{invoice_id}: malformed line-item CSV: {error}.") from error
    if not extracted_lines:
        raise ValueError(f"{invoice_id}: at least one line item is required.")

    return {
        "invoice_id": invoice_id,
        "vendor_id": vendor_id,
        "po_id": po_id,
        "vendor_invoice_number": header["Vendor Invoice Number"],
        "invoice_date": invoice_date.isoformat(),
        "received_date": received_date.isoformat(),
        "currency": header["Currency"],
        "source_channel": header["Source Channel"],
        "net_amount": format(_money(header["Net Amount INR"]), ".2f"),
        "tax_amount": format(_money(header["Tax Amount INR"]), ".2f"),
        "total_amount": format(_money(header["Total Amount INR"]), ".2f"),
        "lines": extracted_lines,
    }


def extract_invoice_file(path: Path) -> dict[str, Any]:
    """Extract a supported invoice document named ``INV-...txt``."""
    if path.suffix.lower() != ".txt":
        raise ValueError(f"Unsupported invoice document type: {path.suffix or '(no extension)'}.")
    try:
        if path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise ValueError(f"Invoice document exceeds {MAX_DOCUMENT_BYTES} bytes: {path}.")
    except OSError as error:
        raise ValueError(f"Could not inspect invoice document {path}: {error}") from error
    invoice_id = path.stem
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"Could not read invoice document {path}: {error}") from error
    return extract_invoice_document(content, invoice_id)


def extract_invoice_source(path: Path) -> list[dict[str, Any]]:
    """Extract one text file, a directory of text files, or a ZIP of text files."""
    if path.is_dir():
        files = sorted(file for file in path.glob("*.txt") if file.is_file())
        if not files:
            raise ValueError(f"No .txt invoice documents found in {path}.")
        if len(files) > MAX_DOCUMENTS_PER_SOURCE:
            raise ValueError(f"Invoice source exceeds {MAX_DOCUMENTS_PER_SOURCE} documents.")
        return [extract_invoice_file(file) for file in files]
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                names = sorted(
                    name for name in archive.namelist()
                    if not name.endswith("/") and name.lower().endswith(".txt")
                )
                if not names:
                    raise ValueError(f"No .txt invoice documents found in {path}.")
                if len(names) > MAX_DOCUMENTS_PER_SOURCE:
                    raise ValueError(f"Invoice source exceeds {MAX_DOCUMENTS_PER_SOURCE} documents.")
                results = []
                seen_ids: set[str] = set()
                for name in names:
                    info = archive.getinfo(name)
                    if info.file_size > MAX_DOCUMENT_BYTES:
                        raise ValueError(
                            f"Invoice document {name!r} exceeds {MAX_DOCUMENT_BYTES} bytes."
                        )
                    invoice_id = Path(name).stem
                    if invoice_id in seen_ids:
                        raise ValueError(f"Duplicate invoice document ID {invoice_id!r} in {path}.")
                    seen_ids.add(invoice_id)
                    try:
                        content = archive.read(name).decode("utf-8")
                    except (KeyError, RuntimeError, UnicodeError) as error:
                        raise ValueError(f"Could not read UTF-8 invoice document {name!r}: {error}") from error
                    results.append(extract_invoice_document(content, invoice_id))
                return results
        except (OSError, zipfile.BadZipFile) as error:
            raise ValueError(f"Could not read invoice ZIP {path}: {error}") from error
    return [extract_invoice_file(path)]


def parse_extracted_json(value: str) -> dict[str, Any]:
    """Parse a saved extraction payload and reject malformed JSON explicitly."""
    try:
        result = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid extracted-fields JSON: {error.msg}.") from error
    if not isinstance(result, dict):
        raise ValueError("Extracted-fields JSON must contain an object.")
    return result
