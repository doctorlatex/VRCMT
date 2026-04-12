"""
self_updater.py — Auto-actualizacion del ejecutable VRCMT en Windows
=====================================================================

Flujo:
  1. Descarga VRCMT.exe nuevo a VRCMT_update.exe en la misma carpeta
  2. Escribe un .bat que espera a que VRCMT cierre, reemplaza el exe y lo relanza
  3. Lanza el .bat como proceso desacoplado
  4. La app llama os._exit(0) para cerrarse limpiamente

En Windows no se puede sobreescribir un exe en ejecucion, pero si se puede
RENOMBRAR el actual (el proceso sigue corriendo desde el handle abierto).
Por eso el bat renombra el viejo a .bak y mueve el nuevo al nombre correcto.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import threading
import urllib.request
from typing import Callable, Optional

# URL del release latest en GitHub — siempre apunta al exe mas reciente
_RELEASE_DOWNLOAD_URL = (
    "https://github.com/doctorlatex/VRCMT/releases/latest/download/VRCMT.exe"
)


def _exe_path() -> str:
    """Ruta del ejecutable actual (frozen o script)."""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    # En modo desarrollo, no hay nada que reemplazar
    return ""


def _exe_dir() -> str:
    return os.path.dirname(_exe_path()) if _exe_path() else ""


def download_update(
    on_progress: Optional[Callable[[int, int], None]] = None,
    on_done: Optional[Callable[[bool, str], None]] = None,
    custom_url: str = "",
) -> None:
    """
    Descarga el nuevo exe en un hilo separado.
    on_progress(bytes_downloaded, total_bytes)
    on_done(success, message_or_path)
    """
    url = (custom_url or _RELEASE_DOWNLOAD_URL).strip()

    def _run():
        try:
            exe_dir = _exe_dir()
            if not exe_dir:
                # Modo desarrollo: simular descarga exitosa
                logging.warning("self_updater: modo desarrollo, sin exe real que actualizar")
                if on_done:
                    on_done(False, "Actualización automática solo disponible en el ejecutable .exe")
                return

            dest = os.path.join(exe_dir, "VRCMT_update.exe")
            logging.info("self_updater: descargando %s → %s", url, dest)

            req = urllib.request.Request(url, headers={"User-Agent": "VRCMT-Updater/2.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk = 1024 * 256  # 256 KB
                with open(dest, "wb") as f:
                    while True:
                        data = resp.read(chunk)
                        if not data:
                            break
                        f.write(data)
                        downloaded += len(data)
                        if on_progress:
                            on_progress(downloaded, total)

            logging.info("self_updater: descarga completa (%d bytes)", downloaded)
            if on_done:
                on_done(True, dest)

        except Exception as e:
            logging.error("self_updater: error de descarga: %s", e)
            if on_done:
                on_done(False, str(e))

    threading.Thread(target=_run, daemon=True, name="VRCMT-Updater").start()


def apply_update_and_restart(update_exe_path: str, new_version: str = "") -> None:
    """
    Escribe un script .bat que, una vez que VRCMT.exe cierre:
      1. Espera 3 segundos
      2. Renombra el exe viejo a VRCMT_old.bak (el proceso sigue ok desde su handle)
      3. Si se conoce new_version: mueve VRCMT_update.exe → VRCMTv{new_version}.exe
         Si no:                    mueve VRCMT_update.exe → mismo nombre que tenía el exe viejo
      4. Relanza el nuevo exe
      5. Se autoelimiina

    Luego lanza el .bat completamente desacoplado y llama os._exit(0).
    """
    current_exe = _exe_path()
    if not current_exe:
        logging.warning("self_updater: no hay exe real, cancelando apply_update")
        return

    exe_dir = _exe_dir()
    bat_path = os.path.join(tempfile.gettempdir(), "vrcmt_updater.bat")
    old_bak  = os.path.join(exe_dir, "VRCMT_old.bak")

    # Decidir nombre del nuevo exe:
    # Si tenemos la versión nueva, lo llamamos VRCMTv{new_version}.exe para que sea visible.
    # Si no, mantenemos el mismo nombre que el exe actual (compatibilidad hacia atrás).
    # Decide new exe name:
    # If we have the new version, name it VRCMTv{new_version}.exe so it's clearly versioned.
    # Otherwise keep the same name as the current exe (backward compatibility).
    if new_version:
        new_exe_name = f"VRCMTv{new_version}.exe"
    else:
        new_exe_name = os.path.basename(current_exe)

    new_exe = os.path.join(exe_dir, new_exe_name)

    bat_content = f"""@echo off
chcp 65001 > nul
echo [VRCMT Updater] Esperando cierre de la app...
timeout /t 3 /nobreak > nul

echo [VRCMT Updater] Reemplazando ejecutable...
if exist "{old_bak}" del /f /q "{old_bak}"
rename "{current_exe}" "VRCMT_old.bak"
move /y "{update_exe_path}" "{new_exe}"

echo [VRCMT Updater] Relanzando VRCMT...
start "" "{new_exe}"

echo [VRCMT Updater] Limpiando archivos temporales...
if exist "{old_bak}" del /f /q "{old_bak}"
del /f /q "%~f0"
"""

    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)

    logging.info(
        "self_updater: lanzando updater bat: %s → nuevo exe: %s",
        bat_path, new_exe,
    )

    # Lanzar completamente desacoplado (sin ventana, sin esperar)
    subprocess.Popen(
        ["cmd", "/c", bat_path],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    logging.info("self_updater: cerrando app para que el updater tome el control")
    os._exit(0)
