# AI Prompt and Workflow Summary

## Scope of this record

This file is a summary of the main prompt topics and AI-assisted work stages. It
is **not** a complete, verbatim transcript of every conversation. Exact prompt
text that is not available has not been reconstructed or presented as a quote.

## Claude

Claude was used for planning, requirements analysis, and review. The main prompt
topics were:

1. Analyze the challenge brief and propose an end-to-end project scope and
   architecture.
2. Organize a time-boxed plan for implementation, testing, and submission.
3. Define the data model, entity relationships, matching policy, synthetic
   scenarios, and documentation requirements.
4. Prepare implementation guidance for a coding agent covering generation,
   invoice-text extraction, matching, validation, and ground-truth isolation.
5. Review project progress and identify release, documentation, and verification
   gaps.

These are topic summaries, not exact prompt text.

## GitHub Copilot in VS Code

Copilot agent mode was used to implement and refine the project. Its work
included:

- The SQLite schemas and seeded synthetic-data generator.
- Plain-text invoice intake and extraction provenance.
- Matching decisions, exception routing, and approval/payment-readiness events.
- Validation, tests, documentation, the dashboard, and CI workflow.
- Follow-up verification and documentation corrections.

The interactive Copilot prompt history could not be fully exported. This section
therefore summarizes the main tasks and milestones rather than reproducing a
complete transcript.

## Transparency

AI assistance was substantial, especially for the first implementation of the
generator, intake, matcher, validation, tests, and documentation. The
[AI-use disclosure](ai_disclosure.md) identifies the tools used and describes
the candidate's own work.
