# Invoice 3-Way Match: Synthetic Data, Decision Engine and Validation

A reproducible, local-only invoice intake and three-way-match workflow. The project
generates linked vendor, purchase order, receipt and invoice records; evaluates
invoices against explicit rules; routes exceptions; creates approval and payment
readiness records only for eligible invoices; and exports separate ground truth
for evaluation.

## Requirements

- Python 3.10 or later
- No runtime third-party packages; SQLite and the data pipeline use the Python
  standard library
- `pytest` is used for tests

Install the test dependency from the project directory:

```powershell
python -m pip install -r requirements.txt
```

## Generate the full dataset

From the project directory:

```powershell
python -m src generate --output data/generated --seed 20261022
```

The default scale (`1.0`) produces 300 vendors, 1,500 purchase orders, 2,200 PO
lines, 1,800 goods receipts and 2,000 invoices. Use `--scale 0.1` for a 10% starter
dataset, or a value from `0.01` through `10` for another scale. The same seed and
scale have produced byte-identical files on the same tested Windows/Python
environment. Byte identity across operating systems or Python environments is
not guaranteed.
Open `data/generated/dashboard.html` in a browser to review metrics and filter
invoice exceptions.

The [hosted dashboard](https://invoice-3way-match-synthetic-data.netlify.app/)
is available after the static site deploys.

Run the tests:

```powershell
python -m pytest -q
```

## Verify everything

Run the release checks on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify.ps1
```

The script runs the tests, compares two seeded generations and a fresh generation
against `data/generated/`, checks input and derived CSV headers for ground-truth
labels, and verifies the main SQLite table row counts. It exits non-zero if any
check fails.

## Generated files

- `data/generated/dataset.sqlite` and `seed.sql`: input schema and records,
  workflow events and derived match results. Neither contains ground-truth labels.
- `data/generated/input/`: CSV files for entity and workflow-event tables.
- `data/generated/derived/`: CSV files for match results and invoice-to-PO-line
  allocations.
- `data/generated/documents/invoice_documents.zip`: 2,000 source invoice text
  documents used by the extraction stage; the ZIP contains no ground-truth labels.
- `data/generated/dashboard.html`: offline, searchable exception dashboard with
  failed checks, routes, lifecycle status, source hashes and aggregate metrics.
- `data/generated/input.json` and `derived.json`: corresponding JSON exports.
- `data/generated/ground_truth.csv`, `ground_truth.json`, and
  `ground_truth.sql`: isolated labels; do not use these as model input features.
- `data/generated/validation.json`: integrity checks, decision/route accuracy,
  extraction-field accuracy, approval/payment policy accuracy, duplicate
  precision/recall, issue distribution, confusion matrix and payment-readiness
  metrics.
- `data/generated/manifest.json`: row counts and SHA-256 checksums for exported
  core records and all database tables; `source_manifest.json` documents
  synthetic provenance.

All records are synthetic. No external, personal or confidential data is used.
The generator is deterministic and uses `config/rules.json` for the seed, scale
baseline, line quantities/prices, tolerances, tax rates, approval threshold and
receipt mix.

Extract the generated text documents independently with the same parser:

```powershell
python -m src extract --input data/generated/documents/invoice_documents.zip --output data/extracted.json
```

`src.intake` parses the project's documented text-invoice layout and persists the
extracted fields, source-document hash, and filename in the one-to-one
`invoice_extraction` table. This is deterministic text parsing, **not OCR**; a
production system would need an OCR or document-AI adapter to convert scanned
PDFs/images into this layout before extraction.

## Matching and workflow policy

Price variance is accepted at or below 2.00%; the boundary is included. Tax code
and calculated tax must match the PO line. An invoice quantity must equal the PO
line quantity and not exceed posted receipt quantity. A missing PO or posted
receipt is held for the requester. Duplicate submissions and closed vendors are
blocked. Arithmetic and date anomalies, tax mismatches, price variances and
quantity mismatches route to operations analysis.

Decision precedence is `DUPLICATE`, `CLOSED_VENDOR`, `ARITHMETIC_ANOMALY`,
`DATE_ANOMALY`, `MISSING_RECEIPT`, `TAX_MISMATCH`, `PRICE_VARIANCE`,
`QUANTITY_MISMATCH`, then `MATCHED`. Only `MATCHED` invoices receive approval
events and a scheduled payment. Invoices above INR 100,000 require L1 and L2
approval; others require L1. A payment-ready invoice has an approved event and a
scheduled payment, not a released payment.

The injected invoice distribution at full scale is 70% clean, 10% price
variance, 8% quantity mismatch, 5% duplicate, 4% missing receipt and 3%
tax/vendor errors. Tax/vendor cases are deterministically split among tax
mismatch, closed vendor, invoice-total arithmetic error and invoice date before
PO date. The exact subtype and expected decision/route are documented in
`docs/data_dictionary.md`.

## Project map

- `src/schema.sql`: input and derived SQLite schema; ground truth is excluded.
- `src/schema_ground_truth.sql`: separate ground-truth schema.
- `src/generator.py`: seeded master, transaction and anomaly generation plus
  CSV/JSON/SQL/SQLite exports.
- `src/intake.py`: source-document format, field extraction, input validation,
  and file/directory/ZIP intake.
- `src/matcher.py`: three-way matching, exception routes and lifecycle/approval/
  payment events.
- `src/validation.py`: joins, arithmetic, dates, lifecycle, eligibility and
  evaluation metrics.
- `docs/data_dictionary.md`, `docs/er_diagram.md`, `docs/assumptions.md`: schema,
  cardinalities, lifecycle and generation assumptions.
- `docs/limitations.md`: supported inputs, integration boundaries and
  reproducibility scope.

## AI usage

AI assistance was used during development. See
[`ai_usage/ai_disclosure.md`](ai_usage/ai_disclosure.md) and
[`ai_usage/prompts.md`](ai_usage/prompts.md) for the disclosure and prompt log.
The disclosure and the prompt log are my own records.

## How I verified it
- Tests: `python -m pytest -q` runs 28 tests. They pass on Windows (Python 3.14) and
  in a fresh Linux clone (Python 3.12).
- Release checks: `scripts/verify.ps1` (see "Verify everything") compares two
  same-seed generations and a fresh generation with the committed data.
- Label leakage: no ground-truth column appears in any input or derived CSV header,
  and `src/matcher.py` never reads ground-truth labels (both covered by tests).
- Platform note: text exports use the operating system's line endings and the
  SQLite and zip files can differ between platforms, so the checksums in
  `manifest.json` are for Windows output. On another operating system, compare two
  runs made on that system.

## Limitations
See docs/limitations.md. In short: synthetic plain-text invoices (not OCR), rules
shared by the generator and matcher, no ERP or payment integration, payments
scheduled not released.
