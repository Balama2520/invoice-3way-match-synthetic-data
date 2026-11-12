-- Input schema: 9 core entities + supporting event/result tables.
-- Ground truth is deliberately NOT here (see schema_ground_truth.sql).
-- Arithmetic reconciliation, date ordering and lifecycle rules are checked in
-- validate.py (not in DDL) so labelled defect cases can be injected.
PRAGMA foreign_keys = ON;

CREATE TABLE vendor (
  vendor_id TEXT PRIMARY KEY CHECK (vendor_id GLOB 'VND-[0-9][0-9][0-9][0-9][0-9]'),
  vendor_code TEXT NOT NULL UNIQUE CHECK (vendor_code = substr(vendor_id, 5)),
  vendor_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','on_hold','closed')),
  payment_terms_days INTEGER NOT NULL CHECK (payment_terms_days IN (15,30,45,60)),
  effective_from TEXT NOT NULL CHECK (date(effective_from) = effective_from),
  effective_to TEXT CHECK (effective_to IS NULL OR (date(effective_to) = effective_to AND effective_to >= effective_from)),
  CHECK (status <> 'closed' OR effective_to IS NOT NULL)
);

CREATE TABLE purchase_order (
  po_id TEXT PRIMARY KEY CHECK (po_id GLOB 'PO-2026-[0-9][0-9][0-9][0-9][0-9][0-9]'),
  vendor_id TEXT NOT NULL REFERENCES vendor(vendor_id),
  po_date TEXT NOT NULL CHECK (date(po_date) = po_date),
  status TEXT NOT NULL CHECK (status IN ('open','partially_received','received','closed')),
  currency TEXT NOT NULL CHECK (currency = 'INR'),
  requester_id TEXT NOT NULL,
  total_amount NUMERIC NOT NULL CHECK (total_amount >= 0)
);
CREATE INDEX ix_po_vendor ON purchase_order(vendor_id);

CREATE TABLE purchase_order_line (
  po_line_id TEXT PRIMARY KEY,
  po_id TEXT NOT NULL REFERENCES purchase_order(po_id),
  line_number INTEGER NOT NULL CHECK (line_number BETWEEN 1 AND 8),
  sku TEXT NOT NULL,
  description TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price NUMERIC NOT NULL CHECK (unit_price > 0),
  tax_code TEXT NOT NULL CHECK (tax_code IN ('T0','T5','T12','T18')),
  line_net NUMERIC NOT NULL CHECK (line_net >= 0),
  tax_amount NUMERIC NOT NULL CHECK (tax_amount >= 0),
  line_total NUMERIC NOT NULL CHECK (line_total >= 0),
  UNIQUE (po_id, line_number),
  CHECK (po_line_id = po_id || '-L' || printf('%02d', line_number))
);
CREATE INDEX ix_poline_po ON purchase_order_line(po_id);

CREATE TABLE goods_receipt (
  grn_id TEXT PRIMARY KEY CHECK (grn_id GLOB 'GRN-2026-[0-9][0-9][0-9][0-9][0-9][0-9]'),
  po_id TEXT NOT NULL REFERENCES purchase_order(po_id),
  receipt_date TEXT NOT NULL CHECK (date(receipt_date) = receipt_date),
  status TEXT NOT NULL CHECK (status IN ('posted','cancelled'))
);
CREATE INDEX ix_grn_po ON goods_receipt(po_id);

CREATE TABLE goods_receipt_line (
  grn_line_id TEXT PRIMARY KEY,
  grn_id TEXT NOT NULL REFERENCES goods_receipt(grn_id),
  line_number INTEGER NOT NULL CHECK (line_number BETWEEN 1 AND 8),
  po_line_id TEXT NOT NULL REFERENCES purchase_order_line(po_line_id),
  quantity_received INTEGER NOT NULL CHECK (quantity_received > 0),
  UNIQUE (grn_id, line_number),
  CHECK (grn_line_id = grn_id || '-L' || printf('%02d', line_number))
);
CREATE INDEX ix_grnline_poline ON goods_receipt_line(po_line_id);

