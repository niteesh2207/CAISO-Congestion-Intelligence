from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os

from ..resource_utils import project_or_package_resource


class EnterpriseGovernance:
    def __init__(self) -> None:
        self.root = Path(__file__).resolve().parents[3]

    def inspect(self) -> dict[str, Any]:
        registry = json.loads(
            project_or_package_resource("config", "source_registry.json").read_text(encoding="utf-8")
        )
        return {
            "deployment": {
                "mode": os.getenv("PWAI_DEPLOYMENT", "LOCAL_OR_ON_PREM"),
                "cloud_upload_enabled": os.getenv("PWAI_ALLOW_CLOUD_UPLOAD", "false").lower()=="true",
                "external_ai_enabled": bool(os.getenv("OPENAI_API_KEY")),
            },
            "data_policy": registry,
            "ceii_policy": {
                "default": "RESTRICTED",
                "cloud_upload_without_authorization": False,
                "redact_or_disable_external_ai_for_sensitive_cases": True,
            },
            "premium_data_policy": registry["premium_data_policy"],
            "audit": {
                "evidence_ledger": True,
                "study_hashing": True,
                "local_study_memory": True,
                "solver_provenance": True,
            },
        }
