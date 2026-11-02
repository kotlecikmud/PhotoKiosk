# PhotoKiosk

PhotoKiosk is an appliance-grade photo ordering, image processing, and local file management system designed for touchscreen kiosks running Linux (Ubuntu Server 24.04 LTS recommended).

---

## Key Features

1. **Touchscreen Interface (CustomTkinter)**
   - High-contrast, large touch targets (64px buttons, clear typography).
   - Dynamic device detection modal dialogs with debounce filtering.
   - Asynchronous thumbnail browser with progressive background loading.
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
- **Python**: 3.12+
- **Services**:
  - `photokiosk-backend.service`: FastAPI web service & Admin GUI (`:8080`).
  - `photokiosk.service`: CustomTkinter fullscreen touchscreen GUI.

---

## Installation & Deployment

### 1. Automated Installation
Run the idempotent installation script as root:
```bash
sudo ./install.sh
```
The script:
- Installs necessary system packages (`xorg`, `openbox`, `samba`, `udisks2`, `libimobiledevice`, `jmtpfs`).
- Sets up Python virtual environment and installs dependencies.
- Generates a random administrative password and displays it once.
- Configures Samba share template and deploys systemd service definitions.

### 2. Manual Startup & Development Modes
You can run the application directly from the command line:

```bash
# Run both Backend and GUI concurrently:
python3 main.py --mode all

# Run only the Backend / Admin service:
python3 main.py --mode backend --port 8080

# Run only the Touchscreen GUI:
python3 main.py --mode gui --fullscreen

# Development / Headless testing with virtual display:
xvfb-run -a python3 -m pytest tests/ -v
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
PYTHONPATH=. xvfb-run -a pytest tests/ -v
```
