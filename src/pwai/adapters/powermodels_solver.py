from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from time import perf_counter
import json
import shutil
import subprocess
import tempfile

from ..validation.models import BranchResult, BusResult, GeneratorResult, SolveResult, SolverIdentity


class PowerModelsUnavailable(RuntimeError):
    pass


class PowerModelsSolver:
    '''PowerModels.jl bridge using Julia subprocess execution and JSON output.'''

    def __init__(self, julia_executable: str | None = None) -> None:
        self.julia = julia_executable or shutil.which('julia')
        self.case_path: str | None = None
        self._branch_overrides: dict[tuple[int, int, str], bool] = {}

    @property
    def identity(self) -> SolverIdentity:
        return SolverIdentity(
            name='PowerModels.jl',
            version='current environment',
            engine_class='OPEN_OPTIMIZATION_FRAMEWORK',
            formulation='AC_DC_AND_CONVEX_FORMULATIONS',
            open_source=True,
            metadata={'julia': self.julia},
        )

    def load_case(self, case_path: str | Path) -> None:
        self.case_path = str(Path(case_path).resolve())

    def clone(self) -> 'PowerModelsSolver':
        other = PowerModelsSolver(self.julia)
        other.case_path = self.case_path
        other._branch_overrides = deepcopy(self._branch_overrides)
        return other

    def set_branch_status(self, from_bus: int, to_bus: int, circuit: str, closed: bool) -> None:
        self._branch_overrides[(int(from_bus), int(to_bus), str(circuit))] = bool(closed)

    def _ensure(self) -> None:
        if not self.julia:
            raise PowerModelsUnavailable('Julia was not found. Install Julia, PowerModels and Ipopt.')
        if not self.case_path:
            raise RuntimeError('No PowerModels case loaded.')

    def _run(self, task: str, dc: bool) -> SolveResult:
        self._ensure()
        t0 = perf_counter()
        overrides = [
            {'from': k[0], 'to': k[1], 'circuit': k[2], 'closed': v}
            for k, v in self._branch_overrides.items()
        ]
        with tempfile.TemporaryDirectory(prefix='pwai_powermodels_') as td:
            cfg = Path(td) / 'config.json'
            out = Path(td) / 'result.json'
            script = Path(td) / 'run.jl'
            cfg.write_text(json.dumps({
                'case': self.case_path,
                'task': task,
                'dc': dc,
                'overrides': overrides,
                'output': str(out),
            }), encoding='utf-8')
            script.write_text(r'''
using JSON3
using PowerModels
using Ipopt

cfg = JSON3.read(read(ARGS[1], String))
data = PowerModels.parse_file(String(cfg.case))

for o in cfg.overrides
    matches = String[]
    for (id, br) in data["branch"]
        f = Int(br["f_bus"]); t = Int(br["t_bus"])
        if (f == Int(o.from) && t == Int(o.to)) || (f == Int(o.to) && t == Int(o.from))
            push!(matches, id)
        end
    end
    length(matches) == 1 || error("Branch override must resolve uniquely.")
    data["branch"][matches[1]]["br_status"] = Bool(o.closed) ? 1 : 0
end

optimizer = optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0)
model = Bool(cfg.dc) ? DCPPowerModel : ACPPowerModel
result = String(cfg.task) == "PF" ? solve_pf(data, model, optimizer) : solve_opf(data, model, optimizer)

open(String(cfg.output), "w") do io
    JSON3.write(io, result)
end
''', encoding='utf-8')
            proc = subprocess.run(
                [self.julia, '--project=@.', str(script), str(cfg)],
                text=True,
                capture_output=True,
                timeout=1800,
            )
            if proc.returncode != 0 or not out.exists():
                return SolveResult(
                    solver=self.identity,
                    converged=False,
                    elapsed_seconds=perf_counter() - t0,
                    buses=[],
                    branches=[],
                    generators=[],
                    warnings=['PowerModels bridge failed.', proc.stderr[-4000:]],
                    raw={'stdout': proc.stdout[-4000:]},
                )
            payload = json.loads(out.read_text(encoding='utf-8'))

        solution = payload.get('solution', {})
        buses = [
            BusResult(bus=int(k), vm_pu=float(v.get('vm', 1.0)), va_deg=float(v.get('va', 0.0)))
            for k, v in solution.get('bus', {}).items()
        ]
        branches = [
            BranchResult(
                from_bus=int(v.get('f_bus', 0)),
                to_bus=int(v.get('t_bus', 0)),
                circuit=str(k),
                status='Closed',
                p_from_mw=float(v.get('pf', 0.0)),
                q_from_mvar=float(v['qf']) if v.get('qf') is not None else None,
                mva_from=((float(v.get('pf', 0.0)) ** 2 + float(v.get('qf', 0.0)) ** 2) ** 0.5 if v.get('qf') is not None else abs(float(v.get('pf', 0.0)))),
            )
            for k, v in solution.get('branch', {}).items()
        ]
        generators = [
            GeneratorResult(
                bus=int(v.get('gen_bus', 0)),
                gen_id=str(k),
                p_mw=float(v.get('pg', 0.0)),
                q_mvar=float(v['qg']) if v.get('qg') is not None else None,
            )
            for k, v in solution.get('gen', {}).items()
        ]
        status = str(payload.get('termination_status', '')).upper()
        converged = any(x in status for x in ['OPTIMAL', 'LOCALLY_SOLVED'])
        return SolveResult(
            solver=self.identity,
            converged=converged,
            elapsed_seconds=perf_counter() - t0,
            buses=buses,
            branches=branches,
            generators=generators,
            objective_usd_per_hour=float(payload['objective']) if payload.get('objective') is not None else None,
            raw={'termination_status': status, 'case': self.case_path},
        )

    def solve_power_flow(self, *, dc: bool = False) -> SolveResult:
        return self._run('PF', dc)

    def solve_opf(self, *, dc: bool = False) -> SolveResult:
        return self._run('OPF', dc)
