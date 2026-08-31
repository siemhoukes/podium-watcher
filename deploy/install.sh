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

# Notification settings live outside the repo so secrets never get committed.
mkdir -p /etc/podium-watcher
if [[ ! -f /etc/podium-watcher/podium.env ]]; then
    install -m 600 -o "$RUN_USER" -g "$RUN_USER"         "$PROJECT_DIR/.env.example" /etc/podium-watcher/podium.env
    echo "Created /etc/podium-watcher/podium.env -- edit it to enable alerts."
fi

cat > /etc/systemd/system/podium-poller.service <<EOF
[Unit]
Description=Podium Watcher - poll venue agendas
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=-/etc/podium-watcher/podium.env
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
install -m 644 "$PROJECT_DIR/services/podium-digest.timer" \
    /etc/systemd/system/podium-digest.timer

cat > /etc/systemd/system/podium-digest.service <<EOF
[Unit]
Description=Podium Watcher - weekly digest
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=-/etc/podium-watcher/podium.env
ExecStart=$PROJECT_DIR/.venv/bin/python -u run_digest.py
TimeoutStartSec=600
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true
ProtectHome=read-only
ReadWritePaths=$PROJECT_DIR/data
EOF

cat > /etc/systemd/system/podium-web.service <<EOF
[Unit]
Description=Podium Watcher - dashboard
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/.venv/bin/gunicorn --workers 2 --bind 127.0.0.1:5011 \\
    --access-logfile - --error-logfile - podiumwatch.web:app
Restart=always
RestartSec=10
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true
ProtectHome=read-only
# The dashboard only reads, but sqlite still wants to touch the WAL files.
ReadWritePaths=$PROJECT_DIR/data
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now podium-poller.timer
systemctl enable --now podium-digest.timer
systemctl enable --now podium-web.service

echo
echo "Installed."
echo "  timer  : 06:00, 12:00, 18:00, 22:00"
echo "  web    : http://127.0.0.1:5011 (nginx fronts it at podium.siem.codes)"
echo
echo "  systemctl list-timers podium-poller.timer --no-pager"
echo "  sudo systemctl start podium-poller.service   # collect right now"
echo "  journalctl -u podium-poller -n 50 --no-pager"
echo "  journalctl -u podium-web -n 50 --no-pager"
echo
echo "nginx (once): sudo cp deploy/podium-nginx.conf /etc/nginx/sites-available/podium"
echo "              sudo ln -s ../sites-available/podium /etc/nginx/sites-enabled/podium"
echo "              sudo nginx -t && sudo systemctl reload nginx"
