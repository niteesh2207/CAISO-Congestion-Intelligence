from __future__ import annotations
import sys
from typing import Any
from .base import PowerWorldAdapter


class SimAutoAdapter(PowerWorldAdapter):
    def __init__(self) -> None:
        self.sim = None
        self.pythoncom = None

    @property
    def solver_backed(self) -> bool:
        return True

    def connect(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("PowerWorld SimAuto requires Microsoft Windows.")
        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:
            raise RuntimeError("Install pywin32: pip install pywin32") from exc
        self.pythoncom = pythoncom
        self.sim = win32com.client.Dispatch("pwrworld.SimulatorAuto")

    def _require(self):
        if self.sim is None:
            raise RuntimeError("SimAuto is not connected.")
        return self.sim

    @staticmethod
    def _error(output: Any) -> str:
        try:
            return str(output[0] or "")
        except Exception:
            return ""

    def _check(self, output: Any, action: str) -> Any:
        err = self._error(output)
        if err:
            raise RuntimeError(f"{action} failed: {err}")
        return output

    def program_information(self) -> dict[str, Any]:
        raw = self._require().ProgramInformation
        result: dict[str, Any] = {}
        for row in raw:
            vals = list(row)
            if vals:
                result[str(vals[0]).lower()] = vals[1:]
        return result

    def open_case(self, path: str) -> None:
        self._check(self._require().OpenCase(str(path)), "OpenCase")

    def close_case(self) -> None:
        self._check(self._require().CloseCase(), "CloseCase")

    def save_state(self) -> None:
        self._check(self._require().SaveState(), "SaveState")

    def load_state(self) -> None:
        self._check(self._require().LoadState(), "LoadState")

    def run_script(self, command: str) -> str:
        sim = self._require()

        # PowerWorld documents RunScriptCommand2 as the more usable interface:
        # Boolean success + status/error message. Prefer it when the running
        # type library exposes it, while retaining RunScriptCommand fallback.
        if hasattr(sim, "RunScriptCommand2"):
            try:
                out = sim.RunScriptCommand2(command)
                if isinstance(out, (tuple, list)) and len(out) >= 2:
                    success = bool(out[0])
                    status = str(out[1] or "")
                else:
                    success = bool(out)
                    status = ""
                if not success:
                    raise RuntimeError(
                        f"RunScriptCommand2 failed: {status or 'no status message returned'}"
                    )
                return status
            except TypeError:
                # Some COM bindings may expose the out parameter differently.
                # Fall through to the legacy function rather than guessing.
                pass

        out = self._check(sim.RunScriptCommand(command), "RunScriptCommand")
        return self._error(out)

    def get_field_list(self, object_type: str) -> list[dict[str, Any]]:
        out = self._check(self._require().GetFieldList(object_type), "GetFieldList")
        data = out[1]
        rows = []
        for row in data:
            vals = list(row)
            rows.append({
                "key_marker": vals[0] if len(vals) > 0 else "",
                "variable": vals[1] if len(vals) > 1 else "",
                "data_type": vals[2] if len(vals) > 2 else "",
                "description": vals[3] if len(vals) > 3 else "",
            })
        return rows

    def get_rows(self, object_type: str, fields: list[str], filter_text: str = "") -> list[dict[str, Any]]:
        sim = self._require()

        # Version 24 typed retrieval is preferred. If COM marshaling differs
        # on a user's machine/build, fall back to the established rectangular API.
        if hasattr(sim, "GetParamsRectTyped") and self.pythoncom is not None:
            try:
                out = self._check(
                    sim.GetParamsRectTyped(object_type, fields, filter_text, self.pythoncom.VT_VARIANT),
                    "GetParamsRectTyped",
                )
                matrix = out[1]
                return [dict(zip(fields, list(row))) for row in matrix]
            except Exception:
                pass

        out = self._check(
            sim.GetParametersMultipleElementRect(object_type, fields, filter_text),
            "GetParametersMultipleElementRect",
        )
        matrix = out[1]
        return [dict(zip(fields, list(row))) for row in matrix]

    def change_single(self, object_type: str, fields: list[str], values: list[Any]) -> None:
        if len(fields) != len(values):
            raise ValueError("fields and values must have equal length")
        self._check(
            self._require().ChangeParametersSingleElement(object_type, fields, values),
            "ChangeParametersSingleElement",
        )
