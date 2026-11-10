# agent-budget-guard

[![PyPI version](https://img.shields.io/pypi/v/trysac-agent-budget-guard)](https://pypi.org/project/trysac-agent-budget-guard/)
[![License: MIT](https://img.shields.io/pypi/l/trysac-agent-budget-guard)](LICENSE)
[![CI](https://github.com/Trysac/u222-agent-budget-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/Trysac/u222-agent-budget-guard/actions/workflows/ci.yml)

Framework-neutral budget guard for agent runs: caps on steps, tokens, tool calls per tool and elapsed time with an injected clock, nested sub-budgets and terminal exceeded state

## Problem

Describe in two or three sentences the concrete problem this utility solves, who hits it, and what
they do today without it. Replace this paragraph when the first real feature lands.

## Install

```sh
pip install trysac-agent-budget-guard
# or
uv add trysac-agent-budget-guard
```

Requires Python 3.10 or newer. No runtime dependencies.

## Usage

```python
import agent_budget_guard

# normalize() trims surrounding whitespace and lower-cases the text.
print(agent_budget_guard.normalize("  Hello World "))  # -> "hello world"

# Blank input is rejected explicitly instead of returning an empty string.
try:
    agent_budget_guard.normalize("   ")
except ValueError as exc:
    print(f"rejected: {exc}")
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) and [decisions](docs/decisions/)
- [Testing](docs/TESTING.md) and [security](docs/SECURITY.md)
- [Project record](docs/PROJECT.md) and [stack](docs/STACK.md)
- [Changelog](CHANGELOG.md) and [contributing guide](CONTRIBUTING.md)

## License

[MIT](LICENSE) (c) 2026 Miguel Gomez
