"""Exact segment / axis-aligned-box intersection tests.

Every quantity is a :class:`fractions.Fraction`; no floating point is used,
so a segment that merely grazes a box face or passes exactly through an
edge/corner is detected reliably.

A forbidden region is a *closed* cuboid: contact with its interior *or*
boundary rejects the move.  The segment is closed as well (both endpoints
participate).
"""

from __future__ import annotations

from fractions import Fraction
from typing import Iterable, Sequence

from .gcode import Move

Point3 = tuple[Fraction, Fraction, Fraction]


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


def boxes_touch_closed(
    a_lo: Point3,
    a_hi: Point3,
    b_lo: Point3,
    b_hi: Point3,
) -> bool:
    """Return True if two closed boxes share at least one point."""
    return all(a_lo[i] <= b_hi[i] and b_lo[i] <= a_hi[i] for i in range(3))


def erode_workspace(
    workspace: tuple[Point3, Point3],
    env_lo: Point3,
    env_hi: Point3,
) -> tuple[Point3, Point3]:
    """Erode the closed workspace by the tool-envelope offsets.

    A probe occupying ``[env_lo, env_hi]`` around the reference point keeps
    its whole body inside the workspace iff the reference point stays inside
    this eroded box (Minkowski erosion).  The eroded box is convex, so the
    endpoint check in :func:`first_move_violation` already covers every
    point of a segment — and therefore the entire swept probe volume.
    """
    ws_lo, ws_hi = workspace
    return (
        (ws_lo[0] - env_lo[0], ws_lo[1] - env_lo[1], ws_lo[2] - env_lo[2]),
        (ws_hi[0] - env_hi[0], ws_hi[1] - env_hi[1], ws_hi[2] - env_hi[2]),
    )


def dilate_region(
    region: tuple[Point3, Point3],
    env_lo: Point3,
    env_hi: Point3,
) -> tuple[Point3, Point3]:
    """Dilate a forbidden region by the reflected envelope (Minkowski sum).

    The probe box swept along a segment touches the region's interior or
    boundary iff the reference segment meets this dilated closed box, so the
    existing exact segment/box test adjudicates the whole swept volume.
    """
    reg_lo, reg_hi = region
    return (
        (reg_lo[0] - env_hi[0], reg_lo[1] - env_hi[1], reg_lo[2] - env_hi[2]),
        (reg_hi[0] - env_lo[0], reg_hi[1] - env_lo[1], reg_hi[2] - env_lo[2]),
    )


def first_move_violation(
    moves: Sequence[Move],
    workspace: tuple[Point3, Point3],
    forbidden: Iterable[tuple[Point3, Point3]],
) -> dict | None:
    """Find the first offending move, in program order.

    For a given move the workspace is checked first, then forbidden regions
    in their submitted (1-based) order.  Returns a structured error dict or
    ``None`` when everything is clear.
    """
    ws_lo, ws_hi = workspace
    regions = list(forbidden)

    for move in moves:
        for which, point in (("start", move.start), ("end", move.end)):
            if not point_in_closed_workspace(point, ws_lo, ws_hi):
                return {
                    "line": move.line_number,
                    "reason": f"segment {which} point lies outside the closed workspace",
                    "code": "outside_workspace",
                }

        for index, (box_lo, box_hi) in enumerate(regions, start=1):
            if segment_intersects_box(move.start, move.end, box_lo, box_hi):
                return {
                    "line": move.line_number,
                    "reason": (
                        f"segment contacts the interior or boundary of "
                        f"forbidden region {index}"
                    ),
                    "code": "forbidden_contact",
                    "region": index,
                }
    return None
