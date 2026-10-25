"""A-basis and B-basis allowables computed from YOUR coupon test data (stdlib only).

One-sided normal tolerance limits with the exact tolerance factor k (solved
numerically, not an approximation), an Anderson-Darling normality test, and a
lognormal fallback. This computes statistics from data you supply; it does NOT
contain MMPDS / CMH-17 values (those are licensed). The result is a
'user_certified' material basis, because handbook bases require a specified
sampling plan (batches/heats) that only your test programme can satisfy.
"""
import hashlib
import json
import math

from aerospace.engine_cycle import CycleError

P_COVER = {"A": 0.99, "B": 0.90}
CONFIDENCE = 0.95
MIN_N = 30
AD_CRIT_5PCT = 0.752          # D'Agostino & Stephens, normal with estimated mean and variance


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _phi_inv(p):
    lo, hi = -10.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _phi(mid) < p else (lo, mid)
    return (lo + hi) / 2


def _confidence(k, n, z_p, steps=3000):
    """gamma(k) = E_W[Phi(sqrt(n) (k W - z_p))], W = sqrt(chi2_{n-1}/(n-1))."""
    nu = n - 1
    log_c = math.log(2) + (nu / 2) * math.log(nu / 2) - math.lgamma(nu / 2)
    hi = 1 + 9 / math.sqrt(nu) + 1.0
    h = hi / steps
    tot = 0.0
    for i in range(steps + 1):
        w = i * h
        if w <= 0:
            continue
        dens = math.exp(log_c + (nu - 1) * math.log(w) - nu * w * w / 2)
        wgt = 1 if i in (0, steps) else (4 if i % 2 else 2)
        tot += wgt * dens * _phi(math.sqrt(n) * (k * w - z_p))
    return tot * h / 3


def tolerance_factor(n: int, basis: str) -> float:
    if basis not in P_COVER:
        raise CycleError("basis must be 'A' or 'B'")
    if n < 5:
        raise CycleError("at least 5 samples are needed for a tolerance factor")
    z_p = _phi_inv(P_COVER[basis])
    lo, hi = z_p, z_p + 15.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _confidence(mid, n, z_p) < CONFIDENCE else (lo, mid)
    return (lo + hi) / 2


def anderson_darling_normal(x):
    n = len(x)
    mean = sum(x) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in x) / (n - 1))
    if sd == 0:
        raise CycleError("all values are identical: no spread to analyse")
    z = sorted((v - mean) / sd for v in x)
    s = 0.0
    for i in range(1, n + 1):
        f1 = min(max(_phi(z[i - 1]), 1e-300), 1 - 1e-16)
        f2 = min(max(_phi(z[n - i]), 1e-300), 1 - 1e-16)
        s += (2 * i - 1) * (math.log(f1) + math.log(1 - f2))
    a2 = -n - s / n
    return a2 * (1 + 0.75 / n + 2.25 / n ** 2)


def allowable_from_coupons(values, basis: str = "B", minimum: bool = True) -> dict:
    x = [float(v) for v in values]
    if len(x) < MIN_N:
        raise CycleError(f"at least {MIN_N} coupon results are required for this screening method (got {len(x)})")
    if any(not math.isfinite(v) or v <= 0 for v in x):
        raise CycleError("coupon results must be finite and positive")
    warn = ["Computed from the supplied data only. A handbook-grade basis also needs a documented sampling plan "
            "(batches/heats, specimen orientation, environment): record the result as 'user_certified' and cite the test report."]
    dist, data = "normal", x
    ad = anderson_darling_normal(x)
    if ad > AD_CRIT_5PCT:
        data = [math.log(v) for v in x]
        ad_log = anderson_darling_normal(data)
        if ad_log > AD_CRIT_5PCT:
            raise CycleError(f"data fail the normality test (A*^2 = {ad:.3f}) and the lognormal test (A*^2 = {ad_log:.3f}); "
                             "use a Weibull or nonparametric method with more specimens")
        dist, ad = "lognormal", ad_log
        warn.append("Normal distribution rejected; lognormal used.")
    n = len(data)
    mean = sum(data) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in data) / (n - 1))
    k = tolerance_factor(n, basis)
    bound = mean - k * sd
    value = math.exp(bound) if dist == "lognormal" else bound
    return {"basis": basis, "value": round(value, 3), "n": n, "mean": round(sum(x) / n, 3), "k": round(k, 4),
            "distribution": dist, "anderson_darling_adjusted": round(ad, 4), "coverage": P_COVER[basis], "confidence": CONFIDENCE,
            "cv_percent": round(100 * math.sqrt(sum((v - sum(x) / n) ** 2 for v in x) / (n - 1)) / (sum(x) / n), 2),
            "data_sha256": hashlib.sha256(json.dumps([round(v, 6) for v in x]).encode()).hexdigest(), "warnings": warn}


def material_from_coupons(mat_id, density_g_cc, e_mpa, tests: dict, basis: str, source: str, nu=0.33, galvanic_group=None) -> dict:
    """tests: {'ftu_mpa': [...], 'fty_mpa': [...], optional 'fsu_mpa', 'fbru_mpa'}; returns a custom-material record."""
    if not str(source).strip():
        raise CycleError("source (test report reference) is required")
    rec = {"id": mat_id, "density_g_cc": density_g_cc, "e_mpa": e_mpa, "nu": nu, "basis": "user_certified"}
    sections, notes = [], []
    for key in ("ftu_mpa", "fty_mpa", "fsu_mpa", "fbru_mpa"):
        if key not in tests:
            continue
        r = allowable_from_coupons(tests[key], basis)
        rec[key] = r["value"]
        sections.append(f"{key} {basis}-basis n={r['n']} {r['distribution']} sha256={r['data_sha256'][:12]}")
        notes += r["warnings"][:0]
    if "ftu_mpa" not in rec or "fty_mpa" not in rec:
        raise CycleError("tests must include ftu_mpa and fty_mpa coupon results")
    if rec["fty_mpa"] > rec["ftu_mpa"]:
        raise CycleError("computed fty exceeds ftu: check the data")
    rec["source"] = f"{source}; " + "; ".join(sections)
    if galvanic_group:
        rec["galvanic_group"] = galvanic_group
    return rec
