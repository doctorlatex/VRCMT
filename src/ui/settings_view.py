from PySide6.QtWidgets import (QWidget, QVBoxLayout, QPushButton, QLabel, QFileDialog, 
                             QMessageBox, QFrame, QLineEdit, QHBoxLayout, QScrollArea, QStackedWidget, QProgressBar, QComboBox, QCheckBox, QSizePolicy)
from PySide6.QtCore import Qt, QObject, Signal, QRunnable, QThreadPool
from src.core.backup_manager import BackupManager
import shiboken6 as shiboken
import logging
import os
import subprocess

# --- SEÑALES PARA HILOS SEGUROS ---
class CloudWorkerSignals(QObject):
    finished = Signal(bool, str)
    
class CloudUploadWorker(QRunnable):
    def __init__(self, engine, backup_manager, discord_id):
        super().__init__()
        self.engine = engine
        self.backup_manager = backup_manager
        self.discord_id = discord_id
        self.signals = CloudWorkerSignals()

    def run(self):
        try:
            # Genera el backup localmente (sin Firebase/gRPC) ANTES de salir
            path = self.backup_manager.export_full_backup(is_premium=True)
            if not path:
                msg = self.engine.config.tr('msg_cloud_error', "No se pudo subir a la nube. Revisa tu conexión.")
                self.signals.finished.emit(False, msg)
                return

            discord_id = self.discord_id
            signals    = self.signals
            tr         = self.engine.config.tr

            def _op():
                return self.engine.firebase.upload_backup(discord_id, path)

            def _cb(result, error):
                success = bool(result) and error is None
                if success:
                    msg = tr('msg_cloud_success', "Tu respaldo ha sido guardado de forma segura en la Nube.")
                else:
                    msg = tr('msg_cloud_error', "No se pudo subir a la nube. Revisa tu conexión.")
                signals.finished.emit(success, msg)

            self.engine.firebase.run_firebase_async(_op, _cb)
            # QRunnable sale inmediatamente; _cb emite desde el thread Firebase persistente
        except Exception as e:
            self.signals.finished.emit(False, str(e))


class CloudDownloadWorker(QRunnable):
    def __init__(self, engine, backup_manager, discord_id):
        super().__init__()
        self.engine = engine
        self.backup_manager = backup_manager
        self.discord_id = discord_id
        self.signals = CloudWorkerSignals()

    def run(self):
        import tempfile
        try:
            temp_zip   = os.path.join(tempfile.gettempdir(), f"vrcmt_cloud_{self.discord_id}.zip")
            discord_id = self.discord_id
            signals    = self.signals
            bm         = self.backup_manager
            tr         = self.engine.config.tr

            def _op():
                return self.engine.firebase.download_backup(discord_id, temp_zip)

            def _cb(result, error):
                downloaded = bool(result) and error is None
                if downloaded:
                    if bm.import_backup(temp_zip):
                        msg = tr('msg_cloud_restore_success', "Historial de la nube restaurado. Por favor, reinicia la aplicación.")
                        signals.finished.emit(True, msg)
                    else:
                        msg = tr('msg_restore_error', "El archivo de respaldo PREMIUM parece estar corrupto.")
                        signals.finished.emit(False, msg)
                else:
                    msg = tr('msg_cloud_not_found', "No se encontró ningún respaldo anterior en tu cuenta PREMIUM.")
                    signals.finished.emit(False, msg)

            self.engine.firebase.run_firebase_async(_op, _cb)
            # QRunnable sale inmediatamente; _cb emite desde el thread Firebase persistente
        except Exception as e:
            self.signals.finished.emit(False, str(e))


class StubManifestWorker(QRunnable):
    def __init__(self, engine, force: bool):
        super().__init__()
        self.engine = engine
        self.force = force
        self.signals = CloudWorkerSignals()

    def run(self):
        try:
            from src.core.vrchat_ytdlp_stub import VRChatYtStubManager
            ok, msg = VRChatYtStubManager(self.engine.config).update_from_manifest(force=self.force)
            self.signals.finished.emit(ok, msg)
        except Exception as e:
            self.signals.finished.emit(False, str(e))


class StubInstallFileWorker(QRunnable):
    def __init__(self, engine, path: str):
        super().__init__()
        self.engine = engine
        self.path = path
        self.signals = CloudWorkerSignals()

    def run(self):
        try:
            from src.core.vrchat_ytdlp_stub import VRChatYtStubManager
            ok, msg = VRChatYtStubManager(self.engine.config).install_stub_from_file(self.path)
            self.signals.finished.emit(ok, msg)
        except Exception as e:
            self.signals.finished.emit(False, str(e))


