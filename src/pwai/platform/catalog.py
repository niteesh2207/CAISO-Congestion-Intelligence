from __future__ import annotations
import json
from ..resource_utils import project_or_package_resource


def capability_catalog() -> dict:
    p=project_or_package_resource("platform","capability_catalog.json")
    return json.loads(p.read_text(encoding="utf-8"))


def answer_profiles() -> dict:
    p=project_or_package_resource("platform","answer_profiles.json")
    return json.loads(p.read_text(encoding="utf-8"))
