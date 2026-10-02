#!/usr/bin/env bash
# GH1200 fixture (AC19, AC20) — a CLI production artifact whose sibling tests
# EXECUTE it rather than import it or read its text. This is the coupling shape
# the gate found the five v1 channels blind to (finding B1): every `.sh` under
# SYSTEM/cli/build is tested exclusively this way.
set -uo pipefail

CLI_TARGET_MODE='gh1200-exec-mode-marker'

usage() {
  echo "usage: cli_target.sh [--mode MODE]"
}

emit_mode() {
  echo "$CLI_TARGET_MODE"
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

emit_mode
