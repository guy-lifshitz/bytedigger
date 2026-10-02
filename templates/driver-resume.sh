#!/usr/bin/env bash
# Driver template: run a task's phases under ONE run id, resuming at the
# failed phase instead of starting over (bd#85).
#
# Usage: driver-resume.sh RUN_ID PHASES_CSV CTX_JSON EVENT_LOG
#
#   RUN_ID      one id per task; reuse it on every run so finished phases and
#               finished steps are served from the engine's caches
#   PHASES_CSV  phase order, e.g. phase_45_spec,phase_5_implement,phase_6_review
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

set +e
plan=$("${engine[@]}" --task-begin "$phases_csv" --run-id "$run_id" --event-log "$event_log" \
  --task-max-runs "${BD_TASK_MAX_RUNS:-3}" --task-max-cost-usd "${BD_TASK_MAX_COST_USD:-60}" \
  | tail -n 1)
begin_rc=${PIPESTATUS[0]}
set -e
# --task-begin exits 0 (allowed) or 1 (not allowed); anything else is a usage
# error or a crash, and its output is not a plan.
if [ "$begin_rc" -ne 0 ] && [ "$begin_rc" -ne 1 ]; then
  echo "driver: --task-begin failed (exit $begin_rc): $plan" >&2
  exit 1
fi
# Parse the plan once into shell variables (plan_<field>).
if ! fields=$(python3 -c '
import json, shlex, sys
d = json.loads(sys.argv[1])
for k in ("action", "allowed", "resume_from", "error_code", "plan_error_code",
          "last_status", "cost_unknown_calls"):
    v = d.get(k)
    print("plan_" + k + "=" + shlex.quote("" if v is None else str(v)))
' "$plan" 2>/dev/null); then
  echo "driver: --task-begin did not print a JSON plan: $plan" >&2
  exit 1
fi
eval "$fields"

action=$plan_action
resume_from=$plan_resume_from
why="$plan_error_code $plan_plan_error_code $plan_last_status"

if [ -n "$plan_cost_unknown_calls" ] && [ "$plan_cost_unknown_calls" != "0" ]; then
  echo "driver: warning: $plan_cost_unknown_calls model call(s) reported no cost; the \$ cap only counts known cost" >&2
fi

case "$action" in
  done)
    echo "driver: task $run_id is done"
    exit 0
    ;;
  resume)
    if [ "$plan_allowed" != "True" ]; then
      echo "driver: task $run_id refused:$why" >&2
      exit 1
    fi
    ;;
  reroute)
    echo "driver: task $run_id needs a reroute to $resume_from. Run it once with" \
         "org_config.phase_reroute set in the ctx, then rerun this driver:" >&2
    echo "  ${engine[*]} --workflow $resume_from --run-id $run_id --ctx <ctx with phase_reroute> --event-log $event_log" >&2
    exit 1
    ;;
  *)
    echo "driver: task $run_id stopped ($action):$why; a human is needed" \
         "(fix the cause, then: ${engine[*]} --task-reset <reason> --run-id $run_id --event-log $event_log)" >&2
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
