"""Configurare AI persistentă (cheie Gemini + mod de lucru + model).

Stocare per-utilizator, în afara folderului aplicației, ca să NU se piardă la update:
  * Windows: %APPDATA%\\CimitirDesktop\\ai_config.json
  * Linux/Mac (fallback pentru testare): ~/.config/CimitirDesktop/ai_config.json

Fișier JSON (UTF-8):
{
  "api_key": "AQ.xxxxx",        # cheia Google AI Studio (goală => mod offline)
  "mode":    "ai" | "offline",  # ai = Gemini + rezervă offline; offline = doar Tesseract
  "model":   "auto" | "gemini-flash-latest" | ...,
  "provider": "google"
}

Notă de securitate: cheia se maschează în interfață și în loguri (vezi `mask_key`).
"""
from __future__ import annotations

import base64
import functools
import hashlib
import json
import os
import platform
from pathlib import Path
from typing import Any, Dict, Optional

APP_FOLDER_NAME = "CimitirDesktop"
CONFIG_FILE_NAME = "ai_config.json"

MODE_AI = "ai"
MODE_OFFLINE = "offline"
MODEL_AUTO = "auto"

_DEFAULT_CONFIG: Dict[str, Any] = {
    "api_key": "",
    "mode": MODE_AI,
    "model": MODEL_AUTO,
    "provider": "google",
}


def _config_dir() -> Path:
    """Directorul de configurare per-utilizator (creat automat)."""
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        d = Path(base) / APP_FOLDER_NAME
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        d = Path(base) / APP_FOLDER_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_DIR = _config_dir()
CONFIG_FILE = CONFIG_DIR / CONFIG_FILE_NAME


# ============ MASCARE ============

def mask_key(key: Optional[str]) -> str:
    """Returnează o formă mascată a cheii pentru afișare/loguri (ex: `AQ.****abcd`)."""
    key = (key or "").strip()
    if not key:
        return "(nicio cheie)"
    if len(key) <= 8:
        return key[:2] + "****"
    prefix = "AQ." if key.startswith("AQ.") else key[:4]
    return f"{prefix}****{key[-4:]}"


# ============ CRIPTARE CHEIE (legată de calculatorul curent) ============

_ENC_PREFIX = "enc:v1:"
_OBF_PREFIX = "b64:v1:"
_APP_SALT = b"CimitirDesktop.ai_config.v1|"


@functools.lru_cache(maxsize=1)
def _machine_secret() -> bytes:
    """Material secret stabil per calculator (machine-id/GUID + utilizator)."""
    parts = []
    if platform.system() == "Windows":
        try:
            import winreg  # type: ignore
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as k:
                parts.append(str(winreg.QueryValueEx(k, "MachineGuid")[0]))
        except Exception:
            pass
    else:
        for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                v = Path(p).read_text().strip()
                if v:
                    parts.append(v)
                    break
            except Exception:
                pass
    parts.append(os.environ.get("USERNAME") or os.environ.get("USER") or "user")
    return _APP_SALT + "|".join(parts).encode("utf-8")


def encrypt_key(plain: str) -> str:
    """Criptează cheia pentru stocare pe disc (Fernet, legată de calculator)."""
    plain = (plain or "").strip()
    if not plain:
        return ""
    try:
        from cryptography.fernet import Fernet
        fkey = base64.urlsafe_b64encode(hashlib.sha256(_machine_secret()).digest())
        return _ENC_PREFIX + Fernet(fkey).encrypt(plain.encode("utf-8")).decode("ascii")
    except Exception:  # cryptography indisponibil => măcar obfuscare
        return _OBF_PREFIX + base64.b64encode(plain.encode("utf-8")).decode("ascii")


def decrypt_key(stored: str) -> str:
    """Decriptează valoarea din fișier; la eșec (alt calculator/corupt) => ''."""
    stored = (stored or "").strip()
    if not stored:
        return ""
    if stored.startswith(_ENC_PREFIX):
        try:
            from cryptography.fernet import Fernet
            fkey = base64.urlsafe_b64encode(hashlib.sha256(_machine_secret()).digest())
            return Fernet(fkey).decrypt(stored[len(_ENC_PREFIX):].encode("ascii")).decode("utf-8")
        except Exception as exc:
            print(f"[ai_config] Cheia criptată nu poate fi decriptată (alt calculator?): {exc}")
            return ""
    if stored.startswith(_OBF_PREFIX):
        try:
            return base64.b64decode(stored[len(_OBF_PREFIX):]).decode("utf-8")
        except Exception:
            return ""
    return stored  # format vechi: text simplu


