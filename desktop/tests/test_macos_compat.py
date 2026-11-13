"""Compatibilitate macOS (Apple Silicon): căi în ~/CIMITIR-MAC/date, identificare dispozitiv, Keychain, fonturi.

Rulează pe orice sistem: ramurile macOS se activează prin `sys.platform="darwin"` și comenzi sistem simulate (fără Mac real, fără Keychain real).
Nu înlocuiesc testarea pe un Mac real. Regresii Windows: căile Windows rămân cele vechi (%LOCALAPPDATA%, %APPDATA%)."""
import importlib
import os
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"
for _p in (str(SHARED), str(DESKTOP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if str(SHARED.parent / "backend") not in sys.path:
    sys.path.append(str(SHARED.parent / "backend"))

def _load_private_ai_config():
    """Copie privată a modulului (nu atinge sys.modules/atributul pachetului: testul existent `isolated_config` depinde de reimportul curat)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_macos_test_ai_config", DESKTOP / "ai_scanare" / "config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# importate ÎNAINTE de simularea `sys.platform="darwin"` (urllib/requests nu pot fi importate sub o platformă falsă)
import app_logging  # noqa: E402
import cimitir_backup  # noqa: E402
import cimitir_config  # noqa: E402
import cimitir_platform  # noqa: E402
import cimitir_secretstore as ss  # noqa: E402
import requests  # noqa: E402,F401
import theme  # noqa: E402
import updater  # noqa: E402
from cimitir import app_logging as backend_logging  # noqa: E402
import macos_support  # noqa: E402
from license import validator  # noqa: E402
import license as license_pkg  # noqa: E402
from security import audit_log, hardware_id, settings_store  # noqa: E402

ai_config = _load_private_ai_config()


@pytest.fixture
def mac_home(tmp_path, monkeypatch):
    home = tmp_path / "CIMITIR-MAC"
    monkeypatch.setenv("CIMITIR_MAC_HOME", str(home))
    return home


@pytest.fixture
def as_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


@pytest.fixture
def as_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


# ------------------------------------------------------------------ cimitir_platform
def test_layout_is_under_cimitir_mac_date(mac_home):
    assert cimitir_platform.mac_home() == mac_home
    assert cimitir_platform.mac_data_root() == mac_home / "date"
    assert cimitir_platform.mac_app_data_dir() == mac_home / "date" / "cimitir-data"
    assert cimitir_platform.mac_cache_dir() == mac_home / "cache"
    for name in ("config", "logs", "audit", "backups"):
        path = cimitir_platform.mac_subdir(name)
        assert path == mac_home / "date" / name and path.is_dir()


def test_default_home_is_cimitir_mac_in_user_home(monkeypatch):
    monkeypatch.delenv("CIMITIR_MAC_HOME", raising=False)
    assert cimitir_platform.mac_home() == Path.home() / "CIMITIR-MAC"


# ------------------------------------------------------------------ cimitir_config
def _fresh_config(monkeypatch):
    monkeypatch.delenv("CIMITIR_DATA_DIR", raising=False)
    monkeypatch.setenv("CIMITIR_IGNORE_PROJECT_ENV", "1")
    monkeypatch.setattr(cimitir_config, "_env_loaded", False)
    return cimitir_config


def test_data_dir_on_macos_defaults_to_cimitir_mac_date_not_the_git_checkout(mac_home, as_darwin, monkeypatch):
    cfg = _fresh_config(monkeypatch)
    assert Path(cfg.data_dir()) == (mac_home / "date" / "cimitir-data").resolve()
    assert Path(cfg.db_path()) == (mac_home / "date" / "cimitir-data" / "cimitir.db").resolve()
    assert cfg.sync_folder_warning() == ""


def test_explicit_data_dir_still_wins_on_macos(mac_home, as_darwin, monkeypatch, tmp_path):
    cfg = _fresh_config(monkeypatch)
    monkeypatch.setenv("CIMITIR_DATA_DIR", str(tmp_path / "alt"))
    assert Path(cfg.data_dir()) == (tmp_path / "alt").resolve()


def test_data_dir_on_windows_and_linux_dev_is_unchanged(monkeypatch, tmp_path):
    cfg = _fresh_config(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(cfg, "DEFAULT_DEV_DATA_DIR", tmp_path / "shared_data")
    assert Path(cfg.data_dir()) == (tmp_path / "shared_data")
    monkeypatch.delenv("CIMITIR_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert cfg._os_user_data_dir() == tmp_path / "Local" / "Cimitir"


def test_icloud_and_cloudstorage_folders_get_the_sqlite_warning(monkeypatch):
    for path in ("/Users/x/Library/Mobile Documents/com~apple~CloudDocs/d", "/Users/x/Library/CloudStorage/OneDrive-X/d"):
        assert cimitir_config.sync_folder_warning(path)
    assert cimitir_config.sync_folder_warning("/Users/x/CIMITIR-MAC/date/cimitir-data") == ""


# ------------------------------------------------------------------ backup
def test_macos_backup_and_failure_log_stay_inside_cimitir_mac(mac_home, as_darwin, tmp_path):
    data = tmp_path / "x" / "data"
    data.mkdir(parents=True)
    ext = cimitir_backup._external_backup_dir(data)
    assert (mac_home / "date" / "Cimitir-backups").resolve() in ext.parents
    assert cimitir_backup._failure_fallback_log() == mac_home / "date" / "logs" / "backup-errors.jsonl"


def test_macos_default_backup_is_a_sibling_of_the_data_dir_inside_date(mac_home):
    data = cimitir_platform.mac_app_data_dir()
    data.mkdir(parents=True)
    assert cimitir_backup.default_backup_dir(data) == (mac_home / "date" / "cimitir-data-backups").resolve()


def test_windows_backup_fallbacks_are_unchanged(as_windows, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "L"))
    assert cimitir_backup._failure_fallback_log() == tmp_path / "L" / "Cimitir" / "backup-errors.jsonl"


# ------------------------------------------------------------------ licență, audit, loguri
def _reload(name):
    return importlib.reload(importlib.import_module(name))


def test_license_cache_audit_and_logs_go_to_cimitir_mac_on_macos(mac_home, as_darwin):
    assert Path(settings_store._data_dir()) == mac_home / "date" / "config"
    assert Path(audit_log._audit_dir()) == mac_home / "date" / "audit"
    os.environ.pop("CIMITIR_LOG_DIR", None)
    assert Path(app_logging._resolve_log_dir()) == mac_home / "date" / "logs"


def test_backend_logging_goes_to_cimitir_mac_on_macos(mac_home, as_darwin, monkeypatch):
    monkeypatch.delenv("CIMITIR_LOG_DIR", raising=False)
    assert Path(backend_logging._resolve_log_dir()) == mac_home / "date" / "logs"


def test_windows_license_audit_and_logs_paths_are_unchanged(as_windows, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.delenv("CIMITIR_LOG_DIR", raising=False)
    assert Path(settings_store._data_dir()) == tmp_path / "Local" / "Cimitir"
    assert Path(audit_log._audit_dir()) == tmp_path / "Local" / "Cimitir" / "audit"
    assert Path(app_logging._resolve_log_dir()) == tmp_path / "Local" / "Cimitir" / "logs"


def test_ai_config_on_macos_is_in_cimitir_mac_config(mac_home, as_darwin):
    assert ai_config._config_dir() == mac_home / "date" / "config" / "CimitirDesktop"


def test_ai_config_on_windows_is_unchanged(as_windows, monkeypatch, tmp_path):
    monkeypatch.setattr(ai_config.platform, "system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert ai_config._config_dir() == tmp_path / "Roaming" / "CimitirDesktop"


def test_macos_support_is_a_noop_off_macos(monkeypatch):
    monkeypatch.setattr(macos_support, "_APPLIED", False)
    for plat in ("win32", "linux"):
        monkeypatch.setattr(sys, "platform", plat)
        assert macos_support.apply() is False


def test_macos_support_moves_license_dir_and_replaces_the_hwid_without_touching_the_license_module(mac_home, as_darwin, monkeypatch):
    monkeypatch.setattr(macos_support, "_APPLIED", False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/ailalta/.config")
    monkeypatch.setattr(validator, "collect_components", validator.collect_components)
    monkeypatch.setattr(validator, "get_hwid", validator.get_hwid)
    monkeypatch.setattr(license_pkg, "get_hwid", license_pkg.get_hwid)
    assert macos_support.apply() is True
    assert os.environ["XDG_CONFIG_HOME"] == str(mac_home / "date" / "config")
    assert validator._license_dir() == mac_home / "date" / "config" / "CimitirDesktop"
    assert validator.get_hwid is macos_support.get_hwid and license_pkg.get_hwid is macos_support.get_hwid


# ------------------------------------------------------------------ identificarea dispozitivului
IOREG = '''+-o Root  <class IORegistryEntry>
  +-o MacBookPro  <class IOPlatformExpertDevice>
    {
      "IOPlatformSerialNumber" = "F5KXXXXXX1"
      "IOPlatformUUID" = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    }
'''
DISKUTIL = "   Device Identifier:         disk3s1s1\n   Volume UUID:              11111111-2222-3333-4444-555555555555\n"


def _fake_run(cmd):
    if cmd[0].endswith("ioreg"):
        return IOREG
    if cmd[0].endswith("diskutil"):
        return DISKUTIL
    return ""


def test_macos_hwid_components_come_from_platform_uuid_serial_and_volume_uuid(monkeypatch):
    monkeypatch.setattr(macos_support, "_run", _fake_run)
    assert macos_support.collect_components() == {"machine_guid": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE", "baseboard": "F5KXXXXXX1",
                                                  "disk": "11111111-2222-3333-4444-555555555555"}


def test_macos_hwid_is_stable_across_runs_and_computer_names_and_has_the_validator_format(monkeypatch):
    def noisy_ioreg(cmd):
        # ioreg schimbă contoare (busy/retain) între rulări; amprenta nu trebuie să depindă de ele
        return IOREG.replace("<class IORegistryEntry>", f"<class IORegistryEntry, busy {next(counter)} ms>") if cmd[0].endswith("ioreg") else DISKUTIL

    import itertools
    counter = itertools.count(1)
    monkeypatch.setattr(macos_support, "_run", noisy_ioreg)
    monkeypatch.setattr(validator.platform, "node", lambda: "MacBook-Pro.local")
    first = macos_support.get_hwid()
    monkeypatch.setattr(validator.platform, "node", lambda: "MacBook-Pro-2.local")
    again = macos_support.get_hwid()
    assert first == again and len(first) == 64 and int(first, 16) >= 0


def test_macos_hwid_differs_when_the_hardware_differs(monkeypatch):
    monkeypatch.setattr(macos_support, "_run", _fake_run)
    one = macos_support.get_hwid()
    monkeypatch.setattr(macos_support, "_run", lambda c: _fake_run(c).replace("F5KXXXXXX1", "F5KYYYYYY2"))
    assert macos_support.get_hwid() != one


def test_windows_hwid_still_includes_the_hostname_and_is_untouched(as_windows, monkeypatch):
    monkeypatch.setattr(validator.platform, "system", lambda: "Windows")
    monkeypatch.setattr(validator, "_win_machine_guid", lambda: "G")
    monkeypatch.setattr(validator, "_win_baseboard_serial", lambda: "B")
    monkeypatch.setattr(validator, "_win_disk_serial", lambda: "D")
    monkeypatch.setattr(validator.platform, "node", lambda: "PC-1")
    first = validator.get_hwid()
    monkeypatch.setattr(validator.platform, "node", lambda: "PC-2")
    assert validator.get_hwid() != first


def test_hardware_fingerprint_on_macos_is_stable_and_uses_volume_uuid(as_darwin, monkeypatch):
    calls = []

    def fake(cmd, timeout=4.0):
        calls.append(cmd[0])
        return {"ioreg": IOREG, "diskutil": DISKUTIL, "sysctl": "Apple M5"}.get(cmd[0], "")

    monkeypatch.setattr(hardware_id, "_run", fake)
    assert hardware_id._machine_id() == "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    assert hardware_id._disk_serial() == "11111111-2222-3333-4444-555555555555"
    assert hardware_id._cpu_info() == "Apple M5"
    assert hardware_id.get_hardware_id() == hardware_id.get_hardware_id()
    assert "system_profiler" not in calls


def test_ai_machine_secret_on_macos_uses_platform_uuid(as_darwin, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: IOREG)
    ai_config._machine_secret.cache_clear()
    try:
        assert b"AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE" in ai_config._machine_secret()
    finally:
        ai_config._machine_secret.cache_clear()


# ------------------------------------------------------------------ Keychain (cheia de sincronizare)
class FakeKeychain:
    def __init__(self, fail=None):
        self.items, self.calls, self.fail = {}, [], fail

    def __call__(self, args):
        self.calls.append(list(args))
        if self.fail is not None:
            return self.fail, ""
        account = args[args.index("-a") + 1]
        if args[0] == "find-generic-password":
            return (0, self.items[account]) if account in self.items else (44, "")
        if args[0] == "add-generic-password":
            self.items[account] = args[args.index("-w") + 1]
            return 0, ""
        return 1, ""


def test_keychain_store_roundtrip_with_master_key_in_keychain_only(tmp_path):
    kc = FakeKeychain()
    store = ss.KeychainSecretStore(tmp_path / "secrets", runner=kc)
    store.set("sync/key", "cheie-secreta-123")
    assert store.get("sync/key") == "cheie-secreta-123" and store.exists("sync/key")
    assert store.get("lipsa") is None
    for f in (tmp_path / "secrets").iterdir():
        assert b"cheie-secreta-123" not in f.read_bytes()
    assert all("cheie-secreta-123" not in " ".join(c) for c in kc.calls)
    assert len(kc.items) == 1
    again = ss.KeychainSecretStore(tmp_path / "secrets", runner=kc)
    assert again.get("sync/key") == "cheie-secreta-123"
    assert sum(1 for c in kc.calls if c[0] == "add-generic-password") == 1
    store.delete("sync/key")
    assert store.get("sync/key") is None


def test_keychain_store_without_the_master_key_cannot_read_old_secrets(tmp_path):
    ss.KeychainSecretStore(tmp_path / "s", runner=FakeKeychain()).set("a", "valoare")
    other = ss.KeychainSecretStore(tmp_path / "s", runner=FakeKeychain())
    with pytest.raises(ss.SecretStoreError):
        other.get("a")


def test_keychain_unavailable_is_reported_never_silently_plaintext(tmp_path):
    store = ss.KeychainSecretStore(tmp_path / "s", runner=FakeKeychain(fail=1))
    with pytest.raises(ss.SecretStoreUnavailable):
        store.set("a", "x")
    assert not list((tmp_path / "s").glob("*")) if (tmp_path / "s").exists() else True


def test_default_store_on_macos_is_keychain_and_on_linux_still_refuses(monkeypatch, tmp_path):
    monkeypatch.delenv(ss.ENV_BACKEND, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert isinstance(ss.default_store(tmp_path), ss.KeychainSecretStore)
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ss.SecretStoreUnavailable):
        ss.default_store(tmp_path)
    with pytest.raises(ss.SecretStoreUnavailable):
        ss.KeychainSecretStore(tmp_path)


def test_default_store_on_windows_is_still_dpapi(monkeypatch, tmp_path):
    monkeypatch.delenv(ss.ENV_BACKEND, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert isinstance(ss.default_store(tmp_path), ss.DpapiSecretStore)


# ------------------------------------------------------------------ interfață
def test_qss_point_sizes_become_equivalent_pixels_only_on_macos(monkeypatch):
    qss = "* { font-size: 10pt; } QLabel { font-size: 12pt; } QToolTip { font-size: 9px; }"
    monkeypatch.setattr(sys, "platform", "win32")
    assert theme.mac_scale_qss(qss) == qss
    monkeypatch.setattr(sys, "platform", "darwin")
    scaled = theme.mac_scale_qss(qss)
    assert "font-size: 13px" in scaled and "font-size: 16px" in scaled and "9px" in scaled and "pt" not in scaled


def test_theme_font_keeps_windows_family_first():
    assert theme.default_font().family() or True
    assert '"Segoe UI", "Helvetica Neue"' in theme.build_qss(theme.DARK)


def test_backup_window_on_macos_explains_there_is_no_windows_scheduler(tmp_path):
    import subprocess
    code = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(DESKTOP)!r}); sys.path.insert(0, {str(SHARED)!r}); os.chdir({str(DESKTOP)!r})\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication([])\n"
        "import database as db, backup_window\n"
        "db.init_db()\n"
        "sys.platform = 'darwin'\n"
        "win = backup_window.BackupWindow()\n"
        "print('CMD=' + win.txt_command.toPlainText())\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               CIMITIR_DB_ENGINE="sqlite", DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-3000:]
    line = next(ln for ln in done.stdout.splitlines() if ln.startswith("CMD="))
    assert "schtasks" not in line and "macOS" in line


def test_update_check_can_be_disabled_for_the_test_phase(monkeypatch):
    monkeypatch.setenv("CIMITIR_DISABLE_UPDATE_CHECK", "1")
    monkeypatch.setattr(updater, "_checked_once", False)
    assert updater.attach_update_banner(object()) is None
    assert updater._checked_once is False


def test_map_label_fonts_keep_the_windows_size_on_macos_and_are_untouched_elsewhere(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    win = theme.scene_font("Arial", 11)
    assert win.family() == "Arial" and win.pointSize() == 11 and not win.bold()
    assert theme.scene_font("Arial", 10, bold=True).bold()
    monkeypatch.setattr(sys, "platform", "darwin")
    mac = theme.scene_font("Arial", 11)
    assert mac.pixelSize() == 15 and mac.family() == "Arial"          # 11 pt Windows = 14,7 px
    assert theme.scene_font("Arial", 7).pixelSize() == 9 and theme.scene_font("Arial", 10, bold=True).bold()


def test_map_code_uses_scene_font_for_all_scene_labels():
    for name in ("map_editor_actions.py", "map_editor_advanced.py", "ui_mainwindow.py"):
        text = (DESKTOP / name).read_text(encoding="utf-8")
        assert 'QFont("Arial"' not in text and "scene_font(" in text


def _exit_backup_run(tmp_path, body):
    import json
    import subprocess
    code = (
        "import json, os, sys\n"
        f"sys.path.insert(0, {str(DESKTOP)!r}); sys.path.insert(0, {str(SHARED)!r}); os.chdir({str(DESKTOP)!r})\n"
        "from PySide6.QtWidgets import QApplication\napp = QApplication([])\n"
        "import database as db, backup_window, cimitir_backup\ndb.init_db()\n"
        f"db.set_setting('backup_dir', {str(tmp_path / 'bk')!r})\nout = {{}}\n"
        + body + "\nprint('RESULT ' + json.dumps(out, default=str))\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               CIMITIR_DB_ENGINE="sqlite", DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(next(ln for ln in done.stdout.splitlines() if ln.startswith("RESULT "))[7:])


def test_exit_backup_on_macos_makes_one_verified_copy_and_skips_repeats_within_the_hour(tmp_path):
    out = _exit_backup_run(tmp_path, """
sys.platform = 'darwin'
first = backup_window.run_exit_backup()
second = backup_window.run_exit_backup()
out['first'] = first['result']; out['second'] = second['result']; out['trigger'] = first.get('trigger')
out['copies'] = len(os.listdir(os.path.join(os.path.dirname(os.environ['CIMITIR_DATA_DIR']), 'bk')))
out['verified'] = first.get('verified')
""")
    assert out["first"] == "ok" and out["trigger"] == "exit" and out["verified"] is True
    assert out["second"] == "skipped" and out["copies"] == 1


def test_exit_backup_respects_both_off_switches_and_is_inactive_off_macos(tmp_path):
    out = _exit_backup_run(tmp_path, """
out['windows'] = backup_window.run_exit_backup()['result']
sys.platform = 'darwin'
db.set_setting('backup_on_exit', '0'); out['exit_off'] = backup_window.run_exit_backup()['result']
db.set_setting('backup_on_exit', '1'); db.set_setting('backup_enabled', '0'); out['backup_off'] = backup_window.run_exit_backup()['result']
out['bk_exists'] = os.path.exists(os.path.join(os.path.dirname(os.environ['CIMITIR_DATA_DIR']), 'bk'))
""")
    assert out["windows"] == "disabled" and out["exit_off"] == "disabled" and out["backup_off"] == "disabled" and out["bk_exists"] is False


def test_main_runs_the_exit_backup_only_on_the_real_exit_path():
    text = (DESKTOP / "main.py").read_text(encoding="utf-8")
    exit_block = text.index('if not logout_flag["value"]:')
    assert text.index("run_exit_backup()") > exit_block and text.index("run_exit_backup()") < text.index('"app_exit"')
