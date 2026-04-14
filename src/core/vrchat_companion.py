"""
Proceso auxiliar (--vrchat-companion): sin GUI, sin mutex de instancia principal.
Si VRChat está abierto y VRCMT (GUI) no, intenta aplicar el stub yt-dlp y lanza el ejecutable principal.

Auxiliary process (--vrchat-companion): no GUI, not the main single-instance mutex.
When VRChat is running and the main VRCMT window is not, applies stub if enabled and starts VRCMT.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
import time

# Mismo nombre que main.py / Same name as main.py
APP_MUTEX_MAIN = "Global\\VRCMediaTracker_v2_SingleInstance_Mutex"
COMPANION_MUTEX = "Global\\VRCMT_VRChatCompanion_v1"


def _kernel32():
    return ctypes.windll.kernel32


def _main_vrcmt_mutex_held() -> bool:
    """True si otra instancia ya tiene el mutex de la app principal."""
    k32 = _kernel32()
    h = k32.OpenMutexW(0x1F0001, False, APP_MUTEX_MAIN)
    if not h:
        return False
    k32.CloseHandle(h)
    return True


def _companion_already_running() -> bool:
    k32 = _kernel32()
    h = k32.OpenMutexW(0x1F0001, False, COMPANION_MUTEX)
    if h:
        k32.CloseHandle(h)
        return True
    return False


def _read_cfg(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _launch_args_main_gui(cfg: dict | None = None) -> tuple[str | None, list[str]]:
    """(exe, argv_list) para arrancar VRCMT sin --vrchat-companion.
    Frozen: usa `vrcmt_last_executable_path` del config si el archivo existe (portable movido);
    si no, `sys.executable` del companion (mismo binario). No busca en disco.
    Frozen: uses config `vrcmt_last_executable_path` if the file exists (moved portable);
    otherwise companion's sys.executable (same binary). Never scans the disk."""
    cfg = cfg or {}
    if getattr(sys, "frozen", False):
        cand = (cfg.get("vrcmt_last_executable_path") or "").strip().strip('"')
        se = os.path.normpath(os.path.abspath(sys.executable))
        exe = None
        if cand and cand.lower().endswith(".exe") and os.path.isfile(cand):
            exe = os.path.normpath(os.path.abspath(cand))
        if exe is None and os.path.isfile(se):
            exe = se
        if exe is None:
            return None, []
        return exe, [exe]
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(here))
    main_py = os.path.join(project_root, "main.py")
    py = os.path.normpath(os.path.abspath(sys.executable))
    if not os.path.isfile(main_py):
        return None, []
    return py, [py, main_py]


def spawn_companion_if_needed() -> None:
    """Desde la instancia GUI: arranca un único proceso companion si la opción está activa (Windows)."""
    if sys.platform != "win32":
        return
    if _companion_already_running():
        return
    from src.core.paths import CONFIG_PATH

    cfg = _read_cfg(CONFIG_PATH)
    exe, base_args = _launch_args_main_gui(cfg)
    if not base_args:
        logging.warning("Companion: no se pudo resolver ejecutable VRCMT para lanzar el proceso auxiliar.")
        return
    args = list(base_args) + ["--vrchat-companion"]
    _cwd = None
    if getattr(sys, "frozen", False):
        _cwd = os.path.dirname(exe)
    elif len(base_args) >= 2:
        _cwd = os.path.dirname(os.path.abspath(base_args[1]))
    try:
        cr = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        cr |= getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            args,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=cr,
            cwd=_cwd,
        )
        logging.info("🤝 Proceso companion VRChat lanzado (detached)")
    except Exception as e:
        logging.warning("No se pudo lanzar companion VRChat: %s", e)


def run_companion_loop() -> None:
    """Punto de entrada del companion (bloqueante)."""
    if sys.platform != "win32":
        return
    from src.core.paths import CONFIG_PATH
    from src.core.vrchat_process import is_vrchat_running

    log_dir = os.path.join(os.environ.get("LOCALAPPDATA", "."), "VRCMT")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "vrchat_companion.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [companion] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )

    k32 = _kernel32()
    h_own = k32.CreateMutexW(None, False, COMPANION_MUTEX)
    if k32.GetLastError() == 183:
        logging.info("Companion ya en ejecución, saliendo.")
        return

    logging.info("Companion VRChat iniciado.")
    _last_vr = False
    _poll = 5
    _last_spawn_mono = 0.0
    _spawn_cooldown_s = 25.0
    _last_no_exe_warn = 0.0

    try:
        while True:
            cfg = _read_cfg(CONFIG_PATH)
            if not bool(cfg.get("launch_vrcmt_with_vrchat", False)):
                time.sleep(_poll)
                continue

            vr = is_vrchat_running()
            if vr and not _last_vr:
                # VRChat acaba de aparecer: stub lo antes posible (mejora YouTube en VRChat)
                try:
                    from src.core.config import ConfigManager
                    from src.core.vrchat_ytdlp_stub import VRChatYtStubManager

                    cm = ConfigManager()
                    VRChatYtStubManager(cm).ensure_stub_if_enabled()
                except Exception as e:
                    logging.debug("ensure_stub_if_enabled (companion): %s", e)

            _last_vr = vr

            if vr and not _main_vrcmt_mutex_held():
                now = time.time()
                if now - _last_spawn_mono >= _spawn_cooldown_s:
                    exe, base_args = _launch_args_main_gui(cfg)
                    if not base_args or not exe:
                        if now - _last_no_exe_warn >= 60.0:
                            logging.warning(
                                "Companion: ejecutable VRCMT no válido (config o binario actual). "
                                "Abre VRCMT una vez desde la carpeta correcta para actualizar la ruta. "
                                "No se busca en todo el disco."
                            )
                            _last_no_exe_warn = now
                    else:
                        _cwd = None
                        if getattr(sys, "frozen", False):
                            _cwd = os.path.dirname(exe)
                        elif len(base_args) >= 2:
                            _cwd = os.path.dirname(os.path.abspath(base_args[1]))
                        elif base_args:
                            _cwd = os.path.dirname(os.path.abspath(base_args[0]))
                        try:
                            cr = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                            cr |= getattr(subprocess, "DETACHED_PROCESS", 0)
                            subprocess.Popen(
                                base_args,
                                close_fds=True,
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=cr,
                                cwd=_cwd,
                            )
                            _last_spawn_mono = now
                            logging.info("VRCMT principal lanzado desde companion.")
                        except Exception as e:
                            logging.warning("Fallo al lanzar VRCMT: %s", e)

            time.sleep(_poll)
    finally:
        if h_own:
            try:
                k32.CloseHandle(h_own)
            except Exception:
                pass
