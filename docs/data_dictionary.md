# Data dictionary

Conventions: dates are ISO text `YYYY-MM-DD`; money is NUMERIC with 2 decimals in INR;
quantities are whole units; percentages are stored as fractions (0.02 = 2%).
"Req" = required (Y/N). All IDs are immutable and produced by deterministic counters.
All foreign keys stay valid in v1, including for defect cases: defects are semantic
(wrong price, closed vendor, duplicate), not broken joins (see assumption A13).
Rules that are not in DDL (arithmetic, date order, lifecycle) are enforced by
`src/validation.py`.

## 1. vendor
| Field | Type | Req | Allowed values / format | Unit | Unique | Validation rule |
|---|---|---|---|---|---|---|
| vendor_id | TEXT | Y | VND-00001 .. VND-00300 | - | PK | matches `VND-NNNNN` |
| vendor_code | TEXT | Y | 5 digits | - | Yes | equals numeric part of vendor_id; used inside invoice_id |
| vendor_name | TEXT | Y | synthetic company name | - | No | non-empty |
| status | TEXT | Y | active, on_hold, closed | - | No | closed requires effective_to |
| payment_terms_days | INTEGER | Y | 15, 30, 45, 60 | days | No | in allowed set |
| effective_from | DATE | Y | ISO date | date | No | valid date |
| effective_to | DATE | N | ISO date, null while open-ended | date | No | >= effective_from; required when closed |

## 2. purchase_order
| Field | Type | Req | Allowed values / format | Unit | Unique | Validation rule |
|---|---|---|---|---|---|---|
| po_id | TEXT | Y | PO-2026-000001 .. | - | PK | matches `PO-2026-NNNNNN` |
| vendor_id | TEXT | Y | FK vendor | - | No | must exist |
| po_date | DATE | Y | within run.po_start..po_end | date | No | valid date |
| status | TEXT | Y | open, partially_received, received, closed | - | No | consistent with receipts (validate.py) |
| currency | TEXT | Y | INR | - | No | fixed |
| requester_id | TEXT | Y | REQ-NNN synthetic | - | No | non-empty |
| total_amount | NUMERIC | Y | >= 0 | INR | No | equals sum of its line_total values |

## 3. purchase_order_line
| Field | Type | Req | Allowed values / format | Unit | Unique | Validation rule |
|---|---|---|---|---|---|---|
| po_line_id | TEXT | Y | `<po_id>-L01` .. `-L08` | - | PK | equals po_id + '-L' + 2-digit line_number |
| po_id | TEXT | Y | FK purchase_order | - | with line_number | must exist |
| line_number | INTEGER | Y | 1..8 | - | with po_id | 1-8 lines per PO |
| sku | TEXT | Y | SKU-0001 .. (about 120 SKUs) | - | No | non-empty |
| description | TEXT | Y | synthetic text | - | No | non-empty |
| quantity | INTEGER | Y | 1..40 (generated range) | units | No | > 0 |
| unit_price | NUMERIC | Y | INR 50..5000 (generated range) | INR/unit | No | > 0 |
| tax_code | TEXT | Y | T0, T5, T12, T18 | - | No | rate from `config/rules.json` |
| line_net | NUMERIC | Y | derived | INR | No | = quantity x unit_price (2 dp) |
| tax_amount | NUMERIC | Y | derived | INR | No | = line_net x tax rate (2 dp) |
| line_total | NUMERIC | Y | derived | INR | No | = line_net + tax_amount |

## 4. goods_receipt
| Field | Type | Req | Allowed values / format | Unit | Unique | Validation rule |
|---|---|---|---|---|---|---|
| grn_id | TEXT | Y | GRN-2026-000001 .. | - | PK | matches `GRN-2026-NNNNNN` |
| po_id | TEXT | Y | FK purchase_order | - | No | must exist |
| receipt_date | DATE | Y | ISO date | date | No | >= po_date |
| status | TEXT | Y | posted, cancelled | - | No | only posted receipts count toward matching |

## 5. goods_receipt_line
| Field | Type | Req | Allowed values / format | Unit | Unique | Validation rule |
|---|---|---|---|---|---|---|
| grn_line_id | TEXT | Y | `<grn_id>-L01` .. | - | PK | equals grn_id + '-L' + 2-digit line_number |
| grn_id | TEXT | Y | FK goods_receipt | - | with line_number | must exist |
| line_number | INTEGER | Y | 1..8 | - | with grn_id | - |
| po_line_id | TEXT | Y | FK purchase_order_line | - | No | must belong to the same PO as the receipt |
| quantity_received | INTEGER | Y | > 0 | units | No | full / partial / over-receipt vs PO line quantity |

## 6. invoice
| Field | Type | Req | Allowed values / format | Unit | Unique | Validation rule |
|---|---|---|---|---|---|---|
| invoice_id | TEXT | Y | INV-<vendor-code>-2026-000001 | - | PK | matches pattern; vendor-code equals the vendor's code |
| vendor_id | TEXT | Y | FK vendor | - | No | must exist |
| po_id | TEXT | N | FK purchase_order | - | No | optional; if set, vendor must match PO vendor |
| vendor_invoice_number | TEXT | Y | number printed by vendor | - | NOT unique | duplicates share it (duplicate-detection key) |
| invoice_date | DATE | Y | ISO date | date | No | normally >= po_date (defect: DATE_BEFORE_PO) |
| received_date | DATE | Y | ISO date | date | No | >= invoice_date |
| currency | TEXT | Y | INR | - | No | fixed |
| net_amount | NUMERIC | Y | >= 0 | INR | No | = sum of line_net (defect: TOTAL_DIFFERS_FROM_LINE_SUM) |
| tax_amount | NUMERIC | Y | >= 0 | INR | No | = sum of line tax_amount |
| total_amount | NUMERIC | Y | >= 0 | INR | No | = net_amount + tax_amount |
| source_channel | TEXT | Y | email, portal, edi | - | No | intake channel |

