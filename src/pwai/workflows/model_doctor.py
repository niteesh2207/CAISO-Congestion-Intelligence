from __future__ import annotations
from typing import Any
from ..field_catalog import FieldCatalog
from ..models import Finding


def num(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


class ModelDoctor:
    def __init__(self, adapter) -> None:
        self.adapter=adapter
        self.catalog=FieldCatalog(adapter)

    def resolve(self,obj:str,candidates:list[str],required:bool=False)->str|None:
        field=self.catalog.choose(obj,candidates)
        if required and not field:
            raise RuntimeError(f"Could not resolve required {obj} field from {candidates}")
        return field

    def branch_fields(self) -> dict[str,str|None]:
        return {
            "from":self.resolve("BRANCH",["BusNum","BusNumFrom"],True),
            "to":self.resolve("BRANCH",["BusNum:1","BusNumTo"],True),
            "circuit":self.resolve("BRANCH",["LineCircuit","Circuit"],True),
            "status":self.resolve("BRANCH",["LineStatus","Status"],False),
            "mw":self.resolve("BRANCH",["LineMW","MWFrom"],False),
            "mva":self.resolve("BRANCH",["LineMVA","MVAFrom"],False),
            "limit":self.resolve("BRANCH",["LineLimMVA","LimitMVAA"],False),
            "pct":self.resolve("BRANCH",["LinePercent","PercentMVALimit","PctMVA"],False),
        }

    def branch_snapshot(self) -> list[dict[str,Any]]:
        f=self.branch_fields()
        fields=list(dict.fromkeys(x for x in f.values() if x))
        raw=self.adapter.get_rows("BRANCH",fields)
        out=[]
        for row in raw:
            mva=num(row.get(f["mva"])) if f["mva"] else None
            lim=num(row.get(f["limit"])) if f["limit"] else None
            pct=num(row.get(f["pct"])) if f["pct"] else None
            if pct is None and mva is not None and lim not in (None,0):
                pct=100*mva/lim
            out.append({
                "from":row.get(f["from"]),
                "to":row.get(f["to"]),
                "circuit":str(row.get(f["circuit"])),
                "status":row.get(f["status"]) if f["status"] else None,
                "mw":num(row.get(f["mw"])) if f["mw"] else None,
                "mva":mva,
                "limit_mva":lim,
                "loading_pct":pct,
            })
        return out

    def bus_snapshot(self) -> list[dict[str,Any]]:
        f_num=self.resolve("BUS",["BusNum"],True)
        f_name=self.resolve("BUS",["BusName"],False)
        f_v=self.resolve("BUS",["BusPUVolt"],True)
        fields=[f_num,f_v]+([f_name] if f_name else [])
        rows=self.adapter.get_rows("BUS",fields)
        return [{
            "bus":row.get(f_num),
            "name":row.get(f_name) if f_name else "",
            "voltage_pu":num(row.get(f_v)),
        } for row in rows]

    def thermal_findings(self,top_n:int=10)->list[Finding]:
        scored=[r for r in self.branch_snapshot() if r["loading_pct"] is not None]
        scored.sort(key=lambda r:r["loading_pct"],reverse=True)
        out=[]
        for rank,row in enumerate(scored[:top_n],1):
            pct=row["loading_pct"]
            if pct<90: continue
            sev="CRITICAL" if pct>=100 else "HIGH" if pct>=95 else "WATCH"
            out.append(Finding(
                finding_id=f"THERM-{rank}",severity=sev,category="THERMAL",
                title=f"Branch {row['from']}–{row['to']} {row['circuit']} at {pct:.1f}%",
                summary=(f"The branch is using {pct:.1f}% of the resolved thermal rating basis. "
                         +("It exceeds the configured limit." if pct>=100 else "It has limited thermal headroom.")),
                simple_explanation=("This transmission path is carrying more power than its configured limit allows."
                                    if pct>=100 else "This path is getting close to its configured capacity."),
                evidence=[{"metric":"loading_percent","value":pct,"solver_backed":self.adapter.solver_backed}],
                confidence="HIGH" if row["limit_mva"] is not None else "MEDIUM",
            ))
        return out

    def voltage_findings(self,top_n:int=10)->list[Finding]:
        rows=[r for r in self.bus_snapshot() if r["voltage_pu"] is not None]
        rows.sort(key=lambda r:r["voltage_pu"])
        out=[]
        for rank,row in enumerate(rows[:top_n],1):
            v=row["voltage_pu"]
            if v>=0.95: continue
            sev="CRITICAL" if v<0.90 else "HIGH" if v<0.93 else "WATCH"
            out.append(Finding(
                finding_id=f"VOLT-{rank}",severity=sev,category="VOLTAGE",
                title=f"Bus {row['bus']} {row['name']} at {v:.3f} pu".strip(),
                summary=f"Bus voltage is {v:.3f} pu, below the Alpha screening threshold of 0.95 pu.",
                simple_explanation="Voltage is low enough that reactive support, nearby loading and network strength should be investigated.",
                evidence=[{"metric":"voltage_pu","value":v,"solver_backed":self.adapter.solver_backed}],
                confidence="HIGH",
            ))
        return out

    def run(self,top_n:int=5)->list[Finding]:
        findings=self.thermal_findings(20)+self.voltage_findings(20)
        weight={"CRITICAL":4,"HIGH":3,"WATCH":2,"INFO":1}
        findings.sort(key=lambda f:weight.get(f.severity,0),reverse=True)
        return findings[:top_n]
