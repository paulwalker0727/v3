"""Modul AI Scanare Documente — Gemini (cheie proprie Google) + rezervă offline."""
from .ai_client import extract_document_sync, DocumentType, active_engine_status
from .db_scans import init_scan_history, save_scan, list_scans, update_scan_status, delete_scan
from . import config

__all__ = [
    "extract_document_sync",
    "DocumentType",
    "active_engine_status",
    "config",
    "init_scan_history",
    "save_scan",
    "list_scans",
    "update_scan_status",
    "delete_scan",
]
