"""Unit + API tests for the toolpath audit service."""

from __future__ import annotations

import pytest

from app.audit import audit
from app.server import create_app


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def run(program, regions=None, initial=(0, 0, 0),
        ws_min=(-100, -100, -100), ws_max=(100, 100, 100)):
    return audit({
        "initial_position_mm": {"x": initial[0], "y": initial[1], "z": initial[2]},
        "workspace": {"min": {"x": ws_min[0], "y": ws_min[1], "z": ws_min[2]},
                      "max": {"x": ws_max[0], "y": ws_max[1], "z": ws_max[2]}},
        "forbidden_regions": regions or [],
        "program": program,
    })


def box(mn, mx):
    return {"bounds": {"min": {"x": mn[0], "y": mn[1], "z": mn[2]},
                       "max": {"x": mx[0], "y": mx[1], "z": mx[2]}}}


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------

def test_basic_absolute_mm_normalizes_segments():
    ok, err = run("G21 G90 G0 X10 Y20 Z0\nG1 X10 Y30 Z5")
    assert err is None
    assert ok["status"] == "accepted"
    assert ok["segments"] == [
        {"start": {"x": "0", "y": "0", "z": "0"},
         "end": {"x": "10", "y": "20", "z": "0"}, "motion": "G0", "line": 1},
        {"start": {"x": "10", "y": "20", "z": "0"},
         "end": {"x": "10", "y": "30", "z": "5"}, "motion": "G1", "line": 2},
    ]
    assert ok["final_position_mm"] == {"x": "10", "y": "30", "z": "5"}


def test_modal_settings_persist_and_new_line_setting_applies_first():
    # G21/G90/G0 established on line 1; line 3 switches to relative before
    # its coordinates are interpreted.
    program = "G21 G90 G0 X1 Y0\n;\nG91 X4"
    ok, err = run(program)
    assert err is None
    assert ok["final_position_mm"] == {"x": "5", "y": "0", "z": "0"}


def test_inch_relative_conversion_is_exact():
    # 1 inch = 25.4 mm exactly; 0.5 inch = 12.7; modal units persist.
    program = "G20 G91 G0 X1 Y0.5\nG1 X-0.25"
    ok, err = run(program)
    assert err is None
    assert ok["segments"][0]["end"] == {"x": "25.4", "y": "12.7", "z": "0"}
    assert ok["segments"][1]["start"] == {"x": "25.4", "y": "12.7", "z": "0"}
    assert ok["final_position_mm"] == {"x": "19.05", "y": "12.7", "z": "0"}


def test_canonical_decimal_inputs_and_outputs():
    ok, err = run("G21 G90 G0 X1.0 Y2.50 Z0")
    assert err is None
    assert ok["final_position_mm"] == {"x": "1", "y": "2.5", "z": "0"}


def test_empty_and_comment_lines_produce_no_motion():
    ok, err = run("\n   ; only a comment\nG21 G90 G0 X1\n\n")
    assert err is None
    assert len(ok["segments"]) == 1
    assert ok["segments"][0]["line"] == 3


def test_workspace_boundary_is_closed():
    ok, err = run("G21 G90 G0 X10 Y10 Z10", initial=(10, 10, 10),
                  ws_min=(10, 10, 10), ws_max=(10, 10, 10))
    assert err is None
    assert ok["final_position_mm"] == {"x": "10", "y": "10", "z": "10"}


def test_zero_length_move_produces_no_segment_but_updates_nothing():
    ok, err = run("G21 G90 G0 X0 Y0 Z0")
    assert err is None
    assert ok["segments"] == []
    assert ok["final_position_mm"] == {"x": "0", "y": "0", "z": "0"}


def test_unspecified_axes_keep_their_position():
    ok, err = run("G21 G91 G0 X5\nG1 Z-2")
    assert err is None
    assert ok["final_position_mm"] == {"x": "5", "y": "0", "z": "-2"}


# ---------------------------------------------------------------------------
# program errors, each pinned to the original line
# ---------------------------------------------------------------------------

def test_coordinates_before_motion_mode():
    ok, err = run("G21 G90 X5")
    assert ok is None
    assert err["error"] == "program_error"
    assert err["line"] == 1
    assert "motion mode" in err["reason"]


def test_coordinates_before_unit_mode():
    ok, err = run("G90 G0 X5")
    assert err["line"] == 1
    assert "unit mode" in err["reason"]


