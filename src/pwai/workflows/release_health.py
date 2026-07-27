from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import sys

from .build_guardian import BuildGuardian
from .market_calibration import MarketCalibrationAuditor
from ..resource_utils import project_or_package_resource


class ReleaseHealth:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.root = Path(__file__).resolve().parents[3]

    def inspect(self) -> dict[str, Any]:
        manifest_path = self.root / "release_manifest.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists() else {}
        )
        build = BuildGuardian(self.adapter).inspect()
        market = MarketCalibrationAuditor().audit()

        files = [
            self.root / "README.md",
            self.root / "app.py",
            self.root / "web" / "index.html",
            self.root / "config" / "storage_assets.json",
            self.root / "config" / "time_series_scenario.json",
        ]
        missing = [str(p.relative_to(self.root)) for p in files if not p.exists()]

        blockers = []
        if missing:
            blockers.append("MISSING_REQUIRED_FILES")
        if not manifest.get("tests_passed"):
            blockers.append("REGRESSION_NOT_GREEN")
        if not manifest.get("python_compile_passed"):
            blockers.append("PYTHON_COMPILE_NOT_GREEN")
        if not manifest.get("licensed_powerworld_acceptance_validated", False):
            blockers.append("LICENSED_POWERWORLD_ACCEPTANCE_PENDING")

        return {
            "release_version": manifest.get("release_version", "UNKNOWN"),
            "python": sys.version.split()[0],
            "tests": manifest.get("tests"),
            "tests_passed": manifest.get("tests_passed", False),
            "python_compile_passed": manifest.get("python_compile_passed", False),
            "demo_acceptance_passed": manifest.get("demo_acceptance_passed", False),
            "licensed_powerworld_acceptance_validated": manifest.get(
                "licensed_powerworld_acceptance_validated", False
            ),
            "build_guardian": build,
            "market_calibration": market,
            "missing_required_files": missing,
            "release_blockers": blockers,
            "production_status": (
                "RELEASE_CANDIDATE_NEEDS_POWERWORLD_ACCEPTANCE"
                if blockers == ["LICENSED_POWERWORLD_ACCEPTANCE_PENDING"]
                else "NOT_PRODUCTION_QUALIFIED"
                if blockers else "PRODUCTION_QUALIFIED"
            ),
        }
