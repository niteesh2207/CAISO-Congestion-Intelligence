from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import numpy as np


@dataclass(frozen=True)
class DCBranch:
    from_bus: int
    to_bus: int
    circuit: str
    x: float


class DCSensitivityModel:
    """
    Lossless DC linear sensitivity engine for demo/testing only.

    This is not a replacement for PowerWorld. It provides mathematically
    consistent synthetic PTDF/LODF values so the product logic can be tested
    without inventing arbitrary percentages.
    """

    def __init__(self, buses: list[int], branches: list[DCBranch], slack_bus: int):
        self.buses = list(buses)
        self.branches = list(branches)
        self.slack_bus = slack_bus
        self.index = {bus: i for i, bus in enumerate(self.buses)}
        if slack_bus not in self.index:
            raise ValueError("Slack bus not in bus list.")

        n = len(self.buses)
        self.B = np.zeros((n, n), dtype=float)
        for br in self.branches:
            if br.x == 0:
                raise ValueError("Zero reactance branch is unsupported in demo DC model.")
            i, j = self.index[br.from_bus], self.index[br.to_bus]
            b = 1.0 / br.x
            self.B[i, i] += b
            self.B[j, j] += b
            self.B[i, j] -= b
            self.B[j, i] -= b

        self.non_slack = [b for b in self.buses if b != slack_bus]
        keep = [self.index[b] for b in self.non_slack]
        self.Bred = self.B[np.ix_(keep, keep)]
        self.Bred_inv = np.linalg.inv(self.Bred)

    def _angles_for_transfer(self, source: int, sink: int, mw: float = 1.0) -> dict[int, float]:
        if source == sink:
            raise ValueError("Source and sink must differ.")
        p = {b: 0.0 for b in self.buses}
        p[source] += mw
        p[sink] -= mw
        rhs = np.array([p[b] for b in self.non_slack], dtype=float)
        theta_red = self.Bred_inv @ rhs
        theta = {self.slack_bus: 0.0}
        theta.update({b: float(theta_red[i]) for i, b in enumerate(self.non_slack)})
        return theta

    def ptdf(self, source: int, sink: int) -> list[dict[str, Any]]:
        theta = self._angles_for_transfer(source, sink, 1.0)
        rows = []
        for br in self.branches:
            flow = (theta[br.from_bus] - theta[br.to_bus]) / br.x
            rows.append({
                "from": br.from_bus,
                "to": br.to_bus,
                "circuit": br.circuit,
                "ptdf_pct": 100.0 * flow,
            })
        return rows

    def lodf(self, outage: DCBranch) -> list[dict[str, Any]]:
        transaction = self.ptdf(outage.from_bus, outage.to_bus)
        key = (outage.from_bus, outage.to_bus, outage.circuit)
        outage_ptdf = next(
            r["ptdf_pct"] / 100.0
            for r in transaction
            if (r["from"], r["to"], r["circuit"]) == key
        )
        denom = 1.0 - outage_ptdf
        if abs(denom) < 1e-9:
            raise RuntimeError("Outage creates a singular/islanding condition in the demo DC model.")

        rows = []
        for r in transaction:
            rkey = (r["from"], r["to"], r["circuit"])
            if rkey == key:
                lodf = -1.0
            else:
                lodf = (r["ptdf_pct"] / 100.0) / denom
            rows.append({
                "from": r["from"],
                "to": r["to"],
                "circuit": r["circuit"],
                "lodf_pct": 100.0 * lodf,
            })
        return rows
