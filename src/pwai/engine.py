from __future__ import annotations
from pathlib import Path
from typing import Any
import re

from .adapters.demo import DemoAdapter
from .adapters.simauto import SimAutoAdapter
from .evidence import EvidenceLedger
from .models import Capability, Finding, StudyAnswer, IntentFamily
from .services.ai_interpreter import ai_plan
from .workflows.case_overview import CaseOverview
from .workflows.model_doctor import ModelDoctor
from .workflows.object_resolver import parse_branch_identity, BranchIdentity
from .workflows.contingency import BranchOutageStudy
from .workflows.sensitivity import SensitivityEngine
from .workflows.sensitivity_parser import parse_transfer, parse_monitored_branch, parse_outage_branch
from .workflows.causal_parser import parse_causal_request
from .workflows.causal_diagnosis import CausalDiagnosis
from .workflows.remedy_parser import parse_remedy_request
from .workflows.remedy import RemedyIntelligence
from .workflows.native_contingency import NativeContingencyEngine
from .workflows.security_remedy import SecurityConstrainedRemedy
from .workflows.capabilities import CapabilityRegistry
from .workflows.optimization import OptimizationIntelligence
from .workflows.build_guardian import BuildGuardian
from .workflows.constraint_economics import ConstraintEconomics
from .workflows.economic_parser import parse_lmp_spread, parse_constraint_branch
from .workflows.market_calibration import MarketCalibrationAuditor
from .workflows.security_price_attribution import SecurityPriceAttribution
from .workflows.ctg_injection_sensitivity import ContingencyInjectionSensitivityEngine
from .workflows.bess import BESSIntelligence
from .workflows.relief_parser import parse_contingency_relief, parse_bess_screen
from .workflows.storage_inventory import StorageInventory
from .workflows.existing_bess_rank import ExistingBESSRanker
from .workflows.bess_action_parser import parse_bess_action
from .workflows.bess_dispatch import BESSDispatchStudy
from .workflows.time_step_bridge import TimeStepBridge
from .workflows.time_series_scenario import load_scenario
from .workflows.storage_portfolio import StoragePortfolioOptimizer
from .workflows.grid_time_machine import GridTimeMachine
from .workflows.transmission_upgrade import TransmissionUpgradeStudy
from .workflows.storage_vs_wires import StorageVsWiresDecision
from .workflows.investment_parser import parse_upgrade_request
from .workflows.scenario_ensemble import ScenarioEnsembleEngine
from .workflows.study_memory import StudyMemory
from .workflows.autonomous_investigator import AutonomousGridInvestigator
from .workflows.release_health import ReleaseHealth
from .workflows.advanced_powerworld import AdvancedPowerWorldGateway, IBRModelInspector
from .workflows.governance import EnterpriseGovernance
from .workflows.visual_canvas import VisualGridCanvas
from .workflows.flow_replay import DifferenceFlowReplay
from .workflows.grid_headroom import GridHeadroomAnalyzer
from .workflows.ras_intelligence import RASIntelligence
from .workflows.weather_dlr import WeatherDLRIntelligence
from .workflows.reserve_intelligence import ReserveIntelligence
from .workflows.topology_mode import FullTopologyIntelligence
from .workflows.scenario_generator import AutomaticScenarioGenerator


