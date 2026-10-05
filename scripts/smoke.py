"""HTTP smoke tests against the running API.

Exercises the health endpoint and the audit endpoint end-to-end, including
a program with inch-unit (G20) relative (G91) moves and a diagonal segment
that crosses a forbidden cuboid although both endpoints are clear.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8080").rstrip("/")


def http(method: str, path: str, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def check(name: str, condition: bool, detail="") -> None:
    if not condition:
        print(f"SMOKE FAIL: {name} {detail}")
        sys.exit(1)
    print(f"  ok: {name}")


def main() -> None:
    print(f"smoke against {BASE_URL}")

    status, body = http("GET", "/health")
    check("health returns 200 ok", status == 200 and body.get("status") == "ok")

    base_request = {
        "initial_position_mm": {"x": 0, "y": 0, "z": 0},
        "workspace": {
            "min": {"x": -50, "y": -50, "z": -50},
            "max": {"x": 50, "y": 50, "z": 50},
        },
        "forbidden_regions": [
            {"bounds": {"min": {"x": 20, "y": 20, "z": -1},
                        "max": {"x": 30, "y": 30, "z": 1}}}
        ],
    }

    # Inch-relative moves: +1 inch on X (25.4 mm exact), then -0.5 inch on X
    # and +0.25 inch on Y. Final = (12.7, 6.35, 0) mm.
    program = "G20 G91 G0 X1\nG1 X-0.5 Y0.25"
    request = dict(base_request, program=program)
    status, body = http("POST", "/api/toolpaths/audit", request)
    check("inch-relative program accepted", status == 200, body)
    check(
        "inch conversion is exact decimal",
        body["final_position_mm"] == {"x": "12.7", "y": "6.35", "z": "0"},
        body.get("final_position_mm"),
    )
    check(
        "segments are normalized millimetres",
        body["segments"][0]["end"]["x"] == "25.4"
        and body["segments"][0]["motion"] == "G0"
        and body["segments"][1]["motion"] == "G1",
        body.get("segments"),
    )

    # Diagonal segment: endpoints (0,0) and (40,40) are clear of the box
    # [20,20]-[30,30] in xy, but the line y=x runs straight through it.
    request = dict(base_request, program="G21 G90 G0 X0 Y0\nG1 X40 Y40")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check(
        "diagonal forbidden-region crossing is rejected",
        status == 422
        and body.get("error") == "forbidden_contact"
        and body.get("line") == 2
        and body.get("forbidden_region") == 1,
        body,
    )
    check("rejection exposes no partial toolpath",
          "segments" not in body and "final_position_mm" not in body, body)

    # Coordinates before a motion mode is established.
    request = dict(base_request, program="G21 G90 X1")
    status, body = http("POST", "/api/toolpaths/audit", request)
    check(
        "motion-before-mode error is pinned to line 1",
        status == 422 and body.get("line") == 1,
        body,
    )

    print("smoke OK")


if __name__ == "__main__":
    main()
