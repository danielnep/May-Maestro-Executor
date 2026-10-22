#!/usr/bin/env bash
# Dedicated VPS worker: clean operational queue, preserved learned memory.
# Existing GitHub Action schedulers are disabled. Publishing source revisions to
# GitHub, when requested by an authorized task, uses a separately configured token.
set -euo pipefail
cd /var/lib/horizon-worker/workspace
while true; do
  if [[ ! -s /var/lib/horizon-worker/state/active.sqlite3 ]]; then
    echo "standby: fresh SQLite database not initialized"
  elif [[ -z "${GROQ_API_KEY:-}${GEMINI_API_KEY:-}" && "${HORIZON_LOCAL_MODEL_ENABLED:-}" != "true" ]]; then
    echo "standby: configure one verified local or external model provider"
  else
    /opt/horizon-maestro/venv/bin/python -m horizon_core.app \
      --db /var/lib/horizon-worker/state/active.sqlite3 \
      --root /var/lib/horizon-worker/workspace \
      --tasks /var/lib/horizon-worker/workspace/tasks/inbox.json \
      --session-seconds 600 --max-tasks 1 \
      --report /var/lib/horizon-worker/report/latest.json || echo "worker cycle failed"
  fi
  sleep 20
done
