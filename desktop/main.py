"""
Punct de intrare al aplicației Cimitir.

Flow:
    0. License gate (Ed25519 offline): verifică licența salvată local.
       - dacă lipsește / e invalidă / expirată → afișează dialogul de activare
       - la succes → aplicația pornește normal
       - locație licență: %APPDATA%/CimitirDesktop/license.key (Windows)
    1. Aplică tema modernă (dark)
    2. Init DB + init auth (creează users default la prima rulare)
    3. Login modal
    4. La login OK:
        - role == 'admin' → deschide CemeteryUI (developer mode, editor complet)
        - role == 'user'  → deschide UserModeWindow (read-only, cu sidebar)
    5. Când user face logout, reîntoarce la login
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shared"))
import cimitir_backup
import cimitir_config

RESTORE_MESSAGE = cimitir_backup.apply_pending_restore(cimitir_config.working_dir())

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from database import init_db, DB_PATH
from theme import apply_theme
import auth
from app_logging import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)


def _run_license_gate(app: QApplication):
    """
    License gate (Ed25519 offline). Blochează pornirea dacă licența nu e validă
    și utilizatorul nu activează una nouă în dialog.

    Return: RezultatVerificare cu info despre licență (client, expira, hwid).
    """
    try:
        from license import ensure_valid_license
    except ImportError as e:
        QMessageBox.critical(
            None, "Cimitir — Eroare Licențiere",
            f"Modulul de licențiere lipsește sau nu poate fi încărcat:\n{e}"
        )
        sys.exit(1)

    ok, info = ensure_valid_license(app)
    if not ok:
        QMessageBox.critical(
            None, "Cimitir — Licență necesară",
            f"Aplicația nu poate porni fără o licență validă.\n\nMotiv: {info.motiv}"
        )
        sys.exit(1)
    return info


def _run_login(app):
    """Deschide fereastra de login. Returnează user dict sau None."""
    from login_window import LoginWindow
    dlg = LoginWindow()
    if dlg.exec() == QDialog.Accepted:
        return dlg.user
    return None


def _choose_registry(app, force_selector: bool = False) -> bool:
    """Activează registrul local: automat (un singur registru + opțiunea bifată) sau prin selector.
    True = continuă (registru activ sau baza veche, la alegerea utilizatorului); False = a renunțat."""
    import cimitir_registry as reg
    data_dir = cimitir_config.data_dir()
    if not force_selector:
        plan = reg.startup_plan(data_dir)
        if plan["action"] == "auto":
            try:
                reg.activate(plan["cemetery_uid"], data_dir)
                return True
            except reg.RegistryError as exc:
                logger.warning("Deschiderea automată a eșuat: %s", exc)
    from registry_selector import RegistrySelector
    dlg = RegistrySelector(data_dir=data_dir)
    if dlg.exec() != QDialog.Accepted:
        return False
    if dlg.legacy_chosen:
        reg.deactivate()
        return True
    return dlg.binding is not None


def main() -> int:
    print(f"[main] DB path: {DB_PATH}")
    logger.info("Cimitir Desktop pornit — DB: %s", DB_PATH)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Cimitir")
    apply_theme(app, mode="dark")
    if RESTORE_MESSAGE:
        logger.warning(RESTORE_MESSAGE)
        QMessageBox.information(None, "Cimitir — Restaurare", RESTORE_MESSAGE)

    # ---- LICENSE GATE ----
    license_info = _run_license_gate(app)
    print(f"[main] License OK: client={license_info.client!r} expira={license_info.expira or 'permanent'}")

    # ---- REGISTRU LOCAL (Etapa 3): Organizație -> Cimitir; apoi DB + AUTH pe registrul ales ----
    force_selector = False
    while True:
        if not _choose_registry(app, force_selector):
            return 0
        force_selector = False
        init_db()
        auth.init_auth()

        # audit: app start (dacă modulul security vechi există, altfel skip)
        try:
            from security import audit_log
            audit_log.log("system", "app_start", meta={"client": license_info.client})
        except Exception:
            pass

        switch_registry = False
        while True:
            user = _run_login(app)
            if not user:
                return 0
            if auth.password_change_required(user["id"]):
                QMessageBox.warning(None, "Cimitir — Parolă implicită",
                                    "Contul folosește încă o parolă implicită. Schimbă parola la autentificare.")
                continue

            if auth.RIGHT_EDIT in user.get("rights", []):
                from ui_mainwindow import CemeteryUI
                window = CemeteryUI(current_user=user)
            else:
                from user_mode_window import UserModeWindow
                window = UserModeWindow(user=user)

            logout_flag = {"value": False, "switch": False}
            try:
                window.logout_requested.connect(lambda: logout_flag.update(value=True))
                window.registry_switch_requested.connect(lambda: logout_flag.update(switch=True))
            except Exception:
                pass

            window.show()
            try:
                from updater import attach_update_banner
                attach_update_banner(window)
            except Exception as exc:  # verificarea de update nu blochează niciodată pornirea
                print(f"[main] verificare update sărită: {exc}")
            app.exec()

            if logout_flag["switch"]:
                switch_registry = True
                break
            if not logout_flag["value"]:
                try:
                    from security import audit_log
                    audit_log.log(user.get("username", "?"), "app_exit")
                except Exception:
                    pass
                return 0
        if switch_registry:
            force_selector = True


if __name__ == "__main__":
    sys.exit(main())
