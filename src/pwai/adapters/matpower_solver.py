from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from time import perf_counter
import json
import shutil
import subprocess
import tempfile

from ..validation.models import (
    BranchResult,
    BusResult,
    GeneratorResult,
    SolveResult,
    SolverIdentity,
)


class MatpowerUnavailable(RuntimeError):
    pass


class MatpowerSolver:
    '''MATPOWER 8.x bridge through GNU Octave command-line execution.'''

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or shutil.which('octave-cli') or shutil.which('octave')
        self.case_path: str | None = None
        self._branch_overrides: dict[tuple[int, int, str], bool] = {}

    @property
    def identity(self) -> SolverIdentity:
        return SolverIdentity(
            name='MATPOWER',
            version='8.1-compatible bridge',
            engine_class='OPEN_STEADY_STATE_SOLVER',
            formulation='AC_DC_PF_OPF',
            open_source=True,
            metadata={'executable': self.executable},
        )

    def load_case(self, case_path: str | Path) -> None:
        self.case_path = str(Path(case_path).resolve())

    def clone(self) -> 'MatpowerSolver':
        other = MatpowerSolver(self.executable)
        other.case_path = self.case_path
        other._branch_overrides = deepcopy(self._branch_overrides)
        return other

    def set_branch_status(self, from_bus: int, to_bus: int, circuit: str, closed: bool) -> None:
        self._branch_overrides[(int(from_bus), int(to_bus), str(circuit))] = bool(closed)

    def _ensure(self) -> None:
        if not self.executable:
            raise MatpowerUnavailable(
                'GNU Octave was not found. Install Octave and MATPOWER 8.1, then set MATPOWER_HOME.'
            )
        if not self.case_path:
            raise RuntimeError('No MATPOWER case loaded.')

    def _script(self, output_path: Path, task: str, dc: bool) -> str:
        overrides = json.dumps([
            {'from': k[0], 'to': k[1], 'circuit': k[2], 'closed': v}
            for k, v in self._branch_overrides.items()
        ])
        case_path = self.case_path.replace("'", "''")
        out_path = str(output_path).replace("'", "''")
        overrides_text = overrides.replace("'", "''")
        dc_flag = 'true' if dc else 'false'
        return f"""
addpath(genpath(getenv('MATPOWER_HOME')));
mpc = loadcase('{case_path}');
overrides = jsondecode('{overrides_text}');
for k = 1:numel(overrides)
  f = overrides(k).from;
  t = overrides(k).to;
  closed = overrides(k).closed;
  idx = find((mpc.branch(:,1)==f & mpc.branch(:,2)==t) | ...
             (mpc.branch(:,1)==t & mpc.branch(:,2)==f));
  if numel(idx) ~= 1
    error('Branch override must resolve uniquely.');
  end
  mpc.branch(idx,11) = double(closed);
end
mpopt = mpoption('verbose', 0, 'out.all', 0);
if strcmp('{task}', 'PF')
  if {dc_flag}
    result = rundcpf(mpc, mpopt);
  else
    result = runpf(mpc, mpopt);
  end
else
  if {dc_flag}
    result = rundcopf(mpc, mpopt);
  else
    result = runopf(mpc, mpopt);
  end
end
payload.success = logical(result.success);
payload.bus = result.bus;
payload.branch = result.branch;
payload.gen = result.gen;
if isfield(result, 'f')
  payload.objective = result.f;
else
  payload.objective = [];
end
payload.version = mpver;
fid = fopen('{out_path}', 'w');
fprintf(fid, '%s', jsonencode(payload));
fclose(fid);
"""

    def _run(self, task: str, dc: bool) -> SolveResult:
        self._ensure()
        t0 = perf_counter()
        with tempfile.TemporaryDirectory(prefix='pwai_matpower_') as td:
            output = Path(td) / 'result.json'
            script = Path(td) / 'run_pwai.m'
            script.write_text(self._script(output, task, dc), encoding='utf-8')
            proc = subprocess.run(
                [self.executable, '--quiet', '--no-gui', str(script)],
                text=True,
                capture_output=True,
                timeout=1800,
            )
            if proc.returncode != 0 or not output.exists():
                return SolveResult(
                    solver=self.identity,
                    converged=False,
                    elapsed_seconds=perf_counter() - t0,
                    buses=[],
                    branches=[],
                    generators=[],
                    warnings=['MATPOWER bridge failed.', proc.stderr[-4000:]],
                    raw={'stdout': proc.stdout[-4000:]},
                )
            payload = json.loads(output.read_text(encoding='utf-8'))

        buses = [BusResult(bus=int(row[0]), vm_pu=float(row[7]), va_deg=float(row[8])) for row in payload['bus']]
        branches = [
            BranchResult(
                from_bus=int(row[0]),
                to_bus=int(row[1]),
                circuit=str(i + 1),
                status='Closed' if int(row[10]) else 'Open',
                p_from_mw=float(row[13]),
                q_from_mvar=float(row[14]),
                mva_from=(float(row[13]) ** 2 + float(row[14]) ** 2) ** 0.5,
                rating_mva=float(row[5]) if float(row[5]) > 0 else None,
            )
            for i, row in enumerate(payload['branch'])
        ]
        generators = [
            GeneratorResult(
                bus=int(row[0]),
                gen_id=str(i + 1),
                p_mw=float(row[1]),
                q_mvar=float(row[2]),
                p_max_mw=float(row[8]),
                p_min_mw=float(row[9]),
            )
            for i, row in enumerate(payload['gen'])
            if int(row[7]) != 0
        ]
        return SolveResult(
            solver=SolverIdentity(
                name='MATPOWER',
                version=str(payload.get('version', '8.1')),
                engine_class='OPEN_STEADY_STATE_SOLVER',
                formulation='AC_DC_PF_OPF',
                open_source=True,
                metadata={'executable': self.executable},
            ),
            converged=bool(payload['success']),
            elapsed_seconds=perf_counter() - t0,
            buses=buses,
            branches=branches,
            generators=generators,
            objective_usd_per_hour=(
                float(payload['objective']) if payload.get('objective') not in (None, []) else None
            ),
            raw={'case': self.case_path},
        )

    def solve_power_flow(self, *, dc: bool = False) -> SolveResult:
        return self._run('PF', dc)

    def solve_opf(self, *, dc: bool = False) -> SolveResult:
        return self._run('OPF', dc)
