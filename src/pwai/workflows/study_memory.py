from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import sqlite3
import time


class StudyMemory:
    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            path = Path(__file__).resolve().parents[3] / "runtime" / "study_memory.sqlite3"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self):
        return sqlite3.connect(self.path)

    def _init(self) -> None:
        with self._conn() as cx:
            cx.execute("""
                CREATE TABLE IF NOT EXISTS studies(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id TEXT,
                    created_unix REAL NOT NULL,
                    question TEXT NOT NULL,
                    study_type TEXT,
                    case_name TEXT,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL
                )
            """)
            cx.execute("""
                CREATE TABLE IF NOT EXISTS graph_edges(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    study_id TEXT,
                    source TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target TEXT NOT NULL,
                    evidence_json TEXT
                )
            """)

    def remember(
        self,
        *,
        study_id: str,
        question: str,
        study_type: str,
        case_name: str,
        payload: dict[str, Any],
        edges: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        text = json.dumps(payload, sort_keys=True, default=str)
        digest = hashlib.sha256(text.encode()).hexdigest()
        with self._conn() as cx:
            cx.execute(
                "INSERT INTO studies(study_id,created_unix,question,study_type,case_name,payload_json,payload_sha256) VALUES(?,?,?,?,?,?,?)",
                (study_id,time.time(),question,study_type,case_name,text,digest),
            )
            for edge in edges or []:
                cx.execute(
                    "INSERT INTO graph_edges(study_id,source,relation,target,evidence_json) VALUES(?,?,?,?,?)",
                    (
                        study_id,
                        str(edge["source"]),
                        str(edge["relation"]),
                        str(edge["target"]),
                        json.dumps(edge.get("evidence", {}), sort_keys=True, default=str),
                    ),
                )
        return {"study_id": study_id, "sha256": digest, "edge_count": len(edges or [])}

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as cx:
            rows = cx.execute(
                "SELECT study_id,created_unix,question,study_type,case_name,payload_sha256 FROM studies ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [
            {
                "study_id": r[0],
                "created_unix": r[1],
                "question": r[2],
                "study_type": r[3],
                "case_name": r[4],
                "sha256": r[5],
            }
            for r in rows
        ]

    def graph(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._conn() as cx:
            rows = cx.execute(
                "SELECT study_id,source,relation,target,evidence_json FROM graph_edges ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [
            {
                "study_id": r[0],
                "source": r[1],
                "relation": r[2],
                "target": r[3],
                "evidence": json.loads(r[4] or "{}"),
            }
            for r in rows
        ]
