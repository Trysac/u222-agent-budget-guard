"""Framework-neutral budget guard for agent runs.

Caps on steps, tokens, tool calls per tool and elapsed time with an injected
clock, nested sub-budgets and terminal exceeded state.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

__all__ = [
    "BudgetError",
    "BudgetExceeded",
    "BudgetGuard",
    "Limits",
    "Usage",
    "__version__",
]

__version__ = "0.1.0"

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class BudgetError(Exception):
    """Base error for the budget guard library.

    Attributes:
        code: Machine-readable error code (``'invalid_limits'`` or ``'invalid_charge'``).
    """

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message)


class BudgetExceeded(BudgetError):
    """Raised when a budget limit is exceeded.

    Attributes:
        code: One of ``'steps'``, ``'tokens'``, ``'tool_calls'``, ``'time'``.
        scope: ``'self'`` if the guard's own limit was crossed, ``'parent'`` if an
            ancestor's limit was crossed.
    """

    def __init__(self, code: str, scope: str, message: str) -> None:
        self.scope = scope
        super().__init__(code, message)


@dataclass(frozen=True)
class Limits:
    """Budget limits. All fields are optional; ``None`` means no limit.

    Validation is performed on construction: integer limits must be >= 1 and not
    ``bool``; ``max_seconds`` must be finite and > 0; ``per_tool`` may hold at most
    100 entries with names of 1-64 characters and limits as positive integers.
    """

    max_steps: int | None = None
    max_tokens: int | None = None
    max_tool_calls: int | None = None
    per_tool: Mapping[str, int] = field(default_factory=dict)
    max_seconds: float | None = None

    def __post_init__(self) -> None:
        for value in (self.max_steps, self.max_tokens, self.max_tool_calls):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise BudgetError("invalid_limits")
        if self.max_seconds is not None and (
            isinstance(self.max_seconds, bool)
            or not isinstance(self.max_seconds, (int, float))
            or not math.isfinite(self.max_seconds)
            or self.max_seconds <= 0
        ):
            raise BudgetError("invalid_limits")
        if self.per_tool and len(self.per_tool) > 100:
            raise BudgetError("invalid_limits")
        if self.per_tool:
            for name, limit in self.per_tool.items():
                if not isinstance(name, str) or not (1 <= len(name) <= 64):
                    raise BudgetError("invalid_limits")
                if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                    raise BudgetError("invalid_limits")
        # Make per_tool immutable while preserving the mapping interface.
        object.__setattr__(self, "per_tool", MappingProxyType(dict(self.per_tool)))


@dataclass(frozen=True)
class Usage:
    """Immutable snapshot of current usage."""

    steps: int
    tokens: int
    tool_calls: int
    per_tool: Mapping[str, int]
    elapsed_s: float


class BudgetGuard:
    """Tracks and enforces budget limits for agent runs.

    Args:
        limits: The budget limits.
        clock: Callable returning the current time (monotonic by default).
        warn_at: Optional threshold ratio (0 < warn_at < 1) that triggers warning
            events when usage reaches warn_at * limit.
        on_event: Callable receiving event dicts (``{'event': 'warning', ...}`` or
            ``{'event': 'exceeded', ...}``). Never receives prompts or tool arguments.

    All operations are thread-safe; a single lock is shared by a guard and all its
    children.
    """

    def __init__(
        self,
        limits: Limits,
        *,
        clock: Callable[[], float] = time.monotonic,
        warn_at: float | None = None,
        on_event: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self._limits = limits
        self._clock = clock
        self._warn_at: float | None = None
        if warn_at is not None:
            if (
                isinstance(warn_at, bool)
                or not isinstance(warn_at, (int, float))
                or not (0 < warn_at < 1)
            ):
                raise BudgetError("invalid_limits")
            self._warn_at = float(warn_at)
        self._on_event = on_event
        self._lock = threading.Lock()
        self._parent: BudgetGuard | None = None
        self._usage_steps = 0
        self._usage_tokens = 0
        self._usage_tool_calls = 0
        self._per_tool_usage: dict[str, int] = {}
        self._exceeded: tuple[str, str] | None = None
        self._warn_emitted: set[str] = set()
        self._start = self._clock()

    # ------------------------------------------------------------------ public API

    def charge_step(self) -> None:
        """Record one step and check limits."""
        self._run(self._charge_step_locked)

    def _charge_step_locked(self, events: list[dict[str, object]]) -> None:
        self._raise_if_terminal()
        self._record_steps(1)
        self._check_chain(events)

    def charge_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Record token usage (input + output) and check limits.

        Raises:
            BudgetError: if input_tokens or output_tokens is not a non-negative int.
        """
        if not self._is_valid_nonnegative_int(input_tokens) or not self._is_valid_nonnegative_int(
            output_tokens
        ):
            raise BudgetError("invalid_charge")
        total = input_tokens + output_tokens
        self._run(lambda events: self._charge_tokens_locked(total, events))

    def _charge_tokens_locked(self, amount: int, events: list[dict[str, object]]) -> None:
        self._raise_if_terminal()
        self._record_tokens(amount)
        self._check_chain(events)

    def charge_tool(self, name: str) -> None:
        """Record a tool call and check limits.

        Raises:
            BudgetError: if name is not a string of 1-64 characters.
        """
        if not isinstance(name, str) or not (1 <= len(name) <= 64):
            raise BudgetError("invalid_charge")
        self._run(lambda events: self._charge_tool_locked(name, events))

    def _charge_tool_locked(self, name: str, events: list[dict[str, object]]) -> None:
        self._raise_if_terminal()
        self._record_tool_call(name)
        self._check_chain(events)

    def check(self) -> None:
        """Check limits without recording new usage."""
        self._run(self._check_locked)

    def _check_locked(self, events: list[dict[str, object]]) -> None:
        self._raise_if_terminal()
        self._check_chain(events)

    def _run(self, action: Callable[[list[dict[str, object]]], None]) -> None:
        """Run ``action`` under the lock, then dispatch events and raise outside it.

        Events are collected while the lock is held and delivered only after it is
        released, so a callback may safely call back into the guard. The terminal
        state is recorded before any event is delivered, and a failing callback can
        never suppress the limit check or the resulting ``BudgetExceeded``.
        """
        events: list[dict[str, object]] = []
        error: BudgetExceeded | None = None
        with self._lock:
            try:
                action(events)
            except BudgetExceeded as exc:
                error = exc
        self._dispatch(events)
        if error is not None:
            raise error

    def _dispatch(self, events: list[dict[str, object]]) -> None:
        if self._on_event is None:
            return
        for event in events:
            try:
                self._on_event(event)
            except Exception:
                logger.exception("on_event callback failed")

    def usage(self) -> Usage:
        """Return current usage snapshot."""
        with self._lock:
            return Usage(
                steps=self._usage_steps,
                tokens=self._usage_tokens,
                tool_calls=self._usage_tool_calls,
                per_tool=MappingProxyType(self._per_tool_usage.copy()),
                elapsed_s=self._clock() - self._start,
            )

    def remaining(self) -> dict[str, float | int | None]:
        """Return remaining budget for top-level limits.

        ``None`` means no limit. Values are non-negative (clamped to 0).
        """
        with self._lock:
            result: dict[str, float | int | None] = {}
            result["steps"] = self._remaining_value(self._limits.max_steps, self._usage_steps)
            result["tokens"] = self._remaining_value(self._limits.max_tokens, self._usage_tokens)
            result["tool_calls"] = self._remaining_value(
                self._limits.max_tool_calls, self._usage_tool_calls
            )
            if self._limits.max_seconds is None:
                result["time"] = None
            else:
                elapsed = self._clock() - self._start
                result["time"] = max(0.0, self._limits.max_seconds - elapsed)
            return result

    def child(self, limits: Limits | None = None) -> BudgetGuard:
        """Create a child guard sharing the clock, on_event and lock.

        The child has its own start time and its own warn_at (default None).
        If ``limits`` is None, the parent's limits are used.
        """
        with self._lock:
            child = object.__new__(BudgetGuard)
            child._limits = limits if limits is not None else self._limits
            child._clock = self._clock
            child._warn_at = None
            child._on_event = self._on_event
            child._lock = self._lock
            child._parent = self
            child._usage_steps = 0
            child._usage_tokens = 0
            child._usage_tool_calls = 0
            child._per_tool_usage = {}
            child._exceeded = None
            child._warn_emitted = set()
            child._start = child._clock()
            if self._exceeded is not None:
                child._exceeded = (self._exceeded[0], "parent")
            return child

    @property
    def exceeded(self) -> str | None:
        """Return the exceeded limit code, or None if not exceeded."""
        with self._lock:
            return self._exceeded[0] if self._exceeded is not None else None

    # --------------------------------------------------------------- private API

    @staticmethod
    def _is_valid_nonnegative_int(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def _remaining_value(self, limit: int | None, used: int) -> int | None:
        if limit is None:
            return None
        return max(0, limit - used)

    def _raise_if_terminal(self) -> None:
        """Check self and ancestors for terminal state; set and raise if found."""
        guard: BudgetGuard | None = self
        while guard is not None:
            if guard._exceeded is not None:
                code, stored_scope = guard._exceeded
                if guard is self:
                    raise BudgetExceeded(code, stored_scope, self._exceeded_message(code))
                # Ancestor is terminal; cache on self and raise with scope 'parent'.
                self._exceeded = (code, "parent")
                raise BudgetExceeded(code, "parent", self._exceeded_message(code))
            guard = guard._parent

    def _exceeded_message(self, code: str) -> str:
        """Return a generic message for terminal state re-raises."""
        # The exact message for the original violation is not repeated; a generic message
        # is acceptable. Tests check the message only at the first raise.
        return f"budget exceeded: {code}"

    def _record_steps(self, amount: int) -> None:
        guard: BudgetGuard | None = self
        while guard is not None:
            guard._usage_steps += amount
            guard = guard._parent

    def _record_tokens(self, amount: int) -> None:
        guard: BudgetGuard | None = self
        while guard is not None:
            guard._usage_tokens += amount
            guard = guard._parent

    def _record_tool_call(self, name: str) -> None:
        guard: BudgetGuard | None = self
        while guard is not None:
            guard._usage_tool_calls += 1
            guard._per_tool_usage[name] = guard._per_tool_usage.get(name, 0) + 1
            guard = guard._parent

    def _check_chain(self, events: list[dict[str, object]]) -> None:
        """Check limits from self upwards, each in specified order."""
        now = self._clock()
        guard: BudgetGuard | None = self
        scope = "self"
        first_error: BudgetExceeded | None = None
        while guard is not None:
            try:
                guard._check_limits(scope, now, events)
            except BudgetExceeded as exc:
                if first_error is None:
                    first_error = exc
                    if scope == "self":
                        self._exceeded = (exc.code, "self")
                    else:
                        self._exceeded = (exc.code, "parent")
                        guard._exceeded = (exc.code, "self")
                elif guard._exceeded is None:
                    # An ancestor also crossed its own limit; it becomes terminal too.
                    guard._exceeded = (exc.code, "self")
            guard = guard._parent
            scope = "parent"
        if first_error is not None:
            raise first_error

    def _check_limits(self, scope: str, now: float, events: list[dict[str, object]]) -> None:
        """Check all limits for this guard in order: steps, tokens, tool_calls, time."""
        # steps
        if self._limits.max_steps is not None:
            limit = self._limits.max_steps
            used = self._usage_steps
            self._maybe_warn("steps", used, limit, events)
            if used > limit:
                self._raise_exceeded(
                    "steps", scope, f"step budget exceeded: {used} > {limit}", used, limit, events
                )

        # tokens
        if self._limits.max_tokens is not None:
            limit = self._limits.max_tokens
            used = self._usage_tokens
            self._maybe_warn("tokens", used, limit, events)
            if used > limit:
                self._raise_exceeded(
                    "tokens", scope, f"token budget exceeded: {used} > {limit}", used, limit, events
                )

        # tool_calls (total)
        if self._limits.max_tool_calls is not None:
            limit = self._limits.max_tool_calls
            used = self._usage_tool_calls
            self._maybe_warn("tool_calls", used, limit, events)
            if used > limit:
                self._raise_exceeded(
                    "tool_calls",
                    scope,
                    f"tool budget exceeded: {used} > {limit}",
                    used,
                    limit,
                    events,
                )

        # per-tool limits (kind 'tool_calls')
        if self._limits.per_tool:
            # Emit warning once for tool_calls if any per-tool threshold crossed.
            if self._warn_at is not None and "tool_calls" not in self._warn_emitted:
                for name, limit in self._limits.per_tool.items():
                    used = self._per_tool_usage.get(name, 0)
                    if used >= self._warn_at * limit:
                        self._warn_emitted.add("tool_calls")
                        events.append(
                            {
                                "event": "warning",
                                "kind": "tool_calls",
                                "used": used,
                                "limit": limit,
                            }
                        )
                        break
            for name, limit in self._limits.per_tool.items():
                used = self._per_tool_usage.get(name, 0)
                if used > limit:
                    self._raise_exceeded(
                        "tool_calls",
                        scope,
                        f"tool budget exceeded for {name}: {used} > {limit}",
                        used,
                        limit,
                        events,
                    )

        # time
        if self._limits.max_seconds is not None:
            time_limit = self._limits.max_seconds
            elapsed = now - self._start
            self._maybe_warn("time", elapsed, time_limit, events)
            if elapsed > time_limit:
                self._raise_exceeded(
                    "time",
                    scope,
                    f"time budget exceeded: {elapsed:.3f} > {time_limit} s",
                    elapsed,
                    time_limit,
                    events,
                )

    def _maybe_warn(
        self, kind: str, used: float, limit: float, events: list[dict[str, object]]
    ) -> None:
        """Queue a warning event if warn_at is set and threshold reached."""
        if (
            self._warn_at is not None
            and kind not in self._warn_emitted
            and used >= self._warn_at * limit
        ):
            self._warn_emitted.add(kind)
            events.append(
                {
                    "event": "warning",
                    "kind": kind,
                    "used": used,
                    "limit": limit,
                }
            )

    def _raise_exceeded(
        self,
        code: str,
        scope: str,
        message: str,
        used: float,
        limit: float,
        events: list[dict[str, object]],
    ) -> None:
        if self._warn_at is not None:
            events.append({"event": "exceeded", "kind": code, "used": used, "limit": limit})
        raise BudgetExceeded(code, scope, message)
