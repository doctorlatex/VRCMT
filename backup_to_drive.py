"""
backup_to_drive.py — Respaldo del codigo fuente de VRCMT a Google Drive
Usa la cuenta de servicio de Firebase (cred.json) para subir un ZIP al folder de Drive.

REQUISITO UNICO (una sola vez):
  Comparte la carpeta de Google Drive con el email de la cuenta de servicio:
  firebase-adminsdk-fbsvc@vrcmt-75823.iam.gserviceaccount.com
  (Permiso: Editor)

Uso:
  python backup_to_drive.py
"""
import os
import sys
import zipfile
import datetime
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
log = logging.getLogger("backup")

DRIVE_FOLDER_ID = "1FTaqPOrLN2F7PXYwO1MM9oo1NzLwkUPV"
CRED_FILE       = os.path.join(os.path.dirname(__file__), "cred.json")
PROJECT_ROOT    = os.path.dirname(__file__)

# Carpetas y archivos a excluir del ZIP
EXCLUDE_DIRS  = {".git", "__pycache__", "dist", "build", ".venv", "venv",
                 "node_modules", ".mypy_cache", ".pytest_cache",
                 "backups", "debug-captures", "_backup_2023", "_backup_2024",
                 "_backup_2025", "_backup_2026"}
EXCLUDE_EXTS  = {".pyc", ".pyo", ".exe", ".dll", ".so", ".egg-info", ".zip"}
EXCLUDE_FILES = {"backup_to_drive.py"}


def _make_zip() -> str:
    """Crea un ZIP del proyecto y retorna la ruta del archivo."""
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
    out  = os.path.join(os.path.expanduser("~"), "AppData", "Local", "VRCMT", "backups", name)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    log.info("Creando ZIP: %s", out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(PROJECT_ROOT):
            # Filtrar carpetas excluidas in-place (tambien excluir _backup_* dinamicamente)
            dirs[:] = [d for d in dirs
                       if d not in EXCLUDE_DIRS and not d.startswith("_backup_")]
            for fname in files:
                if fname in EXCLUDE_FILES:
                    continue
                _, ext = os.path.splitext(fname)
                if ext in EXCLUDE_EXTS:
                    continue
                fpath  = os.path.join(root, fname)
                arcname = os.path.relpath(fpath, PROJECT_ROOT)
                zf.write(fpath, arcname)
    log.info("ZIP listo: %.1f MB", os.path.getsize(out) / 1024 / 1024)
    return out


def _upload_to_drive(zip_path: str) -> str:
    """Sube el ZIP a Google Drive y retorna la URL del archivo."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        log.error("Instala: pip install google-api-python-client google-auth")
        sys.exit(1)

    SCOPES = ["https://www.googleapis.com/auth/drive.file"]
    creds  = service_account.Credentials.from_service_account_file(CRED_FILE, scopes=SCOPES)
    svc    = build("drive", "v3", credentials=creds, cache_discovery=False)

    fname    = os.path.basename(zip_path)
    metadata = {"name": fname, "parents": [DRIVE_FOLDER_ID]}
    media    = MediaFileUpload(zip_path, mimetype="application/zip", resumable=True)

    log.info("Subiendo a Google Drive...")
    resp = svc.files().create(body=metadata, media_body=media, fields="id,name,webViewLink").execute()
    url  = resp.get("webViewLink", f"https://drive.google.com/file/d/{resp['id']}")
    log.info("✅  Subido correctamente: %s", url)
    return url


def _cleanup_old_backups(keep: int = 5):
    """Elimina backups locales antiguos, dejando solo los N más recientes."""
    backup_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local", "VRCMT", "backups")
    if not os.path.isdir(backup_dir):
        return
    zips = sorted(
        [f for f in os.listdir(backup_dir) if f.endswith(".zip")],
        reverse=True
    )
    for old in zips[keep:]:
        os.remove(os.path.join(backup_dir, old))
        log.info("Backup antiguo eliminado: %s", old)


if __name__ == "__main__":
    zip_path = _make_zip()
    _upload_to_drive(zip_path)
    _cleanup_old_backups(keep=5)
    log.info("Backup completado.")
