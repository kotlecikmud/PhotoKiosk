#!/usr/bin/env bash
# PhotoKiosk Automated Idempotent Deployment Script
# Target: Ubuntu Server 24.04 LTS (Minimal headless with lightweight Xorg / Openbox)

set -euo pipefail

INSTALL_DIR="/opt/photokiosk"
USER_NAME="kiosk"

echo "=================================================="
echo " Starting PhotoKiosk Installation..."
echo "=================================================="

# 1. System packages
if [ "$EUID" -eq 0 ]; then
    echo "[1/7] Installing required system packages..."
    apt-get update -y
    apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        python3-dev \
        build-essential \
        libopenjp2-7 \
        libtiff6 \
        libjpeg-turbo8 \
        liblcms2-2 \
        liblcms2-utils \
        xorg \
        openbox \
        x11-xserver-utils \
        samba \
        smbclient \
        cifs-utils \
        udisks2 \
        jmtpfs \
        libimobiledevice-utils \
        ifuse \
        xvfb

    # Ensure dedicated kiosk user exists
    if ! id -u "$USER_NAME" >/dev/null 2>&1; then
        echo "[2/7] Creating system user '$USER_NAME'..."
        useradd -m -s /bin/bash "$USER_NAME"
        usermod -aG video,plugdev,audio,dialout "$USER_NAME"
    fi
else
    echo "Running in user mode without root privileges; skipping apt package installations."
fi

# 2. Deploy directory structure
echo "[3/7] Setting up application directories..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/storage/originals"
mkdir -p "$INSTALL_DIR/storage/thumbnails"
mkdir -p "$INSTALL_DIR/storage/orders"
mkdir -p "$INSTALL_DIR/storage/shared"

# Copy repository files if installing to /opt
if [ "$(pwd)" != "$INSTALL_DIR" ]; then
    cp -ru ./* "$INSTALL_DIR/" || true
fi

# 3. Python Virtual Environment
echo "[4/7] Setting up Python virtual environment..."
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# 4. Generate Random Admin Password and Initialize Database
echo "[5/7] Initializing SQLite database and administrative credentials..."
RANDOM_PASS=$(python3 -c "import secrets, string; print(''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(14)))")

PYTHONPATH="$INSTALL_DIR" "$INSTALL_DIR/venv/bin/python3" -c "
from backend.database import init_database
init_database('$INSTALL_DIR/storage/photokiosk.db', initial_admin_password='$RANDOM_PASS')
"

# 5. Configure Samba Template
echo "[6/7] Deploying Samba configuration..."
if [ -f "$INSTALL_DIR/config/smb.conf.template" ] && [ "$EUID" -eq 0 ]; then
    cp "$INSTALL_DIR/config/smb.conf.template" /etc/samba/smb.conf.photokiosk
    # Append include if not already present in smb.conf
    if ! grep -q "smb.conf.photokiosk" /etc/samba/smb.conf 2>/dev/null; then
        echo "include = /etc/samba/smb.conf.photokiosk" >> /etc/samba/smb.conf
    fi
    systemctl restart smbd || true
fi

# 6. Configure systemd services
echo "[7/7] Registering and enabling systemd services..."
if [ "$EUID" -eq 0 ]; then
    cp "$INSTALL_DIR/systemd/photokiosk-backend.service" /etc/systemd/system/
    cp "$INSTALL_DIR/systemd/photokiosk.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable photokiosk-backend.service
    systemctl enable photokiosk.service
    chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR"
fi

echo "=================================================="
echo " PhotoKiosk Installation Complete!"
echo " "
echo " Administration Web Interface: http://<KIOSK-IP>:8080/admin"
echo " Initial Admin Password:       $RANDOM_PASS"
echo " "
echo " Note: You will be required to change this password"
echo " on your first administrator login."
echo "=================================================="
