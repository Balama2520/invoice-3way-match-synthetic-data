# Limitations

- Invoice intake parses this project's documented plain-text invoice layout. It
  does not perform OCR or extract fields from scanned PDFs or images.
- The workflow is a local synthetic-data demonstration. It has no live ERP,
  vendor portal, approval-service, or payment-rail integration.
- Approvals and payments are simulated as database records and lifecycle events.
  Eligible payments are scheduled; the workflow does not release or transfer
  funds.
- All evaluation labels and accuracy metrics are based on generated synthetic
  records. They do not measure performance on real invoices or establish
  production suitability.
- Repeated generation was verified on Windows with Python 3.14. Byte identity
  across operating systems, Python versions, SQLite versions, or compression
  libraries is not guaranteed.
- The matching rules, INR currency, approval threshold, and synthetic source
  document format are fixed by the project's configuration and are not a
  substitute for organization-specific policy review.
- Strict quantity policy: an invoice quantity must equal the PO line quantity and
  not exceed the posted receipt quantity. A legitimate partial invoice for partly
  delivered goods is therefore routed to QUANTITY_MISMATCH.
- Rules are shared by the generator and the matcher, so 100% agreement with ground
  truth shows the matcher implements the documented rules consistently.
- Vendors on hold exist in the master data but have no invoices.
- Only 14 clean invoices sit at the 2% price boundary.
