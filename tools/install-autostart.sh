#!/usr/bin/env bash
# orgtree autostart installer (Linux) -- F-06 §9, user-ruled.
#   tools/install-autostart.sh            install + enable + start
#   tools/install-autostart.sh uninstall
#
# One systemd USER unit running the backend IN THE FOREGROUND. Under systemd
# there is no stale-backend problem to guard (systemd owns the real process),
# so direct launch is correct here — Restart=always is genuine crash-restart,
# and `loginctl enable-linger` keeps it alive with nobody logged in.
# Deploys become: git pull + build, then `systemctl --user restart orgtree`
# (update.sh still works for manual runs; do not run both at once).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/orgtree.service"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

if [ "${1:-}" = "uninstall" ]; then
    systemctl --user disable --now orgtree 2>/dev/null || true
    rm -f "$UNIT"
    systemctl --user daemon-reload
    echo "removed $UNIT"
    exit 0
fi

mkdir -p "$UNIT_DIR"
cat > "$UNIT" <<EOF
[Unit]
Description=orgtree backend
After=network.target

[Service]
Type=simple
WorkingDirectory=$ROOT/backend
ExecStart=$PY -m orgtree.api
Restart=always
RestartSec=10
# the CLI reads ~/.claude/.credentials.json — a USER unit resolves the right ~

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now orgtree
echo "installed + started: systemctl --user status orgtree"
echo ""
echo "for a box with nobody logged in, ALSO run once:"
echo "  loginctl enable-linger $USER"
echo "deploys: git pull + build the UI, then: systemctl --user restart orgtree"
