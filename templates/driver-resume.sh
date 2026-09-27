#!/usr/bin/env bash
# Driver template: run a task's phases under ONE run id, resuming at the
# failed phase instead of starting over (bd#85).
#
# Usage: driver-resume.sh RUN_ID PHASES_CSV CTX_JSON EVENT_LOG
#
#   RUN_ID      one id per task; reuse it on every run so finished phases and
#               finished steps are served from the engine's caches
#   PHASES_CSV  phase order, e.g. phase_1_discovery,phase_45_spec,phase_5_implement
#   CTX_JSON    WorkflowContext JSON file passed to every phase
#   EVENT_LOG   event log; the task ledger lives in the same directory
#
# BD_ENGINE_RUN overrides the engine command (default: python3 -m bytedigger_engine.run).
# BD_TASK_MAX_RUNS / BD_TASK_MAX_COST_USD override the task cap (3 runs / $60).
#
# Exit codes: 0 when every phase is done, 1 when the task stopped (a phase
# failed, the cap was reached, a human is needed, or a reroute is required).
set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "usage: $0 RUN_ID PHASES_CSV CTX_JSON EVENT_LOG" >&2
  exit 2
fi
run_id=$1
phases_csv=$2
ctx=$3
event_log=$4

read -r -a engine <<< "${BD_ENGINE_RUN:-python3 -m bytedigger_engine.run}"

plan=$("${engine[@]}" --task-begin "$phases_csv" --run-id "$run_id" --event-log "$event_log" \
  --task-max-runs "${BD_TASK_MAX_RUNS:-3}" --task-max-cost-usd "${BD_TASK_MAX_COST_USD:-60}" \
  | tail -n 1) || true

field() {
  python3 -c 'import json,sys; v=json.loads(sys.argv[1]).get(sys.argv[2]); print("" if v is None else v)' "$plan" "$1"
}

if [ -z "$plan" ]; then
  echo "driver: --task-begin produced no plan" >&2
  exit 1
fi
action=$(field action)
allowed=$(field allowed)
resume_from=$(field resume_from)

case "$action" in
  done)
    echo "driver: task $run_id is done"
    exit 0
    ;;
  resume)
    if [ "$allowed" != "True" ]; then
      echo "driver: task $run_id refused: $(field error_code)" >&2
      exit 1
    fi
    ;;
  reroute)
    echo "driver: task $run_id needs a reroute to $resume_from (set org_config.phase_reroute and rerun)" >&2
    exit 1
    ;;
  *)
    echo "driver: task $run_id stopped ($action); a human is needed" >&2
    exit 1
    ;;
esac

started=0
IFS=',' read -r -a phases <<< "$phases_csv"
for phase in "${phases[@]}"; do
  if [ "$started" -eq 0 ] && [ "$phase" != "$resume_from" ]; then
    continue
  fi
  started=1
  echo "driver: running $phase (run $run_id)"
  if ! "${engine[@]}" --workflow "$phase" --run-id "$run_id" --ctx "$ctx" --event-log "$event_log"; then
    echo "driver: $phase failed; the next run resumes here" >&2
    exit 1
  fi
done
if [ "$started" -eq 0 ]; then
  echo "driver: resume point $resume_from is not in $phases_csv" >&2
  exit 1
fi
