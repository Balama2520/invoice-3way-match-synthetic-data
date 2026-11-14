# AI usage disclosure

## Tools I used
- Claude (chat, claude.ai): project planning and schedule, repository setup commands,
  schema, data dictionary and ER diagram drafting, prompts for the coding agent,
  documentation drafts, and review of the agent's output and reports.
- GitHub Copilot (agent mode in VS Code): wrote most of the generator, intake parser,
  matcher, validation, tests, dashboard and CI workflow, and later ran a verification
  pass (documentation fact-check, extra tests, a verification script) and an
  independent release audit.

## Where AI accelerated me
Boilerplate, schema drafting, test scaffolding, documentation drafts and the
dashboard.

## Where I relied on it heavily
The first implementation of the generator, matcher and validation was written by the
coding agent from my prompts. The documents in docs/ and the AI usage records were
also drafted with AI help.

## What I personally did
- Created the public repository and the local environment, and set the project scope
  and deadline plan.
- Ran the test suite and the audit commands myself on a fresh clone of the
  repository.
- Merged the agent's verification and audit fixes through pull requests 1 and 2.
- Acted on the independent audit findings: the README overstated reproducibility
  (byte-identical output was only verified on Windows), a configuration key was
  misnamed in the data dictionary, requirements.txt was in the wrong encoding and
  listed unused packages, and the limitations page was missing. These are fixed in
  the repository.
- Reviewing the code: I reviewed the agents' QA and release-audit reports and ran the tests myself; I did not read every source file line by line.

## Prompt history
ai_usage/prompts.md lists every prompt I sent to Claude. The prompts I sent to Copilot could not be fully exported from the tool, so that section summarizes them instead of reproducing them verbatim.
