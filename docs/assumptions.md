## A13. Referential integrity in defect cases
All foreign keys stay valid, including defect rows. Defects are semantic (price
outside tolerance, closed vendor, duplicate number, arithmetic/date anomalies and
missing receipts) and carry an issue_type/defect_subtype label in the separate
ground-truth exports, rather than broken joins. This keeps join validation strict
and lets `src/validation.py` report zero FK violations.

## A14. Deterministic generated issue mix
Full-scale invoice labels are allocated to exact integer quotas from
`config/rules.json`, then anomaly rows are constructed by recipes. Ground-truth
labels are derived from those recipes and their documented rule mapping; they
are not independently randomized. Duplicate rows reuse a prior vendor invoice
number and reference its earlier invoice ID in ground truth.
