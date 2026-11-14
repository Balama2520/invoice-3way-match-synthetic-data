# Assumptions and design decisions

The brief leaves several points open. Each assumption below is deliberate.
Values come from config/rules.json (volumes, mix, tolerances, tax rates, approval).

## A1. Volumes and duplicates
Full scale (seed 20261022): 300 vendors, 1,500 POs, 2,200 PO lines, 1,800 goods
receipts, 2,000 invoices. The 100 duplicate invoices (5%) are INSIDE the 2,000.
Scale is a parameter; the same generator produces the starter and full datasets.

## A2. Issue mix and the 8 required edge cases
Allocation uses exact integer quotas, not probabilities. Each invoice has one
primary issue_type and one defect_subtype in the separate ground truth:
- clean 1,400 (NONE 1,386; BOUNDARY_CLEAN 14, price variance inside the 2% limit)
- price_variance 200 (PRICE_OUTSIDE_TOLERANCE)
- quantity_mismatch 160 (PARTIAL_DELIVERY 80; INVOICE_EXCEEDS_PO_QTY 80)
- duplicate 100 (DUPLICATE_NUMBER 50; SAME_INVOICE_TWICE 50)
- missing_receipt 80 (NO_RECEIPT)
- tax_vendor_error 60 (TAX_MISMATCH 30; CLOSED_VENDOR 15;
  TOTAL_DIFFERS_FROM_LINE_SUM 8; DATE_BEFORE_PO 7)

## A3. IDs
VND-00001, PO-2026-000001, GRN-2026-000001, INV-<vendor-code>-2026-000001 and
INVL-<invoice-id>-01. The vendor code is the numeric part of the vendor ID
(example: INV-00002-2026-000048); the invoice counter is global. IDs come from
counters, never from random values.

## A4. Duplicate key
vendor_invoice_number (printed by the vendor) is separate from invoice_id (our
immutable ID), so duplicates get a new invoice_id but repeat the vendor number.

## A5. PO lines
2,200 lines over 1,500 POs with 1 to 8 lines each. Real distribution of lines per
PO: 1:1,104, 2:222, 3:100, 4:40, 5:20, 6:8, 7:4, 8:2 (heavily skewed to 1 line).

## A6. Receipts
1,800 receipts. 80 POs have no receipt (missing-receipt scenario); 380 POs have two
receipts; the remaining 1,040 receipted POs have one. Receipt lines cover full,
partial and over-receipt quantities.

## A7. Matching tolerances (not given in the brief)
Price variance is accepted at or below 2.00% (boundary included). Money
comparisons use a 0.01 INR tolerance. Tax code and calculated tax must match the PO
line (T0 0%, T5 5%, T12 12%, T18 18%). Invoice quantity must equal the PO line
quantity and must not exceed posted receipt quantity (strict policy, see
limitations.md). Decision precedence: DUPLICATE, CLOSED_VENDOR, ARITHMETIC_ANOMALY,
DATE_ANOMALY, MISSING_RECEIPT, TAX_MISMATCH, PRICE_VARIANCE, QUANTITY_MISMATCH,
MATCHED.

## A8. The five actors
- Business requester: receives MISSING_RECEIPT holds (route requester_hold).
- Process owner: owns the policy in config/rules.json (tolerances, tax, approval).
- Approver: approval records for MATCHED invoices; L1, plus L2 above INR 100,000.
- Operations analyst: works the exception queue (price, quantity, tax, arithmetic
  and date exceptions).
- System administrator: owns vendor master status (active, on_hold, closed), which
  the matcher reads. There is no separate administration workflow.

## A9. Field extraction
No real documents exist. The generator writes synthetic plain-text invoice
documents, and a deterministic parser extracts header and line fields, which are
compared with the source records. This is not OCR.

## A10. Payments
Only MATCHED invoices get approval records and a scheduled payment (7 days after
approval). Scheduled means payment-ready; payments are never released.

## A11. Provenance
Fully synthetic. No external data and no personal data.

## A12. Simplifications
Single currency (INR); fixed tax codes; quantities 1 to 40; unit prices in steps of
INR 50. Vendors on hold exist for master-data realism but have no invoices in this
version. 12 invoices have no PO.

## A13. Referential integrity in defect cases
All foreign keys stay valid, including defect rows. Defects are semantic (price
outside tolerance, closed vendor, duplicate number, arithmetic/date anomalies and
missing receipts) and carry an issue_type/defect_subtype label in the separate
ground-truth exports, rather than broken joins. This keeps join validation strict
and lets src/validation.py report zero FK violations.

## A14. Deterministic generated issue mix
Full-scale invoice labels are allocated to exact integer quotas from
config/rules.json, then anomaly rows are constructed by recipes. Ground-truth
labels are derived from those recipes and their documented rule mapping; they are
not independently randomized. Duplicate rows reuse a prior vendor invoice number and
reference its earlier invoice ID in ground truth.
