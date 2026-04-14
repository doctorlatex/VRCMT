"""Detección ligera del proceso VRChat.exe (solo Windows). / Lightweight VRChat.exe process detection (Windows only).

Solo lectura del estado del sistema vía CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS):
no abre el proceso de VRChat, no inyecta código, no escribe en su memoria ni modifica archivos
del juego. Es el mismo tipo de enumeración que usan administradores de tareas ligeros.
Read-only system snapshot: does not open/inject/write to VRChat or touch game files.
"""
from __future__ import annotations

import logging
import sys

# Intervalo recomendado entre llamadas desde el motor (segundos). Evita sondeo agresivo.
# Recommended minimum seconds between calls from the engine (avoids aggressive polling).
MIN_POLL_INTERVAL_SECONDS = 6


def is_vrchat_running() -> bool:
    """True si existe un proceso VRChat.exe. En error o no-Windows, True (no limpiar RPC por seguridad)."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002

        # Orden exacto según PROCESSENTRY32W (winbase.h)
        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        k32 = ctypes.windll.kernel32
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap is None or snap == -1:
            return True
        try:
            if not k32.Process32FirstW(snap, ctypes.byref(pe)):
                return True
            while True:
                name = (pe.szExeFile or "").lower()
                if name == "vrchat.exe":
                    return True
                if not k32.Process32NextW(snap, ctypes.byref(pe)):
                    break
            return False
        finally:
            k32.CloseHandle(snap)
    except Exception as e:
        logging.debug("is_vrchat_running: %s", e)
        return True
