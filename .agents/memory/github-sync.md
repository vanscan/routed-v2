---
name: GitHub sync path
description: How this repl reaches GitHub and which git operations are possible where
---

This repl is linked to a GitHub origin remote. Syncing code back to GitHub is constrained:

- The **main repl environment can reach github.com for reads** (`git ls-remote`, `git clone` work, though large clones may exceed the 120s bash tool limit).
- **Destructive git operations are blocked in the main agent** (`git fetch`, `merge`, `reset`, `push`, `commit` all rejected with "Destructive git operations are not allowed in the main agent").
- **Isolated project-task containers historically could NOT reach github.com** (outbound git times out), so delegating the push to a project task is unreliable.

**Why:** Replit's security model keeps GitHub auth/push in the user's hands and sandboxes the agent's git mutations.

**How to apply:** To push Replit code to GitHub, guide the user to Replit's built-in **Git / Version Control pane** (Connect to GitHub → Pull → Push). That uses Replit's own infra and working network. To inspect divergence without mutating the repo, use the GitHub REST API compare endpoint (`/repos/{owner}/{repo}/compare/{base}...{head}`) and read-only local git (`log`, `cat-file`, `ls-remote`) rather than fetch.