def test_coordinates_before_position_mode():
    ok, err = run("G21 G0 X5")
    assert err["line"] == 1
    assert "position mode" in err["reason"]


def test_illegal_word_reports_line():
    program = "G21 G90 G0 X1\nM30\nG1 X2"
    ok, err = run(program)
    assert ok is None
    assert err["line"] == 2
    assert err["error"] == "program_error"


def test_repeated_axis_reports_line():
    ok, err = run("G21 G90 G0 X1 X2")
    assert err["line"] == 1
    assert "repeat" in err["reason"].lower()


def test_conflicting_modal_words_on_one_line():
    ok, err = run("G21 G20 G90 G0 X1")
    assert err["line"] == 1
    assert "conflict" in err["reason"]
    ok, err = run("G21 G90 G91 G0 X1")
    assert err["line"] == 1
    ok, err = run("G21 G90 G0 G1 X1")
    assert err["line"] == 1


def test_non_canonical_and_non_finite_decimals():
    ok, err = run("G21 G90 G0 X0x1")
    assert err["line"] == 1
    ok, err = run("G21 G90 G0 XNaN")
    assert err["line"] == 1
    ok, err = run("G21 G90 G0 X1_0")
    assert err["line"] == 1
    ok, err = run("G21 G90 G0 X")
    assert err["line"] == 1


def test_error_line_refers_to_first_offending_line():
    program = "G21 G90 G0 X1\nG1 X2\nbogus line here"
    # parsing happens while executing; the first bad line is reported
    ok, err = run(program)
    assert err["line"] == 3


# ---------------------------------------------------------------------------
# workspace and forbidden-region enforcement
# ---------------------------------------------------------------------------

def test_endpoint_outside_workspace_rejected_without_partial_track():
    ok, err = run("G21 G90 G0 X1\nG1 X200")
    assert ok is None
    assert "segments" not in err
    assert err["error"] == "outside_workspace"
    assert err["line"] == 2


def test_diagonal_crossing_forbidden_box_is_detected():
    # Both endpoints are well inside the workspace, but the segment slants
    # through the box — checking endpoints only would miss this.
    region = box((4, 4, -1), (6, 6, 1))
    ok, err = run("G21 G90 G0 X0 Y0\nG1 X10 Y10", regions=[region])
    assert err is not None
    assert err["error"] == "forbidden_contact"
    assert err["line"] == 2
    assert err["forbidden_region"] == 1


def test_grazing_box_boundary_is_contact_and_rejected():
    region = box((4, 5, 0), (6, 6, 0))
    ok, err = run("G21 G90 G0 X0 Y5 Z0\nG1 X10 Y5 Z0", regions=[region])
    assert err is not None
    assert err["error"] == "forbidden_contact"
    assert err["forbidden_region"] == 1


def test_path_clear_of_box_is_accepted():
    region = box((4, 7, -1), (6, 9, 1))
    ok, err = run("G21 G90 G0 X0 Y0\nG1 X10 Y10", regions=[region])
    assert err is None and ok is not None


def test_segment_starting_inside_forbidden_box_rejected():
    region = box((-1, -1, -1), (1, 1, 1))
    ok, err = run("G21 G90 G0 X5 Y5", regions=[region])
    assert err is not None
    assert err["line"] == 1


def test_first_violation_and_region_number_are_stable():
    regions = [box((40, 40, -1), (60, 60, 1)),
               box((4, 4, -1), (6, 6, 1))]
    program = "G21 G90 G0 X0 Y0\nG1 X10 Y10\nG1 X50 Y50"
    ok, err = run(program, regions=regions)
    assert err["line"] == 2
    assert err["forbidden_region"] == 2


def test_relative_move_escaping_workspace_is_caught():
    ok, err = run("G21 G91 G0 X90\nG1 X20")
    assert err["error"] == "outside_workspace"
    assert err["line"] == 2


def test_degenerate_move_inside_forbidden_region_is_rejected():
    region = box((-1, -1, -1), (1, 1, 1))
    ok, err = run("G21 G90 G0 X0 Y0 Z0", regions=[region])
    assert err is not None
    assert err["error"] == "forbidden_contact"
    assert err["forbidden_region"] == 1


def test_earlier_geometry_violation_beats_later_lexical_error():
    region = box((4, 4, -1), (6, 6, 1))
    program = "G21 G90 G0 X0 Y0\nG1 X10 Y10\nM99"
    ok, err = run(program, regions=[region])
    assert ok is None
    assert err["line"] == 2
    assert err["error"] == "forbidden_contact"


