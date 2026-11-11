# Architecture

## Purpose

`agent-budget-guard` is a small, dependency-free Python library: Framework-neutral budget guard for agent runs: caps on steps, tokens, tool calls per tool and elapsed time with an injected clock, nested sub-budgets and terminal exceeded state

## Example

```python
from agent_budget_guard import BudgetExceeded, BudgetGuard, Limits

guard = BudgetGuard(Limits(max_steps=2, max_tokens=1000), warn_at=0.8, on_event=print)
guard.charge_step()
guard.charge_tokens(400, 100)
child = guard.child(Limits(max_tokens=200))
child.charge_tokens(150, 0)
try:
    guard.charge_step()
    guard.charge_step()
except BudgetExceeded as exc:
    assert exc.code == "steps" and exc.scope == "self"
```

## Shape

```text
caller code --> agent_budget_guard (public API in __init__.py)
                    |
                    +-- pure functions, no I/O, no global state
```

- **Public API**: the names listed in `__all__` of `src/agent_budget_guard/__init__.py`. Anything else is
  internal and may change without a major version.
- **Errors**: invalid input raises built-in exceptions (`ValueError`, `TypeError`) with a clear
  message; the library never prints or exits.
- **Side effects**: none. If a feature needs I/O, isolate it in its own module behind a small
  interface and record the choice in an ADR.

## Quality contract

`scripts/check.py` is the single definition of the gates; `fabrica.json` tells the factory and CI
how to run it. See [TESTING.md](TESTING.md).

## Decisions

Architecture decisions are recorded in [decisions/](decisions/) as numbered ADRs.
