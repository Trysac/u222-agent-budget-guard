"""Tests for agent_budget_guard."""

from __future__ import annotations

import threading

import pytest

from agent_budget_guard import (
    BudgetError,
    BudgetExceeded,
    BudgetGuard,
    Limits,
)


class FakeClock:
    """Controllable clock for tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.current = start

    def __call__(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


class EventCollector:
    """Collect events emitted by a guard."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def __call__(self, event: dict[str, object]) -> None:
        self.events.append(event)


# ----------------------------------------------------------------- acceptance tests


def test_acceptance_steps_limit_and_message() -> None:
    g = BudgetGuard(Limits(max_steps=2))
    g.charge_step()
    g.charge_step()
    with pytest.raises(BudgetExceeded) as exc_info:
        g.charge_step()
    assert exc_info.value.code == "steps"
    assert exc_info.value.scope == "self"
    assert str(exc_info.value) == "step budget exceeded: 3 > 2"


def test_acceptance_tokens_records_usage_then_raises() -> None:
    g = BudgetGuard(Limits(max_tokens=1000))
    g.charge_tokens(400, 100)
    assert g.usage().tokens == 500
    with pytest.raises(BudgetExceeded) as exc_info:
        g.charge_tokens(300, 300)
    assert exc_info.value.code == "tokens"
    assert g.usage().tokens == 1100


def test_acceptance_time_check_with_injected_clock() -> None:
    clock = FakeClock(0.0)
    g = BudgetGuard(Limits(max_seconds=30), clock=clock)
    clock.advance(30.0)
    g.check()  # at 30.0 passes
    clock.advance(0.5)
    with pytest.raises(BudgetExceeded) as exc_info:
        g.check()
    assert exc_info.value.code == "time"


def test_acceptance_child_scopes() -> None:
    parent = BudgetGuard(Limits(max_tokens=1000))
    parent.charge_tokens(100, 0)
    child = parent.child(Limits(max_tokens=800))
    with pytest.raises(BudgetExceeded) as exc_info:
        child.charge_tokens(900, 0)
    assert exc_info.value.code == "tokens"
    assert exc_info.value.scope == "self"
    assert parent.usage().tokens == 1000
    assert parent.exceeded is None

    parent2 = BudgetGuard(Limits(max_tokens=1000))
    parent2.charge_tokens(500, 0)
    child2 = parent2.child(Limits(max_tokens=2000))
    with pytest.raises(BudgetExceeded) as exc_info:
        child2.charge_tokens(600, 0)
    assert exc_info.value.code == "tokens"
    assert exc_info.value.scope == "parent"
    assert parent2.exceeded == "tokens"


def test_acceptance_terminal_state_repeats_same_error() -> None:
    clock = FakeClock(0.0)
    g = BudgetGuard(Limits(max_steps=1), clock=clock)
    g.charge_step()
    with pytest.raises(BudgetExceeded):
        g.charge_step()
    usage_after_exceed = g.usage()
    for action in [
        lambda: g.charge_step(),
        lambda: g.charge_tokens(1, 1),
        lambda: g.charge_tool("search"),
        lambda: g.check(),
    ]:
        with pytest.raises(BudgetExceeded) as exc_info:
            action()
        assert exc_info.value.code == "steps"
    assert g.usage() == usage_after_exceed


def test_acceptance_per_tool_limit_and_message() -> None:
    g = BudgetGuard(Limits(per_tool={"search": 3}))
    for _ in range(3):
        g.charge_tool("search")
    with pytest.raises(BudgetExceeded) as exc_info:
        g.charge_tool("search")
    assert exc_info.value.code == "tool_calls"
    assert str(exc_info.value) == "tool budget exceeded for search: 4 > 3"


