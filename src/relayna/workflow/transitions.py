from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class TransitionRule:
    source: str
    targets: tuple[str, ...]


def validate_transition(current_stage: str, next_stage: str, rules: tuple[TransitionRule, ...]) -> bool:
    for rule in rules:
        if rule.source == current_stage:
            return next_stage in rule.targets
    return False


__all__ = ["TransitionRule", "validate_transition"]
