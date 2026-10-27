"""Requirements and inputs with explicit states. Unknown never becomes fact."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class State(str, Enum):
    DRAFT = "draft"          # written down, not agreed
    ASSUMED = "assumed"      # working assumption, not confirmed by an owner
    CONFIRMED = "confirmed"  # agreed by a named source/owner
    VERIFIED = "verified"    # confirmed AND demonstrated by current evidence
    FAILED = "failed"


FIRM = (State.CONFIRMED, State.VERIFIED)

# Allowed manual transitions. VERIFIED additionally needs current evidence.
TRANSITIONS = {
    State.DRAFT: {State.ASSUMED, State.CONFIRMED},
    State.ASSUMED: {State.DRAFT, State.CONFIRMED},
    State.CONFIRMED: {State.ASSUMED, State.VERIFIED, State.FAILED},
    State.VERIFIED: {State.CONFIRMED, State.FAILED},   # drops back when evidence goes stale
    State.FAILED: {State.CONFIRMED},
}


class SpecError(ValueError):
    pass


def can_transition(old: State, new: State, has_current_evidence: bool) -> bool:
    if new not in TRANSITIONS[old]:
        return False
    return not (new is State.VERIFIED and not has_current_evidence)


@dataclass
class Sourced:
    """A value with provenance and state (a load, a factor of safety...)."""
    value: float
    state: State
    source: str
    unit: str = ""

    @staticmethod
    def parse(d, what: str) -> "Sourced":
        if not isinstance(d, dict) or "value" not in d:
            raise SpecError(f"{what}: expected an object with value, state, source")
        try:
            val = float(d["value"])
            st = State(d.get("state", "draft"))
        except (TypeError, ValueError):
            raise SpecError(f"{what}: bad value or state")
        if not str(d.get("source", "")).strip():
            raise SpecError(f"{what}: a source is required (who/what supplied this number)")
        return Sourced(val, st, str(d["source"]), str(d.get("unit", "")))


@dataclass
class Requirement:
    id: str
    text: str
    parameter: str            # output key, e.g. 'mass_kg', 'ms_min'
    comparator: str           # '<=', '>=', '=='
    target: float
    state: State
    source: str
    unit: str = ""
    evidence_ids: list = field(default_factory=list)

    @staticmethod
    def parse(d: dict) -> "Requirement":
        try:
            r = Requirement(str(d["id"]), str(d.get("text", "")), str(d["parameter"]),
                            str(d["comparator"]), float(d["target"]),
                            State(d.get("state", "draft")), str(d.get("source", "")),
                            str(d.get("unit", "")), list(d.get("evidence_ids", [])))
        except (KeyError, ValueError, TypeError) as e:
            raise SpecError(f"requirement invalid ({e})")
        if r.comparator not in ("<=", ">=", "=="):
            raise SpecError(f"requirement {r.id}: comparator must be <=, >= or ==")
        if not r.source.strip():
            raise SpecError(f"requirement {r.id}: source is required")
        return r

    def evaluate(self, outputs: dict) -> str:
        """'pass' | 'fail' | 'not_evaluable' against computed outputs."""
        v: Optional[float] = outputs.get(self.parameter)
        if v is None:
            return "not_evaluable"
        ok = {"<=": v <= self.target, ">=": v >= self.target,
              "==": abs(v - self.target) <= 1e-9 * max(1.0, abs(self.target))}[self.comparator]
        return "pass" if ok else "fail"
