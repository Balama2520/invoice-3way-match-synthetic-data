"""Static, self-contained decision and exception report generation."""

from __future__ import annotations

import html
import sqlite3
from pathlib import Path
from typing import Any


CHECK_LABELS = (
    ("vendor_ok", "Vendor is not active"),
    ("date_ok", "Invoice date / receipt date issue"),
    ("arithmetic_ok", "Invoice totals do not reconcile"),
    ("receipt_found", "No posted goods receipt"),
    ("tax_ok", "Tax code or tax amount mismatch"),
    ("price_ok", "Price exceeds tolerance"),
    ("quantity_ok", "Invoice quantity mismatch"),
)


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _format_currency(value: Any, currency: str) -> str:
    return f"{currency} {float(value):,.2f}"


def create_dashboard(
    connection: sqlite3.Connection,
    validation: dict[str, Any],
    output_path: Path,
) -> None:
    """Write an offline HTML dashboard with all invoices that need attention."""
    connection.row_factory = sqlite3.Row
    exceptions = connection.execute(
        "SELECT i.invoice_id,i.vendor_id,i.vendor_invoice_number,i.po_id,"
        "i.invoice_date,i.total_amount,i.currency,i.source_channel,"
        "m.decision,m.route,m.is_duplicate,m.vendor_ok,m.date_ok,m.arithmetic_ok,"
        "m.receipt_found,m.price_ok,m.quantity_ok,m.tax_ok,m.payment_ready,"
        "e.document_name,e.document_sha256,s.current_status,"
        "(SELECT COUNT(*) FROM invoice_line il WHERE il.invoice_id=i.invoice_id) AS line_count "
        "FROM invoice i "
        "JOIN invoice_match_result m ON m.invoice_id=i.invoice_id "
        "JOIN invoice_extraction e ON e.invoice_id=i.invoice_id "
        "LEFT JOIN v_invoice_current_status s ON s.invoice_id=i.invoice_id "
        "WHERE m.decision<>'MATCHED' "
        "ORDER BY m.decision,i.invoice_id"
    ).fetchall()
    counts = validation["counts"]
    metrics = validation["metrics"]
    total_invoices = counts["invoice"]
    exception_count = len(exceptions)
    payment_ready_count = metrics["payment_ready_count"]

    distribution_rows = "".join(
        "<tr><th scope=\"row\">{}</th><td>{:,}</td></tr>".format(
            _escape(decision), count
        )
        for decision, count in sorted(metrics["decision_distribution"].items())
    )
    table_rows: list[str] = []
    for row in exceptions:
        reasons = ["Duplicate invoice submission"] if row["is_duplicate"] else []
        reasons.extend(
            label
            for column, label in CHECK_LABELS
            if not row[column]
        )
        if not reasons:
            reasons = ["Review required by match policy"]
        row_class = "blocked" if row["route"] == "blocked" else "exception"
        table_rows.append(
            "<tr class=\"{}\">"
            "<td><code>{}</code></td>"
            "<td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}</td><td>{}</td>"
            "<td><strong>{}</strong><br>{}</td>"
            "<td>{}</td><td>{}</td><td>{}</td><td><code>{}</code></td>"
            "</tr>".format(
                row_class,
                _escape(row["invoice_id"]),
                _escape(row["vendor_id"]),
                _escape(row["vendor_invoice_number"]),
                _escape(row["po_id"] or "Non-PO"),
                _escape(row["invoice_date"]),
                _escape(_format_currency(row["total_amount"], row["currency"])),
                _escape(row["decision"]),
                _escape("; ".join(reasons)),
                _escape(row["route"]),
                _escape(row["current_status"]),
                int(row["line_count"]),
                _escape(row["document_sha256"]),
            )
        )

    accuracy_metrics = (
        ("Decision accuracy", "decision_accuracy"),
        ("Route accuracy", "route_accuracy"),
        ("Extraction field accuracy", "extraction_field_accuracy"),
        ("Approval policy accuracy", "approval_policy_accuracy"),
        ("Payment policy accuracy", "payment_policy_accuracy"),
        ("Duplicate precision", "duplicate_precision"),
        ("Duplicate recall", "duplicate_recall"),
    )
    metric_cards = "".join(
        "<div class=\"card\"><span>{}</span><strong>{:.1%}</strong></div>".format(
            _escape(label), float(metrics[key])
        )
        for label, key in accuracy_metrics
    )

    page = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Invoice matching review</title>
  <style>
    :root {{ color-scheme: light; font-family: Segoe UI, Arial, sans-serif; color: #172033; background: #f3f6fb; }}
    body {{ margin: 0; }}
    main {{ max-width: 1440px; margin: auto; padding: 28px; }}
    h1 {{ margin-bottom: 4px; }}
    .muted {{ color: #58677e; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(170px,1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ background: white; border: 1px solid #dfe6f0; border-radius: 10px; padding: 16px; }}
    .card span {{ display: block; color: #58677e; font-size: 13px; }}
    .card strong {{ display: block; margin-top: 8px; font-size: 24px; }}
    section {{ background: white; border: 1px solid #dfe6f0; border-radius: 10px; padding: 18px; margin: 16px 0; }}
    .layout {{ display: grid; grid-template-columns: minmax(260px,1fr) 2fr; gap: 16px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th,td {{ text-align: left; vertical-align: top; border-bottom: 1px solid #e8edf4; padding: 9px; }}
    th {{ background: #f7f9fc; position: sticky; top: 0; }}
    .table-wrap {{ max-height: 65vh; overflow: auto; }}
    .blocked strong {{ color: #a12622; }}
    .exception strong {{ color: #955600; }}
    code {{ font-size: 12px; overflow-wrap: anywhere; }}
    input {{ width: min(100%,520px); padding: 10px; border: 1px solid #bbc8d8; border-radius: 6px; }}
    a {{ color: #185abd; }}
    @media (max-width: 760px) {{ main {{ padding: 14px; }} .layout {{ grid-template-columns: 1fr; }} section {{ overflow-x: auto; }} }}
    @media print {{ body {{ background: white; }} section {{ break-inside: avoid; }} .table-wrap {{ max-height: none; overflow: visible; }} }}
  </style>
</head>
<body>
<main>
  <h1>Invoice matching review</h1>
  <p class="muted">Seeded synthetic run. Exception reasons come from match checks; the report contains no per-invoice ground-truth labels.</p>
  <div class="cards">
    <div class="card"><span>Total invoices</span><strong>{total:,}</strong></div>
    <div class="card"><span>Exceptions to review</span><strong>{exceptions:,}</strong></div>
    <div class="card"><span>Payment-ready</span><strong>{ready:,}</strong></div>
    <div class="card"><span>PO lines (1-8 per PO)</span><strong>{po_lines:,}</strong></div>
    <div class="card"><span>Receipts</span><strong>{receipts:,}</strong></div>
    {metric_cards}
  </div>
  <div class="layout">
    <section>
      <h2>Decision distribution</h2>
      <table><thead><tr><th>Decision</th><th>Invoices</th></tr></thead>
      <tbody>{distribution_rows}</tbody></table>
    </section>
    <section>
      <h2>Validation summary</h2>
      <p>Validation: <strong>{validation_state}</strong></p>
      <p>Duplicate pairs identified correctly: <strong>{duplicate_pairs:,}</strong></p>
      <p>Blocked invoices marked payment-ready: <strong>{blocked_ready}</strong></p>
      <p><a href="validation.json">Full validation metrics</a> ·
      <a href="derived/invoice_match_result.csv">Match results CSV</a> ·
      <a href="input/invoice_extraction.csv">Extraction trace CSV</a> ·
      <a href="documents/invoice_documents.zip">Source invoice documents</a></p>
    </section>
  </div>
  <section>
    <h2>Exceptions</h2>
    <p class="muted">Search by invoice, vendor, PO, decision, route, or source-document hash.</p>
    <label for="filter">Filter exceptions</label><br>
    <input id="filter" type="search" placeholder="Type to filter the exception list">
    <p id="visible-count" class="muted">{exceptions:,} exceptions shown</p>
    <div class="table-wrap">
      <table id="exceptions">
        <thead><tr><th>Invoice</th><th>Vendor</th><th>Vendor invoice #</th><th>PO</th>
        <th>Invoice date</th><th>Total</th><th>Decision / failed checks</th>
        <th>Route</th><th>Current status</th><th>Lines</th><th>Source SHA-256</th></tr></thead>
        <tbody>{exception_rows}</tbody>
      </table>
    </div>
  </section>
  <p class="muted">Source text is parsed by the documented deterministic intake parser. This report does not perform OCR.</p>
</main>
<script>
  const filter = document.getElementById("filter");
  const rows = Array.from(document.querySelectorAll("#exceptions tbody tr"));
  const visibleCount = document.getElementById("visible-count");
  filter.addEventListener("input", () => {{
    const query = filter.value.toLocaleLowerCase();
    let visible = 0;
    for (const row of rows) {{
      const show = row.textContent.toLocaleLowerCase().includes(query);
      row.hidden = !show;
      if (show) visible += 1;
    }}
    visibleCount.textContent = `${{visible.toLocaleString()}} exceptions shown`;
  }});
</script>
</body>
</html>
""".format(
        total=total_invoices,
        exceptions=exception_count,
        ready=payment_ready_count,
        po_lines=counts["purchase_order_line"],
        receipts=counts["goods_receipt"],
        metric_cards=metric_cards,
        distribution_rows=distribution_rows,
        validation_state="passed" if validation["passed"] else "failed",
        duplicate_pairs=metrics["duplicate_true_positives"],
        blocked_ready=metrics["blocked_payment_ready_count"],
        exception_rows="".join(table_rows),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(page, encoding="utf-8")

