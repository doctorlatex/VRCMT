import sys
import logging
import os
import shiboken6 as shiboken
from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QFrame,
                             QPushButton, QStackedWidget, QLabel, QLineEdit, QApplication,
                             QComboBox, QSystemTrayIcon, QMenu)
from PySide6.QtCore import Qt, Slot, QObject, QEvent, QTimer
from PySide6.QtGui import QShortcut, QKeySequence, QIcon
from src.ui.catalog_view import CatalogView
from src.ui.stats_view import StatsView
from src.ui.settings_view import SettingsView
from src.ui.media_modal import MediaModal
from src.ui.about_view import AboutView, InstructionsModal
from src.db.models import Multimedia


class _OverlayLayer(QWidget):
    """Overlay modal host instrumented for paint frequency (Windows ghosting)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paint_n = 0
        self._last_paint_ms = 0

    def paintEvent(self, event):
        super().paintEvent(event)
        # #region agent log
        try:
            import time

            from src.debug_ac5f85 import dbg, flow_current

            self._paint_n += 1
            now_ms = int(time.time() * 1000)
            dt = now_ms - int(self._last_paint_ms or 0) if self._last_paint_ms else 0
            self._last_paint_ms = now_ms

            # Log only first paint and then every 20 paints to avoid spam.
            if self._paint_n in (1, 2, 3) or (self._paint_n % 20) == 0:
                rr = event.rect()
                dbg(
                    "H-PAINT",
                    "ui.main_window._OverlayLayer.paintEvent",
                    "paint",
                    {
                        "flow": flow_current(),
                        "n": int(self._paint_n),
                        "dt_ms": int(dt),
                        "geom": [int(self.x()), int(self.y()), int(self.width()), int(self.height())],
                        "dirty": [int(rr.x()), int(rr.y()), int(rr.width()), int(rr.height())],
                        "vis": bool(self.isVisible()),
                    },
                )
        except Exception:
            pass
        # #endregion


class _QtPopupProbe(QObject):
    """App-level probe for Qt tooltip/popup windows that can look like tiny external windows."""

    def eventFilter(self, obj, event):
        try:
            et = int(event.type())
            watched = {
                int(QEvent.Type.ToolTip),
                int(QEvent.Type.Show),
                int(QEvent.Type.Hide),
                int(QEvent.Type.EnterWhatsThisMode),
                int(QEvent.Type.LeaveWhatsThisMode),
            }
            if et not in watched:
                return False
            is_w = isinstance(obj, QWidget)
            if not is_w:
                return False
            wf = int(obj.windowFlags())
            is_popupish = bool(
                (wf & int(Qt.WindowType.ToolTip))
                or (wf & int(Qt.WindowType.Popup))
                or (wf & int(Qt.WindowType.Tool))
            )
            if not is_popupish:
                return False
            # #region agent log
            try:
                from src.debug_ac5f85 import dbg, flow_current

                dbg(
                    "H-QTPOP",
                    "ui.main_window._QtPopupProbe.eventFilter",
                    "qt_popup_event",
                    {
                        "flow": flow_current(),
                        "etype": et,
                        "cls": obj.metaObject().className(),
                        "title": (obj.windowTitle() or "")[:120],
                        "visible": bool(obj.isVisible()),
                        "isWindow": bool(obj.isWindow()),
                        "flags": wf,
                        "geom": [int(obj.x()), int(obj.y()), int(obj.width()), int(obj.height())],
                    },
                )
            except Exception:
                pass
            # #endregion
        except Exception:
            pass
        return False


class MainWindow(QMainWindow):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.setWindowTitle(f"VRCMT v2.0 - {self.engine.config.tr('lbl_app_subtitle', 'Next Gen Tracker')}")
        # Ajuste de tamaño inicial para 5 tarjetas (v2.11.15)
        # 1150px garantiza que las 5 columnas entren con total comodidad
        self.resize(1150, 900)

        self.current_filter = "Todo"
        self.current_sort = self.engine.config.get_val("sort_order", "added_recent_desc")
        self._view_mode = 'grid'          # N2: modo de vista del catálogo
        self._sidebar_collapsed = False   # F8: sidebar colapsado o expandido
        self._pending_catalog_refresh = False  # T2: refresco diferido al volver al catálogo
        self._force_quit_requested = False     # F5: bandera para salida real desde tray
        self._move_evt_n = 0
        self._qt_popup_probe = None
        
        # Estilo Profesional Lightning Dark
        self.setStyleSheet("""
            QMainWindow { background-color: #0f0f0f; }
            QWidget { color: #e0e0e0; font-family: 'Inter', 'Segoe UI', sans-serif; }
            QFrame#Sidebar { background-color: #161616; border-right: 1px solid #252525; }
            QPushButton#NavButton { 
                background-color: transparent; border: none; padding: 12px 20px; 
                text-align: left; font-size: 15px; border-radius: 10px; margin: 4px 10px; color: #888;
            }
            QPushButton#NavButton:hover { background-color: #252525; color: #fff; }
            QPushButton#NavButton:checked { background-color: #1f6aa5; color: white; font-weight: bold; }
            QLineEdit {
                background-color: #1a1a1a; border: 1px solid #333; border-radius: 20px;
                padding: 10px 20px; color: #fff; font-size: 14px; margin: 10px;
            }
        """)

        self.setup_ui()
        try:
            app = QApplication.instance()
            if app is not None:
                self._qt_popup_probe = _QtPopupProbe(self)
                app.installEventFilter(self._qt_popup_probe)
        except Exception:
            self._qt_popup_probe = None
        
        # --- MEJORA v2.11.17: UI EN TIEMPO REAL ---
        if hasattr(self.engine, 'signals'):
            self._connect_engine_signals()

        # F5: Sistema de bandeja / System tray
        self._setup_system_tray()

        # F6: Atajos de teclado globales / Global keyboard shortcuts
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._esc_shortcut)
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: self.search_entry.setFocus() if hasattr(self, 'search_entry') else None)
        QShortcut(QKeySequence("Ctrl+1"), self, lambda: self.change_filter("filter_all"))
        QShortcut(QKeySequence("Ctrl+2"), self, lambda: self.change_filter("filter_movies"))
        QShortcut(QKeySequence("Ctrl+3"), self, lambda: self.change_filter("filter_series"))
        QShortcut(QKeySequence("Ctrl+4"), self, lambda: self.change_filter("filter_anime"))
        QShortcut(QKeySequence("Ctrl+5"), self, lambda: self.change_filter("filter_streams"))

        self.change_filter("filter_all") # Carga inicial

    def _connect_engine_signals(self):
        """Conecta (o reconecta) todas las senales del motor a los slots de la ventana.
        Se llama en __init__ y tambien tras cada setup_ui() (cambio de idioma) para que
        los slots no queden huerfanos si los widgets se recrean.
        Connect engine signals (or reconnect) to window slots.
        Called on __init__ and after each setup_ui() rebuild (language change).
        """
        sig = self.engine.signals
        # Solo desconectar si ya hubo una conexion previa (evita RuntimeWarning en primera llamada)
        # Only disconnect if a previous connection exists (avoids RuntimeWarning on first call)
        if getattr(self, '_engine_signals_connected', False):
            for signal, slot in (
                (sig.media_added,        self.on_media_added),
                (sig.language_changed,   self.on_language_changed),
                (sig.api_error,          self.on_api_error),
            ):
                try:
                    signal.disconnect(slot)
                except Exception:
                    pass
            if hasattr(sig, 'update_available'):
                try:
                    sig.update_available.disconnect(self._show_update_banner)
                except Exception:
                    pass

        sig.media_added.connect(self.on_media_added)
        sig.language_changed.connect(self.on_language_changed)
        sig.api_error.connect(self.on_api_error)

        # N5: Notificacion de nueva version disponible
        if hasattr(sig, 'update_available'):
            sig.update_available.connect(self._show_update_banner)

        self._engine_signals_connected = True

    @Slot(str)
    def on_api_error(self, error_type):
        """Maneja errores críticos de API reportados por el motor (Senior Feedback)"""
        if error_type == "API_QUOTA_EXCEEDED":
            self.show_toast(
                self.engine.config.tr('msg_tmdb_quota_content',
                    "Límite de API TMDb alcanzado. Usa una clave personal en ⚙️ Configuración."),
                kind='warning', duration=6000,
            )

    @Slot(str)
    def on_language_changed(self, new_lang):
        """Refresca toda la interfaz gráfica en vivo sin reiniciar el programa (v4.0)
        Refresh entire UI live without restarting (v4.0)"""
        logging.info(f"Cambiando idioma de UI a: {new_lang}")
        self.setWindowTitle(f"VRCMT v2.0 - {self.engine.config.tr('lbl_app_subtitle', 'Next Gen Tracker')}")

        # Guardar estado actual / Save current state
        current_idx = self.content_stack.currentIndex()

        # Re-dibujar toda la UI principal / Rebuild main UI
        self.setup_ui()

        # D3 FIX: Reconectar señales del motor tras recrear widgets
        # D3 FIX: Reconnect engine signals after widget rebuild
        if hasattr(self.engine, 'signals'):
            self._connect_engine_signals()

        # Restaurar estado / Restore state
        if current_idx == 0:
            self.change_filter(self.current_filter)
        elif current_idx == 1:
            self.change_filter("filter_stats")
        elif current_idx == 2:
            self.change_filter("filter_settings")
        elif current_idx == 3:
            self.change_filter("filter_about")

    def on_media_added(self):
        """Refresco inteligente con blindaje y cooldown (v3.5.8)"""
        # --- MEJORA v3.5.8: COOLDOWN DE ACTUALIZACIÓN ---
        # Evita el "ametrallamiento" de redibujos que causa cierres de ventana (SegFault)
        import time
        now = time.time()
        if hasattr(self, '_last_ui_refresh') and (now - self._last_ui_refresh) < 3.0:
            logging.debug("⏳ Actualización de catálogo ignorada por cooldown (3s).")
            return
        self._last_ui_refresh = now

        # --- MEJORA v3.5.7: BARRERA DE MODAL ---
        if self.modal_stack:
            logging.debug("⏳ Actualización de catálogo diferida: Modal abierto.")
            return

        logging.info("Actualizando UI por nuevo contenido detectado (Tiempo Real)...")
        idx = self.content_stack.currentIndex()
        if idx == 0:
            self.load_catalog()
        elif idx == 1:
            self.stats_view.refresh_stats()
        else:
            # T2: Diferir refresco del catálogo hasta que el usuario vuelva a él
            # T2: Defer catalog refresh until user returns to it
            self._pending_catalog_refresh = True
            logging.debug("Refresco del catálogo diferido (usuario en vista %d)", idx)

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. SIDEBAR
        self.sidebar = QWidget()
        self.sidebar.setObjectName("Sidebar")
        sidebar_w = 54 if self._sidebar_collapsed else 220
        self.sidebar.setFixedWidth(sidebar_w)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(0, 8, 0, 8)
        sidebar_layout.setSpacing(2)

        # Logo / Brand label
        self.logo_lbl = QLabel("VRCMT")
        self.logo_lbl.setStyleSheet("font-size: 28px; font-weight: bold; color: #1f6aa5; margin: 12px 20px;")
        self.logo_lbl.setVisible(not self._sidebar_collapsed)
        sidebar_layout.addWidget(self.logo_lbl)

        # Nav items: (emoji, label_key, default_label, filter_key)
        self.nav_group = []
        nav_items = [
            ("🌐", 'filter_all',       'Todo',              "filter_all"),
            ("🎬", 'filter_movies',    'Películas',         "filter_movies"),
            ("📺", 'filter_series',    'Series',            "filter_series"),
            ("⛩️", 'filter_anime',     'Anime',             "filter_anime"),
            ("📸", 'filter_streams',   'Streams/Imágenes',  "filter_streams"),
            ("📊", 'filter_stats',     'Estadísticas',      "filter_stats"),
            ("⚙️", 'filter_settings',  'Configuración',     "filter_settings"),
            ("ℹ️", 'filter_about',     'Acerca de',         "filter_about"),
        ]
        # Guardar emojis y textos completos para F8 (colapso)
        self._nav_emojis = []
        self._nav_full_texts = []

        for emoji, tr_key, tr_default, key in nav_items:
            label = self.engine.config.tr(tr_key, tr_default)
            full_text = f"{emoji} {label}"
            btn_text = emoji if self._sidebar_collapsed else full_text
            btn = QPushButton(btn_text)
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            if self._sidebar_collapsed:
                btn.setToolTip(label)
            btn.clicked.connect(lambda checked, k=key: self.change_filter(k))
            sidebar_layout.addWidget(btn)
            self.nav_group.append(btn)
            self._nav_emojis.append(emoji)
            self._nav_full_texts.append(full_text)

        sidebar_layout.addStretch()

        # F8: Botón colapsar/expandir sidebar / Collapse-expand sidebar button
        self._sidebar_toggle_btn = QPushButton("◀" if not self._sidebar_collapsed else "▶")
        self._sidebar_toggle_btn.setObjectName("NavButton")
        self._sidebar_toggle_btn.setFixedHeight(36)
        self._sidebar_toggle_btn.setToolTip(
            self.engine.config.tr('btn_collapse_sidebar', 'Colapsar / Collapse sidebar')
        )
        self._sidebar_toggle_btn.clicked.connect(self._toggle_sidebar)
        sidebar_layout.addWidget(self._sidebar_toggle_btn)

        main_layout.addWidget(self.sidebar)

        # 2. CONTENT AREA
        self.content_stack = QStackedWidget()
        
        # Pagina Catálogo
        self.page_catalog = QWidget()
        catalog_layout = QVBoxLayout(self.page_catalog)
        catalog_layout.setContentsMargins(20, 20, 20, 0) # Añadir padding superior y lateral (v2.11.5)

        # Barra superior: búsqueda + orden
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(10)

        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText(self.engine.config.tr('placeholder_search', '🔍 Buscar en mi catálogo...'))
        
        # --- MEJORA v2.9.1: DEBOUNCE DE BÚSQUEDA ---
        from PySide6.QtCore import QTimer
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.load_catalog)
        self.search_entry.textChanged.connect(lambda: self.search_timer.start(300))

        self.sort_label = QLabel(self.engine.config.tr("lbl_sort_by", "Ordenar por:"))
        self.sort_label.setStyleSheet("color: #9aa0a6; font-size: 12px; margin-right: 2px;")
        self.sort_combo = QComboBox()
        self.sort_combo.setFixedWidth(250)
        self.sort_combo.setStyleSheet("""
            QComboBox {
                background-color: #1a1a1a; border: 1px solid #333; border-radius: 10px;
                padding: 8px 10px; color: #f1f3f4; min-height: 20px;
            }
            QComboBox::drop-down { border: none; width: 24px; }
            QComboBox QAbstractItemView {
                background-color: #111; border: 1px solid #333; selection-background-color: #1f6aa5;
            }
        """)

        self._sort_options = [
            ("added_recent_desc", self.engine.config.tr("sort_added_recent_desc", "Agregado: más reciente")),
            ("added_recent_asc", self.engine.config.tr("sort_added_recent_asc", "Agregado: más antiguo")),
            ("year_asc", self.engine.config.tr("sort_year_asc", "Año: ascendente")),
            ("year_desc", self.engine.config.tr("sort_year_desc", "Año: descendente")),
            ("title_asc", self.engine.config.tr("sort_title_az", "Nombre: A → Z")),
            ("title_desc", self.engine.config.tr("sort_title_za", "Nombre: Z → A")),
        ]
        self.sort_combo.clear()
        for val, txt in self._sort_options:
            self.sort_combo.addItem(txt, val)
        # Compatibilidad con valor antiguo
        if self.current_sort == "recent_watch":
            self.current_sort = "added_recent_desc"
        idx = self.sort_combo.findData(self.current_sort)
        if idx < 0:
            self.current_sort = "added_recent_desc"
            idx = self.sort_combo.findData(self.current_sort)
        self.sort_combo.setCurrentIndex(max(0, idx))
        self.sort_combo.currentIndexChanged.connect(self.on_sort_changed)

        # N2: Botón toggle cuadrícula / lista
        self.btn_toggle_view = QPushButton("☰" if self._view_mode == 'grid' else "⊞")
        self.btn_toggle_view.setFixedSize(38, 38)
        self.btn_toggle_view.setToolTip("Alternar vista cuadrícula / lista")
        self.btn_toggle_view.setStyleSheet("""
            QPushButton {
                background-color: #1a1a1a; border: 1px solid #333; border-radius: 10px;
                color: #9aa0a6; font-size: 16px;
            }
            QPushButton:hover { background-color: #1f6aa5; color: #fff; }
        """)
        self.btn_toggle_view.clicked.connect(self._toggle_view_mode)

        top_bar.addWidget(self.search_entry, 1)
        top_bar.addWidget(self.sort_label, 0)
        top_bar.addWidget(self.sort_combo, 0)
        top_bar.addWidget(self.btn_toggle_view, 0)
        catalog_layout.addLayout(top_bar)

        # F1: Filtro por Género / Genre filter (visible en todos excepto streams/stats/settings/about)
        self._genre_filter_row = QWidget()
        gf_l = QHBoxLayout(self._genre_filter_row)
        gf_l.setContentsMargins(0, 0, 0, 4)
        gf_l.setSpacing(8)
        gf_label = QLabel(self.engine.config.tr('lbl_genre_filter', '🎭 Género / Genre:'))
        gf_label.setStyleSheet("color: #9aa0a6; font-size: 12px;")
        self.genre_combo = QComboBox()
        self.genre_combo.setFixedWidth(260)
        self.genre_combo.setStyleSheet("""
            QComboBox {
                background-color: #1a1a1a; border: 1px solid #333; border-radius: 8px;
                padding: 6px 10px; color: #f1f3f4;
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView {
                background-color: #111; border: 1px solid #333;
                selection-background-color: #1f6aa5;
            }
        """)
        self.genre_combo.currentIndexChanged.connect(lambda: self.load_catalog())
        gf_l.addWidget(gf_label)
        gf_l.addWidget(self.genre_combo)
        gf_l.addStretch()
        self._genre_filter_row.setVisible(False)
        catalog_layout.addWidget(self._genre_filter_row)

        # N3: Filtro por mundo (solo visible en Streams/Imágenes)
        self._world_filter_row = QWidget()
        wf_l = QHBoxLayout(self._world_filter_row)
        wf_l.setContentsMargins(0, 0, 0, 4)
        wf_l.setSpacing(8)
        wf_label = QLabel("🌍 " + self.engine.config.tr('lbl_world_filter', 'Mundo / World:'))
        wf_label.setStyleSheet("color: #9aa0a6; font-size: 12px;")
        self.world_combo = QComboBox()
        self.world_combo.setFixedWidth(320)
        self.world_combo.setStyleSheet("""
            QComboBox {
                background-color: #1a1a1a; border: 1px solid #333; border-radius: 8px;
                padding: 6px 10px; color: #f1f3f4;
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView {
                background-color: #111; border: 1px solid #333;
                selection-background-color: #1f6aa5;
            }
        """)
        self.world_combo.currentIndexChanged.connect(lambda: self.load_catalog())
        wf_l.addWidget(wf_label)
        wf_l.addWidget(self.world_combo)
        wf_l.addStretch()
        self._world_filter_row.setVisible(False)
        catalog_layout.addWidget(self._world_filter_row)

        # Paginado del catálogo (20 tarjetas por página)
        self._catalog_page_size = 20
        self._catalog_page = 1
        self._catalog_items = []

        self.catalog_pager = QWidget()
        pager_l = QHBoxLayout(self.catalog_pager)
        pager_l.setContentsMargins(0, 0, 0, 0)
        pager_l.setSpacing(6)

        self.btn_cat_first = QPushButton("⏮")
        self.btn_cat_first.setFixedSize(40, 32)
        self.btn_cat_prev = QPushButton("◀")
        self.btn_cat_prev.setFixedSize(40, 32)
        self.btn_cat_next = QPushButton("▶")
        self.btn_cat_next.setFixedSize(40, 32)
        self.btn_cat_last = QPushButton("⏭")
        self.btn_cat_last.setFixedSize(40, 32)

        self.lbl_cat_page = QLabel(self.engine.config.tr("pager_page_label", "Página 1/1"))
        self.lbl_cat_page.setStyleSheet("""
            color: #cfd8dc;
            background-color: #1a1f24;
            border: 1px solid #2b3742;
            border-radius: 9px;
            padding: 6px 10px;
            font-weight: bold;
        """)

        self.entry_cat_page = QLineEdit()
        self.entry_cat_page.setPlaceholderText(self.engine.config.tr("pager_go_placeholder", "Ir a..."))
        self.entry_cat_page.setFixedWidth(70)
        self.entry_cat_page.setFixedHeight(32)
        self.entry_cat_page.setStyleSheet("""
            QLineEdit {
                background-color: #121212;
                border: 1px solid #313131;
                border-radius: 8px;
                padding: 6px 8px;
                color: #fff;
                margin: 0;
            }
        """)

        self.btn_cat_go = QPushButton(self.engine.config.tr("pager_go_btn", "Ir"))
        self.btn_cat_go.setFixedSize(56, 32)
        nav_btn_css = """
            QPushButton {
                background-color: #1b1b1b;
                border: 1px solid #343434;
                border-radius: 8px;
                color: #f0f0f0;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #262626; border-color: #4b4b4b; }
            QPushButton:disabled { color: #666; border-color: #2a2a2a; background-color: #151515; }
        """
        for b in (self.btn_cat_first, self.btn_cat_prev, self.btn_cat_next, self.btn_cat_last, self.btn_cat_go):
            b.setStyleSheet(nav_btn_css)

        pager_l.addWidget(self.btn_cat_first)
        pager_l.addWidget(self.btn_cat_prev)
        pager_l.addWidget(self.btn_cat_next)
        pager_l.addWidget(self.btn_cat_last)
        pager_l.addSpacing(8)
        pager_l.addWidget(self.lbl_cat_page)
        pager_l.addStretch(1)
        pager_l.addWidget(self.entry_cat_page)
        pager_l.addWidget(self.btn_cat_go)

        self.btn_cat_first.clicked.connect(lambda: self._set_catalog_page(1))
        self.btn_cat_prev.clicked.connect(lambda: self._set_catalog_page(self._catalog_page - 1))
        self.btn_cat_next.clicked.connect(lambda: self._set_catalog_page(self._catalog_page + 1))
        self.btn_cat_last.clicked.connect(lambda: self._set_catalog_page(self._catalog_total_pages()))
        self.btn_cat_go.clicked.connect(self._go_to_catalog_page)
        self.entry_cat_page.returnPressed.connect(self._go_to_catalog_page)

        catalog_layout.addWidget(self.catalog_pager)
        
        self.catalog_view = CatalogView(self.engine)
        self.catalog_view.media_selected.connect(self.on_media_selected)
        # N8: Cargar más al llegar al final del scroll
        self.catalog_view.load_more_requested.connect(self._on_load_more_requested)
        # Aplicar modo de vista guardado
        self.catalog_view.set_view_mode(self._view_mode)
        catalog_layout.addWidget(self.catalog_view)
        
        self.content_stack.addWidget(self.page_catalog)

        # Pagina Estadísticas
        self.stats_view = StatsView(self.engine)
        self.content_stack.addWidget(self.stats_view)

        # Pagina Configuración
        self.settings_view = SettingsView(self.engine)
        self.content_stack.addWidget(self.settings_view)

        # Pagina Acerca de
        self.about_view = AboutView(self.engine)
        self.about_view.show_instructions_requested.connect(self._show_instructions_modal)
        self.content_stack.addWidget(self.about_view)

        main_layout.addWidget(self.content_stack)

        # Contenedor para el Overlay Modal (v2.0 Legacy Style - Stacked)
        self.modal_stack = []

    @Slot(object)
    def on_data_changed(self, item):
        """Recarga el catálogo para reaplicar dedupe (título, tipo); update_single_card no quita duplicados."""
        if item:
            self.load_catalog(preserve_page=True)
        else:
            self.load_catalog()

    def _create_overlay_layer(self):
        """Capa modal sobre el centralWidget (nunca como hijo directo de QMainWindow)."""
        host = self.centralWidget()
        if host is None:
            host = self
        new_overlay = _OverlayLayer(host)
        new_overlay.setObjectName("ModalOverlay")
        new_overlay.setWindowFlags(Qt.Widget)
        new_overlay.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        new_overlay.setStyleSheet("background-color: rgba(0, 0, 0, 180);")
        new_overlay.setGeometry(host.rect())
        overlay_l = QVBoxLayout(new_overlay)
        overlay_l.setAlignment(Qt.AlignCenter)
        overlay_l.setContentsMargins(50, 50, 50, 50)
        return new_overlay, overlay_l

    @Slot(object)
    def on_media_selected(self, item):
        """Abre el modal incrustado. MediaModal nunca debe crearse con padre QMainWindow (Windows)."""
        from src.debug_ac5f85 import dbg, flow_click_trace, flow_current

        iid = str(getattr(item, "id", ""))
        logging.info("[VRCMT-UI] on_media_selected enter id=%s", iid)
        flow_click_trace("main_on_media_selected_enter", item_id=iid)
        dbg(
            "H2",
            "main.on_media_selected",
            "enter",
            {"item_id": iid, "titulo": (getattr(item, "titulo", None) or "")[:100]},
        )
        n_prev = len(self.modal_stack)
        while self.modal_stack:
            self.close_modal()
        if n_prev:
            dbg(
                "H2",
                "main.on_media_selected",
                "closed_previous_modals",
                {"n_closed": n_prev, "flow": flow_current()},
            )
        new_overlay, overlay_l = self._create_overlay_layer()
        flow_click_trace("main_overlay_layer_ready")
        dbg("H2", "main.on_media_selected", "overlay_created", {"flow": flow_current()})
        logging.info("[VRCMT-UI] before MediaModal() id=%s", iid)
        flow_click_trace("main_before_MediaModal_ctor")
        modal = MediaModal(item, self.engine, new_overlay)
        flow_click_trace("main_after_MediaModal_ctor")
        logging.info("[VRCMT-UI] after MediaModal() id=%s", iid)
        modal.setWindowFlags(Qt.Widget)
        modal.data_changed.connect(self.on_data_changed)
        overlay_l.addWidget(modal)
        pcls = modal.parent().metaObject().className() if modal.parent() else None
        flow_click_trace("main_modal_added_to_overlay", modal_parent_cls=pcls or "")
        dbg(
            "H2",
            "main.on_media_selected",
            "modal_in_overlay",
            {"flow": flow_current(), "modal_parent_cls": pcls},
        )
        self.modal_stack.append(new_overlay)
        new_overlay.show()
        new_overlay.raise_()
        flow_click_trace("main_overlay_show_raise_done")
        dbg("H2", "main.on_media_selected", "after_show_raise", {"flow": flow_current()})
        logging.info("[VRCMT-UI] on_media_selected done id=%s", iid)

    # F8: Colapsar / expandir sidebar -----------------------------------------
    def _toggle_sidebar(self):
        """Alterna el sidebar entre modo compacto (iconos) y expandido (texto).
        Toggle sidebar between compact (icons) and expanded (text) mode."""
        self._sidebar_collapsed = not self._sidebar_collapsed
        if self._sidebar_collapsed:
            self.sidebar.setFixedWidth(54)
            self.logo_lbl.setVisible(False)
            for btn, emoji in zip(self.nav_group, self._nav_emojis):
                if shiboken.isValid(btn):
                    btn.setText(emoji)
                    full = next((t for t in self._nav_full_texts if t.startswith(emoji)), emoji)
                    btn.setToolTip(full.split(" ", 1)[1] if " " in full else full)
            self._sidebar_toggle_btn.setText("▶")
        else:
            self.sidebar.setFixedWidth(220)
            self.logo_lbl.setVisible(True)
            for btn, full_text in zip(self.nav_group, self._nav_full_texts):
                if shiboken.isValid(btn):
                    btn.setText(full_text)
                    btn.setToolTip("")
            self._sidebar_toggle_btn.setText("◀")

    # F1: Poblar combo de géneros -----------------------------------------------
    def _populate_genre_combo(self):
        """Rellena el combo de géneros con los géneros distintos de la BD.
        Fills genre combo with distinct genres from the DB."""
        if not hasattr(self, 'genre_combo') or not shiboken.isValid(self.genre_combo):
            return
        self.genre_combo.blockSignals(True)
        prev = self.genre_combo.currentData()
        self.genre_combo.clear()
        self.genre_combo.addItem(
            self.engine.config.tr('genre_all', 'Todos los géneros / All genres'), None
        )
        try:
            from collections import Counter
            all_g = []
            for m in Multimedia.select(Multimedia.generos).where(Multimedia.generos.is_null(False)):
                all_g.extend([g.strip() for g in (m.generos or "").split(",") if g.strip()])
            for genre, _ in Counter(all_g).most_common(30):
                self.genre_combo.addItem(genre, genre)
        except Exception as e:
            logging.debug("_populate_genre_combo: %s", e)
        if prev:
            idx = self.genre_combo.findData(prev)
            if idx >= 0:
                self.genre_combo.setCurrentIndex(idx)
        self.genre_combo.blockSignals(False)

    # F6: Esc shortcut ----------------------------------------------------------
    def _esc_shortcut(self):
        """Cierra el modal superior o desfoca la búsqueda con Escape.
        Closes top modal or clears search focus on Escape."""
        if self.modal_stack:
            self.close_modal()
        elif hasattr(self, 'search_entry') and shiboken.isValid(self.search_entry):
            self.search_entry.clearFocus()

    # F3: Toast notifications ---------------------------------------------------
    def show_toast(self, message: str, kind: str = 'info', duration: int = 3500):
        """Muestra una notificación flotante no intrusiva / Shows a floating notification.
        kind: 'info' | 'success' | 'error' | 'warning'"""
        try:
            from src.ui.toast import Toast
            host = self.centralWidget()
            if not host or not shiboken.isValid(host):
                return
            t = Toast(message, kind, duration, host)
            t.adjustSize()
            margin = 24
            x = host.width() - t.width() - margin
            y = host.height() - t.height() - margin - 44
            t.move(max(0, x), max(0, y))
            t.show()
            t.raise_()
        except Exception as e:
            logging.debug("show_toast: %s", e)

    # F5: Sistema tray ----------------------------------------------------------
    def _setup_system_tray(self):
        """Configura el icono de la bandeja del sistema / Sets up system tray icon."""
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                self._tray = None
                return
            self._tray = QSystemTrayIcon(self)
            # Usar icono de la app si existe, si no usar icono de Qt
            icon_path = os.path.join(os.path.dirname(__file__), '..', '..', 'assets', 'icon.ico')
            if os.path.isfile(icon_path):
                self._tray.setIcon(QIcon(icon_path))
            else:
                self._tray.setIcon(self.style().standardIcon(
                    self.style().StandardPixmap.SP_ComputerIcon
                ))
            self._tray.setToolTip("VRCMT — VRChat Media Tracker")

            tray_menu = QMenu(self)
            act_show = tray_menu.addAction(
                self.engine.config.tr('tray_show', 'Mostrar / Show')
            )
            act_show.triggered.connect(self._tray_show)
            tray_menu.addSeparator()
            act_exit = tray_menu.addAction(
                self.engine.config.tr('tray_exit', 'Salir / Exit')
            )
            act_exit.triggered.connect(self._force_exit)

            self._tray.setContextMenu(tray_menu)
            self._tray.activated.connect(self._on_tray_activated)
            self._tray.show()
        except Exception as e:
            logging.debug("_setup_system_tray: %s", e)
            self._tray = None

    def _tray_show(self):
        """Muestra la ventana desde la bandeja / Restore window from tray."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._tray_show()

    def _force_exit(self):
        """Salida forzada limpia (desde menú tray o última instancia).
        Clean forced exit (from tray menu or last instance)."""
        self._force_quit_requested = True
        self.close()

    # F6: Shortcut helper -------------------------------------------------------
    def _show_instructions_modal(self):
        """Muestra el modal de instrucciones sobre la vista Acerca de."""
        modal = InstructionsModal(self.centralWidget())
        modal.closed.connect(self.close_modal)
        self._build_modal_overlay(modal)

    # N2: Toggle cuadrícula / lista ----------------------------------------
    def _toggle_view_mode(self):
        self._view_mode = 'list' if self._view_mode == 'grid' else 'grid'
        self.btn_toggle_view.setText("⊞" if self._view_mode == 'list' else "☰")
        self.catalog_view.set_view_mode(self._view_mode)
        self.load_catalog(preserve_page=True)

    # N3: Filtro por mundo --------------------------------------------------
    def _populate_world_combo(self):
        """Rellena el combo de mundos con los world_name distintos de la BD."""
        self.world_combo.blockSignals(True)
        prev = self.world_combo.currentData()
        self.world_combo.clear()
        self.world_combo.addItem("Todos los mundos", None)
        try:
            worlds = (
                Multimedia.select(Multimedia.world_name)
                .where(
                    Multimedia.tipo_contenido == 'Stream/Imagen',
                    Multimedia.world_name.is_null(False),
                    Multimedia.world_name != '',
                )
                .group_by(Multimedia.world_name)
                .order_by(Multimedia.world_name.asc())
            )
            for row in worlds:
                wn = row.world_name
                if wn:
                    self.world_combo.addItem(wn, wn)
        except Exception as e:
            logging.debug("_populate_world_combo: %s", e)
        # Restaurar selección previa
        if prev:
            idx = self.world_combo.findData(prev)
            if idx >= 0:
                self.world_combo.setCurrentIndex(idx)
        self.world_combo.blockSignals(False)

    # N5: Banner de actualización -------------------------------------------
    def _show_update_banner(self, version: str):
        """Muestra toast de nueva versión disponible / Show update available toast."""
        try:
            if not shiboken.isValid(self):
                return
            self._pending_update_version = version
            self.show_toast(
                f"🆕 Nueva versión disponible: v{version} — ve a «Acerca de» para actualizar",
                kind='success', duration=10000,
            )
            # Activar boton de descarga en AboutView
            if hasattr(self, 'about_view'):
                self.about_view.notify_update(version)
        except Exception as e:
            logging.debug("_show_update_banner: %s", e)

    # N8: Cargar más (append, nunca reemplaza) --------------------------------
    def _on_load_more_requested(self):
        """Agrega la siguiente pagina de items al final de la vista sin resetear el scroll."""
        next_page = self._catalog_page + 1
        if next_page > self._catalog_total_pages():
            # No hay mas items — desbloquear el flag de carga
            if hasattr(self.catalog_view, '_load_more_locked'):
                self.catalog_view._load_more_locked = False
            return
        self._catalog_page = next_page
        # Calcular solo el trozo de items de la nueva pagina
        i0 = (next_page - 1) * self._catalog_page_size
        i1 = i0 + self._catalog_page_size
        new_items = (self._catalog_items or [])[i0:i1]
        if new_items:
            self.catalog_view.append_items(new_items)
            self._update_catalog_pager()

    def _build_modal_overlay(self, widget):
        """Apila un widget (p. ej. SearchDialog) sobre el área central."""
        new_overlay, overlay_l = self._create_overlay_layer()
        overlay_l.addWidget(widget)
        self.modal_stack.append(new_overlay)
        new_overlay.show()
        new_overlay.raise_()

    def close_modal(self):
        """Cierra el overlay superior (place_forget style)."""
        if self.modal_stack:
            top_overlay = self.modal_stack.pop()
            top_overlay.hide()
            top_overlay.deleteLater()

    def resizeEvent(self, event):
        """Asegura que los overlays cubran todo el área del central (sidebar + catálogo)."""
        super().resizeEvent(event)
        host = self.centralWidget()
        if not host:
            return
        r = host.rect()
        for overlay in self.modal_stack:
            overlay.setGeometry(r)

    def moveEvent(self, event):
        super().moveEvent(event)
        # #region agent log
        try:
            self._move_evt_n += 1
            from src.debug_ac5f85 import dbg, dbg_capture_active_window, flow_current

            if self._move_evt_n <= 6:
                dbg(
                    "H-MOVE",
                    "MainWindow.moveEvent",
                    "moved",
                    {
                        "flow": flow_current(),
                        "n": int(self._move_evt_n),
                        "x": int(self.x()),
                        "y": int(self.y()),
                        "w": int(self.width()),
                        "h": int(self.height()),
                    },
                )
                dbg_capture_active_window(f"main_move_{self._move_evt_n}")
        except Exception:
            pass
        # #endregion

    def closeEvent(self, event):
        """Mejora v3.6.8 + F5: Si el tray está activo, minimizar; si no, salida forzada.
        v3.6.8 + F5: Minimize to tray if available; otherwise force-quit."""
        # F5: Solo cerrar de verdad si se pidió explícitamente (desde tray o Ctrl+Q)
        tray_ok = (
            hasattr(self, '_tray')
            and self._tray is not None
            and shiboken.isValid(self._tray)
            and QSystemTrayIcon.isSystemTrayAvailable()
        )
        if tray_ok and not self._force_quit_requested:
            event.ignore()
            self.hide()
            self._tray.showMessage(
                "VRCMT",
                self.engine.config.tr('tray_minimized', 'Minimizado a la bandeja / Minimized to tray'),
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
            return

        # Salida real / Actual exit
        logging.info("Ventana principal cerrada. Apagado seguro en curso...")
        try:
            for overlay in list(getattr(self, "modal_stack", []) or []):
                lay = overlay.layout()
                w = lay.itemAt(0).widget() if lay and lay.count() > 0 else None
                if w is not None and hasattr(w, "flush_rating_state_before_close"):
                    w.flush_rating_state_before_close()
        except Exception as e:
            logging.debug("closeEvent flush modals: %s", e)
        if hasattr(self.engine, 'stop'):
            self.engine.stop()
        os._exit(0)

    def change_filter(self, key):
        # Mapeo de keys a índices de botones en nav_group
        # P2: Watchlist añadido en posición 5; stats/settings/about desplazados +1
        idx_map = {
            "filter_all":       0,
            "filter_movies":    1,
            "filter_series":    2,
            "filter_anime":     3,
            "filter_streams":   4,
            "filter_stats":     5,
            "filter_settings":  6,
            "filter_about":     7,
        }

        # Actualizar estado visual de los botones
        for i, btn in enumerate(self.nav_group):
            btn.setChecked(i == idx_map.get(key, -1))

        # T2: Limpiar bandera de refresco pendiente al volver al catálogo
        if key not in ("filter_stats", "filter_settings", "filter_about"):
            self._pending_catalog_refresh = False

        if key == "filter_stats":
            self.stats_view.refresh_stats()
            self.content_stack.setCurrentIndex(1)
        elif key == "filter_settings":
            self.content_stack.setCurrentIndex(2)
        elif key == "filter_about":
            self.content_stack.setCurrentIndex(3)
        else:
            # N3: Mostrar/ocultar filtro de mundo (solo Streams/Imágenes)
            is_streams = (key == "filter_streams")
            if hasattr(self, '_world_filter_row'):
                self._world_filter_row.setVisible(is_streams)
                if is_streams:
                    self._populate_world_combo()

            # F1: Mostrar/ocultar filtro de género (todo excepto streams)
            is_catalog_with_genres = key not in ("filter_streams",)
            if hasattr(self, '_genre_filter_row'):
                self._genre_filter_row.setVisible(is_catalog_with_genres)
                if is_catalog_with_genres:
                    self._populate_genre_combo()

            # --- MEJORA v2.11.61: FILTRADO POR LLAVE INTERNA ---
            self.current_filter = key
            self.content_stack.setCurrentIndex(0)
            self.load_catalog()

    def load_catalog(self, preserve_page: bool = False):
        search = self.search_entry.text().strip()
        prev_page = int(self._catalog_page or 1) if preserve_page else 1
        try:
            # #region agent log
            try:
                from src.debug_ac5f85 import dbg, flow_current, win32_foreground

                dbg(
                    "DB",
                    "MainWindow.load_catalog",
                    "before_query",
                    {
                        "flow": flow_current(),
                        "filter": getattr(self, "current_filter", ""),
                        "search": (search or "")[:80],
                        "fg": win32_foreground(),
                    },
                )
            except Exception:
                pass
            # #endregion
            # --- MEJORA v2.11.75: un representante por (título, tipo) = el más reciente por ultima_actualizacion ---
            # SQLite con GROUP BY no garantiza qué fila devuelve por grupo; deduplicamos en orden explícito.
            query = Multimedia.select()
            
            if self.current_filter == "filter_movies":
                query = query.where(Multimedia.tipo_contenido.in_(['Pelicula', 'Video']) & (Multimedia.es_anime == 0))
            elif self.current_filter == "filter_series":
                query = query.where((Multimedia.tipo_contenido == 'Serie') & (Multimedia.es_anime == 0))
            elif self.current_filter == "filter_anime":
                query = query.where(Multimedia.es_anime == 1)
            elif self.current_filter == "filter_streams":
                query = query.where(Multimedia.tipo_contenido == 'Stream/Imagen')
                # N3: Filtro adicional por mundo
                if hasattr(self, 'world_combo') and shiboken.isValid(self.world_combo):
                    selected_world = self.world_combo.currentData()
                    if selected_world:
                        query = query.where(Multimedia.world_name == selected_world)
            # F1: Filtro de género adicional / Extra genre filter
            if hasattr(self, 'genre_combo') and shiboken.isValid(self.genre_combo):
                sel_genre = self.genre_combo.currentData()
                if sel_genre:
                    query = query.where(Multimedia.generos.contains(sel_genre))

            if search:
                query = query.where(Multimedia.titulo.contains(search))
            
            query = query.order_by(Multimedia.ultima_actualizacion.desc())
            seen = set()
            items = []
            max_scan = 3000
            scanned = 0
            for row in query:
                scanned += 1
                if scanned > max_scan:
                    break
                key = (row.titulo, row.tipo_contenido)
                if key in seen:
                    continue
                seen.add(key)
                items.append(row)
                # T3: Aumentado de 100 a 500; N8 (scroll infinito) maneja el display progresivo
                # T3: Raised from 100 to 500; N8 (infinite scroll) handles progressive display
                if len(items) >= 500:
                    break

            items = self._sort_catalog_items(items)
            logging.info(f"Cargando {len(items)} grupos de medios para el filtro {self.current_filter}")
            self._catalog_items = items
            if preserve_page:
                total_pages = max(
                    1,
                    (len(items) + self._catalog_page_size - 1) // self._catalog_page_size,
                )
                self._catalog_page = max(1, min(prev_page, total_pages))
            else:
                self._catalog_page = 1
            self._apply_catalog_page()
            # #region agent log
            try:
                from src.debug_ac5f85 import dbg, flow_current, win32_foreground

                dbg(
                    "DB",
                    "MainWindow.load_catalog",
                    "after_query",
                    {
                        "flow": flow_current(),
                        "n_groups": len(items),
                        "filter": getattr(self, "current_filter", ""),
                        "fg": win32_foreground(),
                    },
                )
            except Exception:
                pass
            # #endregion
        except Exception as e:
            logging.error(f"Error cargando catálogo agrupado: {e}")

    def _catalog_total_pages(self) -> int:
        n = len(self._catalog_items or [])
        if n <= 0:
            return 1
        return max(1, (n + self._catalog_page_size - 1) // self._catalog_page_size)

    def _apply_catalog_page(self):
        total = self._catalog_total_pages()
        p = max(1, min(int(self._catalog_page or 1), total))
        self._catalog_page = p
        i0 = (p - 1) * self._catalog_page_size
        i1 = i0 + self._catalog_page_size
        page_items = (self._catalog_items or [])[i0:i1]
        self.catalog_view.load_items(page_items)
        self._update_catalog_pager()

    def _update_catalog_pager(self):
        """Actualiza solo las etiquetas y botones del paginador sin recargar items."""
        total = self._catalog_total_pages()
        p = self._catalog_page
        self.lbl_cat_page.setText(self.engine.config.tr("pager_page_label_fmt", "Página {p}/{t}").format(p=p, t=total))
        self.btn_cat_first.setEnabled(p > 1)
        self.btn_cat_prev.setEnabled(p > 1)
        self.btn_cat_next.setEnabled(p < total)
        self.btn_cat_last.setEnabled(p < total)

    def _set_catalog_page(self, page: int):
        self._catalog_page = int(page)
        self._apply_catalog_page()

    def _go_to_catalog_page(self):
        txt = (self.entry_cat_page.text() or "").strip()
        if not txt:
            return
        try:
            p = int(txt)
        except ValueError:
            return
        self.entry_cat_page.clear()
        self._set_catalog_page(p)

    def on_sort_changed(self, *_):
        val = self.sort_combo.currentData()
        if not val:
            return
        self.current_sort = str(val)
        self.engine.config.save_config("sort_order", self.current_sort)
        self.load_catalog()

    @staticmethod
    def _year_sort_value(item):
        raw = str(getattr(item, "año", "") or "").strip()
        # Extraer un año tipo 1999/2024 aunque venga con ruido.
        import re
        m = re.search(r"(19|20)\d{2}", raw)
        if m:
            try:
                return int(m.group(0))
            except Exception:
                return None
        return None

    def _sort_catalog_items(self, items):
        mode = (self.current_sort or "added_recent_desc").strip()
        if mode == "added_recent_asc":
            return sorted(
                items,
                key=lambda it: ((getattr(it, "ultima_actualizacion", None) is None), getattr(it, "ultima_actualizacion", None))
            )
        if mode == "year_asc":
            return sorted(
                items,
                key=lambda it: ((self._year_sort_value(it) is None), self._year_sort_value(it) or 9999, (getattr(it, "titulo", "") or "").lower()),
            )
        if mode == "year_desc":
            return sorted(
                items,
                key=lambda it: ((self._year_sort_value(it) is None), -(self._year_sort_value(it) or -1), (getattr(it, "titulo", "") or "").lower()),
            )
        if mode == "title_asc":
            return sorted(
                items,
                key=lambda it: ((getattr(it, "titulo", "") or "").lower(), -(self._year_sort_value(it) or -1)),
            )
        if mode == "title_desc":
            return sorted(
                items,
                key=lambda it: ((getattr(it, "titulo", "") or "").lower(), -(self._year_sort_value(it) or -1)),
                reverse=True,
            )
        # Default: más reciente agregado / actualizado primero.
        return sorted(
            items,
            key=lambda it: ((getattr(it, "ultima_actualizacion", None) is None), getattr(it, "ultima_actualizacion", None)),
            reverse=True
        )