CREATE TABLE invoice (
  invoice_id TEXT PRIMARY KEY CHECK (invoice_id GLOB 'INV-[0-9][0-9][0-9][0-9][0-9]-2026-[0-9][0-9][0-9][0-9][0-9][0-9]'),
  vendor_id TEXT NOT NULL REFERENCES vendor(vendor_id),
  po_id TEXT REFERENCES purchase_order(po_id),
  vendor_invoice_number TEXT NOT NULL,
  invoice_date TEXT NOT NULL CHECK (date(invoice_date) = invoice_date),
  received_date TEXT NOT NULL CHECK (date(received_date) = received_date),
  currency TEXT NOT NULL CHECK (currency = 'INR'),
  net_amount NUMERIC NOT NULL CHECK (net_amount >= 0),
  tax_amount NUMERIC NOT NULL CHECK (tax_amount >= 0),
  total_amount NUMERIC NOT NULL CHECK (total_amount >= 0),
  source_channel TEXT NOT NULL CHECK (source_channel IN ('email','portal','edi')),
  CHECK (substr(invoice_id, 5, 5) = substr(vendor_id, 5))
);
CREATE INDEX ix_inv_vendor_number ON invoice(vendor_id, vendor_invoice_number);
CREATE INDEX ix_inv_po ON invoice(po_id);

CREATE TABLE invoice_line (
  invoice_line_id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL REFERENCES invoice(invoice_id),
  line_number INTEGER NOT NULL CHECK (line_number BETWEEN 1 AND 99),
  po_line_id TEXT REFERENCES purchase_order_line(po_line_id),
  sku TEXT NOT NULL,
  description TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price NUMERIC NOT NULL CHECK (unit_price > 0),
  tax_code TEXT NOT NULL CHECK (tax_code IN ('T0','T5','T12','T18')),
  line_net NUMERIC NOT NULL CHECK (line_net >= 0),
  tax_amount NUMERIC NOT NULL CHECK (tax_amount >= 0),
  line_total NUMERIC NOT NULL CHECK (line_total >= 0),
  UNIQUE (invoice_id, line_number),
  CHECK (invoice_line_id = 'INVL-' || invoice_id || '-' || printf('%02d', line_number))
);
CREATE INDEX ix_invline_invoice ON invoice_line(invoice_id);

CREATE TABLE approval (
  approval_id TEXT PRIMARY KEY CHECK (approval_id GLOB 'APR-2026-[0-9][0-9][0-9][0-9][0-9][0-9]'),
  invoice_id TEXT NOT NULL REFERENCES invoice(invoice_id),
  approval_level TEXT NOT NULL CHECK (approval_level IN ('L1','L2')),
  approver_id TEXT NOT NULL,
  requested_date TEXT NOT NULL CHECK (date(requested_date) = requested_date),
  UNIQUE (invoice_id, approval_level)
);

CREATE TABLE payment (
  payment_id TEXT PRIMARY KEY CHECK (payment_id GLOB 'PAY-2026-[0-9][0-9][0-9][0-9][0-9][0-9]'),
  invoice_id TEXT NOT NULL UNIQUE REFERENCES invoice(invoice_id),
  approval_id TEXT NOT NULL REFERENCES approval(approval_id),
  amount NUMERIC NOT NULL CHECK (amount > 0),
  scheduled_date TEXT NOT NULL CHECK (date(scheduled_date) = scheduled_date)
);

-- Append-only history (never UPDATE or DELETE rows in these tables)
CREATE TABLE invoice_status_event (
  event_id INTEGER PRIMARY KEY,
  invoice_id TEXT NOT NULL REFERENCES invoice(invoice_id),
  event_seq INTEGER NOT NULL CHECK (event_seq >= 1),
  from_status TEXT,
  to_status TEXT NOT NULL CHECK (to_status IN ('received','extracted','matched','exception','blocked','approval_pending','approved','rejected','payment_ready','paid')),
  event_date TEXT NOT NULL CHECK (date(event_date) = event_date),
  actor_role TEXT NOT NULL CHECK (actor_role IN ('system','requester','process_owner','approver','operations_analyst','system_administrator')),
  reason_code TEXT,
  UNIQUE (invoice_id, event_seq)
);

