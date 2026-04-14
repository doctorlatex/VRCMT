"""Detección ligera del proceso VRChat.exe (solo Windows). / Lightweight VRChat.exe process detection (Windows only).

Solo lectura del estado del sistema vía CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS):
no abre el proceso de VRChat, no inyecta código, no escribe en su memoria ni modifica archivos
del juego. Es el mismo tipo de enumeración que usan administradores de tareas ligeros.
Read-only system snapshot: does not open/inject/write to VRChat or touch game files.

[ES] Ignora VRChat.exe bajo steamapps\\downloading (actualización de Steam en curso) para no
     tratarlo como “juego abierto” (companion / RPC).
[EN] Ignores VRChat.exe under steamapps\\downloading (Steam update in progress) so it is not
     treated as “game running” (companion / RPC).
"""
from __future__ import annotations

import logging
import sys

# Intervalo recomendado entre llamadas desde el motor (segundos). Evita sondeo agresivo.
# Recommended minimum seconds between calls from the engine (avoids aggressive polling).
MIN_POLL_INTERVAL_SECONDS = 6


def _norm_path(p: str) -> str:
    return (p or "").replace("/", "\\").lower()


def _path_is_steam_downloading_folder(path: str) -> bool:
    """Steam descarga/parchea aquí; no es la instalación jugable. / Steam patches here; not playable install."""
    return "steamapps\\downloading" in _norm_path(path)


def _path_suspicious_temp_vrchat(path: str) -> bool:
    """vrchat.exe en carpetas temp sin ruta de instalación conocida (sospechoso)."""
    n = _norm_path(path)
    if "steamapps\\common\\vrchat" in n or "vrchat-vrchat" in n:
        return False
    return "appdata\\local\\temp" in n or "\\temp\\" in n or "\\tmp\\" in n


def _query_full_process_image(pid: int) -> str:
    """Ruta completa del .exe del proceso, o cadena vacía si no se puede leer."""
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return ""
            return (buf.value or "").strip()
        finally:
            k32.CloseHandle(h)
    except Exception:
        return ""


def is_vrchat_running() -> bool:
    """True si hay VRChat.exe jugable (no solo carpeta ``downloading`` de Steam).

    En error grave al enumerar, True (no limpiar RPC por seguridad).
    On serious enumeration error, True (do not clear RPC for safety).
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002

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
        vrchat_pids: list[int] = []
        try:
            if not k32.Process32FirstW(snap, ctypes.byref(pe)):
                return True
            while True:
                name = (pe.szExeFile or "").lower()
                if name == "vrchat.exe":
                    vrchat_pids.append(int(pe.th32ProcessID))
                if not k32.Process32NextW(snap, ctypes.byref(pe)):
                    break
        finally:
            k32.CloseHandle(snap)

        if not vrchat_pids:
            return False

        playable = False
        for pid in vrchat_pids:
            img = _query_full_process_image(pid)
            if not img:
                playable = True
                break
            if _path_is_steam_downloading_folder(img):
                continue
            if _path_suspicious_temp_vrchat(img):
                continue
            playable = True
            break

        return playable
    except Exception as e:
        logging.debug("is_vrchat_running: %s", e)
        return True
