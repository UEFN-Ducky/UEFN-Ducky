---
description: "Benchmark impact scoreboard"
metadata:
  label: "Ponytail Gain"
  default_enabled: false
  load_condition: "User asks to measure or score the impact of ponytail simplifications"
---

# Ponytail Gain

Display this scoreboard when invoked. One-shot: do NOT change mode or persist anything.

The figures are the published benchmark medians (5 everyday tasks: email
validator, debounce, CSV sum, countdown timer, rate limiter; three models:
Haiku, Sonnet, Opus). They are measured, not computed from the current repo.

## Scoreboard

```
  ponytail gain                     benchmark median · 5 tasks · 3 models

  Lines of code   no-skill  ████████████████████  100%
                  ponytail  ██▌·················    6–20%   ▼ 80–94%
  Cost            no-skill  ████████████████████  100%
                  ponytail  █████▌··············   23–53%  ▼ 47–77%
  Speed           ponytail  ▸ 3–6× faster

  This repo:  enable debt subskill (shortcuts you deferred)
              enable audit subskill (what's still cuttable)
```

## Honesty boundary

These are benchmark medians, not this repo. NEVER print a per-repo savings
number: the unbuilt version was never written. Per-repo figures come from the
debt subskill (a counted ledger).

## Boundaries

One-shot display. Edits nothing, changes no mode.
