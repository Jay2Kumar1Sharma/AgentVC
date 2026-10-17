# Design notes

One page per feature, written **before** its code (CONTRIBUTING.md "Workflow", plan §13.1). File name: `docs/design/<feature>.md`.

Each note covers:
- what the feature does and why;
- what it borrows from existing work, and that work's licence;
- which guarantees (G1–G8) it touches and how each is protected;
- how it can fail, with the new rows for `docs/testing/fmea.md`.

Decisions that change a guarantee, budget, format or dependency also need an ADR in `docs/adr/`.
