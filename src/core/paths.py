import os
import sys

def get_app_data_dir():
    """Retorna la ruta maestra en AppData/Local/VRCMT."""
    if sys.platform == 'win32':
        base_dir = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
    else:
        base_dir = os.path.expanduser('~')
    
    app_dir = os.path.join(base_dir, 'VRCMT')
    
    # Crear estructura de carpetas necesaria
    subdirs = ['posters', 'captures', 'backups', 'logs']
    for sd in subdirs:
        os.makedirs(os.path.join(app_dir, sd), exist_ok=True)
        
    return app_dir

def resource_path(relative_path):
    """Gestiona rutas de archivos internos para el ejecutable portable.
    Cuando está frozen (PyInstaller onefile), busca primero junto al .exe
    para archivos sensibles como cred.json, y luego en _MEIPASS para el resto.
    """
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        external = os.path.join(exe_dir, relative_path)
        if os.path.exists(external):
            return external
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

APP_DIR = get_app_data_dir()
DB_PATH = os.path.join(APP_DIR, 'catalogo_v2.db')
POSTERS_DIR = os.path.join(APP_DIR, 'posters')
CAPTURES_DIR = os.path.join(APP_DIR, 'captures')
CONFIG_PATH = os.path.join(APP_DIR, 'config.json')


def resolve_local_existing_path(pp: str):
    """Ruta local que existe en disco, o None. En Windows prueba prefijo \\\\?\\ si hace falta (rutas largas)."""
    pp = (pp or "").strip()
    if not pp:
        return None
    pl = pp.lower()
    if pl.startswith(("http://", "https://", "file:")):
        return None
    try:
        seq = [pp, os.path.normpath(os.path.expanduser(pp))]
        seen = set()
        for c in seq:
            if not c or c in seen:
                continue
            seen.add(c)
            if os.path.isfile(c):
                return c
        if os.name == "nt":
            base = os.path.normpath(os.path.expanduser(pp))
            try:
                ap = os.path.abspath(base)
                if ap.startswith("\\\\"):
                    longp = "\\\\?\\UNC\\" + ap[2:].replace("/", "\\")
                else:
                    longp = "\\\\?\\" + ap
                if longp not in seen and os.path.isfile(longp):
                    return longp
            except OSError:
                pass
    except OSError:
        pass
    return None
