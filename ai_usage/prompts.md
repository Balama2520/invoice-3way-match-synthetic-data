# AI Development & Prompt Log

This document records the structured engineering prompts and workflow stages used to build, verify, and audit the **Invoice 3-Way Match: Synthetic Data, Decision Engine, and Validation** project. 

* **Dates:** November 12–13, 2026 (IST)
* **Tools Used:** Claude (Architecture, Planning & QA Review), GitHub Copilot Coding Agent (Implementation, Testing & Auditing)

---

## Phase 1: Architecture, Requirements & Project Planning (Claude)

1. **System Role & Scope:** Act as a Principal Full-Stack and Data Engineer. Review the attached problem statement and technical guidelines to define the end-to-end architecture for the project submission.
2. **Requirements & QA Alignment:** Analyse all functional requirements, data schemas, edge cases, and quality assurance criteria to ensure full compliance before writing code.
3. **Execution Roadmap:** Create a phased, time-boxed development plan covering repository setup, synthetic data generation, matching engine logic, testing, and final verification.
4. **Tooling & Environment Strategy:** Evaluate local development environments (VS Code, GitHub Copilot) to establish a deterministic, zero-dependency Python runtime workflow.
5. **Repository Structure:** Recommend a clean, standard repository layout (`src/`, `config/`, `data/`, `docs/`, `scripts/`, `tests/`) and naming convention (`invoice-3way-match-synthetic-data`).
6. **AI Transparency Policy:** Clarify best practices for documenting AI assistance (`ai_usage/`) to maintain full transparency with reviewers while ensuring all architectural decisions and verification steps are developer-owned.

---

## Phase 2: Core Implementation & Verification Prompts (Claude → GitHub Copilot)

7. **End-to-End Implementation Spec:** Generate a comprehensive technical specification for the coding agent to build the project from scratch:
   - Deterministic synthetic data generator (`src/generator.py`) with configurable scaling and seeding.
   - Plain-text invoice document parser (`src/intake.py`) with SHA-256 source hashing.
   - Rule-based 3-way matching engine (`src/matcher.py`) with strict decision precedence and approval routing.
   - Validation suite (`src/validation.py`) ensuring zero ground-truth label leakage.
8. **Git & Environment Troubleshooting:** Resolve branch rebase conflicts and verify clean working-directory execution paths for documentation and verification scripts.
9. **Automated QA & Verification Pass:** Instruct the coding agent to execute the full `pytest` suite (28 unit/integration tests), validate SQLite/CSV/JSON exports, and verify byte-identical deterministic outputs across repeated runs.
10. **Independent Release Audit:** Run a final pre-submission audit checking README accuracy, schema consistency, ground-truth isolation, and cross-platform reproducibility notes.

---

## Phase 3: Coding Agent Execution Summary (GitHub Copilot)

*Note: Interactive agent session logs are summarised below by task milestone.*

- **Milestone 1 (Core Pipeline):** Implemented `schema.sql`, `generator.py`, `intake.py`, `matcher.py`, and `validation.py` using only the Python standard library.
- **Milestone 2 (Documentation & Dashboard):** Generated `docs/` (`data_dictionary.md`, `er_diagram.md`, `assumptions.md`, `limitations.md`) and the offline `dashboard.html` exception viewer.
- **Milestone 3 (QA & Release Script):** Built `tests/` and `scripts/verify.ps1` to automate label-leakage checks, row-count validation, and determinism verification.