class GridStudioEngine:
    def __init__(self,mode:str="demo")->None:
        self.mode=mode.lower()
        self.adapter=DemoAdapter() if self.mode=="demo" else SimAutoAdapter()
        self.case_name="DEMO-5BUS" if self.mode=="demo" else "NO_CASE"
        self.connected=False
        self.case_open=False

    def start(self,case_path:str|None=None)->None:
        self.adapter.connect();self.connected=True
        if self.mode=="demo":
            self.adapter.open_case("DEMO");self.case_name="DEMO-5BUS";self.case_open=True
        elif case_path:
            self.adapter.open_case(case_path);self.case_name=Path(case_path).name;self.case_open=True

    def load_case(self,case_path:str)->None:
        if not self.connected:
            self.adapter.connect();self.connected=True
        if self.case_open:self.adapter.close_case()
        self.adapter.open_case(case_path)
        self.case_name=Path(case_path).name;self.case_open=True

    def status(self)->dict[str,Any]:
        solver=self.adapter.program_information() if self.connected else {}
        return {
            "mode":self.mode,"connected":self.connected,"case_open":self.case_open,
            "case_name":self.case_name,"solver_backed":self.adapter.solver_backed,"solver":solver,
        }

    def _ensure_case(self)->None:
        if not self.connected:self.start()
        if not self.case_open:raise RuntimeError("No PowerWorld case is open.")

    def solve_base(self,ledger:EvidenceLedger)->None:
        self.adapter.run_script("EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);")
        ledger.action("solve_power_flow",method="RECTNEWT" if self.adapter.solver_backed else "DEMO")
        ledger.record(
            "solve","power_flow_command_completed",
            True if self.adapter.solver_backed else "DEMO_ASSUMED",
            solver_backed=self.adapter.solver_backed
        )

    def _demo_warning(self)->list[str]:
        return [] if self.adapter.solver_backed else [
            "DEMO MODE: numerical values are generated by the synthetic development model, not PowerWorld."
        ]

    def _sensitivity_answer(self, question:str, plan, ledger)->StudyAnswer|None:
        sens=SensitivityEngine(self.adapter)

        if Capability.PTDF in plan.capabilities:
            transfer=parse_transfer(question)
            if not transfer:
                return StudyAnswer(
                    answer="I understand the PTDF request, but I need an explicit source and sink bus.",
                    simple_explanation="Use a form such as: 'PTDF from bus 101 to bus 501.'",
                    intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )
            rows=sens.ptdf(transfer.source_bus,transfer.sink_bus)
            top=rows[:10]
            for row in top:
                ledger.record("PTDF","branch_ptdf",row,solver_backed=self.adapter.solver_backed)
            strongest=top[0] if top else None
            answer=(
                f"The strongest absolute PTDF for the {transfer.source_bus} → {transfer.sink_bus} transfer "
                f"is {strongest['ptdf_pct']:.1f}% on branch {strongest['from']}-{strongest['to']} {strongest['circuit']}."
                if strongest else "No PTDF values were returned."
            )
            simple=(
                "A PTDF tells us how much of a small source-to-sink transfer appears on each transmission branch. "
                "Positive and negative signs indicate whether the transfer increases or decreases flow in the branch's defined direction."
            )
            return StudyAnswer(
                answer=answer,simple_explanation=simple,intent=plan.intent,risk=plan.risk,
                study_id=ledger.study_id,solver_backed=self.adapter.solver_backed,
                evidence=[{"ptdf_top":top},{"ledger":ledger.to_dict()}],
                warnings=self._demo_warning(),
                analysis={"type":"PTDF","source_bus":transfer.source_bus,"sink_bus":transfer.sink_bus,"rows":top},
            )

        if Capability.LODF in plan.capabilities:
            outage=parse_outage_branch(question) or parse_branch_identity(question)
            if not outage:
                return StudyAnswer(
                    answer="I understand the LODF request, but I need the exact outage branch.",
                    simple_explanation="Use a form such as: 'LODF for outage 301-401 circuit 1.'",
                    intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )
            rows=sens.lodf(outage)
            top=[r for r in rows if not (
                {r["from"],r["to"]}=={outage.from_bus,outage.to_bus} and r["circuit"]==outage.circuit
            )][:10]
            for row in top:
                ledger.record("LODF","branch_lodf",row,solver_backed=self.adapter.solver_backed)
            strongest=top[0] if top else None
            answer=(
                f"The largest non-outaged-line LODF is {strongest['lodf_pct']:.1f}% on "
                f"branch {strongest['from']}-{strongest['to']} {strongest['circuit']}."
                if strongest else "No usable non-outaged-line LODFs were returned."
            )
            simple=(
                "LODF tells us what share of the outaged line's pre-outage flow is redistributed onto each monitored line. "
                "A positive value means the monitored line picks up flow in its defined direction."
            )
            return StudyAnswer(
                answer=answer,simple_explanation=simple,intent=plan.intent,risk=plan.risk,
                study_id=ledger.study_id,solver_backed=self.adapter.solver_backed,
                evidence=[{"lodf_top":top},{"ledger":ledger.to_dict()}],
                warnings=self._demo_warning(),
                analysis={"type":"LODF","outage":outage.__dict__,"rows":top},
            )

        if Capability.OTDF in plan.capabilities:
            transfer=parse_transfer(question)
            monitored=parse_monitored_branch(question)
            outage=parse_outage_branch(question)
            if not all([transfer,monitored,outage]):
                return StudyAnswer(
                    answer="I need the transfer, monitored branch, and outage branch to calculate OTDF.",
                    simple_explanation=(
                        "Example: 'OTDF from bus 101 to bus 501 on monitored branch 301-501 "
                        "if line 301-401 trips.'"
                    ),
                    intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )
            result=sens.otdf(
                source_bus=transfer.source_bus,sink_bus=transfer.sink_bus,
                monitored=monitored,outage=outage,
            )
            ledger.record("OTDF","otdf_result",result,solver_backed=self.adapter.solver_backed)
            answer=(
                f"The OTDF on branch {monitored.from_bus}-{monitored.to_bus} is "
                f"{result['otdf_pct']:.1f}% for the {transfer.source_bus} → {transfer.sink_bus} transfer "
                f"during outage {outage.from_bus}-{outage.to_bus}."
            )
            simple=(
                "OTDF combines two effects: where the transfer normally wants to flow (PTDF) and how the outage redirects "
                "that transfer through the remaining grid (LODF)."
            )
            return StudyAnswer(
                answer=answer,simple_explanation=simple,intent=plan.intent,risk=plan.risk,
                study_id=ledger.study_id,solver_backed=self.adapter.solver_backed,
                evidence=[{"otdf":result},{"ledger":ledger.to_dict()}],
                warnings=self._demo_warning(),
                analysis={"type":"OTDF",**result},
            )

        if Capability.SHIFT_FACTOR_SCREEN in plan.capabilities:
            monitored=parse_monitored_branch(question) or parse_branch_identity(question)
            sink_match=re.search(r"(?:sink|reference)\s*(?:bus)?\s*(\d+)",question,re.I)
            sink=int(sink_match.group(1)) if sink_match else None
            if not monitored or sink is None:
                return StudyAnswer(
                    answer="I can rank buses that worsen or relieve a branch, but I need the monitored branch and a sink/reference bus.",
                    simple_explanation="Example: 'Which buses worsen monitored branch 301-501 with sink bus 501?'",
                    intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )
            result=sens.bus_shift_screen(monitored,sink_bus=sink,top_n=8)
            for row in result["worsen"]+result["relieve"]:
                ledger.record("SHIFT_FACTOR","bus_shift",row,solver_backed=self.adapter.solver_backed)
            best_w=result["worsen"][0] if result["worsen"] else None
            best_r=result["relieve"][0] if result["relieve"] else None
            answer=(
                f"Relative to sink bus {sink}, bus {best_w['source_bus']} most strongly increases flow on the monitored branch "
                f"({best_w['shift_factor_pct']:.1f}%), while bus {best_r['source_bus']} provides the strongest signed relief "
                f"({best_r['shift_factor_pct']:.1f}%)."
                if best_w and best_r else "The bus shift-factor screen returned insufficient results."
            )
            simple=(
                "Think of each bus as a possible place to inject 1 MW while withdrawing 1 MW at the reference bus. "
                "The signed shift factor shows whether that transfer pushes more power onto the monitored line or pulls power away from it."
            )
            return StudyAnswer(
                answer=answer,simple_explanation=simple,intent=plan.intent,risk=plan.risk,
                study_id=ledger.study_id,solver_backed=self.adapter.solver_backed,
                evidence=[{"shift_factor_screen":result},{"ledger":ledger.to_dict()}],
                warnings=self._demo_warning(),
                analysis={"type":"SHIFT_FACTOR_SCREEN","monitored":monitored.__dict__,"sink_bus":sink,**result},
            )

        return None

    def ask(self,question:str,*,confirm_changes:bool=False)->StudyAnswer:
        self._ensure_case()
        plan=ai_plan(question)
        ledger=EvidenceLedger(
            question=question,solver_info=self.adapter.program_information(),case_name=self.case_name,
        )
        ledger.action("study_plan",intent=plan.intent.value,capabilities=[c.value for c in plan.capabilities])

        if Capability.VISUAL_GRID_CANVAS in plan.capabilities:
            source = sink = None
            transfer = parse_transfer(question)
            if transfer:
                source, sink = transfer.source_bus, transfer.sink_bus
            focus = parse_monitored_branch(question) or parse_branch_identity(question)
            canvas = VisualGridCanvas(self.adapter).build(
                source_bus=source,
                sink_bus=sink,
                focus_branch=(
                    (focus.from_bus, focus.to_bus, focus.circuit)
                    if focus else None
                ),
            )
            return StudyAnswer(
                answer=(
                    f"Built the visual grid canvas with {len(canvas['nodes'])} buses "
                    f"and {len(canvas['edges'])} branches using {canvas['layout_mode']}."
                ),
                simple_explanation=(
                    "The canvas puts electrical state directly on the network: voltage at buses, "
                    "MW/loading on branches, attached generation/load/storage, source/sink roles, "
                    "and problem severity. Geographic coordinates are used only when actually present."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"visual_grid_canvas": canvas}],
                warnings=self._demo_warning(),
                analysis={"type": "VISUAL_GRID_CANVAS", **canvas},
            )

        if Capability.DIFFERENCE_FLOW_REPLAY in plan.capabilities:
            branch = (
                parse_outage_branch(question)
                or parse_monitored_branch(question)
                or parse_branch_identity(question)
            )
            if branch is None:
                return StudyAnswer(
                    answer="I need the exact branch to replay.",
                    simple_explanation="Example: 'Show before/after flow replay if line 301-401 circuit 1 trips.'",
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )
            if not confirm_changes:
                return StudyAnswer(
                    answer=(
                        f"Difference/flow replay is ready for branch "
                        f"{branch.from_bus}-{branch.to_bus} {branch.circuit}."
                    ),
                    simple_explanation=(
                        "The product will save state, trip the selected branch, solve, capture base/post "
                        "flow and voltage snapshots, then restore the original case."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    requires_confirmation=True,
                    scenario_summary={
                        "event": "DIFFERENCE_FLOW_REPLAY",
                        "branch": vars(branch),
                    },
                    warnings=self._demo_warning(),
                )
            result = DifferenceFlowReplay(self.adapter).branch_outage(branch)
            top = result["top_thermal_movements"][0] if result["top_thermal_movements"] else None
            answer = (
                f"Replay completed for {branch.from_bus}-{branch.to_bus}. "
                + (
                    f"Largest thermal movement: {top['branch']} "
                    f"{top['base_loading_pct']:.1f}% → {top['post_loading_pct']:.1f}%."
                    if top and top.get("base_loading_pct") is not None
                    and top.get("post_loading_pct") is not None
                    else "No ranked thermal movement was available."
                )
            )
            return StudyAnswer(
                answer=answer,
                simple_explanation=(
                    "The replay separates the event from its consequences so the user can see "
                    "which paths pick up flow and which buses lose voltage support."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"difference_flow_replay": result}],
                findings=[],
                warnings=self._demo_warning(),
                analysis={"type": "DIFFERENCE_FLOW_REPLAY", **result},
            )

        if Capability.GRID_HEADROOM in plan.capabilities:
            transfer = parse_transfer(question)
            monitored = parse_monitored_branch(question)
            if transfer is None or monitored is None:
                return StudyAnswer(
                    answer="Grid Headroom needs an explicit source bus, sink bus, and monitored branch.",
                    simple_explanation=(
                        "Example: 'How much additional transfer from bus 101 to bus 501 "
                        "before monitored branch 301-501 circuit 1 reaches its limit?'"
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )
            if not confirm_changes:
                return StudyAnswer(
                    answer=(
                        f"Headroom scan ready for {transfer.source_bus} → {transfer.sink_bus}, "
                        f"monitored on {monitored.from_bus}-{monitored.to_bus} {monitored.circuit}."
                    ),
                    simple_explanation=(
                        "A PTDF estimate gives the first-order margin, then protected stepped AC "
                        "power-flow solves verify the first monitored-limit crossing."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    requires_confirmation=True,
                    scenario_summary={
                        "event": "GRID_HEADROOM",
                        "source_bus": transfer.source_bus,
                        "sink_bus": transfer.sink_bus,
                        "monitored": vars(monitored),
                    },
                    warnings=self._demo_warning(),
                )
            result = GridHeadroomAnalyzer(self.adapter).transfer_headroom(
                source_bus=transfer.source_bus,
                sink_bus=transfer.sink_bus,
                monitored=monitored,
                step_mw=25.0,
                max_scan_mw=1000.0,
            )
            return StudyAnswer(
                answer=(
                    f"Focused verified headroom is approximately "
                    f"{result['verified_secure_transfer_mw']:.0f} MW before the selected "
                    f"monitored branch reaches its configured limit at the current scan resolution."
                ),
                simple_explanation=(
                    "This is the distance from the present operating point to the selected branch's "
                    "thermal boundary for the specified source-to-sink transaction. It is not a full "
                    "system ATC or N-1 transfer limit unless those broader studies are also run."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"grid_headroom": result}],
                warnings=self._demo_warning(),
                analysis={"type": "GRID_HEADROOM", **result},
            )

        if Capability.RAS_INTELLIGENCE in plan.capabilities:
            ras = RASIntelligence(self.adapter)
            if not confirm_changes:
                inventory = ras.inventory()
                return StudyAnswer(
                    answer=(
                        f"RAS intelligence found/retrieved {inventory.get('count', 0)} "
                        f"remedial-action record(s). A with/without-RAS effectiveness study is ready."
                    ),
                    simple_explanation=(
                        "PowerWorld Remedial Actions can be armed by model criteria and contain multiple "
                        "contingency actions. The product keeps the native RAS definition as the source of truth."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    requires_confirmation=True,
                    scenario_summary={
                        "event": "RAS_EFFECTIVENESS",
                        "inventory": inventory,
                    },
                    warnings=self._demo_warning(),
                )
            result = ras.demo_effectiveness()
            relief = result.get("loading_relief_pct_points")
            return StudyAnswer(
                answer=(
                    "RAS effectiveness study completed. "
                    + (
                        f"The configured scheme improves monitored loading by "
                        f"{relief:.1f} percentage points in the synthetic comparison."
                        if relief is not None else
                        "Real-case effectiveness is delegated to native PowerWorld RAS processing."
                    )
                ),
                simple_explanation=(
                    "The useful question is not only whether a RAS exists, but whether the system "
                    "remains secure with it, and what happens if it is unavailable."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"ras_intelligence": result}],
                warnings=self._demo_warning(),
                analysis={"type": "RAS_INTELLIGENCE", **result},
            )

        if Capability.WEATHER_INTELLIGENCE in plan.capabilities:
            scenario = (
                "COOL_WINDY"
                if "cool" in question.lower() or "windy" in question.lower()
                else "HOT_LOW_WIND"
            )
            weather = WeatherDLRIntelligence(self.adapter)
            if not confirm_changes:
                discovery = weather.discover_native_weather()
                return StudyAnswer(
                    answer=f"Weather/DLR study ready for {scenario}.",
                    simple_explanation=(
                        "PowerWorld's configured weather-dependent branch/generator limits stay authoritative. "
                        "Demo mode uses explicit synthetic rating multipliers only to exercise the workflow."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    requires_confirmation=True,
                    scenario_summary={
                        "event": "WEATHER_DLR",
                        "scenario": scenario,
                        "native_discovery": discovery,
                    },
                    warnings=self._demo_warning(),
                )
            result = weather.evaluate_demo(scenario)
            top = (
                result.get("branch_changes", [None])[0]
                if result.get("branch_changes") else None
            )
            return StudyAnswer(
                answer=(
                    f"Weather/DLR evaluation completed for {scenario}. "
                    + (
                        f"Highest resulting branch loading is {top['weather_loading_pct']:.1f}% "
                        f"on {top['branch']}."
                        if top and top.get("weather_loading_pct") is not None
                        else "Native weather model evidence is available for inspection."
                    )
                ),
                simple_explanation=(
                    "Weather can change transmission ratings and generator capability, so the same MW flow "
                    "can be secure under strong cooling and stressed under hot, low-wind conditions."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"weather_dlr": result}],
                warnings=self._demo_warning(),
                analysis={"type": "WEATHER_DLR", **result},
            )

        if Capability.RESERVE_INTELLIGENCE in plan.capabilities:
            result = ReserveIntelligence(self.adapter).demo_market()
            return StudyAnswer(
                answer=(
                    f"Reserve intelligence completed in {result['mode']} mode. "
                    + (
                        f"Cleared {result['cleared_mw']:.0f} MW against a "
                        f"{result['requirement_mw']:.0f} MW {result['service'].lower()} requirement; "
                        f"illustrative RMCP is ${result['rmcp_usd_per_mwh']:.2f}/MWh."
                        if result.get("rmcp_usd_per_mwh") is not None
                        else "No synthetic reserve price was produced for a real PowerWorld case."
                    )
                ),
                simple_explanation=(
                    "Reserve-aware storage must consider the opportunity cost of holding MW capability "
                    "for regulation/spinning/supplemental service instead of using that MW for energy or congestion relief."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"reserve_intelligence": result}],
                warnings=self._demo_warning(),
                analysis={"type": "RESERVE_INTELLIGENCE", **result},
            )

        if Capability.FULL_TOPOLOGY in plan.capabilities:
            topology = FullTopologyIntelligence(self.adapter)
            branch = parse_branch_identity(question)
            result = (
                topology.translate_outage(branch)
                if branch is not None
                else topology.capability()
            )
            return StudyAnswer(
                answer=(
                    "Full-topology/EMS translation is available. "
                    + (
                        f"Planning outage {branch.from_bus}-{branch.to_bus} can be translated "
                        "to breaker-level isolation logic."
                        if branch else
                        "The product inspected the case for Integrated Topology Processing and breaker evidence."
                    )
                ),
                simple_explanation=(
                    "A planning case can represent an outage as OPEN LINE. An EMS/full-topology model "
                    "may need the actual breakers/disconnects that isolate that device. PowerWorld's "
                    "OpenWithBreakers/ITP algorithm remains authoritative for real cases."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"full_topology": result}],
                warnings=self._demo_warning(),
                analysis={"type": "FULL_TOPOLOGY", **result},
            )

        if Capability.AUTOMATIC_SCENARIO_GENERATOR in plan.capabilities:
            result = AutomaticScenarioGenerator().generate()
            highest = result["retained"][0] if result["retained"] else None
            return StudyAnswer(
                answer=(
                    f"Generated {result['theoretical_scenarios']} theoretical combinations "
                    f"and retained the top {result['retained_scenarios']} for deeper study. "
                    + (
                        f"Highest screened case: {highest['scenario_id']} "
                        f"(score {highest['screening_score']:.2f})."
                        if highest else ""
                    )
                ),
                simple_explanation=(
                    "Instead of solving every combination at full fidelity, the product first screens "
                    "load, weather, outage and BESS-SOC combinations, then promotes the most serious "
                    "cases to linear, AC, or AC+N-1 PowerWorld studies."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"automatic_scenario_generator": result}],
                warnings=self._demo_warning(),
                analysis={"type": "AUTOMATIC_SCENARIO_GENERATOR", **result},
            )

        if Capability.ENTERPRISE_GOVERNANCE in plan.capabilities:
            result = EnterpriseGovernance().inspect()
            return StudyAnswer(
                answer=(
                    f"Deployment policy: {result['deployment']['mode']}. "
                    f"External AI enabled: {result['deployment']['external_ai_enabled']}. "
                    f"Unauthorized CEII cloud upload: prohibited."
                ),
                simple_explanation=(
                    "The product keeps engineering facts, premium-data entitlements, CEII handling, "
                    "and external-AI use as explicit governance settings instead of hidden assumptions."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"enterprise_governance": result}],
                warnings=self._demo_warning(),
                analysis={"type":"ENTERPRISE_GOVERNANCE", **result},
            )

        if Capability.IBR_INTELLIGENCE in plan.capabilities:
            result = IBRModelInspector(self.adapter).inspect()
            return StudyAnswer(
                answer=(
                    f"IBR/dynamic-model schema inspection found "
                    f"{len(result['matching_generator_fields'])} relevant generator field(s). "
                    f"No stability conclusion is made from model presence alone."
                ),
                simple_explanation=(
                    "Grid-following, grid-forming and BESS dynamic-model evidence tells us what models are present. "
                    "Only a validated transient study can tell us how the system behaves dynamically."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"ibr_model_inspection":result}],
                warnings=self._demo_warning(),
                analysis={"type":"IBR_MODEL_INSPECTION", **result},
            )

        if Capability.ATC in plan.capabilities:
            nums = [int(x) for x in re.findall(r"\b\d+\b", question)]
            if len(nums) < 2:
                return StudyAnswer(
                    answer="I need explicit source and sink bus numbers for the ATC request.",
                    simple_explanation="Example: 'ATC from bus 101 to bus 501.'",
                    intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )
            gateway=AdvancedPowerWorldGateway(self.adapter)
            if not confirm_changes:
                command=f"ATCDetermine([BUS {nums[0]}], [BUS {nums[1]}], NO, NO);"
                return StudyAnswer(
                    answer=f"ATC study ready for Bus {nums[0]} → Bus {nums[1]}.",
                    simple_explanation=(
                        "PowerWorld will own the ATC calculation and limiting-element logic. "
                        "The AI layer will only execute the documented command and read returned result objects."
                    ),
                    intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,requires_confirmation=True,
                    scenario_summary={"event":"ATC","command":command},
                    warnings=self._demo_warning(),
                )
            result=gateway.atc_bus_to_bus(nums[0],nums[1])
            return StudyAnswer(
                answer=(
                    "ATC gateway execution completed."
                    if result["mode"]=="POWERWORLD"
                    else "ATC gateway preview completed in demo mode; no synthetic ATC MW result was invented."
                ),
                simple_explanation="ATC determines how much additional transfer is possible before a monitored/security limit becomes binding.",
                intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"atc":result}],warnings=self._demo_warning(),
                analysis={"type":"ATC",**result},
            )

        if Capability.PV_CURVE in plan.capabilities:
            groups=[]
            m=re.search(
                r'from\s+(?:group\s+)?["\']([^"\']+)["\']\s+to\s+(?:group\s+)?["\']([^"\']+)["\']',
                question,re.IGNORECASE
            )
            if not m:
                m=re.search(
                    r'from\s+(?:group\s+)?([A-Za-z0-9_.-]+)\s+to\s+(?:group\s+)?([A-Za-z0-9_.-]+)',
                    question,re.IGNORECASE
                )
            if m:
                groups=[m.group(1).strip(),m.group(2).strip()]
            if len(groups)<2:
                return StudyAnswer(
                    answer="PV analysis needs explicit source and sink injection-group names.",
                    simple_explanation='Example: Run PV from group "SolarGen" to group "UrbanLoad".',
                    intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )
            gateway=AdvancedPowerWorldGateway(self.adapter)
            command=f'PVRun([INJECTIONGROUP "{groups[0]}"], [INJECTIONGROUP "{groups[1]}"]);'
            if not confirm_changes:
                return StudyAnswer(
                    answer=f"PV study ready: {groups[0]} → {groups[1]}.",
                    simple_explanation="PowerWorld will run the configured PV tool; no PV curve is estimated by the language model.",
                    intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,requires_confirmation=True,
                    scenario_summary={"event":"PV_CURVE","command":command},
                    warnings=self._demo_warning(),
                )
            result=gateway.pv_run(groups[0],groups[1])
            return StudyAnswer(
                answer="PV gateway execution completed." if result["mode"]=="POWERWORLD" else "PV gateway preview completed; no synthetic PV curve was invented.",
                simple_explanation="PV analysis increases a defined source-to-sink transfer and tracks voltage/security response.",
                intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,evidence=[{"pv":result}],
                warnings=self._demo_warning(),analysis={"type":"PV_CURVE",**result},
            )

        if Capability.QV_CURVE in plan.capabilities:
            gateway=AdvancedPowerWorldGateway(self.adapter)
            filename=str((Path.cwd()/"QV_V1_RESULTS.csv").resolve())
            if not confirm_changes:
                return StudyAnswer(
                    answer="QV study is ready for the buses already selected/configured in PowerWorld.",
                    simple_explanation="QV analysis is bus-specific. V1.0 will not silently change which buses are selected for the user's study.",
                    intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,requires_confirmation=True,
                    scenario_summary={"event":"QV_CURVE","output_file":filename},
                    warnings=self._demo_warning(),
                )
            result=gateway.qv_run(filename)
            return StudyAnswer(
                answer="QV gateway execution completed." if result["mode"]=="POWERWORLD" else "QV gateway preview completed; no synthetic QV margin was invented.",
                simple_explanation="QV analysis tests reactive-power margin and voltage sensitivity at the PowerWorld-selected buses.",
                intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,evidence=[{"qv":result}],
                warnings=self._demo_warning(),analysis={"type":"QV_CURVE",**result},
            )

        if Capability.TRANSIENT_STABILITY in plan.capabilities:
            gateway=AdvancedPowerWorldGateway(self.adapter)
            is_validate=any(x in question.lower() for x in ["validate","validation"])
            if is_validate:
                if not confirm_changes:
                    return StudyAnswer(
                        answer="Transient-stability validation is ready.",
                        simple_explanation="PowerWorld TSValidate checks dynamic models and inputs before simulation.",
                        intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                        solver_backed=self.adapter.solver_backed,requires_confirmation=True,
                        scenario_summary={"event":"TS_VALIDATE","command":"TSValidate;"},
                        warnings=self._demo_warning(),
                    )
                result=gateway.transient_validate()
                return StudyAnswer(
                    answer="Transient validation gateway completed." if result["mode"]=="POWERWORLD" else "Transient validation preview completed; no synthetic validation messages were invented.",
                    simple_explanation="Validation finds dynamic-model input problems; it does not itself prove transient stability.",
                    intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,evidence=[{"transient_validation":result}],
                    warnings=self._demo_warning(),analysis={"type":"TRANSIENT_VALIDATION",**result},
                )
            m=re.search(r'(?:contingency|ctg)\s+["\']?([A-Za-z0-9_.:-]+)',question,re.IGNORECASE)
            if not m:
                return StudyAnswer(
                    answer="I need the exact transient contingency name.",
                    simple_explanation='Example: Run transient contingency "GEN_TRIP_BUS1".',
                    intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )
            name=m.group(1)
            if not confirm_changes:
                return StudyAnswer(
                    answer=f"Transient contingency {name} is ready for a 0–10 s, 0.01 s-step study.",
                    simple_explanation="The simulation will be executed by PowerWorld TSSolve; the AI does not generate the trajectory.",
                    intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,requires_confirmation=True,
                    scenario_summary={"event":"TRANSIENT_SOLVE","contingency":name},
                    warnings=self._demo_warning(),
                )
            result=gateway.transient_solve(name)
            return StudyAnswer(
                answer="Transient contingency gateway execution completed." if result["mode"]=="POWERWORLD" else "Transient gateway preview completed; no synthetic trajectory was invented.",
                simple_explanation="The dynamic conclusion is deferred to PowerWorld's solved trajectory and configured result monitors.",
                intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,evidence=[{"transient":result}],
                warnings=self._demo_warning(),analysis={"type":"TRANSIENT_STABILITY",**result},
            )

        if Capability.RELEASE_HEALTH in plan.capabilities:
            health = ReleaseHealth(self.adapter).inspect()
            return StudyAnswer(
                answer=(
                    f"Release status: {health['production_status']}. "
                    f"Regression green: {health['tests_passed']}; compile green: "
                    f"{health['python_compile_passed']}; licensed PowerWorld acceptance: "
                    f"{health['licensed_powerworld_acceptance_validated']}."
                ),
                simple_explanation=(
                    "The software can be a complete release candidate while still not being production-qualified "
                    "until it is executed against a licensed Windows PowerWorld Simulator 24 installation and real cases."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"release_health": health}],
                warnings=self._demo_warning(),
                analysis={"type": "RELEASE_HEALTH", **health},
            )

        if Capability.STUDY_MEMORY in plan.capabilities and Capability.AUTONOMOUS_INVESTIGATOR not in plan.capabilities:
            memory = StudyMemory()
            recent = memory.recent(20)
            graph = memory.graph(200)
            return StudyAnswer(
                answer=(
                    f"Study Memory contains {len(recent)} recent study record(s) in this local runtime "
                    f"and {len(graph)} retrieved knowledge-graph edge(s)."
                ),
                simple_explanation=(
                    "Every remembered study is hash-addressed, and graph relationships carry their supporting evidence. "
                    "This memory is local to the product runtime and is not a substitute for the PowerWorld case itself."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"recent_studies": recent}, {"knowledge_graph": graph}],
                warnings=self._demo_warning(),
                analysis={
                    "type": "STUDY_MEMORY",
                    "recent_studies": recent,
                    "knowledge_graph": graph,
                },
            )

        if Capability.AUTONOMOUS_INVESTIGATOR in plan.capabilities:
            if not confirm_changes:
                return StudyAnswer(
                    answer=(
                        "The autonomous grid investigation is ready. It will run model-health, N-1, "
                        "and OPF economic diagnostics where licensed, then write an evidence-linked local study-memory record."
                    ),
                    simple_explanation=(
                        "The sequence does not permanently change the case. Confirmation is required because "
                        "it can run optimization studies even though those studies are protected and restored."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    requires_confirmation=True,
                    scenario_summary={"event": "AUTONOMOUS_GRID_INVESTIGATION"},
                    warnings=self._demo_warning(),
                )
            result = AutonomousGridInvestigator(
                self.adapter, self.case_name
            ).run(study_id=ledger.study_id, question=question)
            top = result["priorities"][:5]
            answer = (
                f"Autonomous investigation completed with {len(result['priorities'])} ranked issue(s). "
                + (
                    f"Highest priority: {top[0]['title']} ({top[0]['type']})."
                    if top else "No high-priority issue was detected by the current screens."
                )
            )
            return StudyAnswer(
                answer=answer,
                simple_explanation=(
                    "The investigator combines solver provenance, base-case health, N-1 and available economic evidence, "
                    "then recommends the next study instead of jumping directly to a remedy."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"autonomous_investigation": result}],
                warnings=self._demo_warning(),
                analysis={"type": "AUTONOMOUS_INVESTIGATION", **result},
            )

        if Capability.SCENARIO_ENSEMBLE in plan.capabilities:
            if not confirm_changes:
                return StudyAnswer(
                    answer="The configured scenario-ensemble risk study is ready.",
                    simple_explanation=(
                        "It will optimize the existing storage portfolio under each probability-weighted scenario "
                        "and summarize expected shortfall, tail objective and worst-case scenarios."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    requires_confirmation=True,
                    scenario_summary={"event": "SCENARIO_ENSEMBLE_RISK"},
                    warnings=self._demo_warning(),
                )
            result = ScenarioEnsembleEngine(self.adapter).run()
            risk = result["risk"]
            return StudyAnswer(
                answer=(
                    f"Scenario ensemble completed. Probability of any configured relief shortfall is "
                    f"{100*risk['probability_any_relief_shortfall']:.1f}%; expected unserved relief is "
                    f"{risk['expected_unserved_relief_mwh']:.1f} MWh. Worst objective scenario: "
                    f"{risk['worst_case_scenario']}."
                ),
                simple_explanation=(
                    "This is a discrete risk ensemble: each scenario has a probability, and the portfolio is "
                    "re-optimized under its own load/price/congestion conditions."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"scenario_ensemble": result}],
                warnings=self._demo_warning() + [
                    "The bundled scenario ensemble is synthetic development data until external inputs are verified."
                ],
                analysis={"type": "SCENARIO_ENSEMBLE", **result},
            )

        if Capability.STORAGE_VS_WIRES in plan.capabilities:
            request = parse_upgrade_request(question)
            if request is None:
                request = parse_upgrade_request(
                    "branch 301-501 increase 200 MVA"
                )
            if not confirm_changes:
                return StudyAnswer(
                    answer=(
                        f"Storage-vs-wires comparison is ready for branch "
                        f"{request.branch.from_bus}-{request.branch.to_bus} {request.branch.circuit}."
                    ),
                    simple_explanation=(
                        "The study compares the existing multi-hour battery portfolio with a protected rating increase, "
                        "then applies explicit annualization assumptions and maps modeled LMP beneficiaries."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    requires_confirmation=True,
                    scenario_summary={
                        "event": "STORAGE_VS_WIRES",
                        "branch": vars(request.branch),
                        "delta_mva": request.delta_mva,
                    },
                    warnings=self._demo_warning() + [
                        "Investment assumptions are explicit synthetic defaults until replaced with user-validated costs."
                    ],
                )
            result = StorageVsWiresDecision(self.adapter).run(
                branch=request.branch,
                source_bus=request.source_bus,
                sink_bus=request.sink_bus,
            )
            return StudyAnswer(
                answer=(
                    f"Storage-vs-wires screen completed. Recommendation: "
                    f"{result['recommendation']}. Existing storage leaves "
                    f"{result['storage']['portfolio']['portfolio_metrics']['unserved_relief_mwh']:.1f} MWh "
                    f"of configured relief unmet."
                ),
                simple_explanation=(
                    "Storage is judged on multi-hour SOC-constrained operating relief. The wire option is judged on a "
                    "protected rating increase plus OPF/SCOPF economics. Capital assumptions remain separate, editable inputs."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"storage_vs_wires": result}],
                warnings=self._demo_warning() + [
                    "This is screening-level decision intelligence, not a project-finance or construction-feasibility conclusion."
                ],
                analysis={"type": "STORAGE_VS_WIRES", **result},
            )

        if Capability.TRANSMISSION_UPGRADE in plan.capabilities:
            request = parse_upgrade_request(question)
            if request is None:
                return StudyAnswer(
                    answer="I need the exact branch and MVA upgrade amount.",
                    simple_explanation="Example: 'Upgrade branch 301-501 by 200 MVA, source bus 101 sink bus 501.'",
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )
            if not confirm_changes:
                return StudyAnswer(
                    answer=(
                        f"Protected upgrade study ready: branch {request.branch.from_bus}-"
                        f"{request.branch.to_bus} {request.branch.circuit}, +{request.delta_mva:.1f} MVA."
                    ),
                    simple_explanation=(
                        "Only the branch rating is changed inside a saved state. The case is solved, N-1/OPF/SCOPF "
                        "evidence is collected, and then the original rating is restored."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    requires_confirmation=True,
                    scenario_summary={
                        "event": "TRANSMISSION_RATING_UPGRADE",
                        "request": request.__dict__,
                    },
                    warnings=self._demo_warning(),
                )
            result = TransmissionUpgradeStudy(self.adapter).run(
                branch=request.branch,
                delta_mva=request.delta_mva,
                source_bus=request.source_bus,
                sink_bus=request.sink_bus,
            )
            opf_delta = result["opf"]["cost_delta_per_hour"]
            return StudyAnswer(
                answer=(
                    f"Branch rating study completed: {result['base_limit_mva']:.1f} → "
                    f"{result['new_limit_mva']:.1f} MVA. "
                    + (
                        f"Modeled OPF cost changes by ${opf_delta:+,.0f}/h."
                        if opf_delta is not None else
                        "OPF cost delta was unavailable."
                    )
                ),
                simple_explanation=(
                    "A rating increase can lower operating cost only when the branch was economically constraining "
                    "the optimum. It can also shift which contingency or neighboring path becomes limiting."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"transmission_upgrade": result}],
                warnings=self._demo_warning() + [
                    "Changing an MVA limit is not equivalent to proving a physical transmission upgrade is feasible."
                ],
                analysis={"type": "TRANSMISSION_UPGRADE", **result},
            )

        if Capability.TIME_STEP_SIMULATION in plan.capabilities and Capability.BESS_PORTFOLIO not in plan.capabilities:
            info = TimeStepBridge(self.adapter).capabilities()
            return StudyAnswer(
                answer=(
                    "PowerWorld Time Step Simulation is supported as a native execution bridge. "
                    "V0.12 can run a configured single timepoint or range, while product-owned "
                    "multi-hour storage replay remains separate and fully auditable."
                ),
                simple_explanation=(
                    "Native TSS is the PowerWorld hour-by-hour engine. This product does not silently "
                    "create or overwrite the user's TSS inputs; it executes an existing setup and "
                    "keeps our own portfolio/SOC optimizer separate."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"time_step_bridge": info}],
                warnings=self._demo_warning(),
                analysis={"type": "TIME_STEP_SIMULATION", **info},
            )

        if Capability.BESS_PORTFOLIO in plan.capabilities:
            scenario = load_scenario()
            assets = StorageInventory(self.adapter).rows(battery_only=True)

            if not confirm_changes:
                return StudyAnswer(
                    answer=(
                        f"The multi-hour storage portfolio study is ready for scenario "
                        f"{scenario.name} with {len(scenario.timepoints)} timepoints and "
                        f"{len(assets)} existing BA battery units."
                    ),
                    simple_explanation=(
                        "The optimizer will co-optimize the existing batteries across the horizon, "
                        "track SOC hour by hour, preserve terminal SOC targets, then replay each "
                        "timepoint through protected power-flow and N-1 checks."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    requires_confirmation=True,
                    scenario_summary={
                        "event": "MULTI_HOUR_EXISTING_BESS_PORTFOLIO",
                        "scenario": scenario.to_dict(),
                        "assets": [a.to_dict() for a in assets],
                        "native_tss_bridge": TimeStepBridge(self.adapter).capabilities(),
                    },
                    warnings=self._demo_warning() + [
                        "Scenario price/load inputs retain their own provenance and are not automatically market-calibrated."
                    ],
                )

            optimized = StoragePortfolioOptimizer(self.adapter).optimize(scenario)
            replay = GridTimeMachine(self.adapter).replay(scenario, optimized)

            metrics = optimized["portfolio_metrics"]
            summary = replay["summary"]
            terminal = {
                row["asset"]: row["terminal_soc_pct"]
                for row in optimized["terminal_soc"]
            }

            answer = (
                f"Optimized {len(optimized['assets'])} existing batteries across "
                f"{len(optimized['schedule'])} timepoints. The schedule charges "
                f"{metrics['charge_mwh']:.1f} MWh, discharges {metrics['discharge_mwh']:.1f} MWh, "
                f"and leaves {metrics['unserved_relief_mwh']:.1f} MWh of requested relief unmet. "
                f"Protected replay peak monitored loading is "
                f"{summary['peak_monitored_loading_pct']:.1f}%."
            )

            simple = (
                "The schedule uses low-value hours to preserve or add stored energy and higher-value/"
                "higher-congestion hours to discharge, subject to each battery's MW limits, MWh capacity, "
                "SOC limits, efficiency, terminal SOC target and the monitored corridor's OTDF. "
                "Every replay hour is balanced by the explicit scenario generator."
            )

            ledger.record(
                "BESS_PORTFOLIO_OPTIMIZATION",
                scenario.name,
                optimized,
                solver_backed=False,
                evidence_class="DERIVED",
            )
            ledger.record(
                "GRID_TIME_MACHINE_REPLAY",
                scenario.name,
                replay,
                solver_backed=self.adapter.solver_backed,
                evidence_class="FACT" if self.adapter.solver_backed else "DEMO",
            )

            warnings = [
                *self._demo_warning(),
                (
                    "The multi-hour optimizer is not PowerWorld multi-period OPF. "
                    "It is a product-owned discretized dynamic program using existing BA units and OTDF relief."
                ),
                (
                    "Native PowerWorld TSS can solve PF/OPF/SCOPF timepoint-by-timepoint, "
                    "but V0.12 does not overwrite native TSS input grids."
                ),
            ]

            return StudyAnswer(
                answer=answer,
                simple_explanation=simple,
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[
                    {"portfolio_optimization": optimized},
                    {"grid_time_machine": replay},
                    {"ledger": ledger.to_dict()},
                ],
                warnings=warnings,
                analysis={
                    "type": "BESS_PORTFOLIO_MULTI_HOUR",
                    "optimization": optimized,
                    "replay": replay,
                    "terminal_soc_pct": terminal,
                    "state_restored": replay["state_restored"],
                },
            )

        if Capability.BESS_INVENTORY in plan.capabilities and Capability.BESS_SOLVED_ACTION not in plan.capabilities and Capability.BESS_SCREEN not in plan.capabilities:
            assets = StorageInventory(self.adapter).rows()
            batteries = [a for a in assets if a.unit_type == "BA"]
            answer = (
                f"The case contains {len(batteries)} BA battery unit(s) and "
                f"{len(assets)} total recognized storage generator unit(s)."
            )
            simple = (
                "PowerWorld identifies the steady-state device as a generator with a storage Unit Type. "
                "MW/min/max come from the case; MWh and SOC are attached only when explicit storage metadata exists."
            )
            payload = {
                "type": "BESS_INVENTORY",
                "assets": [a.to_dict() for a in assets],
                "battery_count": len(batteries),
                "storage_count": len(assets),
            }
            return StudyAnswer(
                answer=answer,
                simple_explanation=simple,
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"storage_inventory": payload}],
                warnings=self._demo_warning(),
                analysis=payload,
            )

        if Capability.BESS_SOLVED_ACTION in plan.capabilities:
            request = parse_bess_action(question)
            if request is None:
                return StudyAnswer(
                    answer="I understand the BESS dispatch study, but I could not resolve the existing battery, action, and MW request.",
                    simple_explanation=(
                        "Example: 'Test battery 501/B1 discharge 150 MW for 2 hours, "
                        "balance generator 101/1, monitored branch 301-501, source bus 101 sink bus 501.'"
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )

            asset = StorageInventory(self.adapter).find(request.bus, request.gen_id)
            feasibility = asset.feasible_action_mw(
                request.action, request.duration_hours
            )

            if not confirm_changes:
                return StudyAnswer(
                    answer=(
                        f"Battery {request.bus}/{request.gen_id} is ready for a protected "
                        f"{request.action.lower()} study of {request.mw:.1f} MW for "
                        f"{request.duration_hours:.2f} h."
                    ),
                    simple_explanation=(
                        "The study will change only the existing BA unit and one balancing generator, "
                        "solve the network, verify the requested MWs held, compare N-1 security, run OPF/SCOPF "
                        "economics without changing OPF control flags, and restore the original case."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    requires_confirmation=True,
                    scenario_summary={
                        "event": "EXISTING_BESS_PROTECTED_DISPATCH",
                        "asset": asset.to_dict(),
                        "request": request.__dict__,
                        "feasibility": feasibility,
                    },
                    warnings=self._demo_warning() + (
                        [] if feasibility["energy_feasibility_verified"] else [
                            "SOC/MWh feasibility is not verified for this battery."
                        ]
                    ),
                )

            result = BESSDispatchStudy(self.adapter).run(
                bus=request.bus,
                gen_id=request.gen_id,
                action=request.action,
                requested_mw=request.mw,
                duration_hours=request.duration_hours,
                balancing_bus=request.balancing_bus,
                balancing_gen_id=request.balancing_gen_id,
                monitored=request.monitored,
                source_bus=request.source_bus,
                sink_bus=request.sink_bus,
            )

            monitored = result.get("monitored_branch")
            n1_pass = result["n1"]["comparison"]["pass_security"]
            if monitored and monitored.get("base") and monitored.get("post_action"):
                before = monitored["base"].get("loading_pct")
                after = monitored["post_action"].get("loading_pct")
                branch_text = (
                    f" Monitored loading changes from {before:.1f}% to {after:.1f}%."
                    if before is not None and after is not None else ""
                )
            else:
                branch_text = ""

            answer = (
                f"Battery {request.bus}/{request.gen_id} completed the protected "
                f"{request.action.lower()} test at {request.mw:.1f} MW for "
                f"{request.duration_hours:.2f} h.{branch_text} "
                f"N-1 non-degradation screen: {'PASS' if n1_pass else 'FAIL'}."
            )
            if result.get("projected_soc_pct") is not None:
                answer += f" Projected end-of-study SOC is {result['projected_soc_pct']:.1f}%."

            opf = result["economics"]["opf"]
            scopf = result["economics"]["scopf"]
            simple = (
                "The battery action is balanced by an equal-and-opposite generator move, so the network effect "
                "is attributable to a defined transaction rather than hidden slack response. Economics are accepted "
                "only when OPF/SCOPF leave the battery at the requested MW target."
            )
            if opf.get("cost_delta_per_hour") is not None:
                simple += (
                    f" The protected OPF comparison changes modeled generation cost by "
                    f"${opf['cost_delta_per_hour']:+,.0f}/h."
                )
            if scopf.get("cost_delta_per_hour") is not None:
                simple += (
                    f" The SCOPF comparison changes modeled security-constrained cost by "
                    f"${scopf['cost_delta_per_hour']:+,.0f}/h."
                )

            ledger.record(
                "BESS_SOLVED_ACTION",
                f"{request.bus}/{request.gen_id}",
                result,
                solver_backed=self.adapter.solver_backed,
                evidence_class="FACT" if self.adapter.solver_backed else "DEMO",
            )

            warnings = [*self._demo_warning()]
            if not result["feasibility"]["energy_feasibility_verified"]:
                warnings.append(
                    "Energy feasibility is unverified because SOC/MWh metadata is missing."
                )
            if not opf.get("candidate_setpoint_held", False):
                warnings.append(
                    "OPF did not preserve the requested BESS target; OPF economic delta is not valid for the fixed BESS action."
                )
            if not scopf.get("candidate_setpoint_held", False):
                warnings.append(
                    "SCOPF did not preserve the requested BESS target; SCOPF economic delta is not valid for the fixed BESS action."
                )

            return StudyAnswer(
                answer=answer,
                simple_explanation=simple,
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"bess_dispatch_study": result}, {"ledger": ledger.to_dict()}],
                warnings=warnings,
                analysis={"type": "BESS_SOLVED_ACTION", **result},
            )

        if Capability.BESS_INVENTORY in plan.capabilities and Capability.BESS_SCREEN in plan.capabilities:
            request = parse_bess_screen(question)
            if request is None or request.outage is None:
                return StudyAnswer(
                    answer="I need a battery action, contingency, monitored branch, and battery MW/duration context to rank existing storage.",
                    simple_explanation=(
                        "Example: 'Which existing battery should discharge 200 MW to relieve contingency "
                        "L_301_401 on branch 301-501? reference bus 101.'"
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )

            action = "CHARGE" if "charg" in question.lower() and "discharg" not in question.lower() else "DISCHARGE"
            duration_match = __import__("re").search(
                r"(?:for|duration)\s*(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)",
                question,
                __import__("re").IGNORECASE,
            )
            duration_hours = float(duration_match.group(1)) if duration_match else 1.0

            ranked = ExistingBESSRanker(self.adapter).rank(
                action=action,
                duration_hours=duration_hours,
                monitored=request.monitored,
                outage=request.outage,
                reference_bus=request.reference_bus,
            )
            best = ranked["results"][0] if ranked["results"] else None
            if best:
                asset = best["asset"]
                answer = (
                    f"Existing battery {asset['bus']}/{asset['id']} ranks first for {action.lower()} relief, "
                    f"with up to {best['feasibility']['feasible_mw']:.1f} MW feasible for "
                    f"{duration_hours:.2f} h and approximately "
                    f"{best['maximum_feasible_relief_mw']:+.1f} MW of screened contingency relief."
                )
            else:
                answer = "No existing BA battery produced a feasible ranked result."

            return StudyAnswer(
                answer=answer,
                simple_explanation=(
                    "This ranking uses only battery units already present in the case. Their OTDF relief is multiplied "
                    "by actual MW headroom and, where metadata is verified, by SOC/MWh duration capability."
                ),
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"existing_bess_ranking": ranked}],
                warnings=self._demo_warning(),
                analysis={"type": "EXISTING_BESS_RANKING", **ranked},
            )

        if Capability.BESS_SCREEN in plan.capabilities:
            request = parse_bess_screen(question)
            if request is None or request.outage is None:
                return StudyAnswer(
                    answer=(
                        "I understand the battery-placement request, but I need a battery MW size, "
                        "a monitored branch, and a line-outage contingency that can be resolved to an outaged branch."
                    ),
                    simple_explanation=(
                        "Example: 'Where should a 500 MW battery discharge to relieve contingency "
                        "L_301_401 on branch 301-501? reference bus 101.'"
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )

            result = BESSIntelligence(self.adapter).screen(
                battery_mw=request.battery_mw,
                monitored=request.monitored,
                outage=request.outage,
                reference_bus=request.reference_bus,
                top_n=10,
            )

            if request.requested_mode == "CHARGE_WORSEN":
                ranked = result["charge_worst"]
                mode = "charging"
                metric = "charge_relief_mw"
                objective = "worsening"
            elif request.requested_mode == "DISCHARGE_WORSEN":
                ranked = result["discharge_worst"]
                mode = "discharging"
                metric = "discharge_relief_mw"
                objective = "worsening"
            elif request.requested_mode == "CHARGE":
                ranked = result["charge_best_relief"]
                mode = "charging"
                metric = "charge_relief_mw"
                objective = "relief"
            else:
                ranked = result["discharge_best_relief"]
                mode = "discharging"
                metric = "discharge_relief_mw"
                objective = "relief"

            best = ranked[0] if ranked else None
            if best:
                if objective == "worsening":
                    worsening_mw = max(0.0, -float(best[metric]))
                    answer = (
                        f"For a {request.battery_mw:.0f} MW battery, Bus {best['bus']} is the strongest "
                        f"{mode} worsening location in the V0.10 static screen, increasing absolute monitored "
                        f"flow by approximately {worsening_mw:.1f} MW under the selected reference convention."
                    )
                else:
                    answer = (
                        f"For a {request.battery_mw:.0f} MW battery, Bus {best['bus']} is the strongest "
                        f"{mode} relief location in the V0.10 static screen, with approximately "
                        f"{best[metric]:+.1f} MW of monitored-flow relief under the selected reference convention."
                    )
            else:
                answer = "The battery screen completed but returned no candidate buses."

            simple = (
                "Battery discharge is modeled as positive injection at the candidate bus; charging is the opposite "
                "injection direction. I use OTDF to measure how that MW action changes the monitored line after the "
                "specified outage. This is a placement sensitivity screen, not a full storage dispatch simulation."
            )

            for row in result["discharge_best_relief"][:10]:
                ledger.record(
                    "BESS_DISCHARGE_SCREEN",
                    f"bus_{row['bus']}",
                    row,
                    solver_backed=False,
                    evidence_class="DERIVED",
                )

            warnings = [
                *self._demo_warning(),
                (
                    "BESS screen is static MW sensitivity only: no SOC, MWh duration, efficiency, cycling, "
                    "interconnection, charging-headroom, or solved temporary storage element is modeled."
                ),
            ]
            if request.reference_bus is None:
                warnings.append(
                    "No reference/balancing bus was provided; the fallback reference is explicit in the result "
                    "and should be replaced with an engineering-appropriate balancing convention."
                )

            return StudyAnswer(
                answer=answer,
                simple_explanation=simple,
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"bess_screen": result}, {"ledger": ledger.to_dict()}],
                warnings=warnings,
                analysis={
                    "type": "BESS_SCREEN",
                    "requested_mode": request.requested_mode,
                    **result,
                },
            )

        if Capability.CTG_INJECTION_SENSITIVITY in plan.capabilities:
            request = parse_contingency_relief(question)
            if request is None:
                return StudyAnswer(
                    answer="I understand the contingency-relief request but could not resolve the contingency or violated element.",
                    simple_explanation=(
                        "Example: 'Which generators can relieve contingency L_301_401 on branch 301-501?'"
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )

            engine = ContingencyInjectionSensitivityEngine(self.adapter)
            ranked = engine.rank_relief(
                contingency=request.contingency,
                violated_element=request.violated_element,
                top_n=20,
            )
            rows = ranked["results"]

            if rows:
                best = next((r for r in rows if r["relief_rank_eligible"]), rows[0])
                action_text = best.get("best_action_plain", best["best_action"]).lower().replace("_", " ")
                answer = (
                    f"The strongest retained relief action is {action_text} "
                    f"at {best['injector']}, with a reported/derived MW Effect of "
                    f"{best['best_mw_effect']:+.1f} MW on the selected contingency violation."
                )
            else:
                answer = (
                    "No violation-level injection-sensitivity rows were available for this selection."
                )

            discovery = ranked["discovery"]
            simple = (
                "PowerWorld's contingency injection-sensitivity results combine each injector's shift factor with "
                "its available MW range. A more negative MW Effect is a stronger candidate for reducing the violated "
                "element, but the candidate still needs a solved redispatch and N-1 validation before it becomes a remedy."
            )

            warnings = [ranked["warning"], *self._demo_warning()]
            if discovery.get("status") in {
                "LIMITVIOL_EMBEDDED_FIELDS_ONLY",
                "REAL_MACHINE_SCHEMA_NOT_DISCOVERED",
            }:
                warnings.append(discovery.get("warning", ""))

            for row in rows[:20]:
                ledger.record(
                    "CTG_INJECTION_SENSITIVITY",
                    f"{row['contingency']}:{row['injector']}",
                    row,
                    solver_backed=self.adapter.solver_backed and row["source"].startswith("POWERWORLD:"),
                    evidence_class="FACT" if row["source"].startswith("POWERWORLD:") else "DERIVED",
                )

            return StudyAnswer(
                answer=answer,
                simple_explanation=simple,
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[
                    {"ctg_injection_sensitivity": ranked},
                    {"ledger": ledger.to_dict()},
                ],
                warnings=[w for w in warnings if w],
                analysis={
                    "type": "CTG_RELIEF_RANKING",
                    **ranked,
                },
            )

        if Capability.MARKET_CALIBRATION in plan.capabilities:
            audit = MarketCalibrationAuditor().audit()
            answer = (
                f"Market-calibration status: {audit['status']}. "
                f"{audit['verified_inputs']} of {audit['required_inputs']} required input groups are verified."
            )
            simple = (
                "A solved PowerWorld OPF or SCOPF case is still only model economics until topology, offers, "
                "availability, outages, ratings, load/renewables, losses and market rules are explicitly verified."
            )
            return StudyAnswer(
                answer=answer,
                simple_explanation=simple,
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"market_calibration": audit}],
                warnings=self._demo_warning(),
                analysis={"type": "MARKET_CALIBRATION", **audit},
            )

        if (
            Capability.SCOPF_CONTINGENCY_ECONOMICS in plan.capabilities
            or Capability.SECURITY_PRICE_ATTRIBUTION in plan.capabilities
        ):
            optimizer = OptimizationIntelligence(self.adapter)
            preflight = optimizer.preflight("SCOPF")
            if not preflight["capability_available"]:
                return StudyAnswer(
                    answer="SCOPF contingency economics cannot run because the SCOPF add-on is not available.",
                    simple_explanation=(
                        "This study requires the security-constrained optimization result, not only ordinary OPF."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    evidence=[{"scopf_preflight": preflight}],
                    warnings=preflight["warnings"],
                )

            if not confirm_changes:
                return StudyAnswer(
                    answer=(
                        "The SCOPF security-price attribution study is ready. Execution is paused because "
                        "it will temporarily solve both OPF and SCOPF from the same original case state."
                    ),
                    simple_explanation=(
                        "OPF establishes the base economic price signal. SCOPF then adds contingency security. "
                        "Comparing the two isolates the modeled security effect, while contingency marginal costs "
                        "identify which security constraints matter economically."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    requires_confirmation=True,
                    scenario_summary={
                        "event": "RUN_OPF_VS_SCOPF_SECURITY_ATTRIBUTION",
                        "preflight": preflight,
                    },
                    warnings=self._demo_warning() + preflight["warnings"],
                )

            spread_req = parse_lmp_spread(question)
            result = SecurityPriceAttribution(self.adapter).run(
                spread_request=spread_req
            )

            ranked = result["scopf"]["contingency_constraints"]
            spread_delta = result.get("spread_security_delta")
            calibration = result["market_calibration"]

            if spread_delta and spread_req:
                opf_spread = spread_delta.get("opf_spread_per_mwh")
                scopf_spread = spread_delta.get("scopf_spread_per_mwh")
                security_delta = spread_delta.get("security_incremental_spread_per_mwh")
                answer = (
                    f"Modeled {spread_req.source_bus}→{spread_req.sink_bus} spread changes from "
                    f"${opf_spread:+.2f}/MWh under OPF to ${scopf_spread:+.2f}/MWh under SCOPF, "
                    f"so contingency security changes the modeled spread by ${security_delta:+.2f}/MWh."
                    if None not in (opf_spread, scopf_spread, security_delta)
                    else "OPF-vs-SCOPF price comparison completed, but the full spread could not be resolved."
                )
            else:
                answer = (
                    f"SCOPF returned {len(ranked)} contingency constraints with economic/result records."
                )

            if ranked:
                top = ranked[0]
                if top.get("marginal_cost") is not None:
                    answer += (
                        f" The highest absolute recorded contingency marginal cost is "
                        f"${abs(float(top['marginal_cost'])):,.2f} per constraint unit-hour "
                        f"for {top['contingency']} / {top['element']}."
                    )

            simple = (
                "OPF tells us the economic price pattern without contingency-security constraints. "
                "SCOPF changes the pre-contingency dispatch so those security constraints are respected. "
                "The difference between the two model solutions is the security effect. "
                "I separately rank contingency marginal cost and OTDF exposure as an explainability screen."
            )

            for row in ranked[:50]:
                ledger.record(
                    "SCOPF_CONTINGENCY_ECONOMICS",
                    f"{row['contingency']}:{row['element']}",
                    row,
                    solver_backed=self.adapter.solver_backed,
                )

            warnings = [
                *self._demo_warning(),
                *result["scopf"].get("contingency_constraint_warnings", []),
                (
                    f"Market calibration status is {calibration['status']}; PowerWorld model prices are not "
                    "being represented as ISO/RTO settlement prices."
                ),
            ]

            return StudyAnswer(
                answer=answer,
                simple_explanation=simple,
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[
                    {"security_price_attribution": result},
                    {"ledger": ledger.to_dict()},
                ],
                warnings=warnings,
                analysis={
                    "type": "SCOPF_SECURITY_ATTRIBUTION",
                    **result,
                },
            )

        if Capability.BUILD_GUARDIAN in plan.capabilities:
            result = BuildGuardian(self.adapter).inspect()
            answer = (
                f"PowerWorld build check: version status {result['version_status']}; "
                f"build status {result['build_status']}. "
                f"The public baseline is Simulator {result['public_baseline']['major_version']}; "
                f"latest visible Simulator 24 patch entry "
                f"{result['public_baseline']['simulator24_latest_visible_patch_entry']}, while the general "
                f"software page reports {result['public_baseline']['general_software_page_latest_build_date']}."
            )
            simple = (
                "This checks whether the running Simulator build is older than, equal to, or newer than the "
                "public patch baseline used when this AI build was designed. It does not claim real-machine "
                "acceptance validation."
            )
            return StudyAnswer(
                answer=answer,
                simple_explanation=simple,
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"build_guardian": result}],
                warnings=self._demo_warning(),
                analysis={"type": "BUILD_GUARDIAN", **result},
            )

        if (
            Capability.LMP_DECOMPOSITION in plan.capabilities
            or Capability.BINDING_CONSTRAINTS in plan.capabilities
            or Capability.CONSTRAINT_ECONOMICS in plan.capabilities
        ):
            optimizer = OptimizationIntelligence(self.adapter)
            preflight = optimizer.preflight("OPF")
            if not preflight["capability_available"]:
                return StudyAnswer(
                    answer="Constraint economics cannot run because the OPF add-on is not available.",
                    simple_explanation=(
                        "Binding-constraint marginal costs and OPF bus marginal prices require an OPF solution."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    evidence=[{"optimization_preflight": preflight}],
                    warnings=preflight["warnings"],
                )

            if not confirm_changes:
                return StudyAnswer(
                    answer=(
                        "The constraint-economics study is ready. Execution is paused because it will "
                        "temporarily run OPF before reading binding constraints and nodal marginal prices."
                    ),
                    simple_explanation=(
                        "The study runs inside SaveState/LoadState protection. It uses the case's existing OPF "
                        "controls and costs, reads the economic results, then restores the original operating point."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    requires_confirmation=True,
                    scenario_summary={
                        "event": "RUN_OPF_CONSTRAINT_ECONOMICS",
                        "preflight": preflight,
                    },
                    warnings=self._demo_warning() + preflight["warnings"],
                )

            self.adapter.save_state()
            try:
                opt_result = optimizer.run("OPF")
                economics = ConstraintEconomics(self.adapter)
                snapshot = economics.snapshot()

                spread_req = parse_lmp_spread(question)
                branch_req = parse_constraint_branch(question)

                if Capability.LMP_DECOMPOSITION in plan.capabilities:
                    if spread_req is None:
                        analysis = {
                            "type": "LMP_ECONOMICS",
                            "snapshot": snapshot.to_dict(),
                            "top_price_buses": sorted(
                                snapshot.buses,
                                key=lambda r: float(r["lmp_per_mwh"]),
                                reverse=True,
                            )[:10],
                            "lowest_price_buses": sorted(
                                snapshot.buses,
                                key=lambda r: float(r["lmp_per_mwh"]),
                            )[:10],
                        }
                        answer = (
                            f"OPF economic snapshot contains {len(snapshot.buses)} bus prices and "
                            f"{len(snapshot.binding_branches)} binding/active branch constraints."
                        )
                        simple = (
                            "Bus MW marginal cost is the modeled cost of serving one additional MW at that bus. "
                            "Where native component fields are available, I separate that value into energy, "
                            "congestion and losses."
                        )
                    else:
                        spread = economics.spread(
                            snapshot,
                            source_bus=spread_req.source_bus,
                            sink_bus=spread_req.sink_bus,
                        )
                        trading = economics.trading_translation(spread)
                        total = spread["total_spread_per_mwh"]
                        cong = spread.get("congestion_spread_per_mwh")
                        loss = spread.get("loss_spread_per_mwh")
                        answer = (
                            f"Modeled LMP spread from bus {spread_req.source_bus} to bus "
                            f"{spread_req.sink_bus} is ${total:+.2f}/MWh."
                            if total is not None else
                            "The total modeled LMP spread could not be resolved."
                        )
                        if cong is not None:
                            answer += f" Congestion contributes ${cong:+.2f}/MWh to the source-to-sink difference."
                        if loss is not None:
                            answer += f" Losses contribute ${loss:+.2f}/MWh."
                        simple = trading["headline"] + " " + trading["market_read"]
                        analysis = {
                            "type": "LMP_SPREAD",
                            "spread": spread,
                            "trading_translation": trading,
                            "binding_constraints": snapshot.binding_branches,
                            "binding_interfaces": snapshot.binding_interfaces,
                        }

                elif Capability.BINDING_CONSTRAINTS in plan.capabilities:
                    if branch_req:
                        constraint = economics.branch_constraint(snapshot, branch_req)
                        analysis = {
                            "type": "CONSTRAINT_SHADOW_PRICE",
                            "requested_branch": branch_req.__dict__,
                            "constraint": constraint,
                            "all_binding_branches": snapshot.binding_branches,
                        }
                        if constraint:
                            mc = constraint.get("marginal_cost_per_mva_hour")
                            answer = (
                                f"Branch {branch_req.from_bus}-{branch_req.to_bus} circuit "
                                f"{branch_req.circuit} is {constraint['constraint_status']}."
                            )
                            if mc is not None:
                                answer += (
                                    f" Its modeled MVA marginal enforcement cost is "
                                    f"${mc:,.2f}/MVA-h."
                                )
                            simple = (
                                "For a binding branch, this marginal cost measures the local objective-value benefit "
                                "of relaxing the enforced MVA rating by roughly one MVA near the optimum. "
                                "It is a model shadow-price concept, not automatically an ISO settlement charge."
                            )
                        else:
                            answer = (
                                f"Branch {branch_req.from_bus}-{branch_req.to_bus} circuit "
                                f"{branch_req.circuit} was not found in the binding-constraint result set."
                            )
                            simple = (
                                "That means the available OPF constraint-status/marginal-cost evidence did not identify "
                                "this branch as economically binding in the solved study."
                            )
                    else:
                        analysis = {
                            "type": "BINDING_CONSTRAINTS",
                            "branches": snapshot.binding_branches,
                            "interfaces": snapshot.binding_interfaces,
                        }
                        answer = (
                            f"OPF identified {len(snapshot.binding_branches)} binding/economically active branch "
                            f"constraints and {len(snapshot.binding_interfaces)} binding/economically active interfaces."
                        )
                        simple = (
                            "A binding transmission constraint changes the optimum. Its marginal enforcement cost "
                            "shows how economically valuable an incremental relaxation of that constraint would be."
                        )
                else:
                    analysis = {
                        "type": "CONSTRAINT_ECONOMICS",
                        "snapshot": snapshot.to_dict(),
                    }
                    answer = "Constraint-economics study completed."
                    simple = "The study links OPF marginal prices to binding transmission constraints."

                for row in snapshot.binding_branches[:50]:
                    ledger.record(
                        "OPF_BINDING_CONSTRAINT",
                        f"{row['from']}-{row['to']} {row['circuit']}",
                        row,
                        solver_backed=self.adapter.solver_backed,
                    )
                for row in snapshot.buses[:100]:
                    ledger.record(
                        "OPF_BUS_PRICE",
                        f"bus_{row['bus']}",
                        row,
                        solver_backed=self.adapter.solver_backed,
                    )
            finally:
                self.adapter.load_state()

            warnings = [
                *self._demo_warning(),
                *preflight["warnings"],
                *snapshot.warnings,
                (
                    "PowerWorld model LMPs are model outputs. Do not treat them as CAISO/ERCOT/other ISO "
                    "settlement prices unless the case, offers, topology, losses, limits and market rules are calibrated."
                ),
            ]
            return StudyAnswer(
                answer=answer,
                simple_explanation=simple,
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[
                    {"opf": opt_result.to_dict()},
                    {"economics": analysis},
                    {"ledger": ledger.to_dict()},
                ],
                warnings=warnings,
                analysis={**analysis, "state_restored": True},
            )

        if Capability.CAPABILITY_REGISTRY in plan.capabilities:
            snapshot = CapabilityRegistry(self.adapter).snapshot()
            caps = snapshot["capabilities"]
            opf = "AVAILABLE" if caps["OPF"]["available"] else "NOT AVAILABLE"
            scopf = "AVAILABLE" if caps["SCOPF"]["available"] else "NOT AVAILABLE"
            answer = f"PowerWorld optimization capabilities: OPF {opf}; SCOPF {scopf}."
            simple = (
                "I am reading the add-on list from the running Simulator instance rather than assuming "
                "what your license contains. SCOPF is treated as usable only when both SCOPF and OPF are present."
            )
            return StudyAnswer(
                answer=answer,
                simple_explanation=simple,
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"capabilities": snapshot}, {"ledger": ledger.to_dict()}],
                warnings=self._demo_warning(),
                analysis={"type": "CAPABILITY_REGISTRY", **snapshot},
            )

        if Capability.OPF in plan.capabilities or Capability.SCOPF in plan.capabilities:
            kind = "SCOPF" if Capability.SCOPF in plan.capabilities else "OPF"
            optimizer = OptimizationIntelligence(self.adapter)
            preflight = optimizer.preflight(kind)

            if not preflight["capability_available"]:
                return StudyAnswer(
                    answer=f"{kind} cannot run because the required PowerWorld add-on is not available.",
                    simple_explanation=(
                        "The product checked ProgramInformation and will not pretend an optimization "
                        "capability exists when the running PowerWorld license does not expose it."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    evidence=[{"optimization_preflight": preflight}],
                    warnings=preflight["warnings"],
                    analysis={"type": "OPTIMIZATION_PREFLIGHT", "solution_type": kind, **preflight},
                )

            if not confirm_changes:
                return StudyAnswer(
                    answer=(
                        f"{kind} is available and the study is ready. "
                        "Execution is paused because optimization changes the operating point."
                    ),
                    simple_explanation=(
                        "The tool will use the case's existing generator controls, cost curves, area/super-area "
                        "OPF settings, monitored constraints and—when using SCOPF—the existing contingency set. "
                        "It will not silently rewrite those study assumptions."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    requires_confirmation=True,
                    scenario_summary={
                        "event": f"RUN_{kind}",
                        "preflight": preflight,
                    },
                    warnings=self._demo_warning() + preflight["warnings"],
                )

            self.adapter.save_state()
            try:
                result = optimizer.run(kind)
                payload = result.to_dict()
            finally:
                self.adapter.load_state()

            before_cost = result.before.get("total_generation_cost_per_hour")
            after_cost = result.after.get("total_generation_cost_per_hour")
            before_gens = {
                (g["bus"], g["id"]): g for g in result.before.get("generators", [])
            }
            changes = []
            for g in result.after.get("generators", []):
                old = before_gens.get((g["bus"], g["id"]))
                if old:
                    delta = float(g["mw"]) - float(old["mw"])
                    if abs(delta) > 0.01:
                        changes.append({
                            "bus": g["bus"], "id": g["id"],
                            "before_mw": old["mw"], "after_mw": g["mw"], "delta_mw": delta,
                        })
            changes.sort(key=lambda x: abs(x["delta_mw"]), reverse=True)

            if before_cost is not None and after_cost is not None:
                cost_sentence = (
                    f" Generator cost changes from ${before_cost:,.0f}/h to ${after_cost:,.0f}/h."
                )
            else:
                cost_sentence = " A complete total-generation-cost field was not available for comparison."

            if kind == "SCOPF":
                audit = result.security_audit or {}
                answer = (
                    f"SCOPF completed using the case's configured economics and contingency set."
                    f"{cost_sentence} The post-SCOPF audit processed "
                    f"{audit.get('processed_count', 0)} contingencies with "
                    f"{audit.get('unsolved_count', 0)} unsolved and "
                    f"{audit.get('violation_count', 0)} recorded violations."
                )
                simple = (
                    "SCOPF moves the pre-contingency dispatch to minimize the configured objective while also "
                    "accounting for contingency limits. I then run the contingency set again as an audit and report "
                    "the resulting dispatch and marginal prices."
                )
            else:
                answer = (
                    f"OPF completed using the case's existing OPF controls and cost data.{cost_sentence}"
                )
                simple = (
                    "OPF chooses an economical operating point while enforcing the base-case constraints configured "
                    "in PowerWorld. It is different from our engineering remedy search because the objective is the "
                    "case's OPF objective—not simply the smallest MW intervention."
                )

            for change in changes[:20]:
                ledger.record(
                    f"{kind}_DISPATCH", "generator_change", change,
                    solver_backed=self.adapter.solver_backed,
                )
            for lmp in result.after.get("bus_lmps", [])[:50]:
                ledger.record(
                    f"{kind}_LMP", f"bus_{lmp['bus']}", lmp,
                    solver_backed=self.adapter.solver_backed,
                )

            return StudyAnswer(
                answer=answer,
                simple_explanation=simple,
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"optimization": payload}, {"ledger": ledger.to_dict()}],
                warnings=self._demo_warning() + result.warnings,
                analysis={
                    "type": kind,
                    **payload,
                    "dispatch_changes": changes,
                    "state_restored": True,
                },
            )

        if Capability.NATIVE_CONTINGENCY in plan.capabilities:
            self.solve_base(ledger)
            batch = NativeContingencyEngine(self.adapter).run_all()
            for violation in batch.violations:
                ledger.record(
                    "N1_VIOLATION",
                    f"{violation.contingency}:{violation.object_id}",
                    vars(violation),
                    solver_backed=self.adapter.solver_backed,
                )

            worst = sorted(
                [v for v in batch.violations if v.percent is not None],
                key=lambda v: float(v.percent),
                reverse=True,
            )[:10]

            answer = (
                f"The contingency run processed {batch.processed_count} contingencies, "
                f"recorded {len(batch.violations)} violations, and had "
                f"{batch.unsolved_count} unsolved contingencies."
            )
            simple = (
                "This runs the current non-skipped contingency set from the present solved case. "
                "Each contingency is evaluated against the configured PowerWorld monitoring and limit settings."
            )
            warnings = self._demo_warning()
            if not batch.contingencies:
                warnings.append(
                    "No contingency records were found; N-1 security cannot be established."
                )

            return StudyAnswer(
                answer=answer,
                simple_explanation=simple,
                intent=plan.intent,
                risk=plan.risk,
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                evidence=[{"n1_batch": batch.to_dict()}, {"ledger": ledger.to_dict()}],
                warnings=warnings,
                analysis={
                    "type": "N1_SECURITY",
                    "summary": batch.to_dict(),
                    "worst_violations": [vars(v) for v in worst],
                },
            )

        if Capability.REMEDY_SEARCH in plan.capabilities and plan.intent==IntentFamily.OPTIMIZE:
            request = parse_remedy_request(question)
            if request is None:
                return StudyAnswer(
                    answer="I understand that you want a remedy search, but I need the monitored branch.",
                    simple_explanation=(
                        "Use a form such as: 'Fix branch 301-401 with the smallest generator redispatch, "
                        "reference bus 401, target 98%.'"
                    ),
                    intent=plan.intent, risk=plan.risk, study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )

            remedy = RemedyIntelligence(self.adapter)
            base, ref, screened = remedy.screen(
                request.monitored,
                reference_bus=request.reference_bus,
                target_loading_pct=request.target_loading_pct,
            )

            preview = [{
                "donor_bus": c.donor.bus,
                "donor_id": c.donor.gen_id,
                "receiver_bus": c.receiver.bus,
                "receiver_id": c.receiver.gen_id,
                "redispatch_mw": c.feasible_redispatch_mw,
                "predicted_relief_mw": c.predicted_relief_mw,
                "predicted_to_target": c.predicted_to_target,
            } for c in screened[:5]]

            if not confirm_changes:
                return StudyAnswer(
                    answer=(
                        f"I found {len(screened)} balanced generator-redispatch candidates for "
                        f"branch {request.monitored.from_bus}-{request.monitored.to_bus}. "
                        "The candidates are ready for protected solved-scenario testing."
                    ),
                    simple_explanation=(
                        "The screen pairs a generator that can move down with another that can move up by the same MW. "
                        "That keeps the redispatch balanced instead of silently relying on the slack generator. "
                        "The next step temporarily tests each pair, solves the case, checks the target branch and rejects "
                        "candidates that create new security problems."
                    ),
                    intent=plan.intent,
                    risk=plan.risk,
                    study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                    requires_confirmation=True,
                    scenario_summary={
                        "event": "BALANCED_GENERATOR_REDISPATCH_SEARCH",
                        "monitored": request.monitored.__dict__,
                        "reference_bus": ref,
                        "target_loading_pct": request.target_loading_pct,
                        "base_loading_pct": base.get("loading_pct"),
                        "estimated_required_abs_mw_relief": base.get("estimated_required_abs_mw_relief"),
                        "screened_candidates": preview,
                    },
                    warnings=self._demo_warning(),
                )

            self.solve_base(ledger)
            security_result = SecurityConstrainedRemedy(self.adapter).run(
                request.monitored,
                reference_bus=request.reference_bus,
                target_loading_pct=request.target_loading_pct,
                max_tested=8,
            )

            for row in security_result.tested:
                ledger.record(
                    "N1_REMEDY_TEST", "security_constrained_redispatch_candidate", row,
                    solver_backed=self.adapter.solver_backed,
                    evidence_class="FACT" if self.adapter.solver_backed else "DEMO",
                )

            recommended = security_result.recommended
            if recommended:
                answer = (
                    f"The best tested balanced redispatch moves "
                    f"{recommended['redispatch_mw_each_direction']:.1f} MW from "
                    f"generator {recommended['donor']['bus']}/{recommended['donor']['id']} to "
                    f"generator {recommended['receiver']['bus']}/{recommended['receiver']['id']}. "
                    f"Solved loading changes from {recommended['base_loading_pct']:.1f}% to "
                    f"{recommended['post_loading_pct']:.1f}%."
                )
                if recommended["target_met"] and recommended["overall_security_pass"]:
                    answer += " It meets the target and passes both the base-case and N-1 comparison screens."
                elif recommended["overall_security_pass"]:
                    answer += " It passes the security screens but does not fully reach the requested loading target."
            else:
                answer = (
                    "No tested balanced generator redispatch passed the V0.6 base-case plus N-1 security screen. "
                    "A broader control set is required."
                )

            simple = (
                "I first used grid sensitivity to find where generation should move. Then I changed two generators by "
                "equal and opposite MW, solved the base case, ran the contingency set from that candidate dispatch, "
                "compared those N-1 results with the baseline N-1 results, rejected new or materially worsened security "
                "problems, and restored the original case after every candidate."
            )

            payload = {
                "type": "REMEDY_SEARCH",
                "monitored": security_result.monitored,
                "reference_bus": security_result.reference_bus,
                "target_loading_pct": security_result.target_loading_pct,
                "base": security_result.base,
                "screening": security_result.screening,
                "tested": security_result.tested,
                "recommended": security_result.recommended,
                "baseline_n1": security_result.baseline_n1,
            }
            return StudyAnswer(
                answer=answer,
                simple_explanation=simple,
                intent=plan.intent,
                risk=plan.risk,
                findings=[],
                evidence=[{"remedy_search": payload}, {"ledger": ledger.to_dict()}],
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                warnings=self._demo_warning(),
                analysis=payload,
            )

        if Capability.CAUSAL_DIAGNOSIS in plan.capabilities:
            request = parse_causal_request(question)
            if request is None:
                return StudyAnswer(
                    answer="I understand the causal-diagnosis request, but I need both the monitored branch and the outage branch.",
                    simple_explanation=(
                        "Use a form such as: 'Why did branch 301-501 overload after line 301-401 tripped?' "
                        "You may also add 'reference bus 501' for the source/relief sensitivity screen."
                    ),
                    intent=plan.intent, risk=plan.risk, study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )

            self.solve_base(ledger)
            diagnosis = CausalDiagnosis(self.adapter).run(
                monitored=request.monitored,
                outage=request.outage,
                reference_bus=request.reference_bus,
            )

            for node in diagnosis.causal_graph["nodes"]:
                ledger.record(
                    "CAUSAL_EVIDENCE", node["label"], node["value"],
                    solver_backed=(
                        self.adapter.solver_backed
                        if node["evidence_class"] == "FACT"
                        else False
                    ),
                    evidence_class=node["evidence_class"],
                    confidence=node["confidence"],
                )

            base_pct = diagnosis.base.get("monitored_loading_pct")
            post_pct = diagnosis.solved_post_event.get("monitored_loading_pct")
            actual_delta = diagnosis.solved_post_event.get("actual_delta_mw")
            lodf = diagnosis.linear_explanation.get("lodf_pct")
            coverage = diagnosis.linear_explanation.get("explanation_coverage")

            answer = (
                f"Branch {request.monitored.from_bus}-{request.monitored.to_bus} "
                f"moves from {base_pct:.1f}% to {post_pct:.1f}% after outage "
                f"{request.outage.from_bus}-{request.outage.to_bus}. "
                f"The solved MW change is {actual_delta:+.1f} MW; the linear LODF explanation is "
                f"{lodf:+.1f}%."
                if None not in (base_pct, post_pct, actual_delta, lodf)
                else "The causal study completed, but some expected measurements were unavailable."
            )

            if coverage is not None:
                coverage_text = f"{coverage*100:.0f}%"
            else:
                coverage_text = "unavailable"

            simple = (
                "The monitored line starts from its base loading. When the other line is removed, power finds alternate "
                "paths. LODF estimates how much of the lost line flow should move onto the monitored line, and we compare "
                f"that estimate with the solved result. In this study the linear explanation coverage is {coverage_text}. "
                "The source/relief bus list is a sensitivity screen relative to an explicit reference bus—not a claim that "
                "those buses literally contributed that many megawatts to the present flow."
            )

            warnings = self._demo_warning()
            if diagnosis.linear_explanation.get("topology_warning"):
                warnings.append(
                    "The LODF indicates a topology/islanding condition; ordinary linear redistribution interpretation is unreliable."
                )

            payload = {
                "type": "CAUSAL_DIAGNOSIS",
                "monitored": diagnosis.monitored,
                "outage": diagnosis.outage,
                "base": diagnosis.base,
                "solved_post_event": diagnosis.solved_post_event,
                "linear_explanation": diagnosis.linear_explanation,
                "sensitivity_exposure": diagnosis.sensitivity_exposure,
                "causal_graph": diagnosis.causal_graph,
                "confidence": diagnosis.confidence,
                "network_replay": diagnosis.network_replay,
            }
            return StudyAnswer(
                answer=answer,
                simple_explanation=simple,
                intent=plan.intent,
                risk=plan.risk,
                findings=diagnosis.findings,
                evidence=[{"causal_diagnosis": payload}, {"ledger": ledger.to_dict()}],
                study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                warnings=warnings,
                analysis=payload,
            )

        sensitivity = self._sensitivity_answer(question,plan,ledger)
        if sensitivity is not None:
            return sensitivity

        if Capability.CONTINGENCY in plan.capabilities and plan.intent==IntentFamily.TEST:
            identity=parse_branch_identity(question)
            if identity is None:
                return StudyAnswer(
                    answer="I understand that you want a contingency study, but I cannot uniquely identify the branch yet.",
                    simple_explanation="Give the branch as '301-401' or '301 to 401 circuit 1'.",
                    intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,
                )
            if not confirm_changes:
                return StudyAnswer(
                    answer=f"Ready to run a protected outage of branch {identity.from_bus}-{identity.to_bus} circuit {identity.circuit}.",
                    simple_explanation="The study will save the base state, open only this branch, solve, compare results, and restore the original state.",
                    intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                    solver_backed=self.adapter.solver_backed,requires_confirmation=True,
                    scenario_summary={"event":"BRANCH_OUTAGE","branch":f"{identity.from_bus}-{identity.to_bus}","circuit":identity.circuit},
                    evidence=[{"study_plan":plan.model_dump(mode="json")}],
                )

            self.solve_base(ledger)
            result=BranchOutageStudy(self.adapter).run(identity)
            ledger.action("protected_branch_outage",**result.event)
            for row in result.thermal_changes[:10]:
                ledger.record("contingency_thermal","branch_change",row,solver_backed=self.adapter.solver_backed)
            for row in result.voltage_changes[:10]:
                ledger.record("contingency_voltage","bus_change",row,solver_backed=self.adapter.solver_backed)

            # Add a linear sensitivity explanation of the same outage.
            try:
                lodf_rows=SensitivityEngine(self.adapter).lodf(identity)
                lodf_top=[
                    r for r in lodf_rows
                    if not ({r["from"],r["to"]}=={identity.from_bus,identity.to_bus})
                ][:5]
            except Exception as exc:
                lodf_top=[]
                ledger.warn(f"LODF explanation unavailable: {exc}")

            worst_thermal=next(
                (r for r in result.thermal_changes
                 if r["post_loading_pct"] is not None
                 and not r["branch"].startswith(f"{identity.from_bus}-{identity.to_bus}")),
                None
            )
            worst_voltage=result.voltage_changes[0] if result.voltage_changes else None

            answer=(
                f"The protected outage study found {len(result.findings)} post-event security findings. "
                f"The original base state was restored after the comparison."
                if result.findings else
                "The protected outage study completed without creating an Alpha-screened thermal or voltage violation."
            )
            simple=(
                "I temporarily removed the selected line, let the network redistribute power, measured the physical result, "
                "and then restored the original case. I also calculated LODF as a linear explanation of where the lost line flow tends to go."
            )
            summary={
                "event":result.event,
                "largest_thermal_change":worst_thermal,
                "largest_voltage_change":worst_voltage,
                "lodf_explanation_top":lodf_top,
                "state_restored":True,
            }
            return StudyAnswer(
                answer=answer,simple_explanation=simple,intent=plan.intent,risk=plan.risk,
                findings=result.findings,evidence=[{"scenario_comparison":summary},{"ledger":ledger.to_dict()}],
                study_id=ledger.study_id,solver_backed=self.adapter.solver_backed,
                warnings=self._demo_warning(),scenario_summary=summary,
                analysis={"type":"CONTINGENCY_WITH_LODF","lodf_top":lodf_top},
            )

        if plan.requires_confirmation:
            return StudyAnswer(
                answer="I understood the request, but this model-changing or optimization workflow is not executable yet in Alpha V0.3.",
                simple_explanation="Protected branch outages are enabled. Other changes remain blocked until their safety workflows are built.",
                intent=plan.intent,risk=plan.risk,study_id=ledger.study_id,
                solver_backed=self.adapter.solver_backed,
                warnings=["Mutation is blocked because a validated scenario workflow is not implemented for this request."],
            )

        self.solve_base(ledger)
        findings:list[Finding]=[]
        evidence:list[dict[str,Any]]=[]

        if Capability.MODEL_DOCTOR in plan.capabilities:
            findings=ModelDoctor(self.adapter).run(top_n=5)
            for f in findings:
                for ev in f.evidence:
                    ledger.record(f.category,f.title,ev,solver_backed=self.adapter.solver_backed)
            critical=sum(1 for f in findings if f.severity=="CRITICAL")
            high=sum(1 for f in findings if f.severity=="HIGH")
            answer=f"I found {len(findings)} priority issues in the current case ({critical} critical, {high} high-priority)."
            simple="I solved the case, ranked the strongest thermal and voltage warning signals, and put the most important issues first."

        elif Capability.THERMAL_RANKING in plan.capabilities:
            findings=ModelDoctor(self.adapter).thermal_findings(top_n=10)
            if findings:
                answer=f"The highest-priority thermal finding is: {findings[0].title}."
                simple=findings[0].simple_explanation
            else:
                answer="No branch at or above the Alpha 90% thermal screening threshold was found."
                simple="None of the monitored branches is close enough to its rating to trigger this screening rule."

        elif Capability.VOLTAGE_RANKING in plan.capabilities:
            findings=ModelDoctor(self.adapter).voltage_findings(top_n=10)
            if findings:
                answer=f"The lowest-voltage finding is: {findings[0].title}."
                simple=findings[0].simple_explanation
            else:
                answer="No bus below the Alpha 0.95 pu voltage screening threshold was found."
                simple="All retrieved bus voltages are inside the current screening band."

        else:
            overview=CaseOverview(self.adapter).run()
            evidence.append({"case_overview":overview,"solver_backed":self.adapter.solver_backed})
            for k,v in overview.items():ledger.record("inventory",k,v,solver_backed=self.adapter.solver_backed)
            answer=(f"The loaded case contains {overview['buses']} buses, {overview['branches']} branches, "
                    f"{overview['generators']} generators, and {overview['loads']} loads.")
            simple="This is the basic network inventory. Ask about loading, voltage, sensitivities, or a scenario to go deeper."

        return StudyAnswer(
            answer=answer,simple_explanation=simple,intent=plan.intent,risk=plan.risk,
            findings=findings,evidence=[*evidence,{"ledger":ledger.to_dict()},{"study_plan":plan.model_dump(mode="json")}],
            study_id=ledger.study_id,solver_backed=self.adapter.solver_backed,warnings=self._demo_warning(),
        )

    def network(self)->dict[str,Any]:
        self._ensure_case()
        return VisualGridCanvas(self.adapter).build()