## 7. invoice_line
| Field | Type | Req | Allowed values / format | Unit | Unique | Validation rule |
|---|---|---|---|---|---|---|
| invoice_line_id | TEXT | Y | INVL-<invoice-id>-01 | - | PK | equals 'INVL-' + invoice_id + '-' + 2-digit line_number |
| invoice_id | TEXT | Y | FK invoice | - | with line_number | must exist |
| line_number | INTEGER | Y | 1..99 | - | with invoice_id | - |
| po_line_id | TEXT | N | FK purchase_order_line | - | No | PO line referenced on the invoice, if any |
| sku, description | TEXT | Y | as on invoice | - | No | non-empty |
| quantity | INTEGER | Y | > 0 | units | No | compared with received quantity |
| unit_price | NUMERIC | Y | > 0 | INR/unit | No | compared with PO price (inclusive +/-2%) |
| tax_code | TEXT | Y | T0, T5, T12, T18 | - | No | compared with PO line tax code |
| line_net, tax_amount, line_total | NUMERIC | Y | derived | INR | No | computed from quantity, unit_price, tax rate |

## 8. approval
| Field | Type | Req | Allowed values / format | Unit | Unique | Validation rule |
|---|---|---|---|---|---|---|
| approval_id | TEXT | Y | APR-2026-000001 .. | - | PK | matches pattern |
| invoice_id | TEXT | Y | FK invoice | - | with approval_level | only for eligible (decision MATCHED) invoices |
| approval_level | TEXT | Y | L1, L2 | - | with invoice_id | L2 needed above approval.level1_limit |
| approver_id | TEXT | Y | synthetic id | - | No | non-empty |
| requested_date | DATE | Y | ISO date | date | No | after invoice received_date |

## 9. payment
| Field | Type | Req | Allowed values / format | Unit | Unique | Validation rule |
|---|---|---|---|---|---|---|
| payment_id | TEXT | Y | PAY-2026-000001 .. | - | PK | matches pattern |
| invoice_id | TEXT | Y | FK invoice | - | Yes (0..1 per invoice) | invoice must be approved and not blocked |
| approval_id | TEXT | Y | FK approval | - | No | approval must be approved |
| amount | NUMERIC | Y | > 0 | INR | No | equals invoice total_amount |
| scheduled_date | DATE | Y | ISO date | date | No | approval date + payment_release_delay_days |

## 10. Supporting tables
| Table | Purpose | Key fields |
|---|---|---|
| invoice_status_event | append-only invoice lifecycle history | event_id PK; (invoice_id, event_seq) unique; from_status, to_status, event_date, actor_role, reason_code |
| approval_event | approval history | event_id PK; (approval_id, event_seq) unique; event_type requested/approved/rejected |
| payment_event | payment history | event_id PK; (payment_id, event_seq) unique; event_type scheduled/released |
| invoice_match_result | engine output per invoice | invoice_id PK; decision, route, check flags, blocked, payment_ready |
| invoice_line_po_line_match | bridge (N:M) engine allocation | match_id PK; (invoice_line_id, po_line_id) unique; price/quantity/tax variance |
| ground_truth | SEPARATE file; labels from injection recipes | invoice_id; issue_type, defect_subtype, expected_decision, expected_route, paired_invoice_id, injection_rule |

## 11. Lifecycle (invoice_status_event)
Generated clean path: received -> extracted -> matched -> approval_pending -> approved -> payment_ready.
matched -> exception (terminal in the synthetic data: awaiting analyst)
matched -> blocked (terminal: duplicate or closed vendor)
The schema also allows rejected and paid for downstream lifecycle extensions; this
generator schedules eligible payments but does not release them.
Rules: event_seq starts at 1 and increases by 1; each event's from_status equals the
previous event's to_status; no payment_ready or paid for blocked, exception or rejected invoices.

## 12. Defect label mapping (ground truth)
| issue_type | defect_subtype | expected_decision | expected_route |
|---|---|---|---|
| clean | NONE, BOUNDARY_CLEAN | MATCHED | approver |
| price_variance | PRICE_OUTSIDE_TOLERANCE | PRICE_VARIANCE | operations_analyst |
| quantity_mismatch | PARTIAL_DELIVERY, INVOICE_EXCEEDS_PO_QTY | QUANTITY_MISMATCH | operations_analyst |
| duplicate | DUPLICATE_NUMBER, SAME_INVOICE_TWICE | DUPLICATE | blocked |
| missing_receipt | NO_RECEIPT | MISSING_RECEIPT | requester_hold |
| tax_vendor_error | TAX_MISMATCH | TAX_MISMATCH | operations_analyst |
| tax_vendor_error | CLOSED_VENDOR | CLOSED_VENDOR | blocked |
| tax_vendor_error | TOTAL_DIFFERS_FROM_LINE_SUM | ARITHMETIC_ANOMALY | operations_analyst |
| tax_vendor_error | DATE_BEFORE_PO | DATE_ANOMALY | operations_analyst |

Open point for slot 4: vendors with status on_hold exist for master-data realism but
no invoices are generated against them in v1.
