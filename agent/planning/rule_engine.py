from __future__ import annotations

from .models import ExpertActivation, ExpertRule, ProtocolProfile


def _match(profile_data: dict[str, object], conditions: dict[str, object]) -> bool:
    for key, expected in conditions.items():
        if key == "always":
            return bool(expected)
        actual = profile_data.get(key)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def activate_rules(rules: list[ExpertRule], profile: ProtocolProfile) -> list[ExpertActivation]:
    activations: list[ExpertActivation] = []
    for rule in rules:
        matched = _match(profile.data, rule.conditions)
        reasons = []
        for key, expected in rule.conditions.items():
            if key == "always":
                reasons.append("Always-on rule for spec determinism")
            else:
                reasons.append(f"{key}={profile.data.get(key)!r} matches {expected!r}")
        activations.append(
            ExpertActivation(
                rule_id=rule.rule_id,
                title=rule.title,
                matched=matched,
                reasons=reasons if matched else [],
                engineering_obligations=rule.engineering_obligations if matched else [],
                recommended_patterns=rule.recommended_patterns if matched else [],
                required_components=rule.required_components if matched else [],
                spec_impacts=rule.spec_impacts if matched else [],
                evidence_refs=[],
            )
        )
    return [item for item in activations if item.matched]
