# Invoice 3-Way Match

**Synthetic data generator, invoice intake, matching engine, and workflow validation**

Prepared for the **YottaFlex Associate AI Forward Deployed Engineer challenge**.
This project demonstrates an auditable invoice-to-purchase-order-to-goods-receipt
workflow using reproducible synthetic data.

> **Scope:** This is a local, rule-based prototype. It is not an OCR product, a
> production integration, or a payment service. All records are synthetic.

## What the project does

The pipeline creates linked procurement and invoice records, parses generated
plain-text invoices, applies configurable three-way-match rules, routes
exceptions, and records approval and payment-readiness decisions. It exports
operational data separately from ground-truth labels so the results can be
evaluated without exposing labels to the matcher.

The workflow is designed to make decisions inspectable: match checks, routes,
status events, source-document hashes, evaluation results, and generated
artifacts are retained for review.

## Quick start

### Requirements

- Python 3.10 or later
- `pytest` for tests; runtime data generation and matching use the Python
  standard library and SQLite

From the repository root, install the test dependency and generate the baseline
dataset:

```shell
python -m pip install -r requirements.txt
python -m src generate --output data/generated --seed 20261022
```

Run the test suite:

```shell
python -m pytest -q
```

On Windows, run the additional release checks:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify.ps1
```

The verification script runs the tests, generates the same seeded dataset twice,
compares fresh output with the checked-in dataset, checks CSV headers for label
leakage, and validates the main table row counts. It exits with a non-zero status
if a check fails.

### Extract invoice documents

The generated archive contains the project's supported plain-text invoice
format. Extract it with the same parser:

```shell
python -m src extract --input data/generated/documents/invoice_documents.zip --output data/extracted.json
```

The command reports the number of extracted invoices and writes the extraction
JSON to the requested path.

## Baseline dataset

The default scale (`1.0`) produces:

| Entity | Records |
|---|---:|
| Vendors | 300 |
| Purchase orders | 1,500 |
| Purchase order lines | 2,200 |
| Goods receipts | 1,800 |
| Invoices | 2,000 |

Use `--scale 0.1` for a 10% starter dataset. Supported scale values are `0.01`
through `10`. The baseline seed is `20261022`; both the seed and scale are
configurable.

The invoice issue distribution is generated from deterministic quotas:

| Primary issue | Share | Baseline records |
|---|---:|---:|
| Clean | 70% | 1,400 |
| Price variance | 10% | 200 |
| Quantity mismatch | 8% | 160 |
| Duplicate | 5% | 100 |
| Missing receipt | 4% | 80 |
| Tax or vendor error | 3% | 60 |

Required edge cases are labelled in the separate ground-truth exports. They
include duplicate submissions, arithmetic and date anomalies, partial delivery,
closed vendors, tax mismatches, and invoices exceeding PO quantities.

## Matching and workflow policy

The matcher evaluates invoice lines against purchase-order lines and posted
receipt quantities. The configured policy includes:

- Price variance of **2.00% or less** is accepted; the boundary is included.
- Tax code and calculated tax must agree with the PO line.
- Invoice quantity must equal the PO line quantity and must not exceed the
  quantity received.
- Duplicate invoices and closed vendors are blocked. Missing purchase-order or
  receipt conditions are held for the requester; other exceptions route to
  operations.
- Only `MATCHED` invoices receive approval events and scheduled payment records.
  Invoices above INR 100,000 require L1 and L2 approval; other eligible invoices
  require L1. Payments are scheduled seven days after approval.
- A payment record means **scheduled and payment-ready**, not released or paid.

Possible decisions include `MATCHED`, `PRICE_VARIANCE`, `QUANTITY_MISMATCH`,
`DUPLICATE`, `MISSING_RECEIPT`, `TAX_MISMATCH`, `CLOSED_VENDOR`,
`ARITHMETIC_ANOMALY`, and `DATE_ANOMALY`.

Decision precedence, tax rates, tolerances, approval thresholds, and generation
settings are defined in [`config/rules.json`](config/rules.json). The field
definitions and exact defect-to-decision mapping are in the
[data dictionary](docs/data_dictionary.md).

## Data model and traceability

The nine core entities are Vendor, PurchaseOrder, PurchaseOrderLine,
GoodsReceipt, GoodsReceiptLine, Invoice, InvoiceLine, Approval, and Payment.
The schema also includes extraction records, match results, an invoice-line to
PO-line bridge, and lifecycle, approval, and payment event histories.

Primary keys and foreign keys connect the operational records. Ground-truth
labels are exported separately and are not stored in the operational SQLite
database or passed to the matcher.

See the [ER diagram](docs/er_diagram.md) for cardinalities and the
[assumptions](docs/assumptions.md) for policy decisions and generation details.

## Evaluation and validation

The generator writes `data/generated/validation.json`, which includes integrity
checks and synthetic evaluation metrics for decisions, routes, extraction,
approval/payment policy, and duplicate pairs. On the baseline dataset, the
checked-in report records 100% agreement with the generated labels. Since the
generator and matcher share documented rules and the data is synthetic, this is
a consistency check—not evidence of accuracy on real invoices.

Validation covers key uniqueness, foreign-key integrity, arithmetic, dates,
lifecycle transitions, duplicate handling, approval/payment eligibility, and
ground-truth isolation. The test suite also checks the configured price boundary
and required exception cases.

## Generated artifacts

The checked-in baseline is under [`data/generated/`](data/generated/):

- `dataset.sqlite` and `seed.sql`: operational schema, source records, events,
  and derived match results.
- `input/` and `derived/`: CSV exports of operational inputs and matcher output.
- `input.json` and `derived.json`: corresponding JSON exports.
- `documents/invoice_documents.zip`: generated plain-text invoice source files.
- `dashboard.html`: offline dashboard for reviewing metrics and exceptions.
- `ground_truth.csv`, `ground_truth.json`, and `ground_truth.sql`: isolated
  evaluation labels; these are not model input features.
- `validation.json`, `manifest.json`, and `source_manifest.json`: validation
  results, generated-data checksums, and synthetic provenance.

You can also open the [hosted dashboard](https://invoice-3way-match-synthetic-data.netlify.app/).

## Reproducibility

Repeated generation with the same seed and scale has been verified byte-for-byte
on Windows with Python 3.14, including comparison with the checked-in baseline.
Byte identity across operating systems, Python versions, SQLite versions, or
compression libraries is **not guaranteed**. The manifest checksums describe
the generated files; use same-environment comparisons when verifying output.

## Project structure

| Path | Purpose |
|---|---|
| `src/generator.py` | Seeded master, transaction, and labelled anomaly generation |
| `src/intake.py` | Plain-text invoice parsing and extraction provenance |
| `src/matcher.py` | Matching decisions, exception routes, and workflow records |
| `src/validation.py` | Referential, arithmetic, lifecycle, and policy validation |
| `src/schema.sql` | Operational SQLite schema |
| `src/schema_ground_truth.sql` | Separate ground-truth schema |
| `config/rules.json` | Dataset scale, issue mix, tax, tolerances, and approval rules |
| `tests/` | Unit and pipeline acceptance tests |
| `scripts/verify.ps1` | Windows release verification |
| `docs/` | Data dictionary, ER diagram, assumptions, and limitations |
| `ai_usage/` | AI-use disclosure and prompt-history notes |

## Limitations

- Intake parses the generated plain-text invoice layout. It does not perform OCR
  or read scanned PDFs or images.
- There are no live ERP, vendor portal, approval-service, or payment-rail
  integrations.
- Payments are simulated as scheduled records; funds are never transferred.
- Evaluation uses synthetic data and cannot establish performance on real
  invoices.
- The policy uses one currency (INR), fixed tax codes, and a strict quantity
  rule: invoice quantity must equal the PO line quantity and cannot exceed
  posted receipts. A partial invoice for a partial delivery is therefore routed
  as a quantity mismatch.
- Matching and ground-truth generation use the same documented policy. The
  resulting metrics measure implementation consistency, not independent
  real-world model performance.

For the complete scope and constraints, see [docs/limitations.md](docs/limitations.md).

## AI use

Claude and GitHub Copilot were used during planning, implementation, testing, and
documentation. The project's [AI-use disclosure](ai_usage/ai_disclosure.md) and
[prompt-history notes](ai_usage/prompts.md) describe that assistance.

## License

This project is released under the [MIT License](LICENSE).
