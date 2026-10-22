#!/usr/bin/env bash
set -euo pipefail
ROOT=/opt/horizon-maestro/source
WORK=/var/lib/horizon-worker/workspace
git -C "$ROOT" pull --ff-only
install -d -o horizon-worker -g horizon-worker -m 0700 "$WORK" /var/lib/horizon-worker/report
install -m 0755 "$ROOT/scripts/vps_agent_loop.sh" /usr/local/bin/horizon-worker-loop
install -d -m 0755 /usr/local/lib/horizon-vps
install -m 0755 "$ROOT/scripts/vps_queue_bridge.py" /usr/local/lib/horizon-vps/queue_bridge.py
QUEUE=/var/lib/horizon-worker/direct-queue
install -d -o horizon-worker -g horizon-worker -m 0700 "$QUEUE"
for group in requests paused cancelled archived results agent-holds agent-settings; do
    install -d -o horizon-worker -g horizon-worker -m 0700 "$QUEUE/$group"
done
install -m 0644 "$ROOT/scripts/systemd/horizon-queue-observer.service" /etc/systemd/system/horizon-queue-observer.service
install -m 0644 "$ROOT/scripts/systemd/horizon-queue-observer.timer" /etc/systemd/system/horizon-queue-observer.timer
cp "$ROOT/horizon_core/gateway.py" "$WORK/horizon_core/gateway.py"
chown horizon-worker:horizon-worker "$WORK/horizon_core/gateway.py"
# The original repository's inbox.json is empty. Never reintroduce historic requests.
if [[ ! -f "$WORK/tasks/inbox.json" ]]; then
    install -d -o horizon-worker -g horizon-worker -m 0700 "$WORK/tasks"
    printf '[]\n' > "$WORK/tasks/inbox.json"
    chown horizon-worker:horizon-worker "$WORK/tasks/inbox.json"
fi
install -d -m 0700 /etc/horizon-worker
# Do not overwrite configured provider keys or host preferences during upgrades.
if [[ ! -f /etc/horizon-worker/runtime.env ]]; then
cat > /etc/horizon-worker/runtime.env <<'ENV'
HORIZON_LOCAL_MODEL_ENABLED=true
OLLAMA_MODEL=qwen3:4b
OLLAMA_HTTP_TIMEOUT_SECONDS=360
OLLAMA_MAX_OUTPUT_TOKENS=1024
ENV
fi
chown root:root /etc/horizon-worker/runtime.env
chmod 0600 /etc/horizon-worker/runtime.env
systemctl daemon-reload
systemctl enable --now horizon-queue-observer.timer
systemctl restart horizon-worker.service
systemctl is-active horizon-worker.service
