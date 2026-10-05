"""G-code parsing and execution for the toolpath audit.

Accepted grammar (case-insensitive words, parameters are canonical
decimals)::

    program      := { line }
    line         := { word } [ ";" comment ]   # also empty / comment-only
    word         := unit | mode | motion | axis
    unit         := "G20" | "G21"
    mode         := "G90" | "G91"
    motion       := "G0"  | "G1"
    axis         := ( "X" | "Y" | "Z" ) canonical-decimal

Semantics:

* Modal settings persist across lines.
* Settings appearing on a line apply before that line's coordinates.
* Conflicting modal words on the same line (G20+G21, G90+G91, G0+G1,
  also G0/G1 mixed with each other) are an error.
* Axis words may not repeat on one line; their values are in the unit in
  effect *for that line* (G20 on the line already applies).
* Coordinates without any motion mode established are an error.
* A line that only sets modal state produces no movement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

from .decimal_math import canonical_str, decimal_to_fraction, parse_decimal_text

_UNIT_WORDS = {"G20", "G21"}
_POSITION_WORDS = {"G90", "G91"}
_MOTION_WORDS = {"G0", "G1"}
_AXES = ("X", "Y", "Z")
_MM_PER_INCH = Fraction(127, 5)  # 25.4 exactly


class ProgramError(ValueError):
    """A program error pinned to an original source line (1-based)."""

    def __init__(self, line_number: int, reason: str):
        super().__init__(f"line {line_number}: {reason}")
        self.line_number = line_number
        self.reason = reason
        # Segments produced by earlier, already-valid lines; lets the caller
        # still audit geometry preceding a late parse/modal failure.
        self.partial_moves: list[Move] = []


_TOKEN_RE = re.compile(
    r"[A-Za-z][+-]?(?:\d+(?:\.\d*)?|\.\d+)?(?:[eE][+-]?\d+)?"
)


@dataclass(frozen=True)
class Move:
    """One normalized straight segment in millimetres."""

    start: tuple[Fraction, Fraction, Fraction]
    end: tuple[Fraction, Fraction, Fraction]
    line_number: int
    feed: str  # "G0" or "G1"


def _scan_words(body: str, line_number: int) -> list[str]:
    """Split a whitespace-separated line into raw word tokens.

    A chunk may pack several words (``G1X1Y2``); we walk it letter by
    letter so stray characters get reported on this exact source line.
    """
    tokens: list[str] = []
    for raw in body.split():
        index = 0
        while index < len(raw):
            match = _TOKEN_RE.match(raw, index)
            if match is None or not raw[index].isalpha():
                raise ProgramError(
                    line_number, f"illegal word or character {raw[index:]!r}"
                )
            tokens.append(match.group(0))
            index = match.end()
    return tokens


def _word_kind(word: str) -> str:
    letter = word[0].upper()
    number = word[1:].upper()
    if letter == "G":
        if number in ("20", "21"):
            return f"G{number}"
        if number in ("90", "91"):
            return f"G{number}"
        if number in ("0", "1"):
            return f"G{number}"
        return "UNSUPPORTED"
    if letter in _AXES:
        return letter
    return "UNSUPPORTED"


def _parse_line(line_number: int, raw_line: str) -> tuple[
    Optional[str],
    Optional[str],
    Optional[str],
    dict[str, Fraction],
]:
    """Return (unit, position_mode, motion, axes) for one line."""
    comment_pos = raw_line.find(";")
    body = raw_line if comment_pos == -1 else raw_line[:comment_pos]

    tokens = _scan_words(body, line_number)

    unit: Optional[str] = None
    position: Optional[str] = None
    motion: Optional[str] = None
    axes: dict[str, Fraction] = {}

    for token in tokens:
        letter = token[0].upper()
        rest = token[1:]
        kind = _word_kind(token)

        if letter == "G":
            if kind == "UNSUPPORTED":
                raise ProgramError(
                    line_number, f"unsupported or illegal G-code word {token!r}"
                )
            if kind in _UNIT_WORDS:
                if unit is not None and unit != kind:
                    raise ProgramError(
                        line_number, f"conflicting unit modes {unit} and {kind}"
                    )
                if unit is None:
                    unit = kind
            elif kind in _POSITION_WORDS:
                if position is not None and position != kind:
                    raise ProgramError(
                        line_number,
                        f"conflicting position modes {position} and {kind}",
                    )
                if position is None:
                    position = kind
            else:  # motion G0/G1
                if motion is not None and motion != kind:
                    raise ProgramError(
                        line_number, f"conflicting motion modes {motion} and {kind}"
                    )
                if motion is None:
                    motion = kind
        else:
            if kind == "UNSUPPORTED":
                raise ProgramError(line_number, f"illegal word {token!r}")
            value_text = rest
            if value_text == "":
                raise ProgramError(
                    line_number, f"axis word {letter} is missing a decimal value"
                )
            try:
                value = decimal_to_fraction(parse_decimal_text(value_text))
            except ValueError:
                raise ProgramError(
                    line_number,
                    f"axis {letter} has non-canonical or non-finite decimal {value_text!r}",
                ) from None
            if letter in axes:
                raise ProgramError(
                    line_number, f"axis {letter} is repeated on the same line"
                )
            axes[letter] = value

    return unit, position, motion, axes


def execute(
    program_text: str,
    initial_mm: tuple[Fraction, Fraction, Fraction],
) -> tuple[list[Move], tuple[Fraction, Fraction, Fraction]]:
    """Interpret the program and return (normalized mm segments, final mm point)."""
    unit: Optional[str] = None  # "G20" inch, "G21" mm
    position: Optional[str] = None  # "G90" absolute, "G91" relative
    motion: Optional[str] = None  # "G0" rapid, "G1" feed

    point = tuple(initial_mm)
    moves: list[Move] = []

    for line_number, raw_line in enumerate(program_text.splitlines(), start=1):
        try:
            line_unit, line_position, line_motion, axes = _parse_line(
                line_number, raw_line
            )

            # New settings on the line take effect before its coordinates.
            if line_unit is not None:
                unit = line_unit
            if line_position is not None:
                position = line_position
            if line_motion is not None:
                motion = line_motion

            if not axes:
                continue  # pure modal line: no movement

            if motion is None:
                raise ProgramError(
                    line_number,
                    "coordinates given before any motion mode (G0/G1) was established",
                )
            # unit/position are necessarily set too: axes require a numeric
            # value, but the modes themselves may legitimately remain unset and
            # must be reported distinctly.
            if unit is None:
                raise ProgramError(
                    line_number,
                    "coordinates given before any unit mode (G20/G21) was established",
                )
            if position is None:
                raise ProgramError(
                    line_number,
                    "coordinates given before any position mode (G90/G91) was established",
                )

            scale = _MM_PER_INCH if unit == "G20" else Fraction(1)
            target = list(point)
            for axis_index, axis_name in enumerate(_AXES):
                if axis_name not in axes:
                    continue
                value_mm = axes[axis_name] * scale
                if position == "G90":
                    target[axis_index] = value_mm
                else:
                    target[axis_index] = point[axis_index] + value_mm
            target_tuple = (target[0], target[1], target[2])

            # Even a degenerate (zero-length) motion is emitted: its endpoint
            # still has to clear the workspace and every forbidden region.
            moves.append(Move(point, target_tuple, line_number, motion))
            point = target_tuple
        except ProgramError as exc:
            exc.partial_moves = list(moves)
            raise

    return moves, point


def format_point(point: tuple[Fraction, Fraction, Fraction]) -> dict[str, str]:
    return {axis.lower(): canonical_str(value)
            for axis, value in zip(_AXES, point)}
