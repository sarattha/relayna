from __future__ import annotations

from dataclasses import dataclass

from ..topology.workflow import WorkflowStage


@dataclass(slots=True, frozen=True)
class TransitionRule:
    source: str
    targets: tuple[str, ...]

    @classmethod
    def from_stage(cls, stage: WorkflowStage) -> TransitionRule:
        return cls(source=stage.name, targets=tuple(stage.allowed_next_stages))


def validate_transition(current_stage: str, next_stage: str, rules: tuple[TransitionRule, ...]) -> bool:
    for rule in rules:
        if rule.source == current_stage:
            return next_stage in rule.targets
    return False


__all__ = ["TransitionRule", "validate_transition"]