# ============ CITIRE / SCRIERE ============

def load_config() -> Dict[str, Any]:
    """Citește configul; dacă lipsește sau e corupt, întoarce valorile implicite."""
    cfg = dict(_DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: data.get(k, cfg[k]) for k in cfg})
                # câmpuri extra (ex. model detectat) le păstrăm și pe cele necunoscute
                for k, v in data.items():
                    cfg.setdefault(k, v)
    except Exception as exc:  # config corupt => revenim la implicit, fără să crăpăm
        print(f"[ai_config] Nu am putut citi {CONFIG_FILE.name}: {exc}")
    # decriptare cheie (în memorie lucrăm mereu cu textul simplu)
    enc = str(cfg.pop("api_key_enc", "") or "")
    plain_in_file = (cfg.get("api_key") or "").strip()
    if plain_in_file:
        cfg["api_key"] = plain_in_file
        try:  # migrare: fișier vechi cu cheia în clar => rescriem criptat
            save_config(cfg)
        except Exception as exc:
            print(f"[ai_config] Migrarea cheii la format criptat a eșuat: {exc}")
    elif enc:
        cfg["api_key"] = decrypt_key(enc)
    # normalizare
    if cfg.get("mode") not in (MODE_AI, MODE_OFFLINE):
        cfg["mode"] = MODE_AI
    if not (cfg.get("model") or "").strip():
        cfg["model"] = MODEL_AUTO
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    """Scrie configul pe disc (UTF-8, cu newline final corect)."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(_DEFAULT_CONFIG)
    payload.update({k: cfg.get(k, payload.get(k)) for k in set(_DEFAULT_CONFIG) | set(cfg)})
    plain = (payload.get("api_key") or "").strip()
    payload["api_key_enc"] = encrypt_key(plain)
    payload["api_key"] = ""  # niciodată în clar pe disc
    CONFIG_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[ai_config] Salvat {CONFIG_FILE} (cheie={mask_key(plain)}, "
          f"mod={payload.get('mode')}, model={payload.get('model')})")


# ============ ACCES RAPID ============

def get_api_key() -> str:
    """Cheia Google din config; fallback pe variabile de mediu dacă lipsește."""
    key = (load_config().get("api_key") or "").strip()
    if key:
        return key
    for env_name in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_AI_API_KEY"):
        v = os.environ.get(env_name, "").strip()
        if v:
            return v
    return ""


def get_mode() -> str:
    return load_config().get("mode", MODE_AI)


def get_model() -> str:
    return load_config().get("model", MODEL_AUTO) or MODEL_AUTO


def set_api_key(api_key: str) -> None:
    cfg = load_config()
    cfg["api_key"] = (api_key or "").strip()
    save_config(cfg)


def set_mode(mode: str) -> None:
    cfg = load_config()
    cfg["mode"] = mode if mode in (MODE_AI, MODE_OFFLINE) else MODE_AI
    save_config(cfg)


def set_model(model: str) -> None:
    cfg = load_config()
    cfg["model"] = (model or MODEL_AUTO).strip() or MODEL_AUTO
    save_config(cfg)


def clear_key() -> None:
    """Șterge cheia și trece aplicația în mod offline."""
    cfg = load_config()
    cfg["api_key"] = ""
    cfg["mode"] = MODE_OFFLINE
    cfg["model"] = MODEL_AUTO
    save_config(cfg)


def is_ai_enabled() -> bool:
    """AI activ dacă modul e `ai` ȘI există o cheie (config sau env)."""
    return get_mode() == MODE_AI and bool(get_api_key())


def status_text() -> str:
    """Text scurt pentru indicatorul de stare din interfață."""
    if is_ai_enabled():
        model = get_model()
        suffix = "" if model == MODEL_AUTO else f" · {model}"
        return f"AI activ (Gemini{suffix})"
    return "Mod offline"


__all__ = [
    "CONFIG_DIR", "CONFIG_FILE", "MODE_AI", "MODE_OFFLINE", "MODEL_AUTO",
    "load_config", "save_config", "mask_key", "encrypt_key", "decrypt_key",
    "get_api_key", "get_mode", "get_model",
    "set_api_key", "set_mode", "set_model", "clear_key",
    "is_ai_enabled", "status_text",
]
