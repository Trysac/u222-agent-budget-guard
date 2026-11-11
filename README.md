# agent-budget-guard

[![PyPI version](https://img.shields.io/pypi/v/agent-budget-guard)](https://pypi.org/project/agent-budget-guard/)
[![License: MIT](https://img.shields.io/pypi/l/agent-budget-guard)](LICENSE)
[![CI](https://github.com/Trysac/u222-agent-budget-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/Trysac/u222-agent-budget-guard/actions/workflows/ci.yml)

Framework-neutral budget guard for agent runs: caps on steps, tokens, tool calls per tool and elapsed time with an injected clock, nested sub-budgets and terminal exceeded state

## Problem

Agent loops retry, fan out into sub-agents and run unsupervised, and without hard caps a single bad
run burns thousands of steps, millions of tokens or hours of wall time. Each framework ships its own
partial limits, and sub-agents usually escape the parent budget entirely. This library gives any loop
one small guard object with exact counting, an injected monotonic clock, child budgets that draw from
the parent and a terminal exceeded state; cost in currency stays outside (convert tokens yourself).

## Install

```sh
pip install agent-budget-guard
# or
uv add agent-budget-guard
```

Requires Python 3.10 or newer. No runtime dependencies.

## Usage

```python
from agent_budget_guard import BudgetExceeded, BudgetGuard, Limits

guard = BudgetGuard(
    Limits(max_steps=2, max_tokens=1000),
    warn_at=0.8,
    on_event=print,  # warning/exceeded dicts; never prompts or tool arguments
)

guard.charge_step()
guard.charge_tokens(400, 100)
print(guard.usage())  # Usage(steps=1, tokens=500, ...)
print(guard.remaining())  # {'steps': 1, 'tokens': 500, 'tool_calls': None, 'time': None}

# A child has its own limits but every charge is also recorded on the parent.
child = guard.child(Limits(max_tokens=200))
child.charge_tokens(150, 0)

try:
    guard.charge_step()  # second step, within the limit
    guard.charge_step()  # third step, over the limit
except BudgetExceeded as exc:
    print(exc.code, exc.scope)  # -> steps self
    print(exc)  # -> step budget exceeded: 3 > 2
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) and [decisions](docs/decisions/)
- [Testing](docs/TESTING.md) and [security](docs/SECURITY.md)
- [Project record](docs/PROJECT.md) and [stack](docs/STACK.md)
- [Changelog](CHANGELOG.md) and [contributing guide](CONTRIBUTING.md)

## License

[MIT](LICENSE) (c) 2026 Miguel Gomez
