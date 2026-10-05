#!/bin/sh
# One-shot verification pipeline run by the `verify` compose service.
# The service only starts after `api` is healthy, and every failure is
# reported through this script's exit code.
set -eu

echo "== [1/3] byte-compile / build check =="
python -m compileall -q app

echo "== [2/3] unit + API tests =="
python -m pytest -q tests

echo "== [3/3] API smoke tests (inch relative move + tool envelope included) =="
python scripts/smoke.py

echo "== verify OK =="
