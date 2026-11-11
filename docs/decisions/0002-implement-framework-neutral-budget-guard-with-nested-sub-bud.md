# Implement framework-neutral budget guard with nested sub-budgets

Status: accepted (generated, pending human review)

## Context

Agent runs need hard caps on steps, tokens, tool calls and wall time. Existing framework limits are partial and sub-agents often escape parent budgets. A single small guard object with exact counting, an injected clock, child budgets that draw from the parent, and a terminal exceeded state is required.

## Decision

Create a Python library `agent_budget_guard` with frozen dataclasses Limits and Usage, and a BudgetGuard class. The guard records usage then checks limits in a fixed order (steps, tokens, tool_calls, time); the first violation raises BudgetExceeded with a machine-readable code and scope ('self' or 'parent'). Children share clock, on_event and a root lock, and propagate usage to ancestors. Once exceeded, the guard becomes terminal and subsequent charges raise the same error without recording. Warning events are emitted when a warn_at threshold is crossed, and exceeded events when raised. The library uses only the standard library.

## Consequences

The library provides exact, testable budget enforcement that can be embedded in any agent loop. The terminal state prevents silent bypass after a limit is crossed. Thread safety ensures concurrent charges are counted correctly. No runtime dependencies are introduced.
