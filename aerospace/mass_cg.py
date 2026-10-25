"""Mass and centre-of-gravity roll-up from declared components (stdlib only)."""
from aerospace.aircraft import G
from aerospace.requirements import FIRM, Sourced, SpecError


def rollup(components, mac_le_x_mm=None, mac_mm=None):
    """components: [{name, value|(thrust_n + thrust_to_weight), state, source, x_mm?}].
    Returns (outputs, blockers). Mass is declared or derived from a declared
    thrust-to-weight ratio; it is never computed from CAD volume."""
    if not isinstance(components, list) or not components:
        raise SpecError("mass_components must be a non-empty list")
    total, moment, have_x, blockers, rows = 0.0, 0.0, True, [], []
    for c in components:
        name = str(c.get("name", "?"))
        if "thrust_n" in c:
            tw = Sourced.parse({"value": c.get("thrust_to_weight"), "state": c.get("state", "draft"), "source": c.get("source", "")},
                               f"mass_components[{name}].thrust_to_weight")
            m = float(c["thrust_n"]) / (G * tw.value)
            sv = tw
        else:
            sv = Sourced.parse(c, f"mass_components[{name}]")
            m = sv.value
        if m <= 0:
            raise SpecError(f"mass_components[{name}] mass must be positive")
        if sv.state not in FIRM:
            blockers.append(f"mass component {name} is '{sv.state.value}', must be confirmed or verified")
        total += m
        if c.get("x_mm") is None:
            have_x = False
        else:
            moment += m * float(c["x_mm"])
        rows.append({"name": name, "mass_kg": round(m, 2)})
    out = {"mass_kg": round(total, 3), "components": rows}
    if have_x:
        cg = moment / total
        out["cg_x_mm"] = round(cg, 1)
        if mac_mm:
            out["cg_percent_mac"] = round((cg - mac_le_x_mm) / mac_mm * 100, 1)
    return out, blockers
