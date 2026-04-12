import logging
import threading
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, 
                             QPushButton, QScrollArea, QWidget, QLabel, QFrame, QSizePolicy)
from PySide6.QtCore import Qt, Signal, Slot, QRunnable, QThreadPool, QObject, QByteArray, QTimer
from PySide6.QtGui import QPixmap, QImage, QIcon
import shiboken6 as shiboken

class ThumbLoaderSignals(QObject):
    finished = Signal(QByteArray)

class ThumbLoader(QRunnable):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.signals = ThumbLoaderSignals()

    def run(self):
        try:
            import urllib.request
            req = urllib.request.Request(self.url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    self.signals.finished.emit(QByteArray(resp.read()))
        except:
            pass

class SearchResultRow(QFrame):
    clicked = Signal(dict)
    thumb_loaded = Signal(QImage)

    def __init__(self, result, engine=None):
        super().__init__()
        self.result = result
        self.engine = engine
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(100)
        self.setObjectName("SearchResult")
        self.setStyleSheet("""
            QFrame#SearchResult {
                background-color: #1a1a1a;
                border-radius: 8px;
                border: 1px solid #333;
            }
            QFrame#SearchResult:hover {
                background-color: #252525;
                border: 1px solid #1f6aa5;
            }
        """)

        layout = QHBoxLayout(self)

        # Thumbnail
        self.img_label = QLabel()
        self.img_label.setFixedSize(60, 90)
        self.img_label.setScaledContents(True)
        self.img_label.setStyleSheet("background-color: #000; border-radius: 4px;")
        layout.addWidget(self.img_label)

        # Info
        info_f = QWidget()
        info_l = QVBoxLayout(info_f)

        title = result.get('title') or result.get('name') or "Sin Título"
        year = (result.get('release_date') or result.get('first_air_date', ''))[:4]
        
        m_type_key = 'lbl_movie' if result.get('media_type') == 'movie' else 'lbl_tv'
        m_type_def = "🎬 Película" if result.get('media_type') == 'movie' else "📺 Serie"
        mtype = self.engine.config.tr(m_type_key, m_type_def) if self.engine else m_type_def

        self.lbl_title = QLabel(f"{title} ({year})")
        self.lbl_title.setStyleSheet("font-weight: bold; font-size: 16px; color: #fff;")

        self.lbl_meta = QLabel(f"{mtype} • {result.get('vote_average', 0)} ★")
        self.lbl_meta.setStyleSheet("color: #888; font-size: 13px;")

        info_l.addWidget(self.lbl_title)
        info_l.addWidget(self.lbl_meta)
        layout.addWidget(info_f, 1)

        # Conectar señal de carga de imagen
        self.thumb_loaded.connect(self.set_thumbnail)

        # Load thumbnail
        poster = result.get('poster_path')
        if poster:
            self._load_thumb(f"https://image.tmdb.org/t/p/w92{poster}")

    @Slot(QImage)
    def set_thumbnail(self, img):
        if not shiboken.isValid(self) or img.isNull(): return
        try:
            self.img_label.setPixmap(QPixmap.fromImage(img))
        except RuntimeError:
            pass

    def _load_thumb(self, url):
        # Migrado a QThreadPool para evitar ráfagas de hilos
        loader = ThumbLoader(url)
        self._loader = loader # PREVENIR CRASH: Guardar referencia
        def on_finished(img_data):
            if shiboken.isValid(self):
                try:
                    img = QImage.fromData(img_data)
                    self.thumb_loaded.emit(img)
                except Exception:
                    pass
        loader.signals.finished.connect(on_finished)
        QThreadPool.globalInstance().start(loader)

    def mousePressEvent(self, event):
        self.clicked.emit(self.result)

class SearchSignals(QObject):
    finished = Signal(list)

class SearchWorker(QRunnable):
    def __init__(self, tmdb_client, query, language='es-MX'):
        super().__init__()
        self.tmdb = tmdb_client
        self.query = query
        self.language = language
        self.signals = SearchSignals()

    def run(self):
        try:
            results = self.tmdb.search(self.query, language=self.language)
            self.signals.finished.emit(results)
        except Exception as e:
            logging.error(f"❌ Error en búsqueda TMDb (Hilo): {e}")
            self.signals.finished.emit([])

class SearchDialog(QDialog):
    result_selected = Signal(dict)
    search_finished = Signal(list) 

    def __init__(self, tmdb_client, parent=None):
        super().__init__(parent)
        self.tmdb = tmdb_client
        self.engine = parent.engine if parent and hasattr(parent, 'engine') else None
        
        title = self.engine.config.tr('lbl_search_title', "Buscador Maestro TMDb") if self.engine else "Buscador Maestro TMDb"
        self.setWindowTitle(title)
        self.setMinimumSize(600, 700)
        self.setModal(True)
        self.setWindowModality(Qt.WindowModal)
        
        self.setStyleSheet("""
            QDialog, QFrame#SearchMain { background-color: #0f0f0f; border: 1px solid #333; border-radius: 20px; }
            QLineEdit { 
                background-color: #1a1a1a; border: 1px solid #333; 
                border-radius: 20px; padding: 10px 20px; font-size: 16px; color: #fff;
            }
            QLabel { color: #e0e0e0; }
            QPushButton#CloseButton { background-color: #222; color: #fff; border-radius: 15px; font-weight: bold; }
            QPushButton#CloseButton:hover { background-color: #c62828; }
        """)

        self.setup_ui()
        self.search_finished.connect(self._render_results)

    def setup_ui(self):
        main_v = QVBoxLayout(self)
        main_v.setContentsMargins(0, 0, 0, 0)
        
        self.main_frame = QFrame()
        self.main_frame.setObjectName("SearchMain")
        main_v.addWidget(self.main_frame)
        
        layout_outer = QVBoxLayout(self.main_frame)
        
        # Botón Cerrar Superior
        top_row = QHBoxLayout()
        top_row.addStretch()
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(30, 30)
        self.btn_close.setObjectName("CloseButton")
        self.btn_close.clicked.connect(self.close_search)
        top_row.addWidget(self.btn_close)
        layout_outer.addLayout(top_row)

        layout = QVBoxLayout()
        layout.setContentsMargins(20, 0, 20, 20)
        layout_outer.addLayout(layout)

        # Usar traducción para el placeholder si está disponible
        placeholder = "🔍 Escribe el nombre de la película o serie..."
        if self.parent() and hasattr(self.parent(), 'engine'):
            placeholder = self.parent().engine.config.get('placeholder_tmdb_search', placeholder)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(placeholder)
        layout.addWidget(self.search_input)

        # Label de estado (Buscando...)
        self.searching_lbl = QLabel("")
        self.searching_lbl.setStyleSheet("color: #1f6aa5; font-size: 12px; margin-left: 10px;")
        layout.addWidget(self.searching_lbl)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.results_container)
        layout.addWidget(self.scroll)

        # Timer para búsqueda en tiempo real (300ms)
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.perform_search)
        self.search_input.textChanged.connect(self.on_text_changed)

    def on_text_changed(self):
        if not shiboken.isValid(self): return
        query = self.search_input.text().strip()
        if len(query) >= 2:
            status_text = self.engine.config.tr('lbl_searching', "Buscando coincidencias...") if self.engine else "Buscando coincidencias..."
            self.searching_lbl.setText(status_text)
            self.search_timer.start(300)
        else:
            self.searching_lbl.setText("")
            # Limpiar resultados de forma segura
            while self.results_layout.count():
                child = self.results_layout.takeAt(0)
                if child and child.widget():
                    child.widget().deleteLater()

    def close_search(self):
        main_win = self.window()
        if hasattr(main_win, 'close_modal'):
            main_win.close_modal()
        else:
            self.reject()

    def perform_search(self):
        if not shiboken.isValid(self): return
        query = self.search_input.text().strip()
        if len(query) < 2: 
            self.searching_lbl.setText("")
            return

        logging.info(f"🔎 Iniciando hilo de búsqueda para: '{query}'")

        def bg_search():
            results = []
            try:
                lang = self.engine._get_tmdb_lang() if self.engine else 'es-MX'
                results = self.tmdb.search(query, language=lang)
                logging.info(f"✅ Hilo de búsqueda finalizado. Resultados obtenidos: {len(results)}")
            except Exception as e:
                logging.error(f"❌ Error crítico en hilo de búsqueda: {e}")
            finally:
                if shiboken.isValid(self):
                    self.search_finished.emit(results)
        
        import threading
        t = threading.Thread(target=bg_search, name=f"SearchThread_{query[:10]}")
        t.daemon = True
        t.start()

    def perform_collection_search(self, collection_id):
        """Busca y muestra todos los títulos de una colección específica (Senior saga resolution)"""
        if not shiboken.isValid(self) or not collection_id: return
        
        # Senior Fix: Detener cualquier búsqueda automática por texto para evitar que sobrescriba estos resultados
        self.search_timer.stop() 
        
        status_text = self.engine.config.tr('lbl_loading_collection', "Cargando saga completa...") if self.engine else "Cargando saga completa..."
        self.searching_lbl.setText(status_text)
        
        def bg_collection():
            results = []
            try:
                lang = self.engine._get_tmdb_lang() if self.engine else 'es-MX'
                results = self.tmdb.get_collection(collection_id, language=lang)
                logging.info(f"✅ Saga resuelta. Títulos encontrados: {len(results)}")
            except Exception as e:
                logging.error(f"❌ Error obteniendo colección: {e}")
            finally:
                if shiboken.isValid(self):
                    self.search_finished.emit(results)
        
        import threading
        t = threading.Thread(target=bg_collection, name=f"CollectionThread_{collection_id}")
        t.daemon = True
        t.start()

    @Slot(list)
    def _render_results(self, results):
        if not shiboken.isValid(self): return
        
        try:
            self.searching_lbl.setText("")
        except RuntimeError: return
        
        try:
            # 2. Limpiar resultados anteriores de forma agresiva
            while self.results_layout.count():
                child = self.results_layout.takeAt(0)
                if child and child.widget():
                    child.widget().deleteLater()

            # 3. Si no hay resultados o el diálogo no es visible
            if not results or not self.isVisible():
                if results == []:
                    self._show_no_results()
                return

            # 4. Renderizar resultados válidos
            added_count = 0
            for res in results:
                if not shiboken.isValid(self) or not self.isVisible(): break
                if res.get('media_type') == 'person': continue
                
                title = res.get('title') or res.get('name')
                if not title: continue
                
                row = SearchResultRow(res, self.engine)
                try:
                    row.clicked.connect(self.on_result_clicked)
                    self.results_layout.addWidget(row)
                    added_count += 1
                except (RuntimeError, ValueError):
                    break

            if added_count > 0:
                logging.info(f"✨ Se renderizaron {added_count} resultados en la UI.")
            elif not results:
                self._show_no_results()

        except Exception as e:
            logging.error(f"❌ Fallo al renderizar resultados en UI: {e}")

    def _show_no_results(self):
        if not shiboken.isValid(self): return
        msg = self.engine.config.tr('lbl_no_results', "No se encontraron coincidencias.") if self.engine else "No se encontraron coincidencias."
        self.results_layout.addWidget(QLabel(msg))

    def on_result_clicked(self, result):
        if shiboken.isValid(self):
            self.result_selected.emit(result)
            self.close_search()
