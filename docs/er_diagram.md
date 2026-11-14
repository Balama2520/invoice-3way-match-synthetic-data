# ER diagram and cardinalities

```mermaid
erDiagram
  VENDOR ||--o{ PURCHASE_ORDER : places
  VENDOR ||--o{ INVOICE : issues
  PURCHASE_ORDER ||--|{ PURCHASE_ORDER_LINE : contains
  PURCHASE_ORDER ||--o{ GOODS_RECEIPT : receives
  GOODS_RECEIPT ||--|{ GOODS_RECEIPT_LINE : contains
  PURCHASE_ORDER_LINE ||--o{ GOODS_RECEIPT_LINE : fulfilled_by
  PURCHASE_ORDER |o--o{ INVOICE : referenced_by
  INVOICE ||--|{ INVOICE_LINE : contains
  INVOICE ||--|| INVOICE_EXTRACTION : traced_to
  PURCHASE_ORDER_LINE |o--o{ INVOICE_LINE : referenced_by
  INVOICE_LINE ||--o{ INVOICE_LINE_PO_LINE_MATCH : matched_in
  PURCHASE_ORDER_LINE ||--o{ INVOICE_LINE_PO_LINE_MATCH : matched_in
  INVOICE ||--o{ INVOICE_STATUS_EVENT : history
  INVOICE ||--o| INVOICE_MATCH_RESULT : evaluated_as
  INVOICE ||--o{ APPROVAL : needs
  APPROVAL ||--|{ APPROVAL_EVENT : history
  INVOICE ||--o| PAYMENT : paid_by
  APPROVAL ||--o{ PAYMENT : authorises
  PAYMENT ||--|{ PAYMENT_EVENT : history
```

| Relationship | Cardinality | Optional? | Notes |
|---|---|---|---|
| Vendor - PurchaseOrder | 1:N | PO needs a vendor | closed vendors keep historical POs |
| Vendor - Invoice | 1:N | invoice needs a vendor | closed-vendor defect uses a valid FK |
| PurchaseOrder - PurchaseOrderLine | 1:N (1-8) | at least 1 line | |
| PurchaseOrder - GoodsReceipt | 1:N | a PO may have 0 receipts | missing-receipt scenario |
| GoodsReceipt - GoodsReceiptLine | 1:N | at least 1 line | |
| PurchaseOrderLine - GoodsReceiptLine | 1:N | 0..N | partial receipts split across GRNs |
| PurchaseOrder - Invoice | 1:N | invoice.po_id optional | non-PO invoices exist |
| Invoice - InvoiceLine | 1:N | at least 1 line | |
| Invoice - InvoiceExtraction | 1:1 | required for every ingested invoice | stores document name, SHA-256, extracted fields and extraction date |
| InvoiceLine - PurchaseOrderLine | N:M via invoice_line_po_line_match | 0..N | engine-derived bridge |
| Invoice - Approval | 1:N (L1, L2) | only eligible invoices | |
| Invoice - Payment | 1:0..1 | only approved, unblocked | |
| Invoice / Approval / Payment - event history | 1:N | event tables are defined for these records | state changes are recorded as distinct event rows |

Effective dates: vendor.effective_from / effective_to. Lifecycle states: see section 11 of
the data dictionary.
