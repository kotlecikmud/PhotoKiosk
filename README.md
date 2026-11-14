# PhotoKiosk

PhotoKiosk is an appliance-grade photo ordering, image processing, and local file management system designed for touchscreen kiosks running Linux (Ubuntu Server 24.04 LTS recommended).

---

## Key Features

1. **Touchscreen Interface (web app in a fullscreen browser)**
   - High-contrast, large touch targets, themed from the admin panel; responsive from 1024x768 up.
   - Live device detection (USB, discs, phones) pushed to the UI - no manual refresh needed.
   - Virtualised thumbnail grid that stays smooth with thousands of photos.
   - Individual & batch print configuration.
   - Live visual transformation preview matching final print output.

2. **Decoupled Backend & Remote Administration (FastAPI)**
   - REST API for kiosk communications and remote network integration.
   - Web Administration interface (`http://<KIOSK-IP>:8080/admin`).
   - Secure authentication with bcrypt hashing, brute-force rate-limiting, and forced password change on first login.
   - Management of custom print sizes, paper finishes (Matte, Glossy), system settings (DPI, borders), and order inspections.
   - Storage file manager to inspect and clean shared drop directories.

3. **High-Precision Image Processing (Pillow & LittleCMS)**
   - Exact calculations at **300 DPI**: `pixels = round((mm / 25.4) * 300)`.
   - **Four Crop Modes**:
     - **Fill-in**: Center-crops to format aspect ratio, scales to exact 300 DPI paper size.
     - **Fit-in**: Scales image proportionally, centers onto pure white canvas of exact paper dimensions.
     - **Border (4 mm)**: Fits photograph within inner printable area with exact 4 mm borders on all sides.
     - **No-resize**: Strictly preserves original pixel data and orientation without resampling; reads embedded horizontal and vertical DPI; computes physical mm and warns if dimensions exceed paper size.
   - **Effective EXIF Orientation Matching**: Automatically swaps paper orientation (e.g. 10x15 vs 15x10) to match photograph orientation.
   - **ICC Color Management**: Preserves embedded color profiles in generated JPEG prints and logs profile metadata in order manifest.

4. **Atomic Order Architecture & Crash Recovery**
   - Unique order identifier: `ORD-YYYYMMDD-HHMMSS-XXXX`.
   - Immutable originals: Source files copied byte-for-byte into `ORIGINALS/` before unmounting/processing.
   - Filename collision safe: Automatically disambiguates conflicting filenames (`photo_1.jpg`) without overwriting.
   - Hierarchical print outputs: `[size]/[finish]/x[qty]/[filename]`.
   - Comprehensive `order.json` manifest capturing full trace from source to output pixels, DPI, and physical mm.
   - Crash & power-failure safety: Work happens in `.tmp_<order_id>` and atomically renames on completion. Startup reconciliation marks incomplete orders as `FAILED` while preserving diagnosis logs.

5. **Local Network Storage & Ingestion (Samba)**
   - Exposes shared photo drop directory via SMB (`\\PHOTO-KIOSK\Photos`).
   - `watchdog` monitoring automatically detects newly uploaded files without restarting the application.

---

## System Requirements & Architecture

- **Operating System**: Ubuntu Server 24.04 LTS (minimal installation).
- **Display Server**: Xorg with Openbox window manager (no desktop environment required).
- **Browser**: Chromium, Chrome, Edge or Firefox (auto-detected) shows the kiosk UI in fullscreen.
- **Python**: 3.12+
- **Services**:
  - `photokiosk-backend.service`: FastAPI service (`:8080`) serving the **touchscreen kiosk UI** (`/`),
    the **Admin panel** (`/admin`) and the REST API; it also runs the USB/phone device monitor.
  - `photokiosk.service`: opens `http://127.0.0.1:8080/` in a fullscreen browser and restarts it if it closes.

The kiosk UI is a dependency-free web app (`web/`: plain HTML/CSS/ES modules, **no Node or build step**).
Order drafts live in the browser, so every tap is instant; the server only does the heavy work
(thumbnails, print previews, order processing).

---

## Installation & Deployment

### 1. Automated Installation
Run the idempotent installation script as root:
```bash
sudo ./install.sh
```
The script:
- Installs necessary system packages (`xorg`, `openbox`, `samba`, `udisks2`, `libimobiledevice`, `jmtpfs`, `unclutter`)
  and a browser if none is present. Ubuntu 24.04 ships Chromium only as a snap (`chromium-browser`); to use Google
  Chrome instead, install its `.deb` and/or set `KIOSK_BROWSER=/usr/bin/google-chrome-stable` in the unit.
- Sets up Python virtual environment and installs dependencies.
- Generates a random administrative password and displays it once.
- Configures Samba share template and deploys systemd service definitions.

### 2. Manual Startup & Development Modes
You can run the application directly from the command line:

```bash
# Run the server and open the kiosk UI in a browser (closing the browser stops the server):
python3 main.py --mode all              # add --fullscreen for true kiosk mode

# Run only the server (kiosk UI at /, admin at /admin) - e.g. for development:
python3 main.py --mode backend --port 8080

# Open only the browser (server must already be running; this is what photokiosk.service does):
python3 main.py --mode kiosk --fullscreen
```
The kiosk UI is also reachable from any browser at `http://<KIOSK_IP>:8080/` (handy for development on a laptop;
device scanning and photo sources always belong to the machine the server runs on).

Environment variables: `KIOSK_BROWSER` (browser executable), `KIOSK_BROWSER_PROFILE` (browser profile folder,
default `~/photokiosk-browser`).

The idle timeout comes from `kiosk.auto_reset_timeout_seconds` in `config/settings.yaml` (default 60; `0` disables it):
after that long without a touch on an order in progress, the customer sees a 20-second "Are you still there?"
countdown before the selection is cleared for the next person.

```bash
# Tests (no display needed):
PYTHONPATH=. python3 -m pytest tests/ -v
cd web && node --test "tests/*.test.mjs"     # front-end logic (Node 20+)
```

---

## Administration Interface

Open any LAN browser to:
```text
http://<KIOSK_IP>:8080/admin
```
- Sign in using the generated initial password displayed by `install.sh`.
- Set your personal administrator password on first login.
- From the dashboard, configure print sizes, toggle formats, monitor disk space, inspect generated orders, and view manifests.

---

## Automated Test Suite

Run the full automated test suite verifying geometry, 300 DPI math, EXIF 1–8 handling, ICC profiles, collisions, and recovery:
```bash
PYTHONPATH=. python3 -m pytest tests/ -v
cd web && node --test "tests/*.test.mjs"
```
The JS tests include a parity check of the gallery layout engine against fixtures produced by the original
Python implementation.
