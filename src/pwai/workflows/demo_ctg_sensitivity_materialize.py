from __future__ import annotations


def materialize_demo_injection_sensitivities(adapter, records) -> None:
    if adapter.solver_backed or not hasattr(adapter, "ctg_injection_sensitivities"):
        return
    adapter.ctg_injection_sensitivities = [
        {
            "Injector": r.injector,
            "Name": r.contingency,
            "Element": r.element,
            "MWInjSensitivity": r.mw_inj_sensitivity,
            "MWRangeInc": r.mw_range_inc,
            "MWRangeDec": r.mw_range_dec,
            "MWEffectInc": r.mw_effect_inc,
            "MWEffectDec": r.mw_effect_dec,
        }
        for r in records
    ]
