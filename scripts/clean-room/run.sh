#!/usr/bin/env bash
# Clean-room driver (bd#100). Ships the COMMITTED tree only into a stock
# container and runs it the way an outsider would. Identical invocation in CI
# and on a laptop:
#
#   bash scripts/clean-room/run.sh                  # quickstart, python 3.11
#   bash scripts/clean-room/run.sh suite            # full suite, python 3.11
#   bash scripts/clean-room/run.sh quickstart 3.12  # pick the interpreter
#
# `git archive` (not `docker cp`, not a bind mount) is the point: untracked
# local files cannot leak into the run. See docs/clean-room.md.
set -euo pipefail

MODE="${1:-quickstart}"
PYVER="${2:-3.11}"
IMAGE="python:${PYVER}-slim"
REF="${CLEAN_ROOM_REF:-HEAD}"

case "$MODE" in quickstart|suite) ;; *)
  echo "usage: run.sh [quickstart|suite] [python-version]" >&2; exit 2 ;;
esac

command -v docker >/dev/null || {
  echo "FAIL: docker is not on PATH. The clean-room job needs a docker daemon." >&2
  exit 1
}
docker info >/dev/null 2>&1 || {
  echo "FAIL: docker is on PATH but the daemon is not reachable (is Docker running?)." >&2
  exit 1
}

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "clean-room: mode=$MODE image=$IMAGE ref=$REF ($(git rev-parse --short "$REF"))"

# The script itself travels inside the archive, so the container needs nothing
# mounted and nothing fetched — only the committed tree and the base image.
git archive "$REF" | docker run --rm -i \
  -e MODE="$MODE" \
  "$IMAGE" \
  bash -c 'mkdir -p /work && cd /work && tar -x && exec bash scripts/clean-room/in-container.sh "$MODE"'
