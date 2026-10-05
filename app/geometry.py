"""Exact segment / axis-aligned-box intersection tests.

Every quantity is a :class:`fractions.Fraction`; no floating point is used,
so a segment that merely grazes a box face or passes exactly through an
edge/corner is detected reliably.

A forbidden region is a *closed* cuboid: contact with its interior *or*
boundary rejects the move.  The segment is closed as well (both endpoints
participate).

When a tool envelope is supplied, the audited body is the envelope box
swept along the reference segment (their Minkowski sum).  Containment in
the convex workspace is decided by the two endpoint boxes alone; contact
with a forbidden box is decided by testing the reference segment against
the region grown by the negated envelope.  Both reductions are exact.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Iterable, Optional, Sequence

from .gcode import Move

Point3 = tuple[Fraction, Fraction, Fraction]
# Tool envelope relative to the reference point: (lo, hi) per axis with
# lo <= 0 <= hi, so the reference point always lies inside its own envelope.
Envelope = tuple[Point3, Point3]


def segment_intersects_box(
    start: Point3,
    end: Point3,
    lo: Point3,
    hi: Point3,
) -> bool:
    """Return True if closed segment [start, end] meets closed box [lo, hi].

    Slab (Liang-Yu) clipping with rational arithmetic.  The candidate
    interval ``t_enter <= t <= t_exit`` (initially ``[0, 1]``) is intersected
    with each axis slab; touching a slab boundary keeps the interval because
    equality is allowed everywhere.
    """
    t_enter = Fraction(0)
    t_exit = Fraction(1)

    for i in range(3):
        a = start[i]
        b = end[i]
        delta = b - a
        slab_lo = lo[i]
        slab_hi = hi[i]

        if delta == 0:
            # Parallel to the slab: the constant coordinate must lie inside
            # the closed slab range.
            if a < slab_lo or a > slab_hi:
                return False
            continue

        # Parameter values where the line meets the two slab planes.
        t1 = (slab_lo - a) / delta
        t2 = (slab_hi - a) / delta
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > t_enter:
            t_enter = t1
        if t2 < t_exit:
            t_exit = t2
        if t_enter > t_exit:
            return False

    return True


def point_in_closed_box(point: Point3, lo: Point3, hi: Point3) -> bool:
    return all(lo[i] <= point[i] <= hi[i] for i in range(3))


def point_in_closed_workspace(point: Point3, lo: Point3, hi: Point3) -> bool:
    return point_in_closed_box(point, lo, hi)


def boxes_intersect_closed(
    a_lo: Point3,
    a_hi: Point3,
    b_lo: Point3,
    b_hi: Point3,
) -> bool:
    """Return True if two closed boxes share any point (touching counts)."""
    return all(a_lo[i] <= b_hi[i] and b_lo[i] <= a_hi[i] for i in range(3))


def envelope_box_within_workspace(
    point: Point3,
    env_lo: Point3,
    env_hi: Point3,
    ws_lo: Point3,
    ws_hi: Point3,
) -> bool:
    """Return True if the whole envelope box placed at ``point`` is inside."""
    return all(
        ws_lo[i] <= point[i] + env_lo[i] and point[i] + env_hi[i] <= ws_hi[i]
        for i in range(3)
    )


def grow_box_by_envelope(
    lo: Point3,
    hi: Point3,
    env_lo: Point3,
    env_hi: Point3,
) -> tuple[Point3, Point3]:
    """Minkowski sum of box [lo, hi] with the negated envelope.

    The swept envelope along a segment meets the original box iff the
    reference segment meets this grown box.  Because env_lo <= 0 <= env_hi
    the grown box is always valid (grown_lo <= grown_hi).
    """
    grown_lo = tuple(lo[i] - env_hi[i] for i in range(3))
    grown_hi = tuple(hi[i] - env_lo[i] for i in range(3))
    return grown_lo, grown_hi


def first_move_violation(
    moves: Sequence[Move],
    workspace: tuple[Point3, Point3],
    forbidden: Iterable[tuple[Point3, Point3]],
    envelope: Optional[Envelope] = None,
) -> dict | None:
    """Find the first offending move, in program order.

    For a given move the workspace is checked first, then forbidden regions
    in their submitted (1-based) order.  Returns a structured error dict or
    ``None`` when everything is clear.

    With ``envelope=None`` the reference segment itself is audited (legacy
    behaviour).  Otherwise the whole envelope swept along the segment is
    adjudicated: the workspace is convex, so containment of the swept
    volume is equivalent to containment of the two endpoint boxes; contact
    with a forbidden region is tested against the region grown by the
    negated envelope.
    """
    ws_lo, ws_hi = workspace
    regions = list(forbidden)

    if envelope is None:
        region_boxes = regions
    else:
        env_lo, env_hi = envelope
        region_boxes = [
            grow_box_by_envelope(box_lo, box_hi, env_lo, env_hi)
            for box_lo, box_hi in regions
        ]

    for move in moves:
        for which, point in (("start", move.start), ("end", move.end)):
            if envelope is None:
                inside = point_in_closed_workspace(point, ws_lo, ws_hi)
            else:
                inside = envelope_box_within_workspace(
                    point, env_lo, env_hi, ws_lo, ws_hi
                )
            if not inside:
                reason = (
                    f"segment {which} point lies outside the closed workspace"
                    if envelope is None
                    else f"tool envelope at segment {which} point lies "
                         "outside the closed workspace"
                )
                return {
                    "line": move.line_number,
                    "reason": reason,
                    "code": "outside_workspace",
                }

        for index, (box_lo, box_hi) in enumerate(region_boxes, start=1):
            if segment_intersects_box(move.start, move.end, box_lo, box_hi):
                reason = (
                    f"segment contacts the interior or boundary of "
                    f"forbidden region {index}"
                    if envelope is None
                    else f"swept tool envelope contacts the interior or "
                         f"boundary of forbidden region {index}"
                )
                return {
                    "line": move.line_number,
                    "reason": reason,
                    "code": "forbidden_contact",
                    "region": index,
                }
    return None
