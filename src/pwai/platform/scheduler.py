from __future__ import annotations
from .contracts import SolverAgentHeartbeat, StudyJob


def select_agent(job: StudyJob, agents: list[SolverAgentHeartbeat]) -> SolverAgentHeartbeat | None:
    """Deterministic license-aware routing policy; no network side effects."""
    required={x.lower() for x in job.required_addons}
    eligible=[]
    for agent in agents:
        if not agent.healthy or agent.busy:
            continue
        available={x.lower() for x in agent.addons}
        if required.issubset(available):
            eligible.append(agent)
    eligible.sort(key=lambda a:(a.busy, a.agent_id))
    return eligible[0] if eligible else None
