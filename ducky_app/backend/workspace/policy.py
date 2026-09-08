"""Write policies: pluggable checks the pipeline runs before touching disk."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, runtime_checkable

from backend.workspace.identity import RunContext


@dataclass(frozen=True)
class WriteRequest:
    op: str  # write | create | rename | move | delete
    paths: tuple[str, ...]  # project-relative, normalised; destination last for rename/move
    tool: str = ""
    ctx: RunContext | None = None


@dataclass(frozen=True)
class Decision:
    allow: bool = True
    reason: str = ""
    hint: str = ""
    # True when a policy in shadow mode would have denied: the write proceeds,
    # the violation is journaled and surfaced.
    shadow_violation: bool = False
    policy: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def in_lane(self) -> bool:
        return self.allow and not self.shadow_violation


ALLOW = Decision()


@runtime_checkable
class WritePolicy(Protocol):
    name: str

    def check(self, request: WriteRequest) -> Decision: ...


def evaluate(policies: Iterable[WritePolicy], request: WriteRequest) -> Decision:
    """First denial wins; then the first shadow violation; then the first named
    allow (a policy that applied and passed); otherwise ALLOW."""
    shadow: Decision | None = None
    applied: Decision | None = None
    for policy in policies:
        decision = policy.check(request)
        if not decision.allow:
            return decision
        if decision.shadow_violation:
            if shadow is None:
                shadow = decision
        elif decision.policy and applied is None:
            applied = decision
    return shadow or applied or ALLOW
