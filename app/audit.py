"""Request validation and audit orchestration for POST /api/toolpaths/audit."""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from .decimal_math import coerce_decimal, decimal_to_fraction
from .gcode import Move, ProgramError, execute, format_point
from .geometry import first_move_violation

MAX_REGIONS = 20
MAX_PROGRAM_LINES = 5000

PointT = tuple[Fraction, Fraction, Fraction]


class AuditError(ValueError):
    """Request-level validation failure (no source line applies)."""


def _point_from_payload(payload: Any, field: str) -> PointT:
    if not isinstance(payload, dict):
        raise AuditError(f"{field} must be an object with x, y, z")
    values: list[Fraction] = []
    for axis in ("x", "y", "z"):
        if axis not in payload:
            raise AuditError(f"{field}.{axis} is required")
        try:
            dec = coerce_decimal(payload[axis], f"{field}.{axis}")
        except ValueError as exc:
            raise AuditError(str(exc)) from None
        values.append(decimal_to_fraction(dec))
    return (values[0], values[1], values[2])


def _box_from_payload(payload: Any, index: int) -> tuple[PointT, PointT]:
    label = f"forbidden_regions[{index}]"
    if not isinstance(payload, dict):
        raise AuditError(f"{label} must be an object")
    bounds = payload.get("bounds")
    if not isinstance(bounds, dict):
        raise AuditError(f"{label} must contain a 'bounds' object")
    lo = _point_from_payload(bounds.get("min"), f"{label}.bounds.min")
    hi = _point_from_payload(bounds.get("max"), f"{label}.bounds.max")
    for axis_i in range(3):
        if lo[axis_i] > hi[axis_i]:
            raise AuditError(f"{label}: min must not exceed max on any axis")
    return lo, hi


def _workspace_from_payload(payload: Any) -> tuple[PointT, PointT]:
    if not isinstance(payload, dict):
        raise AuditError("workspace must be an object with min and max")
    lo = _point_from_payload(payload.get("min"), "workspace.min")
    hi = _point_from_payload(payload.get("max"), "workspace.max")
    for axis_i in range(3):
        if lo[axis_i] > hi[axis_i]:
            raise AuditError("workspace: min must not exceed max on any axis")
    return lo, hi


def _validate_request(data: Any) -> tuple[
    PointT,
    tuple[PointT, PointT],
    list[tuple[PointT, PointT]],
    str,
]:
    if not isinstance(data, dict):
        raise AuditError("request body must be a JSON object")

    initial_mm = _point_from_payload(data.get("initial_position_mm"),
                                     "initial_position_mm")

    if "workspace" not in data:
        raise AuditError("workspace is required")
    workspace = _workspace_from_payload(data["workspace"])

    ws_lo, ws_hi = workspace
    for axis_i in range(3):
        if not (ws_lo[axis_i] <= initial_mm[axis_i] <= ws_hi[axis_i]):
            raise AuditError("initial_position_mm must lie within the closed workspace")

    raw_regions = data.get("forbidden_regions", [])
    if not isinstance(raw_regions, list):
        raise AuditError("forbidden_regions must be a list")
    if len(raw_regions) > MAX_REGIONS:
        raise AuditError(f"at most {MAX_REGIONS} forbidden regions are allowed")
    regions = [
        _box_from_payload(item, index)
        for index, item in enumerate(raw_regions)
    ]

    if "program" not in data or not isinstance(data["program"], str):
        raise AuditError("program must be a string")
    program = data["program"]
    if len(program.splitlines()) > MAX_PROGRAM_LINES:
        raise AuditError(f"program may contain at most {MAX_PROGRAM_LINES} lines")

    return initial_mm, workspace, regions, program


def _serialise_segment(move: Move) -> dict:
    return {
        "start": format_point(move.start),
        "end": format_point(move.end),
        "motion": move.feed,
        "line": move.line_number,
    }


def _is_degenerate(move: Move) -> bool:
    return move.start == move.end


def audit(data: Any) -> tuple[dict | None, dict | None]:
    """Run a full audit.

    Returns ``(ok_payload, error_payload)`` where exactly one is non-None.
    On any failure no dispatchable partial toolpath is ever returned.
    """
    try:
        initial_mm, workspace, regions, program_text = _validate_request(data)
    except AuditError as exc:
        return None, {"error": "invalid_request", "reason": str(exc)}

    try:
        moves, final_point = execute(program_text, initial_mm)
    except ProgramError as exc:
        # An earlier line may already violate the geometry; the first
        # violation in program order wins over a later lexical error.
        violation = first_move_violation(exc.partial_moves, workspace, regions)
        if violation is not None:
            error = {"error": violation["code"], "line": violation["line"],
                     "reason": violation["reason"]}
            if "region" in violation:
                error["forbidden_region"] = violation["region"]
            return None, error
        return None, {
            "error": "program_error",
            "line": exc.line_number,
            "reason": exc.reason,
        }

    violation = first_move_violation(moves, workspace, regions)
    if violation is not None:
        error = {"error": violation["code"], "line": violation["line"],
                 "reason": violation["reason"]}
        if "region" in violation:
            error["forbidden_region"] = violation["region"]
        return None, error

    return {
        "status": "accepted",
        "segments": [
            _serialise_segment(move) for move in moves if not _is_degenerate(move)
        ],
        "final_position_mm": format_point(final_point),
    }, None