class SettingsView(QWidget):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.backup_manager = BackupManager()
        self.setup_ui()

    def setup_ui(self):
        main_l = QVBoxLayout(self)
        main_l.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        
        container = QWidget()
        container_l = QVBoxLayout(container)
        container_l.setContentsMargins(40, 40, 40, 40)
        container_l.setSpacing(25)

        header = QLabel(self.engine.config.tr('filter_settings', "⚙️ Configuración y Datos"))
        header.setStyleSheet("font-size: 32px; font-weight: bold; color: #fff; margin-bottom: 10px;")
        container_l.addWidget(header)

        # --- SECCIÓN: CONFIGURACIÓN GENERAL ---
        gen_card = QFrame()
        gen_card.setStyleSheet("background-color: #1a1a1a; border-radius: 15px; padding: 20px;")
        gen_l = QVBoxLayout(gen_card)
        
        gen_title = QLabel(self.engine.config.tr('lbl_gen_settings', "🛠️ Configuración General"))
        gen_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1f6aa5;")
        gen_l.addWidget(gen_title)

        # --- MEJORA v2.11.54: SELECTOR DE IDIOMA EXCLUYENTE ---
        gen_l.addWidget(QLabel(self.engine.config.tr('lbl_lang_selection', "🌎 Cambiar Idioma / Change Language:")))
        lang_row = QHBoxLayout()
        self.lang_combo = QComboBox()
        self.current_app_lang = self.engine.config.get_val('language', 'Español')
        
        # Solo añadir el idioma que NO es el actual
        self._update_lang_options()
        
        self.lang_combo.setStyleSheet("background-color: #252525; padding: 8px; border-radius: 5px; color: white;")
        
        self.btn_apply_lang = QPushButton(self.engine.config.tr('btn_apply_lang', "Aplicar / Apply"))
        self.btn_apply_lang.setFixedWidth(120)
        self.btn_apply_lang.setEnabled(False) # Se habilitará al abrir el combo
        self.btn_apply_lang.setStyleSheet("background-color: #333; color: #888; padding: 8px; border-radius: 5px;")
        
        lang_row.addWidget(self.lang_combo)
        lang_row.addWidget(self.btn_apply_lang)
        gen_l.addLayout(lang_row)

        # Conectar validación y eventos (MEJORA v2.11.55: Validación Forzada)
        self.lang_combo.currentIndexChanged.connect(self._validate_lang_change)
        self.btn_apply_lang.clicked.connect(self.on_apply_lang)
        
        # Forzar chequeo inicial
        self._validate_lang_change()

        # Personal TMDb API Key (Senior Security: Masked internal key)
        gen_l.addWidget(QLabel(self.engine.config.tr('lbl_tmdb_key', "🔑 Clave Personal TMDb API (Opcional):")))
        user_api_key = self.engine.config.get_val('tmdb_api_key', '')
        self.api_input = QLineEdit(user_api_key)
        self.api_input.setPlaceholderText(self.engine.config.tr('placeholder_tmdb_key', "Introduce tu propia API Key si la común falla..."))
        self.api_input.setStyleSheet("background-color: #252525; padding: 8px; border-radius: 5px;")
        gen_l.addWidget(self.api_input)

        # Log Directory
        gen_l.addWidget(QLabel(self.engine.config.tr('lbl_log_dir', "📂 Directorio de Logs VRChat:")))
        log_row = QHBoxLayout()
        self.log_input = QLineEdit(self.engine.config.get_val('log_dir', ''))
        self.log_input.setStyleSheet("background-color: #252525; padding: 8px; border-radius: 5px;")
        btn_browse = QPushButton("📁")
        btn_browse.setFixedSize(40, 35)
        btn_browse.clicked.connect(self.on_browse_logs)
        log_row.addWidget(self.log_input)
        log_row.addWidget(btn_browse)
        gen_l.addLayout(log_row)

        # N6: Selector de tema visual
        gen_l.addWidget(QLabel("🎨 Tema Visual:"))
        theme_row = QHBoxLayout()
        self.theme_combo = QComboBox()
        try:
            from src.core.themes import theme_names
            for tn in theme_names():
                self.theme_combo.addItem(tn)
            current_theme = self.engine.config.get_val('theme', 'Oscuro')
            idx_t = self.theme_combo.findText(current_theme)
            if idx_t >= 0:
                self.theme_combo.setCurrentIndex(idx_t)
        except Exception:
            self.theme_combo.addItem('Oscuro')
        self.theme_combo.setStyleSheet("background-color: #252525; padding: 8px; border-radius: 5px; color: white;")
        btn_apply_theme = QPushButton("Aplicar Tema")
        btn_apply_theme.setFixedWidth(120)
        btn_apply_theme.setStyleSheet("background-color: #1f6aa5; padding: 8px; border-radius: 5px; color: white; font-weight: bold;")
        btn_apply_theme.clicked.connect(self._on_apply_theme)
        theme_row.addWidget(self.theme_combo)
        theme_row.addWidget(btn_apply_theme)
        gen_l.addLayout(theme_row)

        # P4: URL del servidor OTA / OTA update server URL (configurable)
        gen_l.addWidget(QLabel(self.engine.config.tr('lbl_ota_url', "🔗 URL de actualizaciones OTA (opcional):")))
        self.ota_url_input = QLineEdit(self.engine.config.get_val('ota_url', ''))
        self.ota_url_input.setPlaceholderText(
            self.engine.config.tr('placeholder_ota_url',
                "https://raw.githubusercontent.com/tu-usuario/VRCMT/main/version.txt")
        )
        self.ota_url_input.setStyleSheet("background-color: #252525; padding: 8px; border-radius: 5px;")
        gen_l.addWidget(self.ota_url_input)

        btn_save_gen = QPushButton(self.engine.config.tr('btn_save_settings', "💾 Guardar Configuración"))
        btn_save_gen.setStyleSheet("background-color: #1f6aa5; padding: 10px; font-weight: bold; margin-top: 10px;")
        btn_save_gen.clicked.connect(self.on_save_general)
        gen_l.addWidget(btn_save_gen)

        container_l.addWidget(gen_card)

        # F4: SECCIÓN: EXPORTAR CATÁLOGO / Export catalog
        export_card = QFrame()
        export_card.setStyleSheet("background-color: #1a1a1a; border-radius: 15px; padding: 20px;")
        export_l = QVBoxLayout(export_card)
        export_title = QLabel(self.engine.config.tr('lbl_export_catalog', "📤 Exportar Catálogo / Export Catalog"))
        export_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1f6aa5;")
        export_l.addWidget(export_title)
        export_desc = QLabel(self.engine.config.tr(
            'lbl_export_desc',
            "Exporta todo tu catálogo en un formato legible para backup o migración.\n"
            "Export your entire catalog as CSV or JSON for backup/migration."
        ))
        export_desc.setStyleSheet("color: #9aa0a6; font-size: 12px;")
        export_desc.setWordWrap(True)
        export_l.addWidget(export_desc)
        btn_row_exp = QHBoxLayout()
        btn_exp_csv = QPushButton("📄 Exportar CSV")
        btn_exp_csv.setStyleSheet(
            "background-color: #2d4a22; color: #81c784; padding: 10px 20px; "
            "border-radius: 8px; font-weight: bold;"
        )
        btn_exp_csv.clicked.connect(self._export_catalog_csv)
        btn_exp_json = QPushButton("📋 Exportar JSON")
        btn_exp_json.setStyleSheet(
            "background-color: #1a2d4a; color: #5dade2; padding: 10px 20px; "
            "border-radius: 8px; font-weight: bold;"
        )
        btn_exp_json.clicked.connect(self._export_catalog_json)
        btn_row_exp.addWidget(btn_exp_csv)
        btn_row_exp.addWidget(btn_exp_json)
        btn_row_exp.addStretch()
        export_l.addLayout(btn_row_exp)
        container_l.addWidget(export_card)

        # ── SECCIÓN: YOUTUBE EN VRCHAT (stub yt-dlp) ───────────────────────────
        stub_card = QFrame()
        stub_card.setStyleSheet(
            "background-color: #1a1a1a; border-radius: 15px; padding: 20px; border: 1px solid #2e7d32;"
        )
        stub_l = QVBoxLayout(stub_card)
        stub_l.setSpacing(14)
        _tr = self.engine.config.tr

        # Título + botón ayuda
        stub_title_row = QHBoxLayout()
        stub_title = QLabel(_tr("lbl_stub_vrchat_title", "📺 YouTube en VRChat"))
        stub_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #81c784;")
        stub_title_row.addWidget(stub_title, 1)
        btn_stub_why = QPushButton(_tr("btn_stub_what_is", "❓ ¿Qué hace esto?"))
        btn_stub_why.setFixedHeight(34)
        btn_stub_why.setCursor(Qt.PointingHandCursor)
        btn_stub_why.setStyleSheet(
            "QPushButton { background-color: #2a3a2a; color: #aed581; border-radius: 8px; "
            "font-size: 13px; padding: 0 14px; border: 1px solid #388e3c; }"
            "QPushButton:hover { background-color: #1b5e20; }"
        )
        btn_stub_why.clicked.connect(self.on_stub_show_help)
        stub_title_row.addWidget(btn_stub_why)
        stub_l.addLayout(stub_title_row)

        # Estado actual — una línea clara
        self.stub_summary_label = QLabel("")
        self.stub_summary_label.setStyleSheet(
            "color: #e0e0e0; font-size: 13px; padding: 10px 14px; "
            "background-color: #252525; border-radius: 8px; border-left: 3px solid #388e3c;"
        )
        self.stub_summary_label.setWordWrap(True)
        stub_l.addWidget(self.stub_summary_label)

        # Separador
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #2a2a2a; background: #2a2a2a; max-height: 1px; margin: 2px 0;")
        stub_l.addWidget(sep)

        # Casilla principal
        self.stub_chk_enabled = QCheckBox(
            _tr("lbl_stub_enabled", "✅  Activar mejora de YouTube para VRChat")
        )
        self.stub_chk_enabled.setChecked(
            bool(self.engine.config.get_val("vrchat_stub_enabled", False))
        )
        self.stub_chk_enabled.setStyleSheet("color: #eee; font-size: 15px; font-weight: bold;")
        self.stub_chk_enabled.setToolTip(
            _tr(
                "stub_tip_enabled",
                "Activa la mejora para que VRCMT la reinstale automáticamente si VRChat la sobrescribe.\n"
                "Marcar esto no instala nada por sí solo — usa el botón azul para instalar.",
            )
        )
        stub_l.addWidget(self.stub_chk_enabled)

        # ── Botón principal: Instalar / Actualizar ──────────────────────────
        self.btn_stub_install_main = QPushButton(
            _tr("btn_stub_update_manifest", "⬇️  Instalar / Actualizar desde internet")
        )
        self.btn_stub_install_main.setMinimumHeight(52)
        self.btn_stub_install_main.setCursor(Qt.PointingHandCursor)
        self.btn_stub_install_main.setStyleSheet(
            "QPushButton { background-color: #1565c0; color: white; font-size: 15px; "
            "font-weight: bold; border-radius: 10px; padding: 0 20px; }"
            "QPushButton:hover { background-color: #1976d2; }"
            "QPushButton:pressed { background-color: #0d47a1; }"
        )
        self.btn_stub_install_main.setToolTip(
            _tr(
                "stub_tip_download",
                "Descarga automáticamente la última versión del stub desde el repositorio oficial,\n"
                "verifica su integridad (SHA256) e instala reemplazando el yt-dlp de VRChat.\n\n"
                "⚠️ Cierra VRChat antes de hacer clic.",
            )
        )
        self.btn_stub_install_main.clicked.connect(self.on_stub_update_manifest)
        stub_l.addWidget(self.btn_stub_install_main)

        # Fila secundaria: instalar desde archivo | quitar
        btn_row2 = QHBoxLayout()
        btn_row2.setSpacing(10)

        btn_stub_install_file = QPushButton(_tr("btn_stub_install_file", "📂  Instalar desde archivo…"))
        btn_stub_install_file.setMinimumHeight(40)
        btn_stub_install_file.setCursor(Qt.PointingHandCursor)
        btn_stub_install_file.setStyleSheet(
            "QPushButton { background-color: #37474f; color: #ddd; font-size: 13px; "
            "border-radius: 8px; padding: 0 16px; }"
            "QPushButton:hover { background-color: #455a64; }"
        )
        btn_stub_install_file.setToolTip(
            _tr(
                "stub_tip_install_file",
                "Instala un archivo yt-dlp.exe que ya tienes en tu PC.\n"
                "Útil si alguien te lo envió por Discord o lo descargaste manualmente.",
            )
        )
        btn_stub_install_file.clicked.connect(self.on_stub_install_file)

        btn_stub_restore = QPushButton(_tr("btn_stub_restore", "↩️  Quitar mejora"))
        btn_stub_restore.setMinimumHeight(40)
        btn_stub_restore.setCursor(Qt.PointingHandCursor)
        btn_stub_restore.setStyleSheet(
            "QPushButton { background-color: #3a1010; color: #ef9a9a; font-size: 13px; "
            "border-radius: 8px; padding: 0 16px; border: 1px solid #c62828; }"
            "QPushButton:hover { background-color: #c62828; color: white; }"
        )
        btn_stub_restore.setToolTip(
            _tr(
                "stub_tip_restore_btn",
                "Restaura el yt-dlp original de VRChat desde la copia de seguridad.\n"
                "⚠️ Cierra VRChat antes.",
            )
        )
        btn_stub_restore.clicked.connect(self.on_stub_restore)

        btn_row2.addWidget(btn_stub_install_file, 1)
        btn_row2.addWidget(btn_stub_restore, 1)
        stub_l.addLayout(btn_row2)

        # ── Opciones avanzadas (colapsables) ───────────────────────────────
        self.btn_stub_advanced = QPushButton(_tr("btn_stub_advanced_toggle", "⚙️  Opciones avanzadas ▼"))
        self.btn_stub_advanced.setCheckable(True)
        self.btn_stub_advanced.setFixedHeight(36)
        self.btn_stub_advanced.setCursor(Qt.PointingHandCursor)
        self.btn_stub_advanced.setStyleSheet(
            "QPushButton { background-color: #1e1e1e; color: #757575; font-size: 12px; "
            "border-radius: 6px; padding: 0 14px; text-align: left; border: 1px solid #333; }"
            "QPushButton:hover { color: #9e9e9e; }"
            "QPushButton:checked { color: #bbb; }"
        )
        self.btn_stub_advanced.clicked.connect(self._on_stub_advanced_toggled)
        stub_l.addWidget(self.btn_stub_advanced)

        self.stub_advanced_frame = QFrame()
        self.stub_advanced_frame.setStyleSheet(
            "background-color: #141414; border-radius: 10px; padding: 4px;"
        )
        adv_l = QVBoxLayout(self.stub_advanced_frame)
        adv_l.setSpacing(10)
        adv_l.setContentsMargins(14, 12, 14, 12)

        # Cookies
        adv_l.addWidget(
            QLabel(_tr("lbl_stub_step_cookies", "🍪  Cookies de YouTube (opcional — para vídeos con restricción de edad):"))
        )
        cookies_row = QHBoxLayout()
        self.stub_cookies_input = QLineEdit(
            self.engine.config.get_val("vrchat_stub_cookies_path", "") or ""
        )
        self.stub_cookies_input.setPlaceholderText(
            _tr("placeholder_stub_cookies", "Vacío = sin cookies")
        )
        self.stub_cookies_input.setStyleSheet(
            "background-color: #252525; padding: 8px; border-radius: 6px;"
        )
        self.stub_cookies_input.setToolTip(
            _tr(
                "stub_tip_cookies",
                "Exporta las cookies de tu navegador con la extensión «Get cookies.txt» y selecciona el archivo aquí.\n"
                "Necesario solo para vídeos con restricción de edad o contenido privado.",
            )
        )
        btn_stub_cookies = QPushButton("📁")
        btn_stub_cookies.setFixedSize(38, 36)
        btn_stub_cookies.setStyleSheet(
            "background-color: #333; color: white; border-radius: 6px; font-size: 16px;"
        )
        btn_stub_cookies.clicked.connect(self.on_stub_browse_cookies)
        cookies_row.addWidget(self.stub_cookies_input, 1)
        cookies_row.addWidget(btn_stub_cookies)
        adv_l.addLayout(cookies_row)

        # Casilla: restaurar al cerrar
        self.stub_chk_restore_exit = QCheckBox(
            _tr(
                "lbl_stub_restore_exit",
                "Quitar la mejora automáticamente al cerrar VRCMT",
            )
        )
        self.stub_chk_restore_exit.setChecked(
            bool(self.engine.config.get_val("vrchat_stub_restore_on_exit", False))
        )
        self.stub_chk_restore_exit.setStyleSheet("color: #aaa; font-size: 13px;")
        self.stub_chk_restore_exit.setToolTip(
            _tr(
                "stub_tip_restore_exit",
                "Si está marcado, al cerrar VRCMT se restaurará el yt-dlp original de VRChat automáticamente.",
            )
        )
        adv_l.addWidget(self.stub_chk_restore_exit)

        # URL del manifest
        from src.core.vrchat_ytdlp_stub import _DEFAULT_STUB_MANIFEST_URL
        adv_l.addWidget(
            QLabel(_tr("lbl_stub_step_link", "🔗  URL del manifest (no cambiar salvo que sepas lo que haces):"))
        )
        _saved_url = self.engine.config.get_val("vrchat_stub_manifest_url", "") or ""
        self.stub_manifest_input = QLineEdit(_saved_url or _DEFAULT_STUB_MANIFEST_URL)
        self.stub_manifest_input.setStyleSheet(
            "background-color: #252525; padding: 8px; border-radius: 6px; font-size: 12px; color: #888;"
        )
        adv_l.addWidget(self.stub_manifest_input)

        # Ruta personalizada
        adv_l.addWidget(
            QLabel(_tr("lbl_stub_target_exe", "📂  Ruta de yt-dlp de VRChat (vacío = automático):"))
        )
        self.stub_target_input = QLineEdit(
            self.engine.config.get_val("vrchat_ytdlp_target_path", "") or ""
        )
        self.stub_target_input.setPlaceholderText(
            _tr("stub_target_placeholder", "Vacío = detectar automáticamente")
        )
        self.stub_target_input.setStyleSheet(
            "background-color: #252525; padding: 8px; border-radius: 6px; font-size: 12px;"
        )
        adv_l.addWidget(self.stub_target_input)

        # Token de GitHub
        adv_l.addWidget(
            QLabel(_tr("lbl_stub_gh_token", "🔑  Token de GitHub (solo para repos privados):"))
        )
        self.stub_token_input = QLineEdit(
            self.engine.config.get_val("vrchat_stub_github_token", "") or ""
        )
        self.stub_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.stub_token_input.setPlaceholderText(_tr("stub_token_placeholder", "Vacío = descarga pública"))
        self.stub_token_input.setStyleSheet(
            "background-color: #252525; padding: 8px; border-radius: 6px; font-size: 12px;"
        )
        adv_l.addWidget(self.stub_token_input)

        # Detalles técnicos
        self.stub_status_label = QLabel("")
        self.stub_status_label.setStyleSheet(
            "color: #555; font-size: 11px; font-family: monospace; padding: 6px 0;"
        )
        self.stub_status_label.setWordWrap(True)
        adv_l.addWidget(self.stub_status_label)

        btn_adv_row = QHBoxLayout()
        btn_save_stub = QPushButton(_tr("btn_stub_save", "💾  Guardar configuración avanzada"))
        btn_save_stub.setMinimumHeight(38)
        btn_save_stub.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; font-size: 13px; "
            "border-radius: 8px; padding: 0 16px; }"
            "QPushButton:hover { background-color: #388e3c; }"
        )
        btn_save_stub.clicked.connect(self.on_save_stub)
        btn_stub_status = QPushButton("🔄")
        btn_stub_status.setFixedSize(38, 38)
        btn_stub_status.setStyleSheet(
            "background-color: #333; border-radius: 8px; font-size: 16px;"
        )
        btn_stub_status.setToolTip(_tr("btn_stub_refresh_status", "Actualizar estado"))
        btn_stub_status.clicked.connect(self.on_stub_refresh_status)
        btn_adv_row.addWidget(btn_save_stub, 1)
        btn_adv_row.addWidget(btn_stub_status)
        adv_l.addLayout(btn_adv_row)

        self.stub_advanced_frame.setVisible(False)
        stub_l.addWidget(self.stub_advanced_frame)

        container_l.addWidget(stub_card)
        self.on_stub_refresh_status()

        # --- SECCIÓN: RESPALDO UNIVERSAL ---
        backup_card = QFrame()
        backup_card.setStyleSheet("background-color: #1a1a1a; border-radius: 15px; padding: 20px;")
        backup_l = QVBoxLayout(backup_card)
        
        backup_title = QLabel(self.engine.config.tr('lbl_backup_title', "📦 Respaldo Universal (ZIP + Letterboxd)"))
        backup_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1f6aa5;")
        backup_l.addWidget(backup_title)
        
        backup_desc = QLabel(self.engine.config.tr('lbl_backup_desc', "Crea un archivo comprimido con tu base de datos, configuración, imágenes de mundos y un historial listo para importar en Letterboxd."))
        backup_desc.setStyleSheet("color: #888; margin-bottom: 10px;")
        backup_l.addWidget(backup_desc)

        # Botones Locales
        btn_export = QPushButton(self.engine.config.tr('btn_export_local', "🚀 Exportar Todo mi Historial (Local)"))
        btn_export.setStyleSheet("background-color: #1f6aa5; padding: 12px; font-weight: bold;")
        btn_export.clicked.connect(self.on_export)
        backup_l.addWidget(btn_export)

        btn_import = QPushButton(self.engine.config.tr('btn_import_local', "📥 Importar Respaldo (Local)"))
        btn_import.setStyleSheet("background-color: #333; padding: 10px;")
        btn_import.clicked.connect(self.on_import)
        backup_l.addWidget(btn_import)

        # --- SECCIÓN: CLOUD BACKUP (PREMIUM) ---
        cloud_card = QFrame()
        cloud_card.setStyleSheet("background-color: #1a1a1a; border-radius: 15px; padding: 20px; border: 1px solid #ffca28;")
        cloud_l = QVBoxLayout(cloud_card)
        
        cloud_title = QLabel(self.engine.config.tr('lbl_cloud_backup', "☁️ Nube VRCMT (Exclusivo PREMIUM 💎)"))
        cloud_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffca28;")
        cloud_l.addWidget(cloud_title)
        
        cloud_desc = QLabel(self.engine.config.tr('lbl_cloud_desc', "Sincroniza tu catálogo completo en la nube segura. Recupera tu progreso en cualquier computadora con un solo clic."))
        cloud_desc.setStyleSheet("color: #888; margin-bottom: 10px;")
        cloud_l.addWidget(cloud_desc)
        
        # Stack Upload
        self.stack_upload = QStackedWidget()
        self.btn_cloud_upload = QPushButton(self.engine.config.tr('btn_cloud_locked', "☁️ Subir a la Nube (Bloqueado 🔒)"))
        self.btn_cloud_upload.setStyleSheet("background-color: #424242; padding: 12px; font-weight: bold; color: #888;")
        self.btn_cloud_upload.setEnabled(False)
        self.btn_cloud_upload.clicked.connect(self.on_cloud_upload)
        self.stack_upload.addWidget(self.btn_cloud_upload)
        
        self.prog_upload = QProgressBar()
        self.prog_upload.setRange(0, 0)
        self.prog_upload.setStyleSheet("QProgressBar { border: 2px solid #ffca28; border-radius: 8px; background-color: #1a1a1a;} QProgressBar::chunk {background-color: #ffca28; width: 20px; margin: 1px;}")
        self.prog_upload.setFixedHeight(40)
        self.stack_upload.addWidget(self.prog_upload)
        cloud_l.addWidget(self.stack_upload)
        
        # Stack Download
        self.stack_download = QStackedWidget()
        self.btn_cloud_download = QPushButton(self.engine.config.tr('btn_cloud_download_locked', "☁️ Descargar de la Nube (Bloqueado 🔒)"))
        self.btn_cloud_download.setStyleSheet("background-color: #424242; padding: 10px; font-weight: bold; color: #888;")
        self.btn_cloud_download.setEnabled(False)
        self.btn_cloud_download.clicked.connect(self.on_cloud_download)
        self.stack_download.addWidget(self.btn_cloud_download)
        
        self.prog_download = QProgressBar()
        self.prog_download.setRange(0, 0)
        self.prog_download.setStyleSheet("QProgressBar { border: 2px solid #ffca28; border-radius: 8px; background-color: #1a1a1a;} QProgressBar::chunk {background-color: #ffca28; width: 20px; margin: 1px;}")
        self.prog_download.setFixedHeight(35)
        self.stack_download.addWidget(self.prog_download)
        cloud_l.addWidget(self.stack_download)
        
        container_l.addWidget(backup_card)
        container_l.addWidget(cloud_card)

        # --- SECCIÓN: HERRAMIENTAS PREMIUM ---
        self.premium_card = QFrame()
        self.premium_card.setStyleSheet("background-color: #1a1a1a; border-radius: 15px; padding: 20px; border: 1px solid #1f6aa5;")
        premium_l = QVBoxLayout(self.premium_card)
        
        premium_title = QLabel(self.engine.config.tr('lbl_premium_tools', "💎 Herramientas Premium"))
        premium_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1f6aa5;")
        premium_l.addWidget(premium_title)

        # Indicador de carga mientras Firebase responde
        self.lbl_premium_loading = QLabel("⏳ " + self.engine.config.tr('lbl_checking_premium', "Verificando estado…"))
        self.lbl_premium_loading.setStyleSheet("color: #888; font-size: 13px; padding: 4px 0;")
        self.lbl_premium_loading.setVisible(bool(self.engine.discord.get_saved_id()))
        premium_l.addWidget(self.lbl_premium_loading)

        self.btn_manual_player = QPushButton(self.engine.config.tr('btn_open_manual_player', "📺 Abrir Reproductor Manual (PREMIUM)"))
        self.btn_manual_player.setStyleSheet("background-color: #424242; padding: 12px; font-weight: bold; color: #888; border-radius: 8px;")
        self.btn_manual_player.setEnabled(False)
        self.btn_manual_player.setVisible(False)
        self.btn_manual_player.clicked.connect(self.on_open_manual_player)
        premium_l.addWidget(self.btn_manual_player)
        
        container_l.addWidget(self.premium_card)

        # --- SECCIÓN: DISCORD ---
        discord_card = QFrame()
        discord_card.setStyleSheet("background-color: #1a1a1a; border-radius: 15px; padding: 20px;")
        discord_l = QVBoxLayout(discord_card)
        
        discord_title = QLabel(self.engine.config.tr('lbl_discord_title', "🎮 Integración Discord"))
        discord_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #5865F2;")
        discord_l.addWidget(discord_title)

        lbl_discord_hint = QLabel("Necesitas iniciar sesión con tu cuenta de Discord para sincronizar calificaciones, estado PREMIUM y backups en la nube.")
        lbl_discord_hint.setWordWrap(True)
        lbl_discord_hint.setStyleSheet("color: #888; font-size: 12px; margin-bottom: 6px;")
        discord_l.addWidget(lbl_discord_hint)

        self.btn_discord_login = QPushButton(self.engine.config.tr('btn_discord_login', "🔑 Iniciar sesión con Discord"))
        self.btn_discord_login.setObjectName("DiscordBtn")
        self.btn_discord_login.setCursor(Qt.PointingHandCursor)
        self.btn_discord_login.setFixedHeight(46)
        self.btn_discord_login.clicked.connect(self._toggle_discord_login)
        discord_l.addWidget(self.btn_discord_login)

        container_l.addWidget(discord_card)
        container_l.addStretch()

        scroll.setWidget(container)
        main_l.addWidget(scroll)

        # Forzar el chequeo inicial de estatus Premium para restaurar la vista tras cambios de idioma
        self.refresh_premium_ui()

        # --- MEJORA v2.11.20: REFRESCO PREMIUM EN TIEMPO REAL ---
        if hasattr(self.engine, 'signals'):
            self.engine.signals.premium_updated.connect(lambda status: self.refresh_premium_ui())

    def _update_discord_btn(self):
        """Actualiza visualmente el botón según si hay sesión iniciada en Discord"""
        import os
        path = self.engine.discord.id_path
        exists = os.path.exists(path)
        
        # 1. Quitar estilos viejos (Limpieza profunda)
        self.btn_discord_login.style().unpolish(self.btn_discord_login)
        
        if exists:
            self.btn_discord_login.setText(self.engine.config.tr('btn_discord_logout', "🚪 Cerrar sesión de Discord"))
            self.btn_discord_login.setStyleSheet("""
                #DiscordBtn {
                    background-color: #2a2a2a;
                    color: #ff5252;
                    font-weight: bold;
                    font-size: 14px;
                    border-radius: 10px;
                    padding: 12px;
                    border: 2px solid #b71c1c;
                    letter-spacing: 0.3px;
                }
                #DiscordBtn:hover  { background-color: #3a1010; border-color: #ff5252; }
                #DiscordBtn:pressed { background-color: #1a0808; }
            """)
        else:
            self.btn_discord_login.setText(self.engine.config.tr('btn_discord_login', "🔑 Iniciar sesión con Discord"))
            self.btn_discord_login.setStyleSheet("""
                #DiscordBtn {
                    background-color: #5865F2;
                    color: white;
                    font-weight: bold;
                    font-size: 14px;
                    border-radius: 10px;
                    padding: 12px;
                    border: none;
                    letter-spacing: 0.3px;
                }
                #DiscordBtn:hover  { background-color: #6d77f5; }
                #DiscordBtn:pressed { background-color: #4752c4; }
            """)
        
        # 2. Aplicar estilos nuevos y forzar render
        self.btn_discord_login.style().polish(self.btn_discord_login)
        self.btn_discord_login.setEnabled(True)
        self.btn_discord_login.update()

    def _toggle_discord_login(self):
        import threading
        import os
        import logging
        from PySide6.QtCore import QTimer
        
        saved = os.path.exists(self.engine.discord.id_path)
        if saved:
            try:
                os.remove(self.engine.discord.id_path)
            except OSError as e:
                logging.debug(f"No se pudo borrar id de Discord: {e}")
            logging.info("🚪 Sesión de Discord desconectada.")
            
            # --- MEJORA: DESCONEXIÓN PREMIUM AUTOMÁTICA ---
            self.engine._setup_premium_listener()
            # Ocultar indicador de carga y botones premium al desconectar
            if hasattr(self, 'lbl_premium_loading') and shiboken.isValid(self.lbl_premium_loading):
                self.lbl_premium_loading.setVisible(False)
            self.refresh_premium_ui()
            self._update_discord_btn()
        else:
            self.btn_discord_login.setEnabled(False)
            self.btn_discord_login.setText("⏳ Esperando autorización en Discord...")
            self.btn_discord_login.setStyleSheet("background-color: #2a2a2a; color: #888; border-radius: 10px; padding: 12px; font-size: 13px; border: 1px solid #444;")
            # Mostrar indicador de carga mientras Firebase responde tras el login
            if hasattr(self, 'lbl_premium_loading') and shiboken.isValid(self.lbl_premium_loading):
                self.lbl_premium_loading.setVisible(True)
            
            # --- MEJORA v2.11.46: TEMPORIZADOR DE SEGURIDAD (ANTI-STUCK) ---
            self._discord_timeout = QTimer(self)
            self._discord_timeout.setSingleShot(True)
            def on_timeout():
                if not os.path.exists(self.engine.discord.id_path):
                    logging.warning("⚠️ Tiempo de espera de conexión de Discord agotado.")
                    self._update_discord_btn()
            self._discord_timeout.timeout.connect(on_timeout)
            self._discord_timeout.start(60000) # 60 Segundos

            def bg_login():
                # Iniciar el proceso OAuth2 en segundo plano (Bloquea hasta éxito o cierre)
                user_info = self.engine.discord.login()
                
                # --- RESTAURACIÓN: ACTIVACIÓN INMEDIATA DEL MOTOR ---
                if user_info:
                    logging.info(f"✨ Conexión establecida con {user_info.get('username')}. Activando Premium Listener...")
                    self.engine._setup_premium_listener()

                # Actualizar UI devuelta al hilo principal de forma segura siempre
                def restore_ui():
                    if hasattr(self, '_discord_timeout'):
                        self._discord_timeout.stop()
                    
                    self.btn_discord_login.setEnabled(True)
                    if not user_info:
                        logging.warning("⚠️ La conexión con Discord fue cancelada o falló.")
                    
                    self._update_discord_btn() # Esto aplica el color Rojo si el archivo existe o Azul si falló
                    
                QTimer.singleShot(0, restore_ui)
                
            threading.Thread(target=bg_login, daemon=True).start()

    def _validate_lang_change(self):
        """Habilita el botón aplicar solo si el idioma seleccionado es diferente al actual."""
        selected = self.lang_combo.currentText()
        is_different = (selected != self.current_app_lang)
        self.btn_apply_lang.setEnabled(is_different)
        
        if is_different:
            self.btn_apply_lang.setStyleSheet("background-color: #1f6aa5; color: white; padding: 8px; border-radius: 5px; font-weight: bold;")
        else:
            self.btn_apply_lang.setStyleSheet("background-color: #333; color: #888; padding: 8px; border-radius: 5px;")

    def _update_lang_options(self):
        """Muestra solo el idioma al que se puede cambiar."""
        self.lang_combo.clear()
        if self.current_app_lang == "Español":
            self.lang_combo.addItem("English")
        else:
            self.lang_combo.addItem("Español")

    def on_apply_lang(self):
        """Guarda el nuevo idioma y aplica los cambios en tiempo real (v4.0)."""
        new_lang = self.lang_combo.currentText()
        self.engine.config.save_config('language', new_lang)
        self.current_app_lang = new_lang
        
        # Cargar el diccionario nuevo en el motor
        self.engine.config.load_language(new_lang)
        
        # Refrescar las opciones del combo para mostrar el nuevo idioma opuesto
        self._update_lang_options()
        
        self.btn_apply_lang.setEnabled(False)
        self.btn_apply_lang.setStyleSheet("background-color: #333; color: #888; padding: 8px; border-radius: 5px;")
        
        QMessageBox.information(self, self.engine.config.tr('lbl_success', "Idioma Actualizado"), 
                              self.engine.config.tr('msg_lang_changed', "El idioma se ha actualizado en tiempo real."))
                              
        # Emitir señal global para que la MainWindow se redibuje con el nuevo idioma
        # Usamos singleShot para evitar que PySide destruya esta vista mientras ejecuta el MessageBox
        from PySide6.QtCore import QTimer
        if hasattr(self.engine, 'signals') and hasattr(self.engine.signals, 'language_changed'):
            QTimer.singleShot(10, lambda: self.engine.signals.language_changed.emit(new_lang))

    def on_browse_logs(self):
        path = QFileDialog.getExistingDirectory(self, self.engine.config.tr('lbl_select_log_dir', "Seleccionar carpeta de logs de VRChat"), self.log_input.text())
        if path:
            self.log_input.setText(path)

    def _persist_stub_fields_from_ui(self):
        """Guarda en config lo que hay en pantalla (para que instalar/descargar use datos sin pulsar Guardar)."""
        self.engine.config.save_config("vrchat_stub_enabled", self.stub_chk_enabled.isChecked())
        self.engine.config.save_config("vrchat_stub_restore_on_exit", self.stub_chk_restore_exit.isChecked())
        self.engine.config.save_config("vrchat_stub_manifest_url", self.stub_manifest_input.text().strip())
        self.engine.config.save_config("vrchat_stub_cookies_path", self.stub_cookies_input.text().strip())
        self.engine.config.save_config("vrchat_ytdlp_target_path", self.stub_target_input.text().strip())
        self.engine.config.save_config("vrchat_stub_github_token", self.stub_token_input.text().strip())

    def _on_stub_advanced_toggled(self):
        on = self.btn_stub_advanced.isChecked()
        self.stub_advanced_frame.setVisible(on)
        self.btn_stub_advanced.setText(
            self.engine.config.tr("btn_stub_advanced_hide", "⚙️ Ocultar opciones avanzadas ▲")
            if on
            else self.engine.config.tr("btn_stub_advanced_toggle", "⚙️ Opciones avanzadas ▼")
        )

    def on_stub_show_help(self):
        QMessageBox.information(
            self,
            self.engine.config.tr("lbl_stub_help_title", "YouTube en VRChat — Cómo usarlo"),
            self.engine.config.tr(
                "msg_stub_help_body",
                "VRChat usa un programa interno (yt-dlp) para abrir vídeos de YouTube.\n"
                "Esta mejora lo reemplaza por una versión más reciente con soporte de cookies.\n\n"
                "── PASOS PARA ACTIVAR ──\n\n"
                "1. Cierra VRChat completamente.\n\n"
                "2. Activa la casilla «Usar la mejora para YouTube».\n\n"
                "3. Haz clic en «⬇️ Descargar e instalar desde internet».\n"
                "   (La URL ya viene configurada automáticamente.)\n\n"
                "4. Haz clic en «💾 Guardar mi configuración».\n\n"
                "5. Abre VRChat — listo.\n\n"
                "── COOKIES (opcional) ──\n\n"
                "Si tienes vídeos de edad restringida o privados, puedes exportar las cookies\n"
                "de tu navegador (extensión «Get cookies.txt») y elegir el archivo en el paso ②.\n\n"
                "── DESINSTALAR ──\n\n"
                "Pulsa «Quitar la mejora» para restaurar el yt-dlp original de VRChat.",
            ),
        )

    def on_stub_browse_cookies(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.engine.config.tr("lbl_stub_pick_cookies", "Seleccionar archivo de cookies"),
            self.stub_cookies_input.text() or os.path.expanduser("~"),
            "Text (*.txt);;All (*.*)",
        )
        if path:
            self.stub_cookies_input.setText(path)

    def on_save_stub(self):
        self._persist_stub_fields_from_ui()
        try:
            from src.core.vrchat_ytdlp_stub import VRChatYtStubManager
            VRChatYtStubManager(self.engine.config).write_sidecar_from_config()
        except Exception as e:
            logging.warning("Sidecar stub: %s", e)
        QMessageBox.information(
            self,
            self.engine.config.tr("lbl_success", "Éxito"),
            self.engine.config.tr("msg_stub_saved", "Listo. Tu configuración de YouTube / VRChat está guardada."),
        )
        self.on_stub_refresh_status()

    def on_stub_refresh_status(self):
        try:
            from src.core.vrchat_ytdlp_stub import VRChatYtStubManager
            mgr = VRChatYtStubManager(self.engine.config)
            self.stub_summary_label.setText(mgr.get_friendly_status_summary())
            self.stub_status_label.setText(mgr.get_status_text())
        except Exception as e:
            self.stub_summary_label.setText(str(e))
            self.stub_status_label.setText(str(e))

    def on_stub_update_manifest(self):
        self._persist_stub_fields_from_ui()
        # Marcar botón como ocupado
        if hasattr(self, 'btn_stub_install_main') and shiboken.isValid(self.btn_stub_install_main):
            self.btn_stub_install_main.setEnabled(False)
            self.btn_stub_install_main.setText("⏳  Descargando…")

        def done(ok, msg):
            if hasattr(self, 'btn_stub_install_main') and shiboken.isValid(self.btn_stub_install_main):
                self.btn_stub_install_main.setEnabled(True)
                self.btn_stub_install_main.setText(
                    self.engine.config.tr("btn_stub_update_manifest", "⬇️  Instalar / Actualizar desde internet")
                )
            if ok:
                QMessageBox.information(
                    self,
                    self.engine.config.tr("lbl_success", "✅ Listo"),
                    self.engine.config.tr("msg_stub_installed", msg or "Stub instalado correctamente."),
                )
            else:
                QMessageBox.warning(
                    self,
                    self.engine.config.tr("lbl_error", "No se pudo completar"),
                    msg,
                )
            self.on_stub_refresh_status()

        w = StubManifestWorker(self.engine, force=False)
        w.signals.finished.connect(done)
        QThreadPool.globalInstance().start(w)

    def on_stub_install_file(self):
        self._persist_stub_fields_from_ui()
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.engine.config.tr("lbl_stub_pick_exe", "Elige el archivo yt-dlp.exe"),
            "",
            "Programa (*.exe);;Todo (*.*)",
        )
        if not path:
            return

        def done(ok, msg):
            if ok:
                QMessageBox.information(self, self.engine.config.tr("lbl_success", "Listo"), msg)
            else:
                QMessageBox.warning(self, self.engine.config.tr("lbl_error", "No se pudo completar"), msg)
            self.on_stub_refresh_status()

        w = StubInstallFileWorker(self.engine, path)
        w.signals.finished.connect(done)
        QThreadPool.globalInstance().start(w)

    def on_stub_restore(self):
        self._persist_stub_fields_from_ui()
        confirm = QMessageBox.question(
            self,
            self.engine.config.tr("lbl_stub_restore_confirm_title", "¿Quitar la mejora?"),
            self.engine.config.tr(
                "msg_stub_restore_confirm",
                "VRChat volverá a usar su programa original para YouTube.\n¿Seguro? (Cierra VRChat si está abierto.)",
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            from src.core.vrchat_ytdlp_stub import VRChatYtStubManager
            ok, msg = VRChatYtStubManager(self.engine.config).restore_original()
        except Exception as e:
            ok, msg = False, str(e)
        if ok:
            QMessageBox.information(self, self.engine.config.tr("lbl_success", "Listo"), msg)
        else:
            QMessageBox.warning(self, self.engine.config.tr("lbl_error", "No se pudo completar"), msg)
        self.on_stub_refresh_status()

    def _on_apply_theme(self):
        """N6: Aplica el tema seleccionado en tiempo real."""
        try:
            from src.core.themes import get_theme
            from PySide6.QtWidgets import QApplication
            theme_name = self.theme_combo.currentText()
            self.engine.config.save_config('theme', theme_name)
            qss = get_theme(theme_name)
            QApplication.instance().setStyleSheet(qss)
            logging.info("Tema aplicado: %s", theme_name)
        except Exception as e:
            logging.error("Error aplicando tema: %s", e)

    def on_save_general(self):
        new_lang = self.lang_combo.currentText()
        old_lang = self.engine.config.get_val('language', 'Español')
        
        self.engine.config.save_config('language', new_lang)
        self.engine.config.save_config('tmdb_api_key', self.api_input.text().strip())
        self.engine.config.save_config('log_dir', self.log_input.text().strip())
        # P4: Guardar URL OTA si fue modificada / Save OTA URL if changed
        if hasattr(self, 'ota_url_input'):
            ota_val = self.ota_url_input.text().strip()
            self.engine.config.save_config('ota_url', ota_val)
        
        if new_lang != old_lang:
            QMessageBox.information(self, self.engine.config.tr('lbl_success', "Configuración Guardada"), 
                                  self.engine.config.tr('msg_restart_required', "Idioma cambiado. Por favor, reinicia la aplicación para aplicar todos los cambios visuales."))
        else:
            QMessageBox.information(self, self.engine.config.tr('lbl_success', "Configuración Guardada"), 
                                  self.engine.config.tr('msg_save_success', "Los cambios han sido guardados exitosamente."))

    def on_export(self):
        path = self.backup_manager.export_full_backup(is_premium=getattr(self.engine, 'is_premium', False))
        if path:
            QMessageBox.information(self, self.engine.config.tr('lbl_export_success', "Exportación Exitosa"), 
                                  self.engine.config.tr('msg_export_success', f"Tu respaldo ha sido creado en:\n{path}\n\nIncluye tu DB, Imágenes y el CSV para Letterboxd."))
            # Abrir carpeta del archivo
            subprocess.Popen(f'explorer /select,"{os.path.abspath(path)}"')
        else:
            QMessageBox.critical(self, self.engine.config.tr('lbl_error', "Error"), 
                               self.engine.config.tr('msg_export_error', "No se pudo crear el archivo de respaldo."))

    def on_import(self):
        file_path, _ = QFileDialog.getOpenFileName(self, self.engine.config.tr('lbl_select_backup', "Seleccionar archivo de respaldo"), "", "Respaldo VRCMT (*.zip)")
        if file_path:
            confirm = QMessageBox.question(self, self.engine.config.tr('lbl_confirm_restore', "Confirmar Restauración"), 
                                         self.engine.config.tr('msg_confirm_restore', "Esto sobrescribirá tu catálogo actual con el contenido del respaldo.\n¿Deseas continuar?"), 
                                         QMessageBox.Yes | QMessageBox.No)
            if confirm == QMessageBox.Yes:
                if self.backup_manager.import_backup(file_path):
                    QMessageBox.information(self, self.engine.config.tr('lbl_success', "Éxito"), 
                                          self.engine.config.tr('msg_restore_success', "Historial restaurado. Por favor, reinicia la aplicación."))
                else:
                    QMessageBox.critical(self, self.engine.config.tr('lbl_error', "Error"), 
                                       self.engine.config.tr('msg_restore_error', "El archivo de respaldo está corrupto o no es válido."))

    def on_open_manual_player(self):
        from src.ui.video_player import VRCMTPlayer
        # Senior Fix: Usar show() en lugar de exec() para no bloquear la interfaz principal
        # Guardamos referencia en self para evitar que Python lo borre de memoria (Garbage Collection)
        self.manual_player = VRCMTPlayer(url="", title=self.engine.config.tr('lbl_manual_player_title', "Reproductor Manual Premium"), parent=self, engine=self.engine)
        self.manual_player.setAttribute(Qt.WA_DeleteOnClose) # Liberar memoria al cerrar
        self.manual_player.show()

    # --- METODOS PREMIUM CLOUD BACKUP ---
    def refresh_premium_ui(self):
        """Actualiza la interfaz según el estatus PREMIUM y la sesión de Discord."""
        if not shiboken.isValid(self) or not hasattr(self, 'btn_discord_login') or not shiboken.isValid(self.btn_discord_login):
            return

        # 1. Primero actualizar el botón de Discord (v2.11.45: Reactivo)
        self._update_discord_btn()

        # 2. Desbloquear/Bloquear funciones Cloud y Herramientas Premium
        is_premium = hasattr(self.engine, 'is_premium') and self.engine.is_premium
        # Ocultar el indicador de carga una vez que tengamos respuesta de Firebase
        if hasattr(self, 'lbl_premium_loading') and shiboken.isValid(self.lbl_premium_loading):
            self.lbl_premium_loading.setVisible(False)
        if is_premium:
            self.stack_upload.setVisible(True)
            self.btn_cloud_upload.setEnabled(True)
            self.btn_cloud_upload.setText(self.engine.config.tr('btn_cloud_upload', "☁️ Subir Respaldo a Nube (PREMIUM)"))
            self.btn_cloud_upload.setStyleSheet("""
                QPushButton { background-color: #ffca28; color: #000; font-weight: bold; border-radius: 8px; padding: 12px; }
                QPushButton:hover { background-color: #ffb300; }
            """)

            self.stack_download.setVisible(True)
            self.btn_cloud_download.setEnabled(True)
            self.btn_cloud_download.setText(self.engine.config.tr('btn_cloud_download', "☁️ Descargar Respaldo de Nube (PREMIUM)"))
            self.btn_cloud_download.setStyleSheet("""
                QPushButton { background-color: #ffca28; color: #000; font-weight: bold; border-radius: 8px; padding: 10px; }
                QPushButton:hover { background-color: #ffb300; }
            """)

            self.btn_manual_player.setVisible(True)
            self.btn_manual_player.setEnabled(True)
            self.btn_manual_player.setText(self.engine.config.tr('btn_open_manual_player', "📺 Abrir Reproductor Manual (PREMIUM)"))
            self.btn_manual_player.setStyleSheet("""
                QPushButton { background-color: #1f6aa5; color: white; font-weight: bold; border-radius: 8px; padding: 12px; }
                QPushButton:hover { background-color: #2980b9; }
            """)
        else:
            # Ocultar completamente los controles premium para usuarios Free
            self.stack_upload.setVisible(False)
            self.stack_download.setVisible(False)
            self.btn_manual_player.setVisible(False)

    def on_cloud_upload(self):
        discord_id = self.engine.discord.get_saved_id()
        if not discord_id:
            QMessageBox.warning(self, self.engine.config.tr('lbl_error', "Error"), self.engine.config.tr('msg_login_required', "Debes iniciar sesión con Discord primero."))
            return
            
        # Cambiar de botón a barra de carga animada
        self.stack_upload.setCurrentIndex(1)
        
        def on_finished(success, message):
            # Regresar al botón
            self.stack_upload.setCurrentIndex(0)
            if success:
                QMessageBox.information(self, self.engine.config.tr('lbl_cloud_premium', "Nube PREMIUM"), self.engine.config.tr('msg_cloud_success', message))
            else:
                QMessageBox.critical(self, self.engine.config.tr('lbl_error', "Error"), self.engine.config.tr('msg_cloud_error', message))
                
        self._upload_worker = CloudUploadWorker(self.engine, self.backup_manager, discord_id)
        self._upload_worker.signals.finished.connect(on_finished)
        QThreadPool.globalInstance().start(self._upload_worker)

    def on_cloud_download(self):
        discord_id = self.engine.discord.get_saved_id()
        if not discord_id:
            QMessageBox.warning(self, self.engine.config.tr('lbl_error', "Error"), self.engine.config.tr('msg_login_required', "Debes iniciar sesión con Discord primero."))
            return
            
        confirm = QMessageBox.question(self, self.engine.config.tr('lbl_cloud_premium', "Descargar Nube PREMIUM"), 
                                     self.engine.config.tr('msg_confirm_cloud_restore', "Esto sobrescribirá tu catálogo actual con lo que esté en la nube.\n¿Estás seguro?"), 
                                     QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes: return
        
        # Cambiar de botón a barra de carga animada
        self.stack_download.setCurrentIndex(1)
        
        def on_finished(success, message):
            # Regresar al botón
            self.stack_download.setCurrentIndex(0)
            if success:
                QMessageBox.information(self, self.engine.config.tr('lbl_success_premium', "Éxito PREMIUM"), self.engine.config.tr('msg_cloud_restore_success', message))
            else:
                QMessageBox.warning(self, self.engine.config.tr('lbl_cloud_notice', "Aviso de Nube"), self.engine.config.tr('msg_cloud_error', message))
                
        self._download_worker = CloudDownloadWorker(self.engine, self.backup_manager, discord_id)
        self._download_worker.signals.finished.connect(on_finished)
        QThreadPool.globalInstance().start(self._download_worker)

    # F4: Exportar catálogo como CSV / Export catalog as CSV ----------------------
    def _export_catalog_csv(self):
        """Exporta todos los items del catálogo a un archivo CSV.
        Exports all catalog items to a CSV file."""
        import csv
        from src.db.models import Multimedia
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar catálogo CSV", "catalogo_vrcmt.csv",
            "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return
        try:
            fields = [
                'id', 'titulo', 'tipo_contenido', 'año', 'generos',
                'sinopsis', 'calificacion_personal', 'estado_visto',
                'es_favorito', 'en_watchlist', 'world_name',
                'ultima_actualizacion', 'imdb_id',
            ]
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
                writer.writeheader()
                for item in Multimedia.select():
                    row = {k: getattr(item, k, '') for k in fields}
                    writer.writerow(row)
            QMessageBox.information(
                self,
                self.engine.config.tr('lbl_export_success', "Exportación Exitosa"),
                f"CSV guardado en:\n{path}"
            )
            subprocess.Popen(f'explorer /select,"{os.path.abspath(path)}"')
        except Exception as e:
            logging.error("Export CSV error: %s", e)
            QMessageBox.critical(self, "Error", f"No se pudo exportar:\n{e}")

    def _export_catalog_json(self):
        """Exporta todos los items del catálogo a un archivo JSON.
        Exports all catalog items to a JSON file."""
        import json
        from src.db.models import Multimedia
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar catálogo JSON", "catalogo_vrcmt.json",
            "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            fields = [
                'id', 'titulo', 'tipo_contenido', 'año', 'generos',
                'sinopsis', 'calificacion_personal', 'estado_visto',
                'es_favorito', 'en_watchlist', 'world_name',
                'ultima_actualizacion', 'imdb_id',
            ]
            data = []
            for item in Multimedia.select():
                row = {k: getattr(item, k, None) for k in fields}
                # Convertir tipos no serializables
                for k, v in row.items():
                    if hasattr(v, 'isoformat'):
                        row[k] = v.isoformat()
                    elif v is None:
                        row[k] = None
                    else:
                        row[k] = str(v) if not isinstance(v, (int, float, bool, str)) else v
                data.append(row)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            QMessageBox.information(
                self,
                self.engine.config.tr('lbl_export_success', "Exportación Exitosa"),
                f"JSON guardado en:\n{path}"
            )
            subprocess.Popen(f'explorer /select,"{os.path.abspath(path)}"')
        except Exception as e:
            logging.error("Export JSON error: %s", e)
            QMessageBox.critical(self, "Error", f"No se pudo exportar:\n{e}")
