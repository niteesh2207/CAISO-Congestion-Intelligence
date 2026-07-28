from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any

from ..validation.models import (
    BranchResult,
    BusResult,
    GeneratorResult,
    SolveResult,
    SolverIdentity,
)


class PandapowerUnavailable(RuntimeError):
    pass


class PandapowerSolver:
    """
    Optional AC/DC solver adapter for pandapower.

    The module imports pandapower lazily so the core Grid Studio package remains
    usable without the optional dependency.
    """

    def __init__(self) -> None:
        self.net = None
        self._source: str | None = None

    @staticmethod
    def _pp():
        try:
            import pandapower as pp
            import pandapower.converter as pc
        except ImportError as exc:
            raise PandapowerUnavailable(
                "Install the optional dependency with "
                '`python -m pip install -e ".[multisolver]"`.'
            ) from exc
        return pp, pc

    @property
    def identity(self) -> SolverIdentity:
        pp, _ = self._pp()
        return SolverIdentity(
            name="pandapower",
            version=getattr(pp, "__version__", "unknown"),
            engine_class="OPEN_AC_DC_SOLVER",
            formulation="AC_OR_DC_POWER_FLOW_AND_OPF",
            open_source=True,
            metadata={
                "adapter": "pwai.adapters.pandapower_solver.PandapowerSolver",
            },
        )

    def load_case(self, case_path: str | Path) -> None:
        pp, pc = self._pp()
        path = Path(case_path)
        suffix = path.suffix.lower()
        if suffix == ".json":
            self.net = pp.from_json(str(path))
        elif suffix == ".p":
            self.net = pp.from_pickle(str(path))
        elif suffix == ".m":
            self.net = pc.from_mpc(str(path), casename_mpc_file="mpc")
        else:
            raise ValueError(
                f"Unsupported pandapower input {suffix!r}; use .json, .p or MATPOWER .m."
            )
        self._source = str(path)

    def load_network(self, net: Any, source: str = "memory") -> None:
        self.net = deepcopy(net)
        self._source = source

    def clone(self) -> "PandapowerSolver":
        other = PandapowerSolver()
        if self.net is not None:
            other.load_network(self.net, self._source or "clone")
        return other

    def set_branch_status(
        self, from_bus: int, to_bus: int, circuit: str, closed: bool
    ) -> None:
        if self.net is None:
            raise RuntimeError("No network loaded.")
        matches = self.net.line[
            (
                (
                    (self.net.line.from_bus == int(from_bus))
                    & (self.net.line.to_bus == int(to_bus))
                )
                |
                (
                    (self.net.line.from_bus == int(to_bus))
                    & (self.net.line.to_bus == int(from_bus))
                )
            )
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "The current pandapower adapter requires a unique line between "
                "the selected buses. Circuit-aware mapping is supplied by the "
                "case conversion manifest when parallel circuits exist."
            )
        self.net.line.loc[matches.index[0], "in_service"] = bool(closed)

    def _results(self, elapsed: float, converged: bool, warnings: list[str]) -> SolveResult:
        assert self.net is not None
        buses = [
            BusResult(
                bus=int(idx),
                vm_pu=float(row.vm_pu) if row.vm_pu == row.vm_pu else None,
                va_deg=float(row.va_degree) if row.va_degree == row.va_degree else None,
                p_injection_mw=(
                    -float(row.p_mw) if hasattr(row, "p_mw") and row.p_mw == row.p_mw
                    else None
                ),
                q_injection_mvar=(
                    -float(row.q_mvar) if hasattr(row, "q_mvar") and row.q_mvar == row.q_mvar
                    else None
                ),
            )
            for idx, row in self.net.res_bus.iterrows()
        ]

        branches: list[BranchResult] = []
        for idx, row in self.net.line.iterrows():
            res = self.net.res_line.loc[idx]
            rating = None
            if float(row.max_i_ka) > 0:
                # max_mva ≈ sqrt(3) * kV * kA at the from bus
                vn_kv = float(self.net.bus.loc[int(row.from_bus), "vn_kv"])
                rating = 3 ** 0.5 * vn_kv * float(row.max_i_ka)
            branches.append(BranchResult(
                from_bus=int(row.from_bus),
                to_bus=int(row.to_bus),
                circuit=str(row.get("name", idx)),
                status="Closed" if bool(row.in_service) else "Open",
                p_from_mw=float(res.p_from_mw),
                q_from_mvar=(
                    float(res.q_from_mvar)
                    if hasattr(res, "q_from_mvar") else None
                ),
                mva_from=(
                    (
                        float(res.p_from_mw) ** 2
                        + float(res.q_from_mvar) ** 2
                    ) ** 0.5
                    if hasattr(res, "q_from_mvar") else abs(float(res.p_from_mw))
                ),
                rating_mva=rating,
            ))

        generators: list[GeneratorResult] = []
        if hasattr(self.net, "res_gen") and len(self.net.gen):
            for idx, row in self.net.gen.iterrows():
                res = self.net.res_gen.loc[idx]
                generators.append(GeneratorResult(
                    bus=int(row.bus),
                    gen_id=str(row.get("name", idx)),
                    p_mw=float(res.p_mw),
                    q_mvar=float(res.q_mvar),
                    p_min_mw=(
                        float(row.min_p_mw) if "min_p_mw" in row else None
                    ),
                    p_max_mw=(
                        float(row.max_p_mw) if "max_p_mw" in row else None
                    ),
                ))
        if hasattr(self.net, "res_ext_grid") and len(self.net.ext_grid):
            for idx, row in self.net.ext_grid.iterrows():
                res = self.net.res_ext_grid.loc[idx]
                generators.append(GeneratorResult(
                    bus=int(row.bus),
                    gen_id=f"EXT_GRID_{idx}",
                    p_mw=float(res.p_mw),
                    q_mvar=float(res.q_mvar),
                ))

        objective = None
        if hasattr(self.net, "res_cost") and self.net.res_cost is not None:
            try:
                objective = float(self.net.res_cost)
            except (TypeError, ValueError):
                pass

        return SolveResult(
            solver=self.identity,
            converged=bool(converged),
            elapsed_seconds=elapsed,
            buses=buses,
            branches=branches,
            generators=generators,
            objective_usd_per_hour=objective,
            warnings=warnings,
            raw={"source": self._source},
        )

    def solve_power_flow(self, *, dc: bool = False) -> SolveResult:
        if self.net is None:
            raise RuntimeError("No pandapower network loaded.")
        pp, _ = self._pp()
        t0 = perf_counter()
        warnings: list[str] = []
        try:
            if dc:
                pp.rundcpp(self.net, calculate_voltage_angles=True)
            else:
                pp.runpp(
                    self.net,
                    algorithm="nr",
                    calculate_voltage_angles=True,
                    init="auto",
                    enforce_q_lims=True,
                    check_connectivity=True,
                )
            converged = bool(getattr(self.net, "converged", True))
        except Exception as exc:
            converged = False
            warnings.append(f"{type(exc).__name__}: {exc}")
        return self._results(perf_counter() - t0, converged, warnings)

    def solve_opf(self, *, dc: bool = False) -> SolveResult:
        if self.net is None:
            raise RuntimeError("No pandapower network loaded.")
        pp, _ = self._pp()
        t0 = perf_counter()
        warnings: list[str] = []
        try:
            if dc:
                pp.rundcopp(self.net)
            else:
                pp.runopp(self.net, calculate_voltage_angles=True)
            converged = bool(getattr(self.net, "OPF_converged", True))
        except Exception as exc:
            converged = False
            warnings.append(f"{type(exc).__name__}: {exc}")
        return self._results(perf_counter() - t0, converged, warnings)
