# architecture/

A live map of how AgentVC fits together (plan §5). It is meant for contributors and users who want to see the moving parts before reading code.

| File | What it is |
|---|---|
| `graph.json` | **The single source of truth**: layers, nodes, edges, the Data flow and Lifecycle views, and roadmap cards. Hand-maintained |
| `index.html` | Offline renderer. No external resources: no CDN, no fonts, no network |
| `codemap.json` | Module and import map, generated from the code (arrives in v0.2; never hand-edited) |

**Viewing the page:** browsers block a page opened from disk (`file://`) from reading `graph.json`, so serve the folder:

```text
python -m http.server --directory architecture 8000
```

then open <http://localhost:8000/>. On GitHub Pages it works as-is.

**Current state (Discovery):** every node is `planned`, and the page renders the Overview view only. Data flow and Lifecycle views arrive with v0.1 (plan §5.2).

## The update rule (plan §5.3)

Any change to AgentVC's architecture, data flow or workflow updates `architecture/` **in the same pull request**. A stale node is worse than a missing one, because people trust it.

**Counts as an architecture change**
- a new module under `agentvc/` that another module imports, or a removed one
- a new or removed agent adapter, hook event, or change to which tools an event covers (`wiring.py`)
- a new table or column in `ledger.db`, or a new file under `.agentvc/`
- a new recovery class, effect adapter (git, Postgres, Docker…) or contract category
- a new external dependency in the data path (a binary, an MCP server, a database)
- a new CLI command that adds a flow (e.g. `fork`, `commit`)
- a new stage in the evaluation pipeline

**Doesn't need an update:** bug fixes, docs and comments, new tests, refactors that don't change who-imports-whom, new contract YAML files within an existing category.

**Update steps**
1. Edit `graph.json`: add, remove or relabel nodes and edges; keep `id`s stable; keep `sub` short (≤ 42 characters, so boxes don't overlap); put detail in `desc`.
2. Update the affected view (`dataflow`, `lifecycle`).
3. If planned work was **built**: flip its nodes to `shipped` and remove or shrink its roadmap card. Any unmet acceptance criterion stays as a smaller card.
4. Bump `meta.updated`, and prepend one line to `meta.changelog`.
5. Run `make arch` (Windows: `./make.ps1 arch`).
6. Commit the architecture change together with the code change.

## Checks

| Check | Runs in | Status |
|---|---|---|
| Structure: meta fields, unique ids, known layers and statuses, shipped paths exist, `sub` ≤ 42, no dangling edges, views reference real nodes and states, roadmap cards complete (`scripts/arch_check.py`) | `make arch`, `make test` | **in place** |
| Render: the page draws every node, details open on click, no script errors, no horizontal scroll at phone width (Playwright, `tests/test_arch_render.py`) | `make arch`, `make test` | **in place** |
| Every `.py` in `agentvc/`, `eval/`, `scripts/` has a node | CI, pre-commit | v0.1 |
| Declared edges match real imports (`codemap.json`) | CI | v0.2 |
| Docs and README mention no missing paths; README diagrams match `arch_export.py` | CI | v0.1 |

The render check counts only after it has been **seen to fail once**: break the page on purpose, confirm the check fails, restore it, confirm it passes. That was done when the check was added (see the commit message of the render check).
