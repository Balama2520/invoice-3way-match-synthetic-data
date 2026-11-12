
## A13. Referential integrity in defect cases
All foreign keys stay valid in v1, including defect rows. Defects are semantic
(price outside tolerance, closed vendor, duplicate number, wrong total) and carry an
issue_type label in ground_truth, rather than broken joins. This keeps join validation
strict and lets validate.py report zero FK violations.