CREATE TABLE approval_event (
  event_id INTEGER PRIMARY KEY,
  approval_id TEXT NOT NULL REFERENCES approval(approval_id),
  event_seq INTEGER NOT NULL CHECK (event_seq >= 1),
  event_type TEXT NOT NULL CHECK (event_type IN ('requested','approved','rejected')),
  event_date TEXT NOT NULL CHECK (date(event_date) = event_date),
  actor_id TEXT NOT NULL,
  UNIQUE (approval_id, event_seq)
);

CREATE TABLE payment_event (
  event_id INTEGER PRIMARY KEY,
  payment_id TEXT NOT NULL REFERENCES payment(payment_id),
  event_seq INTEGER NOT NULL CHECK (event_seq >= 1),
  event_type TEXT NOT NULL CHECK (event_type IN ('scheduled','released')),
  event_date TEXT NOT NULL CHECK (date(event_date) = event_date),
  UNIQUE (payment_id, event_seq)
);

-- Engine output (derived from the tables above; never read by the generator)
CREATE TABLE invoice_match_result (
  invoice_id TEXT PRIMARY KEY REFERENCES invoice(invoice_id),
  decision TEXT NOT NULL CHECK (decision IN ('MATCHED','PRICE_VARIANCE','QUANTITY_MISMATCH','TAX_MISMATCH','MISSING_RECEIPT','DATE_ANOMALY','ARITHMETIC_ANOMALY','DUPLICATE','CLOSED_VENDOR')),
  route TEXT NOT NULL CHECK (route IN ('approver','operations_analyst','requester_hold','blocked')),
  is_duplicate INTEGER NOT NULL CHECK (is_duplicate IN (0,1)),
  duplicate_of_invoice_id TEXT REFERENCES invoice(invoice_id),
  vendor_ok INTEGER NOT NULL CHECK (vendor_ok IN (0,1)),
  date_ok INTEGER NOT NULL CHECK (date_ok IN (0,1)),
  arithmetic_ok INTEGER NOT NULL CHECK (arithmetic_ok IN (0,1)),
  receipt_found INTEGER NOT NULL CHECK (receipt_found IN (0,1)),
  price_ok INTEGER NOT NULL CHECK (price_ok IN (0,1)),
  quantity_ok INTEGER NOT NULL CHECK (quantity_ok IN (0,1)),
  tax_ok INTEGER NOT NULL CHECK (tax_ok IN (0,1)),
  blocked INTEGER NOT NULL CHECK (blocked IN (0,1)),
  payment_ready INTEGER NOT NULL CHECK (payment_ready IN (0,1)),
  CHECK (blocked = 0 OR payment_ready = 0),
  CHECK (decision NOT IN ('DUPLICATE','CLOSED_VENDOR') OR blocked = 1),
  CHECK (payment_ready = 0 OR decision = 'MATCHED')
);

-- Bridge (N:M): engine allocation of invoice lines to PO lines
CREATE TABLE invoice_line_po_line_match (
  match_id INTEGER PRIMARY KEY,
  invoice_line_id TEXT NOT NULL REFERENCES invoice_line(invoice_line_id),
  po_line_id TEXT NOT NULL REFERENCES purchase_order_line(po_line_id),
  matched_quantity INTEGER NOT NULL CHECK (matched_quantity >= 0),
  price_variance_pct NUMERIC NOT NULL,
  quantity_variance INTEGER NOT NULL,
  tax_variance NUMERIC NOT NULL,
  UNIQUE (invoice_line_id, po_line_id)
);

CREATE VIEW v_invoice_current_status AS
SELECT e.invoice_id, e.to_status AS current_status, e.event_date AS status_date
FROM invoice_status_event e
JOIN (SELECT invoice_id, MAX(event_seq) AS max_seq
      FROM invoice_status_event GROUP BY invoice_id) m
  ON m.invoice_id = e.invoice_id AND m.max_seq = e.event_seq;