def test_acceptance_invalid_limits_and_charges() -> None:
    with pytest.raises(BudgetError) as exc_info:
        Limits(max_steps=0)
    assert exc_info.value.code == "invalid_limits"
    with pytest.raises(BudgetError) as exc_info:
        Limits(max_tokens=True)
    assert exc_info.value.code == "invalid_limits"
    with pytest.raises(BudgetError) as exc_info:
        Limits(per_tool={f"tool{i}": 1 for i in range(101)})
    assert exc_info.value.code == "invalid_limits"

    g = BudgetGuard(Limits())
    with pytest.raises(BudgetError) as exc_info:
        g.charge_tokens(-1, 0)
    assert exc_info.value.code == "invalid_charge"
    with pytest.raises(BudgetError) as exc_info:
        g.charge_tool("x" * 65)
    assert exc_info.value.code == "invalid_charge"


def test_acceptance_warn_events() -> None:
    collector = EventCollector()
    g = BudgetGuard(Limits(max_tokens=1000), warn_at=0.8, on_event=collector)
    g.charge_tokens(800, 0)
    assert len(collector.events) == 1
    assert collector.events[0] == {"event": "warning", "kind": "tokens", "used": 800, "limit": 1000}
    with pytest.raises(BudgetExceeded):
        g.charge_tokens(300, 0)
    assert len(collector.events) == 2
    assert collector.events[1] == {
        "event": "exceeded",
        "kind": "tokens",
        "used": 1100,
        "limit": 1000,
    }


def test_on_event_can_call_back_into_guard() -> None:
    seen: list[tuple[dict[str, object], int, str | None]] = []
    guard: BudgetGuard

    def callback(event: dict[str, object]) -> None:
        seen.append((event, guard.usage().tokens, guard.exceeded))

    guard = BudgetGuard(Limits(max_tokens=10), warn_at=0.5, on_event=callback)
    guard.charge_tokens(5, 0)
    assert seen[0][0]["event"] == "warning"
    assert seen[0][1] == 5


def test_failing_callback_does_not_skip_exceeded() -> None:
    def callback(event: dict[str, object]) -> None:
        raise RuntimeError("callback boom")

    guard = BudgetGuard(Limits(max_tokens=10), warn_at=0.5, on_event=callback)
    guard.charge_tokens(5, 0)  # the failing warning callback is swallowed
    with pytest.raises(BudgetExceeded) as exc_info:
        guard.charge_tokens(6, 0)
    assert exc_info.value.code == "tokens"
    assert guard.exceeded == "tokens"


# ------------------------------------------------------------------- edge cases


def test_no_limits_never_raises() -> None:
    g = BudgetGuard(Limits())
    for _ in range(100):
        g.charge_step()
        g.charge_tokens(10, 20)
        g.charge_tool("a")
    g.check()
    assert g.usage().steps == 100


def test_usage_exactly_at_limit_passes() -> None:
    g = BudgetGuard(Limits(max_steps=2, max_tokens=10, max_tool_calls=3, per_tool={"x": 2}))
    g.charge_step()
    g.charge_step()
    g.charge_tokens(5, 5)
    g.charge_tool("x")
    g.charge_tool("x")
    g.charge_tool("y")
    g.check()
    assert g.exceeded is None


def test_grandchild_crossing_root_limit_scope_parent() -> None:
    root = BudgetGuard(Limits(max_tokens=100))
    child = root.child(Limits(max_tokens=200))
    grandchild = child.child(Limits(max_tokens=300))
    with pytest.raises(BudgetExceeded) as exc_info:
        grandchild.charge_tokens(101, 0)
    assert exc_info.value.code == "tokens"
    assert exc_info.value.scope == "parent"
    assert root.exceeded == "tokens"


