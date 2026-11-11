# Project record

## Objective

Framework-neutral budget guard for agent runs: caps on steps, tokens, tool calls per tool and elapsed time with an injected clock, nested sub-budgets and terminal exceeded state

## Scope

- In scope: the public API documented in [README.md](../README.md), typed and tested.
- Out of scope: anything that needs runtime dependencies or I/O without an accepted ADR.

## Timeline

| Date | Event |
|---|---|
| 2026 | Repository created from the factory utility template. |

## Effort metrics

Measured values only; a value that has not been measured stays "pending measurement".

| Metric | Value |
|---|---|
| Tokens (input / output) | 1657583 / 85955 |
| Hours (human) | pending measurement |
| Cost (USD) | 0.1102 |

## Factory run u222 (measured from API usage)

| Date (UTC) | Phase | Model | Batch | Input | Output | Cache write | Cache read | Cost (USD) |
|---|---|---|---|---|---|---|---|---|
| 2026-11-10 | generation | deepseek-v4-pro | yes | 1691 | 19637 | 0 | 3328 | 0.0401 |
| 2026-11-10 | fix-1 | deepseek-flash | no | 15256 | 13996 | 0 | 250496 | 0.0114 |
| 2026-11-10 | audit | deepseek-flash | no | 32518 | 19020 | 0 | 467328 | 0.0177 |
| 2026-11-10 | fix-2 | deepseek-flash | no | 27461 | 17302 | 0 | 761856 | 0.0168 |
| 2026-11-10 | docs | deepseek-flash | no | 97393 | 16000 | 0 | 256 | 0.0242 |

Total: $0.1102. Prices: deepseek line, as measured by its adapter. Providers serve the current version of each model id, so the date tells which version answered; versions, launch stages and price sources per line are in the factory's model register (docs/MODELS.md).
