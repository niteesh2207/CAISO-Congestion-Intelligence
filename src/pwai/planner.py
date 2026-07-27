from __future__ import annotations
from .models import Capability, IntentFamily, RiskClass, StudyPlan


def _has(q: str, *terms: str) -> bool:
    low = q.lower()
    return any(term in low for term in terms)


def plan(question: str) -> StudyPlan:
    question = question.strip()
    if not question:
        raise ValueError("Question cannot be empty.")
    q = question.lower()

    if _has(
        q,
        "visual grid canvas",
        "interactive grid canvas",
        "show the grid canvas",
        "show network canvas",
        "visualize the case",
        "visualize this case",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.ASK,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.VISUAL_GRID_CANVAS],
            objective="Build an evidence-rich interactive grid canvas from the open case.",
            requires_confirmation=False,
            evidence_required=["bus voltage", "branch MW/MVA/rating", "generation", "load", "storage", "layout provenance"],
            assumptions=["Derived layout is used only when case geography is unavailable."],
        )

    if _has(
        q,
        "flow replay",
        "difference replay",
        "before and after",
        "before/after",
        "show me what changed when",
        "animate the outage",
        "replay the outage",
    ) and _has(q, "line", "branch", "outage", "trip"):
        return StudyPlan(
            question=question,
            intent=IntentFamily.EXPLAIN,
            risk=RiskClass.SCENARIO_MUTATION,
            capabilities=[Capability.DIFFERENCE_FLOW_REPLAY],
            objective="Create a protected base-event-post event replay showing flow and voltage redistribution.",
            requires_confirmation=True,
            evidence_required=["base branch state", "post-event branch state", "base/post bus voltage", "state restoration"],
            assumptions=["Replay is created from protected snapshots; the stored base case is not changed."],
        )

    if _has(
        q,
        "grid headroom",
        "distance to failure",
        "distance-to-failure",
        "how much more transfer",
        "how much additional transfer",
        "transfer headroom",
        "how much can i increase",
        "how much can we increase",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.TEST,
            risk=RiskClass.SCENARIO_MUTATION,
            capabilities=[Capability.GRID_HEADROOM],
            objective="Estimate and verify additional source-to-sink transfer before the selected monitored branch reaches its configured limit.",
            requires_confirmation=True,
            evidence_required=["source bus", "sink bus", "monitored branch", "PTDF", "stepped AC verification", "state restoration"],
            assumptions=["Focused headroom is relative to the selected monitored branch; full N-1 headroom is a separate study."],
        )

    if _has(
        q,
        "ras",
        "remedial action scheme",
        "special protection scheme",
        "sps",
        "operating guide",
        "what protects this corridor",
        "ras failed",
        "without ras",
        "with ras",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.EXPLAIN,
            risk=RiskClass.SCENARIO_MUTATION,
            capabilities=[Capability.RAS_INTELLIGENCE],
            objective="Inspect remedial-action schemes and compare protected behavior where the scheme can be explicitly resolved.",
            requires_confirmation=True,
            evidence_required=["RAS arming", "RAS elements/actions", "trigger", "with/without RAS result", "state restoration"],
            assumptions=["Real RAS logic is never inferred when PowerWorld does not expose it."],
        )

    if _has(
        q,
        "weather risk",
        "weather driven",
        "weather-driven",
        "dynamic line rating",
        "dynamic rating",
        "dlr",
        "hot low wind",
        "cool windy",
        "weather dependent limit",
        "weather-dependent limit",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.TEST,
            risk=RiskClass.SCENARIO_MUTATION,
            capabilities=[Capability.WEATHER_INTELLIGENCE, Capability.DYNAMIC_LINE_RATING],
            objective="Evaluate weather-dependent grid capability while keeping native PowerWorld weather models authoritative.",
            requires_confirmation=True,
            evidence_required=["weather provenance", "weather-dependent limits", "base/weather branch loading", "state restoration"],
            assumptions=["Synthetic multipliers are used only in demo mode; real cases use configured PowerWorld weather-dependent limits."],
        )

    if _has(
        q,
        "reserve intelligence",
        "opf reserves",
        "spinning reserve",
        "supplemental reserve",
        "regulating reserve",
        "ancillary service",
        "reserve aware battery",
        "reserve-aware battery",
        "reserve market",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.EXPLAIN,
            risk=RiskClass.OPTIMIZATION,
            capabilities=[Capability.RESERVE_INTELLIGENCE],
            objective="Inspect reserve capability, bids, clearing evidence and battery opportunity cost without confusing demo reserve logic with PowerWorld OPF Reserves.",
            requires_confirmation=False,
            evidence_required=["reserve add-on/capability", "provider availability", "reserve requirement", "RMCP when solver-backed"],
            assumptions=["PowerWorld OPF Reserves is the authoritative co-optimization engine when licensed."],
        )

    if _has(
        q,
        "full topology",
        "full-topology",
        "ems topology",
        "breaker level",
        "breaker-level",
        "open with breakers",
        "integrated topology",
        "topology processing",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.EXPLAIN,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.FULL_TOPOLOGY],
            objective="Translate planning-level equipment actions into full-topology / breaker-level operating logic.",
            requires_confirmation=False,
            evidence_required=["ITP add-on signal", "branch device types", "breaker mapping or OpenWithBreakers command"],
            assumptions=["Real breaker selection is delegated to PowerWorld Integrated Topology Processing/OpenWithBreakers."],
        )

    if _has(
        q,
        "generate scenarios",
        "automatic scenario",
        "automatic scenarios",
        "scenario generator",
        "what should i worry about tomorrow",
        "what should we worry about tomorrow",
        "generate tomorrow scenarios",
        "stress case generator",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.OPTIMIZE,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.AUTOMATIC_SCENARIO_GENERATOR],
            objective="Generate and rank multi-factor grid scenarios, then recommend the appropriate PowerWorld solution depth.",
            requires_confirmation=False,
            evidence_required=["scenario dimensions", "screening score", "retained cases", "solution-depth recommendation", "provenance"],
            assumptions=["Scenario screening score is not a probability or a solved security result."],
        )

    if _has(
        q,
        "enterprise governance",
        "data governance",
        "ceii policy",
        "premium data policy",
        "security policy",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.ASK,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.ENTERPRISE_GOVERNANCE],
            objective="Report deployment, CEII, premium-data, external-AI, and audit policy.",
            requires_confirmation=False,
            evidence_required=["source registry", "deployment settings", "audit controls"],
        )

    if _has(
        q,
        "inspect ibr models",
        "inspect grid forming",
        "grid-forming models",
        "grid forming models",
        "ibr model inventory",
        "bess dynamic models",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.ASK,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.IBR_INTELLIGENCE],
            objective="Inspect dynamic-model schema evidence for inverter-based and grid-forming resources.",
            requires_confirmation=False,
            evidence_required=["generator model fields", "dynamic model schema"],
            assumptions=["Model presence does not prove dynamic stability."],
        )

    if _has(
        q,
        "validate transient",
        "transient validation",
        "transient stability validation",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.TEST,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.TRANSIENT_STABILITY],
            objective="Run or preview PowerWorld transient-stability validation.",
            requires_confirmation=True,
            evidence_required=["TSValidate", "TSValidation results"],
        )

    if _has(
        q,
        "run transient contingency",
        "solve transient contingency",
        "transient contingency",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.TEST,
            risk=RiskClass.SCENARIO_MUTATION,
            capabilities=[Capability.TRANSIENT_STABILITY],
            objective="Run a named PowerWorld transient-stability contingency with explicit simulation timing.",
            requires_confirmation=True,
            evidence_required=["TSSolve command", "named transient contingency"],
        )

    if _has(
        q,
        "run qv",
        "qv curve",
        "qv study",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.TEST,
            risk=RiskClass.SCENARIO_MUTATION,
            capabilities=[Capability.QV_CURVE],
            objective="Run or preview the configured PowerWorld QV analysis.",
            requires_confirmation=True,
            evidence_required=["QVRun command", "selected QV buses"],
        )

    if _has(
        q,
        "run pv",
        "pv curve",
        "pv study",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.TEST,
            risk=RiskClass.SCENARIO_MUTATION,
            capabilities=[Capability.PV_CURVE],
            objective="Run or preview PowerWorld PV analysis using explicit source/sink injection groups.",
            requires_confirmation=True,
            evidence_required=["PVRun command", "source injection group", "sink injection group"],
        )

    if _has(
        q,
        "available transfer capability",
        "run atc",
        "atc from",
        "atc between",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.TEST,
            risk=RiskClass.SCENARIO_MUTATION,
            capabilities=[Capability.ATC],
            objective="Run or preview PowerWorld Available Transfer Capability between explicit seller/source and buyer/sink.",
            requires_confirmation=True,
            evidence_required=["ATCDetermine command", "seller", "buyer", "TransferLimiter results"],
        )

    if _has(
        q,
        "release health",
        "release status",
        "production readiness",
        "is the product ready",
        "is this ready for production",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.ASK,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.RELEASE_HEALTH],
            objective="Report regression, solver-provenance, market-calibration, and production-readiness blockers.",
            requires_confirmation=False,
            evidence_required=["release manifest", "build guardian", "market calibration"],
        )

    if _has(
        q,
        "show study memory",
        "recent studies",
        "what have we learned",
        "knowledge graph",
        "study history",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.ASK,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.STUDY_MEMORY, Capability.KNOWLEDGE_GRAPH],
            objective="Return recent evidence-linked studies and knowledge-graph relationships.",
            requires_confirmation=False,
            evidence_required=["study hashes", "graph edges"],
        )

    if _has(
        q,
        "investigate this case",
        "investigate the case",
        "autonomous investigation",
        "autonomous grid investigator",
        "find what is wrong with this case",
        "tell me what matters most in this case",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.EXPLAIN,
            risk=RiskClass.OPTIMIZATION,
            capabilities=[
                Capability.AUTONOMOUS_INVESTIGATOR,
                Capability.STUDY_MEMORY,
                Capability.KNOWLEDGE_GRAPH,
            ],
            objective="Run an evidence-first autonomous health, N-1, and economics investigation and rank priorities.",
            requires_confirmation=True,
            evidence_required=[
                "build provenance", "model findings", "N-1 results",
                "OPF economics when available", "audit memory receipt"
            ],
            assumptions=[
                "Autonomous investigation never permanently mutates the base case.",
                "Human review remains required before an operating or investment action."
            ],
        )

    if _has(
        q,
        "scenario ensemble",
        "risk ensemble",
        "probability of congestion",
        "probability of relief shortfall",
        "cvar",
        "worst case scenario",
        "stress scenarios",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.EXPLAIN,
            risk=RiskClass.OPTIMIZATION,
            capabilities=[Capability.SCENARIO_ENSEMBLE, Capability.RISK_ANALYTICS],
            objective="Run the configured storage/congestion scenario ensemble and summarize probability-weighted risk.",
            requires_confirmation=True,
            evidence_required=[
                "scenario probabilities", "scenario provenance",
                "expected objective", "relief-shortfall probability", "worst case", "CVaR"
            ],
            assumptions=[
                "The bundled ensemble is synthetic until external scenario inputs are verified."
            ],
        )

    if _has(
        q,
        "storage vs wires",
        "battery vs transmission",
        "bess vs transmission",
        "storage or transmission",
        "battery or line upgrade",
        "solve with batteries or transmission",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.OPTIMIZE,
            risk=RiskClass.OPTIMIZATION,
            capabilities=[
                Capability.STORAGE_VS_WIRES,
                Capability.INVESTMENT_ECONOMICS,
                Capability.TRANSMISSION_UPGRADE,
                Capability.BENEFICIARY_MAPPING,
            ],
            objective="Compare existing-storage relief with a protected transmission-rating upgrade and explicit investment assumptions.",
            requires_confirmation=True,
            evidence_required=[
                "storage horizon result", "branch upgrade result",
                "OPF/SCOPF cost delta", "annualized assumptions", "beneficiary LMP changes"
            ],
            assumptions=[
                "Investment costs and representative congestion hours must be explicitly validated.",
                "The result is screening-level decision intelligence, not project-finance approval."
            ],
        )

    if (
        (
            _has(q, "upgrade branch", "upgrade line", "increase branch rating", "increase line rating")
            or (
                _has(q, "upgrade", "increase", "raise")
                and _has(q, "branch", "line", "rating")
            )
        )
        and _has(q, "mva", "branch", "line", "rating")
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.TEST,
            risk=RiskClass.SCENARIO_MUTATION,
            capabilities=[
                Capability.TRANSMISSION_UPGRADE,
                Capability.INVESTMENT_ECONOMICS,
                Capability.BENEFICIARY_MAPPING,
            ],
            objective="Run a protected branch-MVA-rating upgrade and compare physics, N-1, OPF/SCOPF economics, and nodal beneficiaries.",
            requires_confirmation=True,
            evidence_required=[
                "base/new branch rating", "branch loading", "N-1",
                "OPF cost", "SCOPF cost", "LMP changes"
            ],
            assumptions=[
                "The study changes rating only; it does not infer conductor, impedance, topology, construction, permitting, or feasibility."
            ],
        )

    if _has(
        q,
        "time step simulation capabilities",
        "time-step simulation capabilities",
        "tss capabilities",
        "powerworld time step",
        "powerworld time-step",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.ASK,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.TIME_STEP_SIMULATION],
            objective="Describe the PowerWorld Time Step Simulation bridge and execution boundaries.",
            requires_confirmation=False,
            evidence_required=[
                "TimeStepDoRun", "TimeStepDoSinglePoint",
                "documented TSS solution types", "real-machine validation status"
            ],
            assumptions=[
                "Native TSS must already be configured before V0.12 executes it.",
                "Product-owned replay is separate from PowerWorld's native TSS result store."
            ],
        )

    if _has(
        q,
        "optimize battery portfolio",
        "optimize bess portfolio",
        "multi-hour battery",
        "multi hour battery",
        "multi-hour bess",
        "multi hour bess",
        "battery portfolio over",
        "bess portfolio over",
        "grid time machine",
        "storage portfolio",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.OPTIMIZE,
            risk=RiskClass.OPTIMIZATION,
            capabilities=[
                Capability.BESS_PORTFOLIO,
                Capability.BESS_MULTI_HOUR,
                Capability.TIME_SERIES_REPLAY,
                Capability.GRID_TIME_MACHINE,
                Capability.TIME_STEP_SIMULATION,
            ],
            objective="Optimize an existing-BESS multi-hour dispatch trajectory, preserve SOC constraints, and replay the schedule through protected grid states.",
            requires_confirmation=True,
            evidence_required=[
                "existing BA units", "initial SOC/MWh", "hourly scenario",
                "charge/discharge MW", "SOC trajectory", "OTDF relief",
                "load multiplier", "balancing generator", "hourly PF",
                "hourly N-1 summary", "state restoration"
            ],
            assumptions=[
                "The V0.12 portfolio optimizer is a discretized product-owned multi-period optimizer, not PowerWorld multi-period OPF.",
                "Native PowerWorld TSS execution is used only when a TSS configuration already exists.",
                "Scenario prices and hourly inputs retain their own provenance."
            ],
        )

    if _has(
        q,
        "show existing batteries",
        "show existing bess",
        "list existing batteries",
        "list existing bess",
        "battery inventory",
        "bess inventory",
        "storage inventory",
        "which batteries are in the case",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.ASK,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.BESS_INVENTORY, Capability.BESS_ENERGY_FEASIBILITY],
            objective="Inventory existing storage generators in the open PowerWorld case and attach verified energy metadata when available.",
            requires_confirmation=False,
            evidence_required=[
                "generator bus/id", "Unit Type", "current MW", "min/max MW",
                "OPF MW control", "storage metadata provenance"
            ],
            assumptions=[
                "Unit Type BA is treated as battery energy storage.",
                "MWh/SOC capability is unknown unless explicitly registered in storage metadata."
            ],
        )

    if (
        _has(
            q,
            "test battery",
            "test bess",
            "dispatch battery",
            "dispatch bess",
            "charge battery",
            "charge bess",
            "discharge battery",
            "discharge bess",
            "battery action",
            "bess action",
        )
        and _has(q, "bus", "battery", "bess")
        and _has(q, "mw")
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.TEST,
            risk=RiskClass.SCENARIO_MUTATION,
            capabilities=[
                Capability.BESS_INVENTORY,
                Capability.BESS_ENERGY_FEASIBILITY,
                Capability.BESS_SOLVED_ACTION,
                Capability.N1_SECURITY,
                Capability.BESS_ECONOMICS,
            ],
            objective="Execute a protected charge/discharge study on an existing PowerWorld battery and validate physical, N-1, energy, and economic effects.",
            requires_confirmation=True,
            evidence_required=[
                "existing BA unit", "requested MW", "duration", "SOC/MWh metadata",
                "balancing generator", "base/post branch state", "N-1 comparison",
                "actual BESS MW after solve", "OPF/SCOPF setpoint retention"
            ],
            assumptions=[
                "No new hypothetical generator is inserted.",
                "The BESS and balancing-generator MW changes are equal and opposite.",
                "OPF/SCOPF economics are accepted only if the requested BESS MW remains held."
            ],
        )

    if (
        _has(
            q,
            "which existing battery",
            "which existing bess",
            "best existing battery",
            "best existing bess",
        )
        and _has(q, "contingency", "constraint", "branch", "line")
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.EXPLAIN,
            risk=RiskClass.READ_ONLY,
            capabilities=[
                Capability.BESS_INVENTORY,
                Capability.BESS_ENERGY_FEASIBILITY,
                Capability.BESS_SCREEN,
            ],
            objective="Rank only the existing battery units in the case by feasible contingency relief.",
            requires_confirmation=False,
            evidence_required=[
                "existing BA units", "MW headroom", "energy-duration headroom",
                "OTDF", "reference bus", "monitored branch", "outage"
            ],
            assumptions=[
                "Only existing BA units are ranked.",
                "Energy-feasible MW is limited by both generator MW bounds and verified SOC/MWh metadata when present."
            ],
        )

    if (
        _has(
            q,
            "where should a battery",
            "where should battery",
            "where should bess",
            "battery discharge",
            "bess discharge",
            "battery charging worsen",
            "battery charge worsen",
            "bess charging worsen",
            "bess charge worsen",
            "battery placement",
            "bess placement",
        )
        and _has(q, "contingency", "constraint", "branch", "line")
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.EXPLAIN,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.BESS_SCREEN, Capability.BESS_REVERSIBLE_CONTROL],
            objective="Rank candidate battery buses by post-contingency MW relief for charging and discharging.",
            requires_confirmation=False,
            evidence_required=[
                "battery MW", "candidate buses", "explicit balancing/reference bus",
                "monitored branch", "outage/contingency", "OTDF", "post-contingency flow direction"
            ],
            assumptions=[
                "This is a static power-only screen; energy duration, SOC, efficiency, cycling and market dispatch are not inferred.",
                "Battery discharge is positive injection; charging is negative injection.",
                "The result depends on the selected balancing/reference bus."
            ],
        )

    if (
        _has(
            q,
            "which generators can relieve",
            "which generator can relieve",
            "which loads can relieve",
            "which load can relieve",
            "contingency injection sensitivities",
            "violation injection sensitivities",
            "mw effect inc",
            "mw effect dec",
            "injector relief",
            "relieve contingency",
        )
        and _has(q, "contingency", "violation", "branch", "interface")
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.EXPLAIN,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.CTG_INJECTION_SENSITIVITY, Capability.CTG_RELIEF_RANKING],
            objective="Rank generator/load injectors that can relieve a selected contingency violation.",
            requires_confirmation=False,
            evidence_required=[
                "injector", "contingency", "violated element", "MW injection sensitivity",
                "MW Range Inc", "MW Range Dec", "MW Effect Inc", "MW Effect Dec"
            ],
            assumptions=[
                "Native PowerWorld result fields are preferred when exposed by the running case.",
                "The product will not silently change contingency injection-sensitivity settings."
            ],
        )

    if _has(
        q,
        "market calibrated",
        "market calibration",
        "is this market calibrated",
        "is this case market calibrated",
        "can i trade this",
        "can this be used for trading",
        "iso calibrated",
        "settlement calibrated",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.ASK,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.MARKET_CALIBRATION],
            objective="Audit whether the current model has enough verified market inputs to support trading interpretation.",
            requires_confirmation=False,
            evidence_required=[
                "topology provenance", "offer/bid provenance", "outage provenance",
                "ratings provenance", "load provenance", "loss treatment",
                "commitment provenance", "market-rule provenance"
            ],
            assumptions=[
                "MODEL_ECONOMICS and MARKET_CALIBRATED are separate product states.",
                "A PowerWorld OPF/SCOPF result is not automatically an ISO/RTO settlement or forecast."
            ],
        )

    if (
        _has(
            q,
            "which contingency constraint drives",
            "contingency constraint drives",
            "security component of lmp",
            "security price attribution",
            "security-constrained price spread",
            "scopf price spread",
            "contingency shadow price",
            "scopf marginal cost",
            "binding contingency constraints",
            "which contingencies are binding economically",
            "contingency constraints are binding economically",
            "contingency economics",
        )
        or (
            "scopf" in q
            and "contingency" in q
            and _has(
                q,
                "binding",
                "economically",
                "marginal cost",
                "shadow price",
                "price spread",
            )
        )
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.EXPLAIN,
            risk=RiskClass.OPTIMIZATION,
            capabilities=[
                Capability.SCOPF,
                Capability.SCOPF_CONTINGENCY_ECONOMICS,
                Capability.SECURITY_PRICE_ATTRIBUTION,
            ],
            objective="Explain which contingency constraints are economically active in SCOPF and how security changes nodal economics.",
            requires_confirmation=True,
            evidence_required=[
                "OPF price baseline", "SCOPF price result", "SCOPF contingency violations",
                "included flag", "marginal cost", "new value vs scaled limit",
                "unenforceable flag", "source/sink OTDF screening where requested"
            ],
            assumptions=[
                "The exact security effect on modeled bus prices is measured by comparing OPF and SCOPF model outputs.",
                "Marginal-cost × OTDF is used as a contingency-driver screening signal, not as an exact LMP contribution identity."
            ],
        )

    if _has(
        q,
        "powerworld build",
        "powerworld patch",
        "build guardian",
        "is powerworld current",
        "is my powerworld current",
        "latest powerworld build",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.ASK,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.BUILD_GUARDIAN],
            objective="Compare the running PowerWorld version/build with the product's public patch baseline.",
            requires_confirmation=False,
            evidence_required=["ProgramInformation version", "public baseline version/build date"],
        )

    if _has(
        q,
        "lmp spread",
        "price spread",
        "why is bus",
        "why bus",
        "energy congestion loss",
        "energy congestion losses",
        "decompose lmp",
        "lmp decomposition",
        "congestion component",
        "loss component",
        "trading intelligence from opf",
        "opf trading intelligence",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.EXPLAIN,
            risk=RiskClass.OPTIMIZATION,
            capabilities=[
                Capability.OPF,
                Capability.LMP_DECOMPOSITION,
                Capability.CONSTRAINT_ECONOMICS,
                Capability.TRADING_TRANSLATION,
            ],
            objective="Explain modeled nodal price formation and the source-to-sink price spread after OPF.",
            requires_confirmation=True,
            evidence_required=[
                "OPF result", "bus MW marginal cost", "energy component",
                "congestion component", "loss component", "binding constraints",
                "constraint marginal costs", "PTDF screening exposure"
            ],
            assumptions=[
                "PowerWorld model LMPs are not automatically ISO settlement prices.",
                "Constraint PTDF × marginal-cost screening is supporting evidence, not an exact price-component reconstruction."
            ],
        )

    if (
        _has(
            q,
            "binding constraints",
            "constraints are binding",
            "constraints binding",
            "binding lines",
            "which constraints bind",
            "shadow price",
            "constraint marginal cost",
            "marginal cost of constraint",
        )
        and "scopf" not in q
        and "contingency" not in q
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.EXPLAIN,
            risk=RiskClass.OPTIMIZATION,
            capabilities=[
                Capability.OPF,
                Capability.BINDING_CONSTRAINTS,
                Capability.CONSTRAINT_ECONOMICS,
            ],
            objective="Identify binding OPF constraints and explain their marginal enforcement cost.",
            requires_confirmation=True,
            evidence_required=[
                "OPF result", "constraint status", "constraint marginal cost",
                "flow/loading", "limit"
            ],
            assumptions=[
                "A nonzero line/interface marginal cost is economic evidence that the enforced constraint affects the optimum.",
            ],
        )

    if _has(
        q,
        "what optimization capabilities",
        "what addons",
        "what add-ons",
        "which addons",
        "which add-ons",
        "opf available",
        "scopf available",
        "optimization available",
        "license capabilities",
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.ASK,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.CAPABILITY_REGISTRY],
            objective="Inspect the running PowerWorld version and licensed add-on capabilities.",
            requires_confirmation=False,
            evidence_required=["ProgramInformation version", "ProgramInformation addons"],
        )

    if _has(q, "lowest-cost n-1", "lowest cost n-1", "minimum-cost n-1",
            "minimum cost n-1", "security-constrained economic", "security constrained economic",
            "run scopf", "solve scopf", "scopf dispatch"):
        return StudyPlan(
            question=question,
            intent=IntentFamily.OPTIMIZE,
            risk=RiskClass.OPTIMIZATION,
            capabilities=[Capability.SCOPF, Capability.ECONOMIC_OPTIMIZATION],
            objective="Find an economical pre-contingency dispatch that respects contingency security.",
            requires_confirmation=True,
            evidence_required=[
                "SCOPF capability", "case OPF configuration", "generator dispatch before/after",
                "bus marginal prices", "post-SCOPF contingency audit"
            ],
            assumptions=[
                "The product will use the case's existing OPF/SCOPF controls, limits and cost data.",
                "It will not silently alter OPF participation, cost curves or contingency definitions."
            ],
        )

    if _has(q, "run economic opf", "solve economic opf", "run opf", "solve opf",
            "lowest-cost dispatch", "lowest cost dispatch", "economic dispatch with constraints",
            "minimum-cost dispatch", "minimum cost dispatch"):
        return StudyPlan(
            question=question,
            intent=IntentFamily.OPTIMIZE,
            risk=RiskClass.OPTIMIZATION,
            capabilities=[Capability.OPF, Capability.ECONOMIC_OPTIMIZATION],
            objective="Run the configured PowerWorld OPF and explain the economic dispatch and marginal prices.",
            requires_confirmation=True,
            evidence_required=[
                "OPF capability", "case OPF configuration", "generator dispatch before/after",
                "generator costs", "bus marginal prices"
            ],
            assumptions=[
                "The product will use existing case cost curves and OPF controls.",
                "No cost curves, area OPF status, or generator OPF-control flags are auto-created."
            ],
        )

    if _has(q, "fix this", "fix branch", "fix line", "fix constraint", "redispatch",
            "best redispatch", "best location", "where should", "optimize", "least cost",
            "smallest intervention", "best upgrade"):
        return StudyPlan(
            question=question,
            intent=IntentFamily.OPTIMIZE,
            risk=RiskClass.OPTIMIZATION,
            capabilities=[Capability.SENSITIVITY, Capability.REMEDY_SEARCH, Capability.N1_SECURITY],
            objective="Search for effective interventions and validate side-effects.",
            requires_confirmation=True,
            evidence_required=["base solved state", "candidate definitions", "candidate effects", "security re-check"],
            assumptions=["Optimization objective and allowed controls must be explicit before execution."],
        )

    if _has(q, "run n-1", "run n1", "n-1 contingency", "n1 contingency",
            "all contingencies", "contingency list", "security screen"):
        return StudyPlan(
            question=question,
            intent=IntentFamily.ASK,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.NATIVE_CONTINGENCY],
            objective="Run the current PowerWorld contingency list and summarize security results.",
            requires_confirmation=False,
            evidence_required=[
                "contingency count", "processed status", "solved status",
                "violation contingency", "violated object", "violation percent"
            ],
            assumptions=[
                "The current non-skipped PowerWorld contingency list defines the N-1/security study set.",
                "Distributed contingency execution is disabled in V0.6 unless explicitly added later."
            ],
        )

    # Causal diagnosis is intentionally checked before generic TEST/outage parsing.
    if _has(q, "why did", "why does", "why is", "what caused", "root cause", "driving") and _has(
        q, "after outage", "after line", "when line", "if line", "contingency", "trips", "trip"
    ):
        return StudyPlan(
            question=question,
            intent=IntentFamily.EXPLAIN,
            risk=RiskClass.READ_ONLY,
            capabilities=[Capability.CAUSAL_DIAGNOSIS],
            objective="Explain a monitored branch outcome using solved before/after evidence and linear sensitivities.",
            requires_confirmation=False,
            evidence_required=[
                "base monitored flow", "base monitored loading", "outage line pre-event flow",
                "post-event monitored flow", "LODF", "linear predicted MW change",
                "actual MW change", "sensitivity reference bus"
            ],
            assumptions=[
                "Shift-factor rankings identify sensitivity exposure, not literal additive generator contribution.",
                "Linear LODF explanation is checked against the solved post-event result."
            ],
        )

    if "otdf" in q or ("post-outage" in q and "transfer" in q):
        return StudyPlan(
            question=question, intent=IntentFamily.ASK, risk=RiskClass.READ_ONLY,
            capabilities=[Capability.OTDF],
            objective="Calculate post-outage transfer sensitivity.",
            requires_confirmation=False,
            evidence_required=["source", "sink", "monitored branch", "outage branch", "PTDFs", "LODF", "OTDF"],
        )

    if "lodf" in q or "outage distribution factor" in q:
        return StudyPlan(
            question=question, intent=IntentFamily.ASK, risk=RiskClass.READ_ONLY,
            capabilities=[Capability.LODF],
            objective="Calculate line-outage distribution factors.",
            requires_confirmation=False,
            evidence_required=["outage branch", "monitored branch LODFs"],
        )

    if "ptdf" in q or "transfer distribution factor" in q:
        return StudyPlan(
            question=question, intent=IntentFamily.ASK, risk=RiskClass.READ_ONLY,
            capabilities=[Capability.PTDF],
            objective="Calculate transfer distribution factors between source and sink.",
            requires_confirmation=False,
            evidence_required=["source", "sink", "branch PTDFs"],
        )

    if _has(q, "which buses worsen", "which buses relieve", "source side", "sink side",
            "injection sensitivity", "shift factor"):
        return StudyPlan(
            question=question, intent=IntentFamily.EXPLAIN, risk=RiskClass.READ_ONLY,
            capabilities=[Capability.SHIFT_FACTOR_SCREEN],
            objective="Rank bus injections by their effect on a monitored branch.",
            requires_confirmation=False,
            evidence_required=["monitored branch", "reference sink", "signed shift factors"],
            assumptions=["Bus screening is expressed relative to the selected sink/reference bus."],
        )

    if _has(q, "what happens if", "what if", "trip", "outage", "retire",
            "take out", "lose generator", "open this line"):
        return StudyPlan(
            question=question, intent=IntentFamily.TEST, risk=RiskClass.SCENARIO_MUTATION,
            capabilities=[Capability.CONTINGENCY, Capability.THERMAL_RANKING, Capability.VOLTAGE_RANKING],
            objective="Run a protected counterfactual and compare it with the base case.",
            requires_confirmation=True,
            evidence_required=["base solve", "event definition", "post-event solve", "before/after results"],
            assumptions=["The original case must remain unchanged."],
        )

    if _has(q, "set ", "change ", "increase generation", "decrease generation",
            "increase load", "switch ", "close line"):
        return StudyPlan(
            question=question, intent=IntentFamily.CHANGE, risk=RiskClass.SCENARIO_MUTATION,
            capabilities=[Capability.RAW_QUERY],
            objective="Apply a requested change in protected scenario mode.",
            requires_confirmation=True,
            evidence_required=["original value", "new value", "scenario identifier", "post-change solve status"],
            assumptions=["No destructive overwrite of the original case."],
        )

    if _has(q, "why", "explain", "cause", "reason"):
        return StudyPlan(
            question=question, intent=IntentFamily.EXPLAIN, risk=RiskClass.READ_ONLY,
            capabilities=[Capability.MODEL_DOCTOR, Capability.SENSITIVITY],
            objective="Diagnose the physical mechanism using solver-backed evidence.",
            requires_confirmation=False,
            evidence_required=["solved state", "relevant measurements", "relevant sensitivities when available"],
        )

    if _has(q, "problem", "wrong", "health", "check case", "model doctor",
            "five most important", "most important issue", "issues"):
        caps = [Capability.MODEL_DOCTOR]
    elif _has(q, "highest loading", "most loaded", "overload", "thermal"):
        caps = [Capability.THERMAL_RANKING]
    elif _has(q, "low voltage", "weak bus", "voltage"):
        caps = [Capability.VOLTAGE_RANKING]
    else:
        caps = [Capability.CASE_OVERVIEW]

    return StudyPlan(
        question=question, intent=IntentFamily.ASK, risk=RiskClass.READ_ONLY,
        capabilities=caps,
        objective="Answer a read-only question from the current solved model.",
        requires_confirmation=False,
        evidence_required=["case identity", "requested solver-backed values"],
    )
