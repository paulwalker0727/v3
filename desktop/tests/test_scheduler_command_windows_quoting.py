"""Comanda `schtasks /Create ... /TR` pentru copia programată: ghilimele, căi cu spații, backslash final, --data-dir.
Nu rulează Windows/schtasks (NECONFIRMAT pe Windows real): se emulează regulile CommandLineToArgvW pe șirul rezultat."""
import re
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
for p in (DESKTOP, DESKTOP.parent / "shared"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def argv_windows(cmd: str):
    """Implementare a regulilor CommandLineToArgvW (2n backslash+\" -> n backslash și închide/deschide; 2n+1 -> n backslash + \" literal)."""
    out, cur, i, inq, has = [], [], 0, False, False
    while i < len(cmd):
        c = cmd[i]
        if c == "\\":
            j = i
            while j < len(cmd) and cmd[j] == "\\":
                j += 1
            n = j - i
            if j < len(cmd) and cmd[j] == '"':
                cur.append("\\" * (n // 2)); has = True
                if n % 2:
                    cur.append('"'); i = j + 1
                else:
                    inq = not inq; i = j + 1
            else:
                cur.append("\\" * n); has = True; i = j
            continue
        if c == '"':
            inq = not inq; has = True
        elif c in " \t" and not inq:
            if has:
                out.append("".join(cur)); cur, has = [], False
        else:
            cur.append(c); has = True
        i += 1
    if has:
        out.append("".join(cur))
    return out


def task_run_of(schtasks_cmd: str) -> str:
    m = re.search(r'/TR "(.*)" /F$', schtasks_cmd)
    assert m, schtasks_cmd
    return m.group(1).replace('\\"', '"')          # schtasks: \" în /TR devine " în comanda memorată


@pytest.mark.parametrize("data_dir", [r"C:\Program Files\Cimitir Date", "D:\\", r"C:\Date cimitir\\", r"\\server\share\Cimitir Date"])
def test_task_command_roundtrips_to_exact_arguments(monkeypatch, data_dir):
    import backup_window as bw
    monkeypatch.setattr(bw.cimitir_config, "working_dir", lambda: data_dir)
    monkeypatch.setattr(bw.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bw.sys, "executable", r"C:\Program Files\Cimitir\Cimitir.exe")
    cmd = bw.scheduler_command("02:00")
    argv = argv_windows(task_run_of(cmd))
    assert argv[0] == r"C:\Program Files\Cimitir\Cimitir.exe"
    assert argv[1] == "--backup-run" and argv[2] == "--data-dir"
    assert argv[3] == data_dir and len(argv) == 4
    assert cmd.startswith('schtasks /Create /TN "Cimitir - Copie de rezerva" /SC DAILY /ST 02:00 /TR ') and cmd.endswith("/F")


def test_script_variant_has_script_and_data_dir(monkeypatch):
    import backup_window as bw
    monkeypatch.setattr(bw.cimitir_config, "working_dir", lambda: r"C:\Cimitir Date")
    monkeypatch.setattr(bw.sys, "frozen", False, raising=False)
    monkeypatch.setattr(bw.sys, "executable", r"C:\Python 311\python.exe")
    argv = argv_windows(task_run_of(bw.scheduler_command("23:30")))
    assert argv[0] == r"C:\Python 311\python.exe" and argv[1].endswith("backup_scheduled.py") and argv[2:] == ["--data-dir", r"C:\Cimitir Date"]


def test_documented_limit_tr_length_is_checked_by_the_test_not_hidden(monkeypatch):
    """schtasks /TR acceptă cel mult 261 de caractere; o cale foarte lungă trebuie raportată, nu ignorată."""
    import backup_window as bw
    monkeypatch.setattr(bw.cimitir_config, "working_dir", lambda: r"C:\Program Files\Cimitir Date")
    monkeypatch.setattr(bw.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bw.sys, "executable", r"C:\Program Files\Cimitir\Cimitir.exe")
    assert len(task_run_of(bw.scheduler_command("02:00"))) <= 261