def test_concurrent_charges_total_exactly_limit() -> None:
    limit = 1000
    g = BudgetGuard(Limits(max_tokens=limit))
    threads = []
    for _ in range(8):
        t = threading.Thread(target=g.charge_tokens, args=(limit // 8, 0))
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert g.usage().tokens == limit
    g.check()
    assert g.exceeded is None


def test_time_checked_by_charge_step() -> None:
    clock = FakeClock(0.0)
    g = BudgetGuard(Limits(max_seconds=10), clock=clock)
    clock.advance(10.0)
    g.charge_step()
    clock.advance(0.1)
    with pytest.raises(BudgetExceeded) as exc_info:
        g.charge_step()
    assert exc_info.value.code == "time"


def test_remaining_returns_none_for_unbounded() -> None:
    g = BudgetGuard(Limits())
    rem = g.remaining()
    assert rem["steps"] is None
    assert rem["tokens"] is None
    assert rem["tool_calls"] is None
    assert rem["time"] is None


def test_remaining_clamps_to_zero() -> None:
    g = BudgetGuard(Limits(max_steps=2, max_seconds=5))
    g.charge_step()
    g.charge_step()
    rem = g.remaining()
    assert rem["steps"] == 0
    # time remaining positive until clock advances
    assert rem["time"] is not None and rem["time"] > 0


def test_child_inherits_parent_limits_when_none() -> None:
    parent = BudgetGuard(Limits(max_steps=1))
    child = parent.child()
    child.charge_step()
    with pytest.raises(BudgetExceeded) as exc_info:
        child.charge_step()
    assert exc_info.value.scope == "self"
    assert parent.exceeded == "steps"


def test_child_of_terminal_parent_raises_at_first_charge() -> None:
    parent = BudgetGuard(Limits(max_steps=1))
    parent.charge_step()
    with pytest.raises(BudgetExceeded):
        parent.charge_step()
    child = parent.child()
    with pytest.raises(BudgetExceeded) as exc_info:
        child.charge_step()
    assert exc_info.value.code == "steps"
    assert exc_info.value.scope == "parent"
    with pytest.raises(BudgetExceeded):
        child.charge_tokens(1, 0)


def test_warning_emitted_once_per_kind() -> None:
    collector = EventCollector()
    g = BudgetGuard(Limits(max_steps=10, max_tokens=100), warn_at=0.5, on_event=collector)
    for _ in range(5):
        g.charge_step()
    g.charge_tokens(50, 0)
    g.charge_tokens(10, 0)
    assert len(collector.events) == 2  # one warning for steps, one for tokens
    assert {e["kind"] for e in collector.events if e["event"] == "warning"} == {"steps", "tokens"}


def test_warning_not_emitted_when_warn_at_none() -> None:
    collector = EventCollector()
    g = BudgetGuard(Limits(max_tokens=10), on_event=collector)
    g.charge_tokens(5, 0)
    assert collector.events == []


def test_usage_returns_immutable_mapping() -> None:
    g = BudgetGuard(Limits(per_tool={"search": 3}))
    g.charge_tool("search")
    usage = g.usage()
    with pytest.raises(TypeError):
        usage.per_tool["search"] = 100  # type: ignore[index]


def test_invalid_warn_at_raises_invalid_limits() -> None:
    with pytest.raises(BudgetError) as exc_info:
        BudgetGuard(Limits(), warn_at=1.0)
    assert exc_info.value.code == "invalid_limits"
    with pytest.raises(BudgetError) as exc_info:
        BudgetGuard(Limits(), warn_at=0.0)
    assert exc_info.value.code == "invalid_limits"
    with pytest.raises(BudgetError) as exc_info:
        BudgetGuard(Limits(), warn_at=-0.5)
    assert exc_info.value.code == "invalid_limits"


def test_child_does_not_inherit_warn_at() -> None:
    collector = EventCollector()
    parent = BudgetGuard(Limits(max_tokens=1000), warn_at=0.5, on_event=collector)
    child = parent.child(Limits(max_tokens=100))
    child.charge_tokens(60, 0)
    assert collector.events == []


def test_time_warning_and_exceeded_events() -> None:
    clock = FakeClock(0.0)
    collector = EventCollector()
    g = BudgetGuard(Limits(max_seconds=10), clock=clock, warn_at=0.5, on_event=collector)
    clock.advance(5.0)
    g.check()
    assert collector.events[0]["event"] == "warning"
    assert collector.events[0]["kind"] == "time"
    clock.advance(5.1)
    with pytest.raises(BudgetExceeded):
        g.check()
    assert collector.events[1]["event"] == "exceeded"
    assert collector.events[1]["kind"] == "time"
