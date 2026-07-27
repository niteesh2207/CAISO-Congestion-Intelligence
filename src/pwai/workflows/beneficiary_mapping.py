from __future__ import annotations

from typing import Any


def map_lmp_beneficiaries(
    baseline_buses: list[dict[str, Any]],
    candidate_buses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    base = {int(r["bus"]): r for r in baseline_buses}
    cand = {int(r["bus"]): r for r in candidate_buses}
    result = []
    for bus in sorted(set(base) & set(cand)):
        b = base[bus].get("lmp_per_mwh")
        c = cand[bus].get("lmp_per_mwh")
        if b is None or c is None:
            continue
        change = float(c)-float(b)
        result.append({
            "bus": bus,
            "baseline_lmp_per_mwh": float(b),
            "candidate_lmp_per_mwh": float(c),
            "lmp_change_per_mwh": change,
            "classification": (
                "LOWER_MODELED_LMP" if change < -1e-9
                else "HIGHER_MODELED_LMP" if change > 1e-9
                else "UNCHANGED"
            ),
        })
    result.sort(key=lambda r: r["lmp_change_per_mwh"])
    return result
