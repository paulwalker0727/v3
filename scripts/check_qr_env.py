"""Verifică mediul pentru TESTELE de decodare QR (pyzbar + libzbar). Nu afectează generarea QR din aplicație (qrcode/segno).

Exit 0 = decodorul funcționează; 1 = lipsește (mesaj cu pașii de instalare)."""
import platform
import sys


def main() -> int:
    try:
        import pyzbar  # noqa: F401
    except ModuleNotFoundError:
        print("[qr-env] LIPSEȘTE pachetul Python pyzbar:  python -m pip install pyzbar")
        return 1
    try:
        from pyzbar.pyzbar import decode  # noqa: F401
    except ImportError as exc:
        system = platform.system()
        hint = {"Windows": "pe Windows, roata pip `pyzbar` include libzbar-64.dll; dacă lipsește, instalează "
                           "„Visual C++ Redistributable 2013 (x64)” și reinstalează: python -m pip install --force-reinstall pyzbar",
                "Linux": "instalează biblioteca nativă: sudo apt-get install libzbar0",
                "Darwin": "instalează biblioteca nativă: brew install zbar"}.get(system, "instalează biblioteca nativă zbar")
        print(f"[qr-env] pyzbar este instalat, dar libzbar lipsește ({exc}). {hint}")
        return 1
    print("[qr-env] OK: pyzbar + libzbar disponibile.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
