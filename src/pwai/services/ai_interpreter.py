from __future__ import annotations
import json, os
from typing import Any
from ..models import StudyPlan
from ..planner import plan as deterministic_plan


SYSTEM = """
You are the intent interpreter for PowerWorld AI Grid Studio.
You do NOT calculate electrical quantities.
You do NOT invent PowerWorld results.
Your only job is to classify the user's engineering intent and propose which
approved study capabilities are needed.

Approved intent families:
ASK, EXPLAIN, CHANGE, TEST, OPTIMIZE

Approved capabilities:
CASE_OVERVIEW, MODEL_DOCTOR, THERMAL_RANKING, VOLTAGE_RANKING,
CONTINGENCY, NATIVE_CONTINGENCY, N1_SECURITY, CAPABILITY_REGISTRY, OPF, SCOPF, ECONOMIC_OPTIMIZATION, LMP_DECOMPOSITION, BINDING_CONSTRAINTS, CONSTRAINT_ECONOMICS, TRADING_TRANSLATION, BUILD_GUARDIAN, SCOPF_CONTINGENCY_ECONOMICS, SECURITY_PRICE_ATTRIBUTION, MARKET_CALIBRATION, CTG_INJECTION_SENSITIVITY, CTG_RELIEF_RANKING, BESS_SCREEN, BESS_REVERSIBLE_CONTROL, BESS_INVENTORY, BESS_ENERGY_FEASIBILITY, BESS_SOLVED_ACTION, BESS_ECONOMICS, TIME_STEP_SIMULATION, TIME_SERIES_REPLAY, BESS_PORTFOLIO, BESS_MULTI_HOUR, GRID_TIME_MACHINE, TRANSMISSION_UPGRADE, STORAGE_VS_WIRES, INVESTMENT_ECONOMICS, BENEFICIARY_MAPPING, SCENARIO_ENSEMBLE, RISK_ANALYTICS, STUDY_MEMORY, KNOWLEDGE_GRAPH, AUTONOMOUS_INVESTIGATOR, RELEASE_HEALTH, ATC, PV_CURVE, QV_CURVE, TRANSIENT_STABILITY, IBR_INTELLIGENCE, ENTERPRISE_GOVERNANCE, VISUAL_GRID_CANVAS, DIFFERENCE_FLOW_REPLAY, GRID_HEADROOM, RAS_INTELLIGENCE, WEATHER_INTELLIGENCE, DYNAMIC_LINE_RATING, RESERVE_INTELLIGENCE, FULL_TOPOLOGY, AUTOMATIC_SCENARIO_GENERATOR, PTDF, LODF, OTDF, SHIFT_FACTOR_SCREEN, CAUSAL_DIAGNOSIS, SENSITIVITY, REMEDY_SEARCH, FIELD_DISCOVERY, RAW_QUERY

Return only JSON with:
intent, objective, capabilities, assumptions.
Never include numerical study results.
"""


def ai_plan(question: str) -> StudyPlan:
    """
    Optional semantic planner.

    The local deterministic planner is always available. OpenAI is imported only
    when OPENAI_API_KEY is configured, so the engineering core remains usable
    and testable completely offline.
    """
    fallback = deterministic_plan(question)
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return fallback

    try:
        from openai import OpenAI
    except ImportError:
        return fallback

    client = OpenAI(api_key=key)
    try:
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5.6"),
            input=f"{SYSTEM}\n\nUSER QUESTION:\n{question}",
            reasoning={"effort": "medium"},
        )
        data: dict[str, Any] = json.loads(response.output_text.strip())
    except Exception:
        return fallback

    approved = {
        "CASE_OVERVIEW","MODEL_DOCTOR","THERMAL_RANKING","VOLTAGE_RANKING",
        "CONTINGENCY","NATIVE_CONTINGENCY","N1_SECURITY","CAPABILITY_REGISTRY","OPF","SCOPF","ECONOMIC_OPTIMIZATION","LMP_DECOMPOSITION","BINDING_CONSTRAINTS","CONSTRAINT_ECONOMICS","TRADING_TRANSLATION","BUILD_GUARDIAN","SCOPF_CONTINGENCY_ECONOMICS","SECURITY_PRICE_ATTRIBUTION","MARKET_CALIBRATION","CTG_INJECTION_SENSITIVITY","CTG_RELIEF_RANKING","BESS_SCREEN","BESS_REVERSIBLE_CONTROL","BESS_INVENTORY","BESS_ENERGY_FEASIBILITY","BESS_SOLVED_ACTION","BESS_ECONOMICS","TIME_STEP_SIMULATION","TIME_SERIES_REPLAY","BESS_PORTFOLIO","BESS_MULTI_HOUR","GRID_TIME_MACHINE","TRANSMISSION_UPGRADE","STORAGE_VS_WIRES","INVESTMENT_ECONOMICS","BENEFICIARY_MAPPING","SCENARIO_ENSEMBLE","RISK_ANALYTICS","STUDY_MEMORY","KNOWLEDGE_GRAPH","AUTONOMOUS_INVESTIGATOR","RELEASE_HEALTH","ATC","PV_CURVE","QV_CURVE","TRANSIENT_STABILITY","IBR_INTELLIGENCE","ENTERPRISE_GOVERNANCE","VISUAL_GRID_CANVAS","DIFFERENCE_FLOW_REPLAY","GRID_HEADROOM","RAS_INTELLIGENCE","WEATHER_INTELLIGENCE","DYNAMIC_LINE_RATING","RESERVE_INTELLIGENCE","FULL_TOPOLOGY","AUTOMATIC_SCENARIO_GENERATOR","PTDF","LODF","OTDF","SHIFT_FACTOR_SCREEN","CAUSAL_DIAGNOSIS","SENSITIVITY","REMEDY_SEARCH","FIELD_DISCOVERY","RAW_QUERY"
    }

    # AI can add semantic study suggestions, but deterministic policy still owns
    # risk and confirmation requirements.
    combined = [c.value for c in fallback.capabilities]
    combined.extend(str(x) for x in data.get("capabilities", []))
    combined = [x for x in dict.fromkeys(combined) if x in approved]

    payload = fallback.model_dump()
    payload["objective"] = str(data.get("objective") or fallback.objective)
    payload["capabilities"] = combined
    payload["assumptions"] = list(dict.fromkeys([
        *fallback.assumptions,
        *[str(x) for x in data.get("assumptions", [])[:5]]
    ]))
    return StudyPlan.model_validate(payload)
