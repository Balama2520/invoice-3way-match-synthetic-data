-- Ground truth: kept in its own file/database. Never joined into input features.
CREATE TABLE ground_truth (
  invoice_id TEXT PRIMARY KEY,
  issue_type TEXT NOT NULL CHECK (issue_type IN ('clean','price_variance','quantity_mismatch','duplicate','missing_receipt','tax_vendor_error')),
  defect_subtype TEXT NOT NULL CHECK (defect_subtype IN ('NONE','BOUNDARY_CLEAN','PRICE_OUTSIDE_TOLERANCE','PARTIAL_DELIVERY','INVOICE_EXCEEDS_PO_QTY','DUPLICATE_NUMBER','SAME_INVOICE_TWICE','NO_RECEIPT','TAX_MISMATCH','CLOSED_VENDOR','TOTAL_DIFFERS_FROM_LINE_SUM','DATE_BEFORE_PO')),
  secondary_issue_types TEXT,
  expected_decision TEXT NOT NULL CHECK (expected_decision IN ('MATCHED','PRICE_VARIANCE','QUANTITY_MISMATCH','TAX_MISMATCH','MISSING_RECEIPT','DATE_ANOMALY','ARITHMETIC_ANOMALY','DUPLICATE','CLOSED_VENDOR')),
  expected_route TEXT NOT NULL CHECK (expected_route IN ('approver','operations_analyst','requester_hold','blocked')),
  paired_invoice_id TEXT,
  injection_rule TEXT NOT NULL
);
