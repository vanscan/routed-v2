---
name: Dangling server imports after route-module refactor
description: server.py refactor extracted endpoints into routes/ but left lazy `from server import X` blocks in handlers that reference symbols no longer on server — causing 500s only when that endpoint is invoked.
---

# Dangling `from server import` after Route-Module Refactor

## The Rule
After any refactor that moves symbols out of `backend/server.py` into `backend/routes/*.py`, run a full AST audit before shipping:

```python
import ast, glob, server
for f in sorted(glob.glob("routes/*.py")):
    tree = ast.parse(open(f).read(), f)
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module == "server":
            for a in n.names:
                if not hasattr(server, a.name):
                    print(f"{f}:{n.lineno} DANGLING → {a.name}")
```

**Why:** Route handlers use lazy imports (inside the function body) to avoid circular imports. These only fail at runtime when that endpoint is called — not on startup — so they're invisible until the user hits the broken route.

**How to apply:** Run this audit after any `server.py` structural refactor, and after any merge that touches `server.py` or `routes/*.py`. A clean run prints nothing.

## Known Fixes Applied (as of 2026-06-12)

- `routes/import_stops.py`: `_OPTIMIZE_RUNNER_TASKS` moved from server → `routes/optimize_jobs.py`
- `routes/optimize.py`: `_srv` was a duplicate of the local alias `import server as _srv`; `parse_start_time` lives in `routes/_route_constraints.py` not server

## Edit-Persistence Trap

The `edit` tool can report "No replacement performed" yet *appear* to have changed the file in subsequent reads — then the change disappears at the next checkpoint. Always confirm edits persisted by:
1. Re-reading the file immediately after a successful edit-tool call.
2. Running `git --no-optional-locks status` / `git diff` to confirm the working-tree shows the hunk.
3. Running a verification that imports from the **actual file** (not a re-typed copy of the import block).
