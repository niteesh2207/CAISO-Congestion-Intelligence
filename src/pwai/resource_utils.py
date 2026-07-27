from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def project_or_package_resource(*parts: str) -> Path:
    """
    Resolve a resource in both source-tree/editable and installed-wheel modes.

    Source tree:
        <repo>/<parts>

    Wheel:
        pwai/resources/<parts>
    """
    source_root = Path(__file__).resolve().parents[2]
    candidate = source_root.joinpath(*parts)
    if candidate.exists():
        return candidate

    packaged = files("pwai.resources").joinpath(*parts)
    # The wheel is currently unpacked by normal pip installation, so converting
    # Traversable to Path is valid for this deployment model.
    p = Path(str(packaged))
    if not p.exists():
        raise FileNotFoundError(
            f"Required resource {'/'.join(parts)} was not found in source tree or package."
        )
    return p