def test_when_geometry_is_clear_later_lexical_error_is_reported():
    region = box((40, 40, -1), (60, 60, 1))
    program = "G21 G90 G0 X0 Y0\nG1 X10 Y10\nM99"
    ok, err = run(program, regions=[region])
    assert ok is None
    assert err["line"] == 3


def test_exponent_decimal_and_lowercase_words_and_crlf():
    ok, err = run("g21 g90 g0 x1e2\r\nG1 Y2.5e1")
    assert err is None
    assert ok["final_position_mm"] == {"x": "100", "y": "25", "z": "0"}
    assert ok["segments"][0]["line"] == 1
    assert ok["segments"][1]["line"] == 2


def test_endpoint_exactly_on_region_boundary_rejected():
    region = box((10, 0, -1), (20, 10, 1))
    ok, err = run("G21 G90 G0 X10 Y0 Z0", regions=[region])
    assert err is not None
    assert err["error"] == "forbidden_contact"
    assert err["forbidden_region"] == 1


def test_comment_after_words_is_ignored():
    ok, err = run("G21 G90 G0 X7 ; plunge comment\n; another")
    assert err is None
    assert ok["final_position_mm"] == {"x": "7", "y": "0", "z": "0"}
    assert len(ok["segments"]) == 1


def test_packed_words_without_spaces():
    ok, err = run("G21G90G1X1Y2Z3")
    assert err is None
    assert ok["final_position_mm"] == {"x": "1", "y": "2", "z": "3"}


def test_5000_lines_is_allowed():
    program = "\n".join(["G21 G91 G0 X0.001"] * 5000)
    ok, err = run(program, ws_max=(10, 10, 10))
    assert err is None
    assert ok["final_position_mm"]["x"] == "5"


def test_empty_program_yields_empty_track():
    ok, err = run("\n; nothing\n   ; here\n")
    assert err is None
    assert ok["segments"] == []
    assert ok["final_position_mm"] == {"x": "0", "y": "0", "z": "0"}


def test_extreme_exponent_is_rejected_on_its_line():
    ok, err = run("G21 G90 G0 X1e999999")
    assert err["line"] == 1
    assert err["error"] == "program_error"


def test_signed_and_dot_leading_decimals():
    ok, err = run("G21 G90 G0 X+2 Y-.5 Z.25")
    assert err is None
    assert ok["final_position_mm"] == {"x": "2", "y": "-0.5", "z": "0.25"}


# ---------------------------------------------------------------------------
# tool envelope (offset probe head) auditing
# ---------------------------------------------------------------------------

def run_envelope(program, env_min, env_max, regions=None, initial=(0, 0, 0),
                 ws_min=(-100, -100, -100), ws_max=(100, 100, 100)):
    return audit({
        "initial_position_mm": {"x": initial[0], "y": initial[1], "z": initial[2]},
        "workspace": {"min": {"x": ws_min[0], "y": ws_min[1], "z": ws_min[2]},
                      "max": {"x": ws_max[0], "y": ws_max[1], "z": ws_max[2]}},
        "forbidden_regions": regions or [],
        "program": program,
        "tool_envelope_mm": {
            "min": {"x": env_min[0], "y": env_min[1], "z": env_min[2]},
            "max": {"x": env_max[0], "y": env_max[1], "z": env_max[2]},
        },
    })


def envelope_payload(envelope):
    return {
        "initial_position_mm": {"x": 0, "y": 0, "z": 0},
        "workspace": {"min": {"x": -100, "y": -100, "z": -100},
                      "max": {"x": 100, "y": 100, "z": 100}},
        "forbidden_regions": [],
        "program": "G21 G90 G0 X1",
        "tool_envelope_mm": envelope,
    }


def test_envelope_main_flow_returns_reference_trajectory():
    # Asymmetric probe: reaches 2 below and 10 above the reference on Z, etc.
    ok, err = run_envelope("G21 G90 G0 X10 Y20\nG1 X30 Y40 Z5",
                           env_min=(-2, -3, -2), env_max=(4, 1, 10))
    assert err is None
    assert ok["status"] == "accepted"
    # Success still reports the controller reference trajectory in mm.
    assert ok["segments"] == [
        {"start": {"x": "0", "y": "0", "z": "0"},
         "end": {"x": "10", "y": "20", "z": "0"}, "motion": "G0", "line": 1},
        {"start": {"x": "10", "y": "20", "z": "0"},
         "end": {"x": "30", "y": "40", "z": "5"}, "motion": "G1", "line": 2},
    ]
    assert ok["final_position_mm"] == {"x": "30", "y": "40", "z": "5"}


