#!/usr/bin/env bash
# Install the Podium Watcher poller as a systemd timer on the Pi.
#   cd ~/podium-watcher && chmod +x deploy/install.sh && sudo ./deploy/install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_USER="${SUDO_USER:-$(whoami)}"
PYBIN="$(command -v python3)"

[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo $0" >&2; exit 1; }

echo "Installing Podium Watcher"
echo "  project : $PROJECT_DIR"
echo "  user    : $RUN_USER"

sudo -u "$RUN_USER" "$PYBIN" -m venv "$PROJECT_DIR/.venv"
sudo -u "$RUN_USER" "$PROJECT_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
sudo -u "$RUN_USER" "$PROJECT_DIR/.venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

mkdir -p "$PROJECT_DIR/data"
chown -R "$RUN_USER":"$RUN_USER" "$PROJECT_DIR/data"

cat > /etc/systemd/system/podium-poller.service <<EOF
[Unit]
Description=Podium Watcher - poll venue agendas
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/.venv/bin/python -u run_once.py
TimeoutStartSec=1800
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true
ProtectHome=read-only
ReadWritePaths=$PROJECT_DIR/data
EOF

install -m 644 "$PROJECT_DIR/services/podium-poller.timer" \
    /etc/systemd/system/podium-poller.timer

systemctl daemon-reload
systemctl enable --now podium-poller.timer

echo
echo "Installed. The timer runs at 06:00, 12:00, 18:00 and 22:00."
echo "  systemctl list-timers podium-poller.timer --no-pager"
echo "  sudo systemctl start podium-poller.service   # collect right now"
echo "  journalctl -u podium-poller -n 50 --no-pager"
