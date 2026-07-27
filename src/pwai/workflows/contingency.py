from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from ..models import Finding
from .model_doctor import ModelDoctor
from .object_resolver import BranchIdentity, BranchResolver


@dataclass
class ContingencyResult:
    event:dict[str,Any]
    base_branches:list[dict[str,Any]]
    post_branches:list[dict[str,Any]]
    base_buses:list[dict[str,Any]]
    post_buses:list[dict[str,Any]]
    thermal_changes:list[dict[str,Any]]
    voltage_changes:list[dict[str,Any]]
    findings:list[Finding]


def _branch_key(row:dict[str,Any])->tuple:
    a,b=int(row["from"]),int(row["to"])
    return (min(a,b),max(a,b),str(row["circuit"]))


class BranchOutageStudy:
    """
    Protected single-branch outage study.

    Real PowerWorld execution:
    - SaveState()
    - change BRANCH LineStatus/Status to Open
    - solve AC power flow
    - snapshot results
    - LoadState() in finally block

    This is intentionally a protected counterfactual workflow rather than a
    replacement for PowerWorld's full Contingency Analysis tool. Native CTG
    workflows are a later phase.
    """

    def __init__(self,adapter)->None:
        self.adapter=adapter
        self.doctor=ModelDoctor(adapter)
        self.resolver=BranchResolver(adapter)

    def _status_fields(self)->dict[str,str]:
        cat=self.doctor.catalog
        return {
            "from":cat.choose("BRANCH",["BusNum","BusNumFrom"]),
            "to":cat.choose("BRANCH",["BusNum:1","BusNumTo"]),
            "circuit":cat.choose("BRANCH",["LineCircuit","Circuit"]),
            "status":cat.choose("BRANCH",["LineStatus","Status"]),
        }

    def run(self,identity:BranchIdentity)->ContingencyResult:
        target=self.resolver.resolve(identity)
        fields=self._status_fields()
        if not all(fields.values()):
            raise RuntimeError("Unable to resolve branch key/status fields for outage execution.")

        base_branches=self.doctor.branch_snapshot()
        base_buses=self.doctor.bus_snapshot()

        self.adapter.save_state()
        try:
            self.adapter.change_single(
                "BRANCH",
                [fields["from"],fields["to"],fields["circuit"],fields["status"]],
                [target["from"],target["to"],target["circuit"],"Open"],
            )
            self.adapter.run_script("EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);")
            post_branches=self.doctor.branch_snapshot()
            post_buses=self.doctor.bus_snapshot()
        finally:
            self.adapter.load_state()

        base_map={_branch_key(r):r for r in base_branches}
        post_map={_branch_key(r):r for r in post_branches}
        thermal=[]
        for key,post in post_map.items():
            base=base_map.get(key)
            if not base: continue
            thermal.append({
                "branch":f"{post['from']}-{post['to']} {post['circuit']}",
                "base_mw":base.get("mw"),
                "post_mw":post.get("mw"),
                "delta_mw":(
                    post["mw"]-base["mw"]
                    if post.get("mw") is not None and base.get("mw") is not None else None
                ),
                "base_loading_pct":base.get("loading_pct"),
                "post_loading_pct":post.get("loading_pct"),
                "delta_loading_pct":(
                    post["loading_pct"]-base["loading_pct"]
                    if post.get("loading_pct") is not None and base.get("loading_pct") is not None else None
                ),
            })
        thermal.sort(
            key=lambda r:abs(r["delta_loading_pct"]) if r["delta_loading_pct"] is not None else -1,
            reverse=True,
        )

        base_v={int(r["bus"]):r for r in base_buses}
        voltage=[]
        for post in post_buses:
            bus=int(post["bus"])
            base=base_v.get(bus)
            if not base or post["voltage_pu"] is None or base["voltage_pu"] is None: continue
            voltage.append({
                "bus":bus,
                "name":post.get("name",""),
                "base_voltage_pu":base["voltage_pu"],
                "post_voltage_pu":post["voltage_pu"],
                "delta_voltage_pu":post["voltage_pu"]-base["voltage_pu"],
            })
        voltage.sort(key=lambda r:abs(r["delta_voltage_pu"]),reverse=True)

        findings=[]
        rank=1
        for row in thermal:
            pct=row["post_loading_pct"]
            if pct is None or pct<100: continue
            findings.append(Finding(
                finding_id=f"CTG-THERM-{rank}",severity="CRITICAL",category="CONTINGENCY_THERMAL",
                title=f"{row['branch']} reaches {pct:.1f}%",
                summary=(
                    f"Following the outage, loading changes from "
                    f"{row['base_loading_pct']:.1f}% to {pct:.1f}%."
                ),
                simple_explanation="The outage redirects power onto this path and pushes it beyond its configured rating.",
                evidence=[row],
                confidence="HIGH",
            ))
            rank+=1

        for row in voltage:
            if row["post_voltage_pu"]>=0.95: continue
            sev="CRITICAL" if row["post_voltage_pu"]<0.90 else "HIGH" if row["post_voltage_pu"]<0.93 else "WATCH"
            findings.append(Finding(
                finding_id=f"CTG-VOLT-{row['bus']}",severity=sev,category="CONTINGENCY_VOLTAGE",
                title=f"Bus {row['bus']} {row['name']} falls to {row['post_voltage_pu']:.3f} pu".strip(),
                summary=(
                    f"Voltage changes from {row['base_voltage_pu']:.3f} pu to "
                    f"{row['post_voltage_pu']:.3f} pu."
                ),
                simple_explanation="The outage weakens electrical support to this location, so voltage declines.",
                evidence=[row],
                confidence="HIGH",
            ))

        return ContingencyResult(
            event={
                "type":"BRANCH_OUTAGE",
                "from_bus":identity.from_bus,
                "to_bus":identity.to_bus,
                "circuit":identity.circuit,
            },
            base_branches=base_branches,
            post_branches=post_branches,
            base_buses=base_buses,
            post_buses=post_buses,
            thermal_changes=thermal,
            voltage_changes=voltage,
            findings=findings,
        )
