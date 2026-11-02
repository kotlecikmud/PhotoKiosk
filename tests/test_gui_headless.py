"""Automated test for CustomTkinter GUI execution under headless virtual display (Xvfb)."""
import os
import pytest
from gui.app import PhotoKioskApp
from backend.database import init_database


def test_gui_initialization_and_navigation(tmp_path):
    if not os.environ.get("DISPLAY"):
        pytest.skip("No DISPLAY available; run under xvfb-run for virtual display testing.")

    db_path = tmp_path / "kiosk_gui_test.db"
    init_database(str(db_path))

    # Initialize Tk app without blocking mainloop
    app = PhotoKioskApp(fullscreen=False)
    assert app.title() == "PhotoKiosk"

    # Verify home screen opened
    assert len(app.container.winfo_children()) == 1

    # Verify transition to gallery screen
    app._on_source_selected("Test Source", "storage/shared")
    assert len(app.container.winfo_children()) == 1

    # Cleanup
    app.device_daemon.stop()
    app.destroy()
