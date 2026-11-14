"""Browser launcher: detection and kiosk command lines."""
from backend import kiosk_browser as kb


def test_chromium_kiosk_command_uses_own_profile_and_kiosk_flags(tmp_path):
    cmd = kb.build_command("/usr/bin/chromium", "http://127.0.0.1:8080/", True, str(tmp_path / "prof"))
    assert cmd[0] == "/usr/bin/chromium" and cmd[-1] == "http://127.0.0.1:8080/"
    assert "--kiosk" in cmd and "--overscroll-history-navigation=0" in cmd
    assert any(a.startswith("--user-data-dir=") and a.endswith("prof") for a in cmd)


def test_edge_gets_fullscreen_kiosk_type_and_window_mode_uses_app():
    edge = kb.build_command("C:/Edge/msedge.exe", "http://x/", True)
    assert "--edge-kiosk-type=fullscreen" in edge
    assert "--app=http://x/" in kb.build_command("/usr/bin/chromium", "http://x/", False)


def test_firefox_uses_kiosk_flag():
    assert kb.build_command("/usr/bin/firefox", "http://x/", True) == ["/usr/bin/firefox", "--kiosk", "http://x/"]


def test_find_browser_prefers_override_then_chromium(monkeypatch, tmp_path):
    fake = tmp_path / "mybrowser"
    fake.write_text("")
    monkeypatch.setenv("KIOSK_BROWSER", str(fake))
    assert kb.find_browser() == str(fake)
    monkeypatch.delenv("KIOSK_BROWSER")
    monkeypatch.setattr(kb.shutil, "which", lambda n: f"/bin/{n}" if n in ("chromium-browser", "firefox") else None)
    assert kb.find_browser() == "/bin/chromium-browser"
    monkeypatch.setattr(kb.shutil, "which", lambda n: None)
    monkeypatch.setattr(kb.sys, "platform", "linux")
    assert kb.find_browser() is None


def test_default_profile_is_a_visible_folder_in_home_and_overridable(monkeypatch, tmp_path):
    monkeypatch.delenv("KIOSK_BROWSER_PROFILE", raising=False)
    monkeypatch.setattr(kb.Path, "home", classmethod(lambda cls: tmp_path))
    prof = kb.default_profile_dir()
    assert prof == str(tmp_path / "photokiosk-browser") and "/." not in prof  # snap forbids hidden dirs
    monkeypatch.setenv("KIOSK_BROWSER_PROFILE", str(tmp_path / "custom"))
    assert kb.default_profile_dir() == str(tmp_path / "custom")
