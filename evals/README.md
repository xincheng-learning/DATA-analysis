# Evals

This directory holds versioned gold standards, independent teachers, offline scorers and the real application evaluation harness.

## Current entry points

- [gold_v1](gold_v1/README.md): 72 cases / 78 turns, with grouped development/validation/test splits.
- [gold_v2](gold_v2/README.md): supplementary business inputs; original results remain separate.
- [reliability_v1](reliability_v1/README.md): 36 original synthetic semantic canaries, runnable wrong answers, production gate checks and source/limitation notes. Development calibration only.
- [Tableau teachers](tableau_reference_v1/README.md): independent chart references; numerical checks do not replace visual/interaction/export acceptance.

Use `scripts/build_reliability_gold.py --check` for the new pack. Use `scripts/check_reliability_canaries.py --require-product-gates --output output/my-gate.json` for strict product release checks. The latter currently fails on known semantic escapes; successful execution is not a passing answer. Paid runs require `--live` and retain the shared budget ledger.

Original seed-set notes follow; they are historical planning material, not measured capability claims.

## Files

- `seed_questions.yaml` — The initial 23-question test set covering SQL, Python, viz, ambiguous, trick, and follow-up categories. This is the design spec **and** test set.

## How we use this

1. **Now (week 1):** The seed set drives the design. Every question in here is a capability the agent must eventually handle. Adding a feature that doesn't help any of these questions is scope creep.
2. **Evaluation:** The harness records execution, gold answer correctness and report coverage separately. Human review checks methods and evidence; an LLM judge alone cannot certify numerical correctness.
3. **Always:** When the agent fails on a real question during dev, **add it to this file** with the failure mode in `notes`. The seed set should grow.

## Categories — what they mean for tool choice

| Category    | Expected tool | Why                                              |
| ----------- | ------------- | ------------------------------------------------ |
| `sql`       | DuckDB SQL    | Aggregation/filter/join over full dataset        |
| `python`    | pandas        | Stats, time-series, distributions, ML-flavored   |
| `viz`       | Plotly        | User asked for a chart, OR result reads better as one |
| `ambiguous` | clarify       | Under-specified — agent must ask, not guess     |
| `trick`     | refuse        | Question is unanswerable from data, OR unsafe   |
| `followup`  | depends       | Resolves via session memory of prior turn        |

## Editing rules

- Keep `id`s stable. They appear in eval result files.
- Each question must have honest `notes` describing what's hard about it.
- When in doubt, prefer adding a question over removing one.