def test_zero_envelope_matches_omitted_envelope_regression():
    program = "G21 G90 G0 X10 Y20\nG1 X-5 Y-8 Z3"
    ok_omitted, err_omitted = run(program)
    ok_zero, err_zero = run_envelope(program, env_min=(0, 0, 0),
                                     env_max=(0, 0, 0))
    assert err_omitted is None and err_zero is None
    assert ok_zero == ok_omitted


def test_omitted_envelope_keeps_legacy_verdict():
    # Reference path at y=18.5 clears the region; an envelope reaching +2 in
    # Y would graze it (see next test) — omitted field means no such check.
    region = box((20, 20, -1), (30, 30, 1))
    ok, err = run("G21 G90 G0 X0 Y18.5\nG1 X40 Y18.5", regions=[region])
    assert err is None
    assert ok["final_position_mm"] == {"x": "40", "y": "18.5", "z": "0"}


def test_envelope_grazing_forbidden_region_is_contact():
    region = box((20, 20, -1), (30, 30, 1))
    ok, err = run_envelope("G21 G90 G0 X0 Y18.5\nG1 X40 Y18.5",
                           env_min=(0, -1, 0), env_max=(0, 2, 0),
                           regions=[region])
    assert ok is None
    assert err["error"] == "forbidden_contact"
    assert err["line"] == 2
    assert err["forbidden_region"] == 1
    assert "segments" not in err and "final_position_mm" not in err


def test_envelope_swept_volume_mid_segment_contact():
    # Pure +Y offset probe; both endpoint probe boxes are clear of the
    # region — only the body swept midway along the segment touches it.
    region = box((8, 5, -1), (12, 7, 1))
    ok, err = run_envelope("G21 G90 G0 X0 Y3.5\nG1 X20 Y3.5",
                           env_min=(0, 0, 0), env_max=(0, 2, 0),
                           regions=[region])
    assert ok is None
    assert err["error"] == "forbidden_contact"
    assert err["line"] == 2
    assert err["forbidden_region"] == 1


def test_envelope_exiting_workspace_is_rejected():
    # Reference x=10 is inside [-100, 12]; the probe tip reaches x=15.
    ok, err = run_envelope("G21 G90 G0 X10",
                           env_min=(0, 0, 0), env_max=(5, 0, 0),
                           ws_max=(12, 100, 100))
    assert ok is None
    assert err["error"] == "outside_workspace"
    assert err["line"] == 1
    assert "segments" not in err


def test_envelope_exactly_filling_workspace_is_accepted():
    # Closed workspace: probe faces may lie exactly on the boundary.
    ok, err = run_envelope("G21 G90 G0 X7",
                           env_min=(-2, 0, 0), env_max=(5, 0, 0),
                           ws_min=(-2, -100, -100), ws_max=(12, 100, 100))
    assert err is None
    assert ok["final_position_mm"] == {"x": "7", "y": "0", "z": "0"}


def test_initial_envelope_outside_workspace_is_invalid_request():
    ok, err = run_envelope("G21 G90 G0 X1",
                           env_min=(0, 0, 0), env_max=(0, 0, 10),
                           ws_max=(100, 100, 5))
    assert ok is None
    assert err["error"] == "invalid_request"
    assert "line" not in err


def test_initial_envelope_touching_region_is_invalid_request():
    # Probe face at x=3 exactly meets the region boundary: contact counts.
    region = box((3, -1, -1), (5, 1, 1))
    ok, err = run_envelope("G21 G90 G0 X10",
                           env_min=(0, 0, 0), env_max=(3, 0, 0),
                           regions=[region])
    assert ok is None
    assert err["error"] == "invalid_request"
    assert "forbidden region 1" in err["reason"]


@pytest.mark.parametrize("env_min,env_max", [
    ((1, 0, 0), (2, 0, 0)),    # min > 0 on x
    ((0, 0, 0), (0, -1, 0)),   # max < 0 on y
    ((-3, 0, 0), (-1, 0, 0)),  # whole envelope below the reference
])
def test_envelope_must_straddle_reference_point(env_min, env_max):
    ok, err = run_envelope("G21 G90 G0 X1", env_min=env_min, env_max=env_max)
    assert ok is None
    assert err["error"] == "invalid_request"
    assert "min <= 0 <= max" in err["reason"]


