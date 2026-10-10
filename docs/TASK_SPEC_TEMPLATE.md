# Task Spec Template

How to use: copy this file, fill in every section, save as `docs/<task-name>-spec.md`,
then send MiniMax Code a short chat message pointing at the path (template at the bottom).
Delete this header when you copy it.

---

## Objective

One or two sentences: what "done" looks like. Be concrete and verifiable.
A task without a falsifiable done-state is not ready to send.

## Background

Why this task exists. Link the evidence: prior results, conversations, open threads.
Include every number with its source — never restate a metric without saying where it came from
and whether it has been withdrawn or superseded.

## Inputs

1. Exact data/code locations (repo-relative paths).
2. External data sources — pick ONE, document the choice.
3. Availability constraints, stated as prohibitions (e.g. "T-1 only: a signal dated T may
   only use data available at T-1 close"). Say what the task may NOT use, not just what it may.

## Steps

Numbered, in order. Each step states its output. No step should require MiniMax to guess
intent — if a decision is genuinely open, say who decides and by when.

## Guardrails

- **No look-ahead:** the exact rule for this task, plus a probe that verifies it.
- **Placebo / negative control:** what would prove the result is real, run before claiming anything.
- **Multiple comparisons:** disclose how many variants/forms are tested; a marginal p-value from
  one of many is not evidence.
- **Event dominance:** if the result could be driven by one or two events, report with and
  without them.
- **Branch only:** implement behind a `<feature>_flag` config flag, default OFF.
  No production changes until Kenneth approves.

## Deliverables

1. One-page summary: verdict first, then key numbers. Negative results reported with the
   same energy as positive ones.
2. Machine-readable summary (JSON): inputs, parameters, results, guardrail outcomes.
3. Data artifacts (CSVs) with column definitions documented in the file or alongside it.
4. Reproducible script/notebook — one command reproduces everything.

## Verification checklist (all must pass before reporting done)

- [ ] Self-tests pass — add new ones for new logic; include a must-fail guard wherever
      silent corruption is possible
- [ ] No-look-ahead probe passes
- [ ] Placebo / negative control run and reported
- [ ] Production code untouched (grep for new identifiers in build paths returns nothing)
- [ ] Bugs found during the work are disclosed in the summary, even if already fixed
- [ ] Definitions documented (any metric whose meaning could be misread gets one line)

## Non-goals

Explicitly list what is out of scope. If it is not listed here or in Steps, it does not get done.

## Chat message to MiniMax

Keep it short, in Cantonese, pointing at the spec path. Example:

「新任務：<name>。Spec 寫好喺 <path>，請先讀晒成份 spec，再按入面做。重點：<1–2 行>。Research only：唔改 production，flag 預設 OFF。做完交：one-page summary、JSON、CSV、可重現 script。」
