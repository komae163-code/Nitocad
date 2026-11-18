"""Turns typed or pasted engineering symbols into the plain words the parsers and planners read.

People write 4.7kΩ, 10µF, Ø12, 45°, 50×60, ±0.1 and "pressure angle α = 20". The regex parsers and the
AI prompts are written for ASCII, so every entry point passes the user's text through here first.
Exported files stay ASCII on purpose: non-ASCII text can break CAD and silkscreen fonts. Symbols are
only shown to the user in the Studio.
"""
from __future__ import annotations

import re
from typing import Any

# Greek letters read as words. Capital omega is the ohm sign in electronics, handled separately.
GREEK_WORDS = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "Δ": "delta", "ε": "epsilon", "θ": "theta",
    "λ": "lambda", "ν": "nu", "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "φ": "phi", "Φ": "phi",
    "ψ": "psi", "ω": "omega",
}
_OHM = "ΩΩ"          # U+03A9 Greek capital omega and U+2126 ohm sign
_MICRO = "µμ"        # U+00B5 micro sign and U+03BC Greek small mu

_GREEK_RE = re.compile("[" + "".join(GREEK_WORDS) + "]")


def _greek(m: re.Match) -> str:
    s, i = m.string, m.start()
    word = GREEK_WORDS[m.group(0)]
    before = s[i - 1] if i > 0 else " "
    after = s[m.end()] if m.end() < len(s) else " "
    return (" " if before.isalnum() else "") + word + (" " if after.isalnum() else "")


def normalize(text: str) -> str:
    """ASCII version of `text`. Text without special symbols comes back unchanged."""
    if not text or text.isascii():
        return text
    t = text
    t = re.sub(r"[" + _OHM + r"]", "ohm", t)                                   # 4.7kΩ -> 4.7kohm
    t = re.sub(r"[" + _MICRO + r"](?=[A-Za-z])", "u", t)                        # 10µF -> 10uF
    t = re.sub(r"[" + _MICRO + r"]", "mu", t)                                   # friction coefficient mu
    t = re.sub(r"°\s*([CF])\b", r" deg\1", t)                                   # 25°C -> 25 degC
    t = re.sub(r"(\d)\s*°", r"\1 deg", t)                                       # 45° -> 45 deg
    t = t.replace("°", " deg ")
    t = re.sub(r"[Øø⌀]\s*(?=\d)", "diameter ", t)                               # Ø12 -> diameter 12
    t = t.replace("±", "+/-").replace("×", "x").replace("−", "-").replace("∅", "diameter ")
    t = _GREEK_RE.sub(_greek, t)
    return t


def normalize_history(history: list[dict] | None) -> list[dict] | None:
    """Copy of a chat history with every user message normalised. Other roles are left alone."""
    if not history:
        return history
    out: list[dict[str, Any]] = []
    for m in history:
        if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str):
            m = {**m, "content": normalize(m["content"])}
        out.append(m)
    return out
