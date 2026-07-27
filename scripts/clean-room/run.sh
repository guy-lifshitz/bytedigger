#!/usr/bin/env bash
# Clean-room driver (bd#100). Ships the COMMITTED tree only into a stock
# container and runs it the way an outsider would. Identical invocation in CI
# and on a laptop:
#
#   bash scripts/clean-room/run.sh                        # quickstart, py3.11
#   bash scripts/clean-room/run.sh suite                  # full suite
#   bash scripts/clean-room/run.sh expect-fail:bd102      # debt watch
#   bash scripts/clean-room/run.sh expect-fail:py39 3.9   # debt watch
#   CLEAN_ROOM_REF=origin/main bash scripts/clean-room/run.sh
#
# `git archive` (not `docker cp`, not a bind mount) is the point: untracked
# local files cannot leak into the run. NOTE THE COROLLARY — this tests your
# last COMMIT, never your working copy. Uncommitted fixes are invisible here.
set -euo pipefail

MODE="${1:-quickstart}"
PYVER="${2:-3.11}"
IMAGE="python:${PYVER}-slim"
REF="${CLEAN_ROOM_REF:-HEAD}"

case "$MODE" in
  quickstart|suite|expect-fail:bd102|expect-fail:py39) ;;
  *) echo "usage: run.sh [quickstart|suite|expect-fail:bd102|expect-fail:py39] [python-version]" >&2; exit 2 ;;
esac

command -v docker >/dev/null || {
  echo "FAIL: docker is not on PATH. The clean-room job needs a docker daemon." >&2; exit 1; }

# Resolve the daemon explicitly rather than trusting `docker context`. A CI
# runner started as a launchd/systemd service does not inherit the interactive
# shell's HOME or docker context, so the CLI silently falls back to the
# `default` context (/var/run/docker.sock) and reports the daemon as down even
# though it is running. Try the ambient config first, then known socket paths.
if ! docker info >/dev/null 2>&1; then
  HOME_DIR="${HOME:-$(eval echo "~$(whoami)")}"
  # Endpoints the CLI itself knows about (covers non-default colima profiles and
  # Docker Desktop variants), then the well-known socket paths.
  ctx_endpoints="$(docker context ls --format '{{.DockerEndpoint}}' 2>/dev/null | sed 's|^unix://||' || true)"
  for sock in \
      $ctx_endpoints \
      "$HOME_DIR/.colima/default/docker.sock" \
      "$HOME_DIR/.docker/run/docker.sock" \
      /var/run/docker.sock; do
    [ -S "$sock" ] || continue
    if DOCKER_HOST="unix://$sock" docker info >/dev/null 2>&1; then
      export DOCKER_HOST="unix://$sock"
      echo "clean-room: resolved docker daemon at $DOCKER_HOST"
      break
    fi
  done
fi
docker info >/dev/null 2>&1 || {
  echo "FAIL: docker is on PATH but no reachable daemon was found." >&2
  echo "      Tried the ambient docker context and the known socket paths." >&2
  echo "      Start the daemon, or set DOCKER_HOST explicitly." >&2
  exit 1; }

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

if [ -n "$(git status --porcelain)" ] && [ "$REF" = "HEAD" ]; then
  echo "WARNING: working tree is dirty. The clean room ships $(git rev-parse --short HEAD)," >&2
  echo "         so your uncommitted changes are NOT under test." >&2
fi

# Computed outside, asserted inside: the container tree must contain exactly
# the tracked files of the ref, no more and no fewer.
EXPECT_FILES="$(git ls-tree -r --name-only "$REF" | wc -l | tr -d ' ')"

echo "clean-room: mode=$MODE image=$IMAGE ref=$REF ($(git rev-parse --short "$REF")) files=$EXPECT_FILES"

# The scripts travel inside the archive, so the container needs nothing mounted
# and nothing fetched but the base image itself. No bind mount, no docker
# socket, no writable host path.
git archive "$REF" | docker run --rm -i \
  -e MODE="$MODE" \
  -e EXPECT_FILES="$EXPECT_FILES" \
  "$IMAGE" \
  bash -c 'mkdir -p /work && cd /work && tar -x && exec bash scripts/clean-room/in-container.sh "$MODE"'
