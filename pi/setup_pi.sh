#!/bin/bash
# Run once on the Raspberry Pi:  bash pi/setup_pi.sh   (from inside the shop-menu folder)
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="$(whoami)"

echo "Installing packages..."
sudo apt update
sudo apt install -y python3-venv curl x11-xserver-utils

echo "Setting up Python environment..."
python3 -m venv "$PROJECT_DIR/venv"
"$PROJECT_DIR/venv/bin/pip" install --quiet flask gunicorn

if [ ! -f "$PROJECT_DIR/admin_config.json" ]; then
    echo
    echo "Let's set the username and password for the menu editor (/admin)."
    "$PROJECT_DIR/venv/bin/python" "$PROJECT_DIR/set_password.py"
    echo
fi

echo "Creating the menu server service..."
sudo tee /etc/systemd/system/menu-board.service >/dev/null <<SERVICE
[Unit]
Description=Restaurant menu board (Flask)
After=network.target

[Service]
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/gunicorn -w 1 -b 0.0.0.0:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable --now menu-board.service

echo "Setting up the screens to open on login..."
chmod +x "$PROJECT_DIR/pi/start-kiosk.sh"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/menu-kiosk.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Menu Kiosk
Exec=$PROJECT_DIR/pi/start-kiosk.sh
DESKTOP

echo
echo "Done. Next: make sure the Pi logs in to the desktop automatically and uses X11"
echo "(see the notes), then reboot with:  sudo reboot"