def test_envelope_must_be_object_with_min_and_max():
    bad_payloads = (
        5, "box", [1, 2], None,
        {"min": {"x": 0, "y": 0, "z": 0}},
        {"max": {"x": 0, "y": 0, "z": 0}},
        {"min": {"x": 0, "y": 0}, "max": {"x": 0, "y": 0, "z": 0}},
    )
    for bad in bad_payloads:
        ok, err = audit(envelope_payload(bad))
        assert ok is None
        assert err["error"] == "invalid_request"


def test_envelope_rejects_non_canonical_decimals():
    for axis_value in ("NaN", "Infinity", "0.1.2", "1_0", "0x1"):
        bad = {"min": {"x": axis_value, "y": 0, "z": 0},
               "max": {"x": 0, "y": 0, "z": 0}}
        ok, err = audit(envelope_payload(bad))
        assert ok is None
        assert err["error"] == "invalid_request"


def test_envelope_reports_first_violating_line_and_region():
    regions = [box((50, 50, -1), (60, 60, 1)),
               box((10, 10, -1), (20, 20, 1))]
    # Probe reaches 5 past the reference in X; line 2 clips region 2 while
    # the reference segment itself stays clear of every region.
    program = "G21 G90 G0 X0 Y15\nG1 X8 Y15\nG1 X30 Y30"
    ok, err = run_envelope(program, env_min=(0, 0, 0), env_max=(5, 0, 0),
                           regions=regions)
    assert ok is None
    assert err["error"] == "forbidden_contact"
    assert err["line"] == 2
    assert err["forbidden_region"] == 2


def test_envelope_workspace_first_line_is_stable():
    ok, err = run_envelope("G21 G90 G0 X10\nG1 X20",
                           env_min=(0, 0, 0), env_max=(5, 0, 0),
                           ws_max=(12, 100, 100))
    assert ok is None
    assert err["error"] == "outside_workspace"
    assert err["line"] == 1


def test_envelope_applies_after_exact_inch_conversion():
    # 1 inch = 25.4 mm exactly; probe extends 30 mm past the reference in X.
    ok, err = run_envelope("G20 G90 G0 X1",
                           env_min=(0, 0, 0), env_max=(30, 0, 0),
                           ws_max=(60, 100, 100))
    assert err is None
    assert ok["final_position_mm"] == {"x": "25.4", "y": "0", "z": "0"}

    ok, err = run_envelope("G20 G90 G0 X1",
                           env_min=(0, 0, 0), env_max=(30, 0, 0),
                           ws_max=(50, 100, 100))
    assert ok is None
    assert err["error"] == "outside_workspace"
    assert err["line"] == 1


def test_envelope_with_relative_inch_moves_exact():
    ok, err = run_envelope("G20 G91 G0 X1\nG1 X-0.5",
                           env_min=(0, 0, 0), env_max=(10, 0, 0),
                           ws_max=(40, 100, 100))
    assert err is None
    assert ok["final_position_mm"] == {"x": "12.7", "y": "0", "z": "0"}


def test_envelope_zero_length_move_still_produces_no_segment():
    ok, err = run_envelope("G21 G90 G0 X0 Y0 Z0",
                           env_min=(-1, -1, -1), env_max=(2, 2, 2))
    assert err is None
    assert ok["segments"] == []
    assert ok["final_position_mm"] == {"x": "0", "y": "0", "z": "0"}


def test_envelope_violation_beats_later_program_error():
    ok, err = run_envelope("G21 G90 G0 X10\nM99",
                           env_min=(0, 0, 0), env_max=(5, 0, 0),
                           ws_max=(12, 100, 100))
    assert ok is None
    assert err["error"] == "outside_workspace"
    assert err["line"] == 1


def test_envelope_clear_geometry_yields_to_later_program_error():
    ok, err = run_envelope("G21 G90 G0 X5\nM99",
                           env_min=(0, 0, 0), env_max=(5, 0, 0),
                           ws_max=(12, 100, 100))
    assert ok is None
    assert err["error"] == "program_error"
    assert err["line"] == 2


# ---------------------------------------------------------------------------
# request validation
# ---------------------------------------------------------------------------

