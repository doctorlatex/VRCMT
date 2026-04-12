"""
backup_to_drive.py — Respaldo local del codigo fuente de VRCMT
Crea un ZIP del proyecto en la carpeta _backups (excluye exe, dist, debug-captures, etc.)
Mantiene solo los 5 backups mas recientes automaticamente.

Uso:
  python backup_to_drive.py
"""
import os
import zipfile
import datetime
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
log = logging.getLogger("backup")

PROJECT_ROOT  = os.path.dirname(os.path.abspath(__file__))
BACKUPS_DIR   = os.path.join(PROJECT_ROOT, "_backups")
KEEP_BACKUPS  = 5

EXCLUDE_DIRS  = {".git", "__pycache__", "dist", "build", ".venv", "venv",
                 "backups", "debug-captures", "node_modules",
                 ".mypy_cache", ".pytest_cache"}
EXCLUDE_EXTS  = {".pyc", ".pyo", ".exe", ".dll", ".so", ".zip"}


def make_backup() -> str:
    version = "unknown"
    try:
        vc = os.path.join(PROJECT_ROOT, "src", "core", "version_check.py")
        for line in open(vc, encoding="utf-8"):
            if "CURRENT_VERSION" in line and "=" in line:
                version = line.split("=")[1].strip().strip('"\'')
                break
    except Exception:
        pass

    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    name = f"VRCMT_v{version}_backup_{ts}.zip"
    out  = os.path.join(BACKUPS_DIR, name)
    os.makedirs(BACKUPS_DIR, exist_ok=True)

    log.info("Creando backup v%s ...", version)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs
                       if d not in EXCLUDE_DIRS and not d.startswith("_backup")]
            for fname in files:
                _, ext = os.path.splitext(fname)
                if ext in EXCLUDE_EXTS:
                    continue
                fpath = os.path.join(root, fname)
                zf.write(fpath, os.path.relpath(fpath, PROJECT_ROOT))

    size_mb = os.path.getsize(out) / 1024 / 1024
    log.info("Backup listo: %.1f MB  →  %s", size_mb, out)

    # Limpiar backups antiguos
    zips = sorted([f for f in os.listdir(BACKUPS_DIR) if f.endswith(".zip")], reverse=True)
    for old in zips[KEEP_BACKUPS:]:
        os.remove(os.path.join(BACKUPS_DIR, old))
        log.info("Antiguo eliminado: %s", old)

    return out


if __name__ == "__main__":
    make_backup()
    log.info("Backup completado.")
