from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any
import hashlib, json, uuid


@dataclass
class EvidenceLedger:
    question: str
    solver_info: dict[str, Any]
    case_name: str
    study_id: str = field(default_factory=lambda: f"PWAI-{uuid.uuid4().hex[:12].upper()}")
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    actions: list[dict[str, Any]] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def action(self, action: str, **meta: Any) -> None:
        self.actions.append({"action": action, **meta})

    def record(self, category: str, label: str, value: Any, *, solver_backed: bool, **meta: Any) -> None:
        self.records.append({
            "category": category,
            "label": label,
            "value": value,
            "solver_backed": solver_backed,
            **meta,
        })

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def hash(self) -> str:
        blob = json.dumps({
            "question": self.question,
            "solver_info": self.solver_info,
            "case_name": self.case_name,
            "actions": self.actions,
            "records": self.records,
            "warnings": self.warnings,
        }, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "ledger_hash": self.hash(),
        }
