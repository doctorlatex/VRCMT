import os
import shutil
import sqlite3
import zipfile
import csv
import logging
from datetime import datetime
from src.core.paths import APP_DIR, DB_PATH, CONFIG_PATH, CAPTURES_DIR
from src.db.models import Multimedia

# Hosts que se consideran URLs públicas (no requieren premium para reproducir)
_FREE_HOSTS = ('youtube.com', 'youtu.be', 'twitch.tv', 'kick.com', 'soundcloud.com')


def _is_free_url(url: str) -> bool:
    u = (url or "").lower()
    return any(h in u for h in _FREE_HOSTS)


def _is_fingerprint_url(url: str) -> bool:
    """True si 'url' es un fingerprint Free (sin schema http/https/file).

    Los fingerprints (ej. "2020_elanimal.mp4") no exponen CDN ni path privado;
    se conservan en respaldos para que la deduplicación siga funcionando al restaurar.
    """
    if not url:
        return False
    u = url.lower()
    return not (u.startswith("http://") or u.startswith("https://") or u.startswith("file://"))


class BackupManager:
    def __init__(self):
        self.backup_dir = os.path.join(APP_DIR, 'backups')
        os.makedirs(self.backup_dir, exist_ok=True)

    def export_full_backup(self, is_premium: bool = True) -> str:
        """Crea un paquete ZIP con DB, Config, Capturas y CSV de Letterboxd (v2.9).
        Si is_premium=False, se exporta la BD con las URLs privadas en blanco para
        no exponer enlaces de contenido premium en el archivo de respaldo.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"VRCMT_Backup_{timestamp}.zip"
        backup_path = os.path.join(self.backup_dir, backup_filename)

        # 1. Generar CSV de Letterboxd temporalmente
        csv_path = self.generate_letterboxd_csv()

        # 2. Si el usuario es Free, hacer una copia sanitizada de la BD
        db_to_zip = DB_PATH
        sanitized_db = None
        if not is_premium and os.path.exists(DB_PATH):
            sanitized_db = os.path.join(self.backup_dir, f"_tmp_sanitized_{timestamp}.db")
            try:
                shutil.copy2(DB_PATH, sanitized_db)
                con = sqlite3.connect(sanitized_db)
                cur = con.cursor()
                # Limpiar URLs privadas: solo conservar las de plataformas públicas
                cur.execute("SELECT id, url FROM multimedia")
                rows = cur.fetchall()
                for row_id, url in rows:
                    # Conservar: URLs de plataformas públicas (YouTube, Twitch…) y
                    # fingerprints Free (nombre de archivo sin schema, ej. "pelicula.mp4").
                    # Borrar solo las URLs privadas completas (http/https con dominio propio).
                    if url and not _is_free_url(url) and not _is_fingerprint_url(url):
                        cur.execute("UPDATE multimedia SET url='' WHERE id=?", (row_id,))
                con.commit()
                con.close()
                db_to_zip = sanitized_db
                logging.info("🔒 Respaldo de usuario Free: URLs privadas eliminadas de la copia exportada.")
            except Exception as e:
                logging.error(f"Error sanitizando BD para respaldo Free: {e}")
                db_to_zip = DB_PATH  # fallback al original

        try:
            with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Agregar Base de Datos (sanitizada si Free)
                if os.path.exists(db_to_zip):
                    zipf.write(db_to_zip, arcname='catalogo_v2.db')

                # Agregar Configuración
                if os.path.exists(CONFIG_PATH):
                    zipf.write(CONFIG_PATH, arcname='config.json')

                # Agregar CSV de Letterboxd
                if csv_path and os.path.exists(csv_path):
                    zipf.write(csv_path, arcname='history_letterboxd.csv')

                # Agregar Carpeta de Capturas (Lo más valioso)
                if os.path.exists(CAPTURES_DIR):
                    for root, dirs, files in os.walk(CAPTURES_DIR):
                        for file in files:
                            full_path = os.path.join(root, file)
                            rel_path = os.path.relpath(full_path, APP_DIR)
                            zipf.write(full_path, arcname=rel_path)

            logging.info(f"🎁 Respaldo universal creado: {backup_filename}")
            if csv_path: os.remove(csv_path)
            return backup_path

        except Exception as e:
            logging.error(f"Error creando respaldo: {e}")
            return ""
        finally:
            if sanitized_db and os.path.exists(sanitized_db):
                try:
                    os.remove(sanitized_db)
                except OSError:
                    pass

    def generate_letterboxd_csv(self) -> str:
        """Genera un CSV compatible con Letterboxd (Solo títulos con IMDb ID) (v2.10)."""
        csv_filename = "temp_letterboxd.csv"
        csv_path = os.path.join(self.backup_dir, csv_filename)
        
        try:
            # Filtrar solo títulos con imdb_id (Regla v2.10)
            items = Multimedia.select().where(Multimedia.imdb_id.is_null(False))
            
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                # Cabeceras estándar de Letterboxd
                writer.writerow(['Title', 'Year', 'Rating', 'WatchedDate', 'imdbID'])
                
                for item in items:
                    # Asegurar formato ttXXXXXXX
                    imdb_id = item.imdb_id
                    if imdb_id and not str(imdb_id).startswith('tt'):
                        imdb_id = f"tt{str(imdb_id).zfill(7)}"
                        
                    writer.writerow([
                        item.titulo,
                        item.año,
                        item.calificacion_personal,
                        item.ultimo_visto.strftime("%Y-%m-%d"),
                        imdb_id
                    ])
            
            logging.info(f"📄 CSV de Letterboxd generado con {len(items)} títulos.")
            return csv_path
        except Exception as e:
            logging.error(f"Error generando CSV: {e}")
            return ""

    def import_backup(self, zip_path: str) -> bool:
        """Restaura un respaldo ZIP en la carpeta de AppData (v2.7)."""
        if not os.path.exists(zip_path): return False
        
        try:
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                zipf.extractall(APP_DIR)
            logging.info("✨ Respaldo importado y restaurado exitosamente.")
            return True
        except Exception as e:
            logging.error(f"Error importando respaldo: {e}")
            return False
