from __future__ import annotations

from datetime import date
from typing import Any
import re

from .capabilities import CapabilityRegistry


PUBLIC_BASELINE_VERSION = 24

# Public pages do not presently show the same date:
# - General Software & Patches page: July 2, 2026
# - Simulator 24 patch history: latest visible Simulator 24 entry June 29, 2026
GENERAL_PUBLIC_BUILD_DATE = date(2026, 7, 22)
SIMULATOR24_LATEST_PATCH_ENTRY = date(2026, 6, 29)
BASELINE_VERIFIED_DATE = date(2026, 7, 27)


class BuildGuardian:
    """
    Provenance guard for the running PowerWorld installation.

    The public PowerWorld pages currently expose two useful reference dates:
    a general latest-build date and the latest visible Simulator 24 patch-log
    entry. The product retains both instead of silently treating them as one.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter

    @staticmethod
    def _parse_date(tokens: list[str]) -> date | None:
        for token in tokens:
            text = str(token)
            m = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text)
            if m:
                try:
                    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    pass

            m = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text)
            if m:
                try:
                    return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
                except ValueError:
                    pass

            m = re.search(
                r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(20\d{2})\b",
                text,
                re.IGNORECASE,
            )
            if m:
                months = {
                    name.lower(): idx for idx, name in enumerate(
                        ["January","February","March","April","May","June",
                         "July","August","September","October","November","December"], 1
                    )
                }
                try:
                    return date(int(m.group(3)), months[m.group(1).lower()], int(m.group(2)))
                except ValueError:
                    pass
        return None

    @staticmethod
    def _parse_major(tokens: list[str]) -> int | None:
        for token in tokens:
            m = re.search(r"\b(?:version\s*)?(\d{1,2})\b", str(token), re.IGNORECASE)
            if m:
                value = int(m.group(1))
                if 10 <= value <= 99:
                    return value
        return None

    def inspect(self) -> dict[str, Any]:
        snapshot = CapabilityRegistry(self.adapter).snapshot()
        tokens = [str(x) for x in snapshot.get("version", [])]
        major = self._parse_major(tokens)
        running_date = self._parse_date(tokens)

        if major is None:
            version_status = "UNKNOWN"
        elif major == PUBLIC_BASELINE_VERSION:
            version_status = "MATCH"
        elif major < PUBLIC_BASELINE_VERSION:
            version_status = "OLDER_MAJOR_VERSION"
        else:
            version_status = "NEWER_MAJOR_VERSION"

        if running_date is None:
            date_status = "UNKNOWN"
        elif running_date < SIMULATOR24_LATEST_PATCH_ENTRY:
            date_status = "OLDER_THAN_LATEST_VISIBLE_SIMULATOR24_PATCH"
        elif running_date == SIMULATOR24_LATEST_PATCH_ENTRY:
            if running_date < GENERAL_PUBLIC_BUILD_DATE:
                date_status = "MATCHES_PATCH_LOG_BUT_OLDER_THAN_GENERAL_BUILD"
            else:
                date_status = "MATCHES_LATEST_VISIBLE_SIMULATOR24_PATCH"
        elif running_date < GENERAL_PUBLIC_BUILD_DATE:
            date_status = "BETWEEN_SIMULATOR24_PATCH_LOG_AND_GENERAL_BUILD_DATE"
        elif running_date == GENERAL_PUBLIC_BUILD_DATE:
            date_status = "MATCHES_GENERAL_PUBLIC_BUILD_DATE"
        else:
            date_status = "NEWER_THAN_PRODUCT_PUBLIC_BASELINE"

        severity = "INFO"
        if version_status == "OLDER_MAJOR_VERSION" or date_status in {
            "OLDER_THAN_LATEST_VISIBLE_SIMULATOR24_PATCH",
            "MATCHES_PATCH_LOG_BUT_OLDER_THAN_GENERAL_BUILD",
        }:
            severity = "WARNING"
        elif version_status in {"UNKNOWN", "NEWER_MAJOR_VERSION"} or date_status in {
            "UNKNOWN", "NEWER_THAN_PRODUCT_PUBLIC_BASELINE"
        }:
            severity = "REVALIDATE"

        return {
            "running_version_tokens": tokens,
            "running_major_version": major,
            "running_build_date": running_date.isoformat() if running_date else None,
            "public_baseline": {
                "major_version": PUBLIC_BASELINE_VERSION,
                "general_software_page_latest_build_date": GENERAL_PUBLIC_BUILD_DATE.isoformat(),
                "simulator24_latest_visible_patch_entry": SIMULATOR24_LATEST_PATCH_ENTRY.isoformat(),
                "verified_as_of": BASELINE_VERIFIED_DATE.isoformat(),
            },
            "version_status": version_status,
            "build_status": date_status,
            "severity": severity,
            "product_real_machine_acceptance_validated": False,
            "note": (
                "The PowerWorld public general software page and Simulator 24 patch log currently expose different "
                "reference dates. Both are retained. This is provenance comparison only; it is not real-machine "
                "acceptance certification."
            ),
        }
