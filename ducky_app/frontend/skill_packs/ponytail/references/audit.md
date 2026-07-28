---
description: "Whole-repo over-engineering audit"
metadata:
  label: "Ponytail Audit"
  default_enabled: false
  load_condition: "User asks for a repo-wide over-engineering audit"
---

Repo-wide over-engineering audit. Like review, but scans the entire codebase
instead of a diff: a ranked list of what to delete, simplify, or replace with
stdlib/native equivalents.

## Tags

- `delete:` dead code, unused flexibility, speculative feature. Replacement: nothing.
- `stdlib:` hand-rolled thing the standard library ships. Name the function.
- `native:` dependency or code doing what the platform already does. Name the feature.
- `yagni:` abstraction with one implementation, config nobody sets, layer with one caller.
- `shrink:` same logic, fewer lines. Show the shorter form.

## Hunt

Deps the stdlib or platform already ships, single-implementation interfaces,
factories with one product, wrappers that only delegate, files exporting one
thing, dead flags and config, hand-rolled stdlib.

## Output

One line per finding, ranked: `<tag> <what to cut>. <replacement>. [path]`.
End with `net: -<N> lines, -<M> deps possible.` Nothing to cut: `Lean already. Ship.`

## Boundaries

Scope: over-engineering and complexity only. Correctness bugs, security holes,
and performance are explicitly out of scope. Lists findings, applies nothing.
