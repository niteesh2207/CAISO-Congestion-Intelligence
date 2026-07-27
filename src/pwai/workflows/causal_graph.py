from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceNode:
    node_id: str
    kind: str
    label: str
    value: Any
    evidence_class: str  # FACT, DERIVED, INTERPRETATION
    confidence: str


@dataclass
class EvidenceEdge:
    source: str
    target: str
    relation: str


@dataclass
class CausalEvidenceGraph:
    nodes: list[EvidenceNode] = field(default_factory=list)
    edges: list[EvidenceEdge] = field(default_factory=list)

    def add_node(
        self, node_id: str, kind: str, label: str, value: Any,
        evidence_class: str, confidence: str
    ) -> None:
        self.nodes.append(EvidenceNode(
            node_id=node_id, kind=kind, label=label, value=value,
            evidence_class=evidence_class, confidence=confidence
        ))

    def link(self, source: str, target: str, relation: str) -> None:
        self.edges.append(EvidenceEdge(source=source, target=target, relation=relation))

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [vars(n) for n in self.nodes],
            "edges": [vars(e) for e in self.edges],
        }