def test_too_many_regions():
    ok, err = run("G21 G90 G0 X1", regions=[box((0, 0, 0), (1, 1, 1))] * 21)
    assert ok is None
    assert err["error"] == "invalid_request"


def test_too_many_lines():
    program = "\n".join(["G21 G90 G0 X0"] * 5001)
    ok, err = run(program)
    assert err["error"] == "invalid_request"


def test_initial_position_outside_workspace():
    ok, err = run("G21 G90 G0 X1", initial=(500, 0, 0))
    assert err["error"] == "invalid_request"


def test_non_finite_json_number_rejected():
    payload = {
        "initial_position_mm": {"x": "NaN", "y": 0, "z": 0},
        "workspace": {"min": {"x": -10, "y": -10, "z": -10},
                      "max": {"x": 10, "y": 10, "z": 10}},
        "forbidden_regions": [],
        "program": "G21 G90 G0 X1",
    }
    ok, err = audit(payload)
    assert err["error"] == "invalid_request"


def test_failure_never_returns_segments():
    ok, err = run("G21 G90 G0 X5\nG1 X1000")
    assert ok is None
    assert "segments" not in err
    assert "final_position_mm" not in err


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    return create_app().test_client()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_audit_endpoint_success(client):
    resp = client.post("/api/toolpaths/audit", json={
        "initial_position_mm": {"x": 0, "y": 0, "z": 0},
        "workspace": {"min": {"x": 0, "y": 0, "z": 0},
                      "max": {"x": 100, "y": 100, "z": 100}},
        "forbidden_regions": [],
        "program": "G21 G90 G0 X1",
    })
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "accepted"


def test_audit_endpoint_program_error_status(client):
    resp = client.post("/api/toolpaths/audit", json={
        "initial_position_mm": {"x": 0, "y": 0, "z": 0},
        "workspace": {"min": {"x": 0, "y": 0, "z": 0},
                      "max": {"x": 100, "y": 100, "z": 100}},
        "forbidden_regions": [],
        "program": "X1",
    })
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["line"] == 1
    assert "segments" not in body


def test_audit_endpoint_malformed_json(client):
    resp = client.post("/api/toolpaths/audit", data="not json",
                       content_type="application/json")
    assert resp.status_code == 400


def test_audit_endpoint_envelope_accepted(client):
    resp = client.post("/api/toolpaths/audit", json={
        "initial_position_mm": {"x": 0, "y": 0, "z": 0},
        "workspace": {"min": {"x": -50, "y": -50, "z": -50},
                      "max": {"x": 100, "y": 100, "z": 100}},
        "forbidden_regions": [],
        "program": "G21 G90 G0 X10",
        "tool_envelope_mm": {"min": {"x": -1, "y": 0, "z": 0},
                             "max": {"x": 5, "y": 0, "z": 12}},
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "accepted"
    assert body["final_position_mm"] == {"x": "10", "y": "0", "z": "0"}


def test_audit_endpoint_envelope_initial_invalid_is_400(client):
    resp = client.post("/api/toolpaths/audit", json={
        "initial_position_mm": {"x": 0, "y": 0, "z": 0},
        "workspace": {"min": {"x": 0, "y": 0, "z": 0},
                      "max": {"x": 100, "y": 100, "z": 8}},
        "forbidden_regions": [],
        "program": "G21 G90 G0 X10",
        "tool_envelope_mm": {"min": {"x": 0, "y": 0, "z": 0},
                             "max": {"x": 0, "y": 0, "z": 12}},
    })
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_request"


def test_audit_endpoint_envelope_contact_is_422_without_partial_track(client):
    resp = client.post("/api/toolpaths/audit", json={
        "initial_position_mm": {"x": 0, "y": 0, "z": 0},
        "workspace": {"min": {"x": -50, "y": -50, "z": -50},
                      "max": {"x": 50, "y": 50, "z": 50}},
        "forbidden_regions": [
            {"bounds": {"min": {"x": 20, "y": 20, "z": -1},
                        "max": {"x": 30, "y": 30, "z": 1}}}
        ],
        "program": "G21 G90 G0 X0 Y18.5\nG1 X40 Y18.5",
        "tool_envelope_mm": {"min": {"x": 0, "y": -1, "z": 0},
                             "max": {"x": 0, "y": 2, "z": 0}},
    })
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"] == "forbidden_contact"
    assert body["line"] == 2
    assert body["forbidden_region"] == 1
    assert "segments" not in body and "final_position_mm" not in body
