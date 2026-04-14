import json
import os
import logging
from src.core.paths import CONFIG_PATH, resource_path

class ConfigManager:
    # Senior Security: Clave maestra interna (No se muestra en UI)
    MASTER_KEY = '9d60335276532da9905a120bfc8ae8db'

    def __init__(self):
        self.config = self.load_config()
        self.lang_data = {}
        self.load_language(self.config.get('language', 'Español'))

    def load_config(self):
        default_log_dir = os.path.expandvars(r'%USERPROFILE%\AppData\LocalLow\VRChat\VRChat')
        defaults = {
            'language': 'Español',
            'sort_order': 'recent_watch',
            'tmdb_api_key': '', # Campo vacío por defecto para el usuario
            'log_dir': default_log_dir,
            'auto_save_interval': 30,
            'theme': 'Dark',
            # Stub yt-dlp VRChat (ver docs/VRCHAT_STUB.md)
            'vrchat_stub_enabled': False,
            'vrchat_stub_manifest_url': '',
            'vrchat_stub_cookies_path': '',
            'vrchat_ytdlp_target_path': '',
            'vrchat_stub_github_token': '',
            'vrchat_stub_restore_on_exit': False,
            # Cerrar ventana: minimizar a bandeja solo si está activado (si no, salida completa).
            # Close window: minimize to tray only when enabled (otherwise full exit).
            'minimize_to_tray_on_close': False,
        }
        
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    user_cfg = json.load(f)
                    # Senior Security Fix: Saneamiento radical
                    # Si el archivo del usuario aún tiene la Master Key vieja, la borramos de la vista
                    if user_cfg.get('tmdb_api_key') == self.MASTER_KEY:
                        user_cfg['tmdb_api_key'] = ''
                    defaults.update(user_cfg)
            except Exception as e:
                logging.warning(f"No se pudo leer config.json, usando valores por defecto: {e}")
        return defaults

    def get_val(self, key, default=None):
        return self.config.get(key, default)

    def save_config(self, key, value):
        self.config[key] = value
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            logging.error(f"Error guardando config: {e}")

    def load_language(self, language):
        # --- MEJORA v2.11.52: CARGA LOCAL E INDEPENDIENTE ---
        lang_code = "es" if language == "Español" else "en"
        lang_file = f"{lang_code}.json"

        # Intentar cargar desde la carpeta assets de la aplicación actual
        from src.core.paths import resource_path
        local_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets", lang_file)

        # Si no existe, intentar ruta de recursos empaquetados
        if not os.path.exists(local_path):
            local_path = resource_path(os.path.join("assets", lang_file))

        try:
            if os.path.exists(local_path):
                with open(local_path, 'r', encoding='utf-8') as f:
                    self.lang_data = json.load(f)
                logging.info(f"🌎 Idioma cargado: {language} ({local_path})")
            else:
                logging.warning(f"⚠️ No se encontró el archivo de idioma local: {local_path}")
                # Fallback a un diccionario vacío si falla todo
                self.lang_data = {}
        except Exception as e:
            logging.error(f"❌ Error cargando idioma: {e}")

    def tr(self, key, default=None):
        """Función de traducción rápida (v2.11.52)"""
        return self.lang_data.get(key, default or key)
    def get(self, key, default=""):
        return self.lang_data.get(key, default)
