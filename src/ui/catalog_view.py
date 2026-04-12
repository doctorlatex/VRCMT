from PySide6.QtWidgets import (QWidget, QVBoxLayout, QScrollArea, QGridLayout,
                             QLabel, QFrame, QSizePolicy, QPushButton, QHBoxLayout,
                             QProgressBar)
from PySide6.QtCore import Qt, Signal, Slot, QRunnable, QThreadPool, QSize, QTimer, QObject, QByteArray
from PySide6.QtGui import QPixmap, QImage, QPainter
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtCore import QUrl
import shiboken6 as shiboken
import logging
import os
import sys
import threading

# Windows + Python 3.13: OpenSSL/urlopen concurrente desde QThreadPool → access violation
# (native_trace.log). Una sola descarga HTTP de cartel a la vez en todo el proceso.
_HTTP_THUMB_SEM = threading.BoundedSemaphore(1)

from src.core.paths import resolve_local_existing_path


def _http_body_looks_like_html(body: bytes) -> bool:
    if not body:
        return False
    head = body.lstrip()[:400].lower()
    return head.startswith(b"<") or b"<html" in head or b"<!doctype" in head

class _CardThumbCanvas(QWidget):
    """Miniatura de tarjeta sin QLabel.setPixmap (HWND extra en Windows)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pm = QPixmap()
        self.setFixedSize(150, 225)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def clear(self):
        self._pm = QPixmap()
        self.update()

    def setPixmap(self, pm: QPixmap):
        self._pm = QPixmap(pm) if pm is not None and not pm.isNull() else QPixmap()
        self.update()

    def paintEvent(self, event):
        if self._pm.isNull():
            return
        p = QPainter(self)
        p.drawPixmap(self.rect(), self._pm, self._pm.rect())


class ImageSignals(QObject):
    finished = Signal(QByteArray, str)


class _ThumbBridge(QObject):
    """Conecta ImageLoader.finished al catálogo con un @Slot real (evita cierres raros con cierres/locales)."""

    def __init__(self, catalog, kid: str, seq: int):
        super().__init__(catalog)
        self._c = catalog
        self._k = kid
        self._s = seq

    @Slot(QByteArray, str)
    def on_finished(self, data, mid):
        try:
            if shiboken.isValid(self._c):
                self._c._apply_card_thumb(self._k, data, self._s)
        finally:
            self.deleteLater()


class ImageLoader(QRunnable):
    """Cargador de imágenes asíncrono con blindaje de errores (v2.11.8)"""
    def __init__(self, url, media_id):
        super().__init__()
        self.url = url
        self.media_id = media_id
        self.signals = ImageSignals()

    def run(self):
        try:
            self._run_inner()
        except Exception:
            logging.exception(
                "[poster] ImageLoader.run excepción media_id=%s url=%s",
                self.media_id,
                (self.url or "")[:120],
            )
            try:
                self.signals.finished.emit(QByteArray(), str(self.media_id))
            except Exception:
                pass

    def _run_inner(self):
        import urllib.request
        import urllib.parse

        u = (self.url or "").strip()
        ul = u.lower()

        # Windows + Python 3.13: urlopen(https) desde hilos del QThreadPool ha coincidido
        # con access violation (native_trace.log). En modo estable, no descargamos carteles remotos.
        # (Los carteles locales siguen funcionando.)
        if sys.platform == "win32" and (ul.startswith("https://") or ul.startswith("http://")):
            logging.warning(
                "[poster] ImageLoader omite descarga remota (win32 estabilidad) media_id=%s url=%s",
                self.media_id,
                u[:120],
            )
            self.signals.finished.emit(QByteArray(), str(self.media_id))
            return

        local_path = None
        if u.lower().startswith("file:"):
            parsed = urllib.parse.urlparse(u)
            try:
                local_path = urllib.request.url2pathname(parsed.path)
            except Exception:
                local_path = None
        elif u and not u.lower().startswith("http://") and not u.lower().startswith("https://"):
            try:
                local_path = resolve_local_existing_path(u)
            except OSError:
                local_path = None

        if local_path and os.path.isfile(local_path):
            try:
                with open(local_path, "rb") as f:
                    data = f.read()
                self.signals.finished.emit(QByteArray(data), str(self.media_id))
                return
            except Exception:
                self.signals.finished.emit(QByteArray(), str(self.media_id))
                return

        data = None
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        }
        if "twimg.com" in ul or "twitter.com" in ul or "x.com" in ul:
            headers["Referer"] = "https://twitter.com/"

        fetch_urls = [u]
        if "img.youtube.com" in ul and "maxresdefault" in ul:
            u_alt = u.replace("maxresdefault", "hqdefault")
            if u_alt != u:
                fetch_urls.append(u_alt)

        def _fetch_http_once(url: str):
            if not url:
                return None
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=12) as response:
                    if response.status != 200:
                        return None
                    raw = response.read()
                    if not raw:
                        return None
                    ct = (response.headers.get("Content-Type") or "").lower()
                    if "text/html" in ct:
                        logging.warning(
                            "[poster] urlopen Content-Type HTML url=%s ct=%s",
                            (url or "")[:120],
                            ct[:80],
                        )
                    elif _http_body_looks_like_html(raw):
                        logging.warning(
                            "[poster] urlopen cuerpo parece HTML url=%s",
                            (url or "")[:120],
                        )
                    else:
                        return raw
            except Exception:
                pass
            return None

        if ul.startswith("http://") or ul.startswith("https://"):
            _HTTP_THUMB_SEM.acquire()
            try:
                for fetch_u in dict.fromkeys(fetch_urls):
                    data = _fetch_http_once(fetch_u)
                    if data:
                        break
            finally:
                _HTTP_THUMB_SEM.release()
        else:
            for fetch_u in dict.fromkeys(fetch_urls):
                data = _fetch_http_once(fetch_u)
                if data:
                    break

        if data:
            self.signals.finished.emit(QByteArray(data), str(self.media_id))
        else:
            logging.warning(
                "[poster] ImageLoader sin datos media_id=%s url=%s",
                self.media_id,
                (self.url or "")[:120],
            )
            # Siempre notificar al modal/tarjeta para limpiar el cartel (evita icono roto centrado).
            self.signals.finished.emit(QByteArray(), str(self.media_id))

class MediaCard(QFrame):
    clicked = Signal(object)

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = item
        self.setFixedSize(160, 280)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("MediaCard")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.setStyleSheet("""
            QFrame#MediaCard { background-color: #111; border-radius: 12px; border: 1px solid #222; }
            QFrame#MediaCard:hover { border: 1px solid #1f6aa5; background-color: #161616; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        self.poster = _CardThumbCanvas()
        self.poster.setStyleSheet("border-radius: 8px; background-color: #080808;")
        layout.addWidget(self.poster)

        self.title_lbl = QLabel(item.titulo)
        self.title_lbl.setStyleSheet("color: #eee; font-size: 12px; font-weight: bold;")
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setAlignment(Qt.AlignCenter)
        self.title_lbl.setFixedHeight(35)
        layout.addWidget(self.title_lbl)

        # F2: Barra de progreso para series/anime / Progress bar for series & anime
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setStyleSheet("""
            QProgressBar { background-color: #222; border-radius: 2px; border: none; }
            QProgressBar::chunk { background-color: #1f6aa5; border-radius: 2px; }
        """)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_lbl = QLabel("")
        self._progress_lbl.setStyleSheet("color: #666; font-size: 10px;")
        self._progress_lbl.setAlignment(Qt.AlignCenter)
        self._progress_lbl.setFixedHeight(14)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._progress_lbl)
        # Ocultar hasta que se carguen los datos / Hide until data loaded
        self._progress_bar.setVisible(False)
        self._progress_lbl.setVisible(False)

        # N1: Badges de estado superpuestos sobre el poster (posicionamiento absoluto)
        self._badge_seen = QLabel("✓", self)
        self._badge_seen.setFixedSize(22, 22)
        self._badge_seen.setAlignment(Qt.AlignCenter)
        self._badge_seen.setStyleSheet(
            "background-color: rgba(39,174,96,220); color: #fff; border-radius: 11px; "
            "font-size: 12px; font-weight: bold;"
        )
        self._badge_seen.move(8, 8)
        self._badge_seen.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._badge_fav = QLabel("♥", self)
        self._badge_fav.setFixedSize(22, 22)
        self._badge_fav.setAlignment(Qt.AlignCenter)
        self._badge_fav.setStyleSheet(
            "background-color: rgba(231,76,60,220); color: #fff; border-radius: 11px; "
            "font-size: 12px; font-weight: bold;"
        )
        self._badge_fav.move(34, 8)
        self._badge_fav.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.refresh_badges(item)

    def refresh_badges(self, item):
        """Actualiza la visibilidad de los badges sin recrear la tarjeta."""
        self.item = item
        seen = bool(getattr(item, 'estado_visto', 0))
        fav  = bool(getattr(item, 'es_favorito', 0))
        if shiboken.isValid(self._badge_seen):
            self._badge_seen.setVisible(seen)
            self._badge_seen.raise_()
        if shiboken.isValid(self._badge_fav):
            self._badge_fav.setVisible(fav)
            self._badge_fav.raise_()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.item)
            event.accept()
            return
        super().mousePressEvent(event)

    def set_series_progress(self, seen: int, total: int):
        """F2: Muestra barra de progreso de episodios vistos / Shows episode progress bar."""
        if not shiboken.isValid(self) or total <= 0:
            return
        pct = int((seen / total) * 100)
        self._progress_bar.setValue(pct)
        self._progress_lbl.setText(f"{seen}/{total} eps")
        self._progress_bar.setVisible(True)
        self._progress_lbl.setVisible(True)
        # Color: verde si completado, azul si en progreso
        color = "#27ae60" if seen >= total else "#1f6aa5"
        self._progress_bar.setStyleSheet(f"""
            QProgressBar {{ background-color: #222; border-radius: 2px; border: none; }}
            QProgressBar::chunk {{ background-color: {color}; border-radius: 2px; }}
        """)

    def set_pixmap(self, pixmap):
        """Blindaje v3.6.5: Validación estricta antes de tocar C++"""
        if shiboken.isValid(self) and shiboken.isValid(self.poster):
            if pixmap is None or pixmap.isNull():
                self.poster.clear()
            else:
                self.poster.setPixmap(pixmap)
            # Re-elevar badges para que queden sobre el poster
            self._badge_seen.raise_()
            self._badge_fav.raise_()


# N2: Fila de vista lista ---------------------------------------------------
class MediaListRow(QFrame):
    """Fila compacta para la vista Lista (alternativa a la cuadrícula de tarjetas)."""
    clicked = Signal(object)

    _TIPO_EMOJI = {
        'Pelicula': '🎬', 'Video': '🎬', 'Serie': '📺',
        'Anime': '⛩️', 'Stream/Imagen': '📸',
    }

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = item
        self.setMinimumHeight(52)
        self.setMaximumHeight(52)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("MediaListRow")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("""
            QFrame#MediaListRow {
                background-color: #111; border-radius: 8px; border: 1px solid #1e1e1e;
            }
            QFrame#MediaListRow:hover {
                background-color: #161616; border: 1px solid #1f6aa5;
            }
        """)
        hl = QHBoxLayout(self)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.setSpacing(10)

        tipo = getattr(item, 'tipo_contenido', 'Pelicula') or 'Pelicula'
        emoji = '⛩️' if getattr(item, 'es_anime', 0) else self._TIPO_EMOJI.get(tipo, '🎬')
        lbl_tipo = QLabel(emoji)
        lbl_tipo.setFixedWidth(26)
        lbl_tipo.setAlignment(Qt.AlignCenter)
        lbl_tipo.setStyleSheet("font-size: 18px;")
        hl.addWidget(lbl_tipo)

        lbl_title = QLabel(item.titulo or '')
        lbl_title.setStyleSheet("color: #eee; font-size: 13px; font-weight: bold;")
        lbl_title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hl.addWidget(lbl_title, 1)

        year = str(getattr(item, 'año', '') or '')
        lbl_year = QLabel(year)
        lbl_year.setFixedWidth(46)
        lbl_year.setAlignment(Qt.AlignCenter)
        lbl_year.setStyleSheet("color: #666; font-size: 12px;")
        hl.addWidget(lbl_year)

        rating = float(getattr(item, 'calificacion_personal', 0) or 0)
        lbl_rating = QLabel(f"⭐ {rating:.1f}" if rating > 0 else "—")
        lbl_rating.setFixedWidth(58)
        lbl_rating.setAlignment(Qt.AlignCenter)
        lbl_rating.setStyleSheet("color: #f9a825; font-size: 12px;")
        hl.addWidget(lbl_rating)

        if getattr(item, 'estado_visto', 0):
            b_seen = QLabel("✓")
            b_seen.setFixedSize(20, 20)
            b_seen.setAlignment(Qt.AlignCenter)
            b_seen.setStyleSheet(
                "background: rgba(39,174,96,180); color: #fff; border-radius: 10px; font-size: 11px; font-weight: bold;"
            )
            b_seen.setToolTip("Ya vista")
            hl.addWidget(b_seen)

        if getattr(item, 'es_favorito', 0):
            b_fav = QLabel("♥")
            b_fav.setFixedSize(20, 20)
            b_fav.setAlignment(Qt.AlignCenter)
            b_fav.setStyleSheet(
                "background: rgba(231,76,60,180); color: #fff; border-radius: 10px; font-size: 11px; font-weight: bold;"
            )
            b_fav.setToolTip("Favorita")
            hl.addWidget(b_fav)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.item)
            event.accept()
            return
        super().mousePressEvent(event)

class CatalogView(QScrollArea):
    media_selected = Signal(object)
    load_more_requested = Signal()   # N8: emitido cuando el scroll llega al final

    def __init__(self, engine, parent=None):
        # Blindaje Senior: Asegurar que el padre sea un QWidget válido o None
        # Esto previene el TypeError si engine se pasa accidentalmente como parent
        if parent is not None and not isinstance(parent, QWidget):
            parent = None
        super().__init__(parent)
        self.engine = engine
        self._view_mode = 'grid'   # N2: 'grid' | 'list'
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("background-color: transparent; border: none;")
        
        self.container = QWidget()
        self.container.setStyleSheet("background-color: transparent;")
        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(15)
        self.grid.setContentsMargins(10, 10, 10, 10)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        
        self.setWidget(self.container)
        if sys.platform == "win32":
            na = Qt.WidgetAttribute
            self.setAttribute(na.WA_NativeWindow, False)
            vp = self.viewport()
            vp.setAttribute(na.WA_NativeWindow, False)
            try:
                vp.setAttribute(na.WA_DontCreateNativeAncestors, True)
            except AttributeError:
                pass
            self.container.setAttribute(na.WA_NativeWindow, False)
            for _sb in (self.horizontalScrollBar(), self.verticalScrollBar()):
                _sb.setAttribute(na.WA_NativeWindow, False)
        self.cards = {}  # str(id) -> MediaCard | MediaListRow
        self._poster_load_seq = {}  # str(id) -> int; evita que un fallo tarde borre un thumb ya cargado
        self._is_loading = False
        self._nam = QNetworkAccessManager(self)
        self._net_replies = {}  # kid -> QNetworkReply (evitar GC)

        # N8: Detectar cuando el scroll llega al final para emitir load_more_requested
        self._load_more_locked = False   # evita disparos repetidos durante la carga
        self.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)

    # N8: Scroll to load more -----------------------------------------------
    def _on_scroll_changed(self, value: int):
        if self._load_more_locked:
            return
        sb = self.verticalScrollBar()
        if sb.maximum() > 0 and value >= sb.maximum() - 60:
            self._load_more_locked = True   # bloquear hasta que append_items termine
            self.load_more_requested.emit()

    def append_items(self, items):
        """N8: Agrega items a la vista SIN borrar los existentes (scroll infinito).
        Los items se insertan a continuacion de los ya visibles."""
        if self._is_loading or not items:
            self._load_more_locked = False
            return
        self._is_loading = True
        view_mode = self._view_mode
        existing_count = len(self.cards)

        def _process():
            if not shiboken.isValid(self):
                self._is_loading = False
                self._load_more_locked = False
                return
            for i, item in enumerate(items):
                real_i = existing_count + i
                kid = str(item.id)
                if kid in self.cards:
                    continue
                if view_mode == 'list':
                    card = MediaListRow(item)
                    card.clicked.connect(self._emit_media_selected)
                    self.cards[kid] = card
                    self.grid.addWidget(card, real_i, 0)
                else:
                    card = MediaCard(item)
                    card.clicked.connect(self._emit_media_selected)
                    self.cards[kid] = card
                    self.grid.addWidget(card, real_i // 5, real_i % 5)

                    poster_src = (item.poster_path or "").strip()
                    if getattr(item, 'tipo_contenido', '') == 'Stream/Imagen':
                        if not poster_src or (
                            not poster_src.lower().startswith(("http://", "https://"))
                            and not os.path.isfile(poster_src)
                        ):
                            wn = (getattr(item, 'world_name', '') or getattr(item, 'titulo', '') or '').strip()
                            if wn:
                                try:
                                    cover = self.engine.img_manager.get_album_cover(wn)
                                    if cover:
                                        poster_src = cover
                                except Exception:
                                    pass
                    if poster_src:
                        self._poster_load_seq[kid] = self._poster_load_seq.get(kid, 0) + 1
                        seq = self._poster_load_seq[kid]
                        if poster_src.lower().startswith("http://") or poster_src.lower().startswith("https://"):
                            self._fetch_thumb_qt(poster_src, kid, seq)
                        else:
                            loader = ImageLoader(poster_src, item.id)
                            bridge = _ThumbBridge(self, kid, seq)
                            loader.signals.finished.connect(bridge.on_finished)
                            QThreadPool.globalInstance().start(loader)

            QTimer.singleShot(200, self.adjust_grid)
            self._is_loading = False
            # Desbloquear después de un breve cooldown para evitar re-disparo inmediato
            QTimer.singleShot(600, self._unlock_load_more)

        QTimer.singleShot(10, _process)

    def _unlock_load_more(self):
        if shiboken.isValid(self):
            self._load_more_locked = False

    # N2: Cambio de modo de vista -------------------------------------------
    def set_view_mode(self, mode: str):
        """Cambia entre 'grid' y 'list'. Requiere llamar a load_items nuevamente."""
        if mode not in ('grid', 'list'):
            return
        self._view_mode = mode
        # Ajustar spacing del grid según el modo
        self.grid.setSpacing(15 if mode == 'grid' else 6)

    def _fetch_thumb_qt(self, url: str, kid: str, seq: int):
        """Descarga de imagen en hilo GUI (QtNetwork) para evitar SSL en hilos Python."""
        if not shiboken.isValid(self):
            return
        if not url:
            self._apply_card_thumb(kid, QByteArray(), seq)
            return
        qurl = QUrl(url)
        req = QNetworkRequest(qurl)
        req.setRawHeader(b"User-Agent", b"Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        reply = self._nam.get(req)
        self._net_replies[kid] = reply

        def _done():
            try:
                if self._poster_load_seq.get(kid, 0) != seq:
                    return
                raw = reply.readAll()
                self._apply_card_thumb(kid, raw, seq)
            finally:
                try:
                    reply.deleteLater()
                except Exception:
                    pass
                if self._net_replies.get(kid) is reply:
                    self._net_replies.pop(kid, None)

        reply.finished.connect(_done)

    def _emit_media_selected(self, item):
        # #region agent log
        import logging

        from src.debug_ac5f85 import dbg, flow_begin, flow_click_trace, flow_click_trace_reset

        flow_begin()
        flow_click_trace_reset()
        flow_click_trace(
            "catalog_card_mouse_press_emit",
            item_id=str(getattr(item, "id", "")),
            titulo=(getattr(item, "titulo", None) or "")[:80],
        )
        logging.info(
            "[VRCMT-UI] card_click id=%s",
            str(getattr(item, "id", "")),
        )
        dbg(
            "H0",
            "CatalogView._emit_media_selected",
            "card_click",
            {
                "item_id": str(getattr(item, "id", "")),
                "titulo": (getattr(item, "titulo", None) or "")[:120],
                "tipo": getattr(item, "tipo_contenido", None),
            },
        )
        try:
            if (getattr(item, "titulo", "") or "") == "Video Playback":
                from src.debug_ac5f85 import dbg_capture_active_window

                dbg_capture_active_window("video_card_click")
        except Exception:
            pass
        # #endregion
        flow_click_trace("catalog_signal_media_selected_emitted")
        self.media_selected.emit(item)

    def load_items(self, items):
        """Carga Atómica v3.6.5: Blindaje total contra cierres por memoria"""
        if self._is_loading: return
        self._is_loading = True
        self._load_more_locked = False  # N8: reset al cargar nueva página

        # 1. Desconectar y limpiar de forma diferida para no bloquear el hilo principal
        self.cards.clear()
        self._poster_load_seq.clear()
        while self.grid.count():
            child = self.grid.takeAt(0)
            if child.widget():
                w = child.widget()
                if shiboken.isValid(w):
                    w.setParent(None)
                    w.deleteLater()

        # 2. Reconstrucción por lotes (Batching) para fluidez
        view_mode = self._view_mode

        def process_items():
            if not shiboken.isValid(self): return

            # F2: Batch query de progreso de episodios para series/anime
            # F2: Batch query for episode progress on series/anime items
            _progress_map = {}
            try:
                from src.db.models import Multimedia as _MM
                from peewee import fn as _fn
                serie_titles = list({
                    it.titulo for it in items
                    if getattr(it, 'tipo_contenido', '') in ('Serie',) or getattr(it, 'es_anime', 0)
                })
                if serie_titles:
                    rows = (
                        _MM.select(
                            _MM.titulo,
                            _fn.COUNT(_MM.id).alias('total'),
                            _fn.SUM(_MM.estado_visto).alias('seen'),
                        )
                        .where(_MM.titulo.in_(serie_titles))
                        .group_by(_MM.titulo)
                    )
                    _progress_map = {
                        r.titulo: (int(r.seen or 0), int(r.total or 0))
                        for r in rows
                    }
            except Exception as _pe:
                logging.debug("F2 progress batch: %s", _pe)

            for i, item in enumerate(items):
                kid = str(item.id)

                if view_mode == 'list':
                    # N2: Vista lista — fila compacta sin imágenes
                    card = MediaListRow(item)
                    card.clicked.connect(self._emit_media_selected)
                    self.cards[kid] = card
                    self.grid.addWidget(card, i, 0)
                else:
                    # Vista cuadrícula — tarjeta con poster
                    card = MediaCard(item)
                    card.clicked.connect(self._emit_media_selected)
                    self.cards[kid] = card
                    self.grid.addWidget(card, i // 5, i % 5)

                    poster_src = (item.poster_path or "").strip()
                    if getattr(item, 'tipo_contenido', '') == 'Stream/Imagen':
                        if not poster_src or (
                            not poster_src.lower().startswith(("http://", "https://"))
                            and not os.path.isfile(poster_src)
                        ):
                            wn = (getattr(item, 'world_name', '') or getattr(item, 'titulo', '') or '').strip()
                            if wn:
                                try:
                                    cover = self.engine.img_manager.get_album_cover(wn)
                                    if cover:
                                        poster_src = cover
                                except Exception:
                                    pass
                    if poster_src:
                        self._poster_load_seq[kid] = self._poster_load_seq.get(kid, 0) + 1
                        seq = self._poster_load_seq[kid]
                        if poster_src.lower().startswith("http://") or poster_src.lower().startswith("https://"):
                            self._fetch_thumb_qt(poster_src, kid, seq)
                        else:
                            loader = ImageLoader(poster_src, item.id)
                            bridge = _ThumbBridge(self, kid, seq)
                            loader.signals.finished.connect(bridge.on_finished)
                            QThreadPool.globalInstance().start(loader)

                    # F2: Aplicar barra de progreso si hay datos de episodios
                    # F2: Apply progress bar if episode data is available
                    if _progress_map and item.titulo in _progress_map:
                        seen_c, total_c = _progress_map[item.titulo]
                        if total_c > 1 and hasattr(card, 'set_series_progress'):
                            card.set_series_progress(seen_c, total_c)

            QTimer.singleShot(100, self.adjust_grid)
            self._is_loading = False

        QTimer.singleShot(10, process_items)

    def _apply_card_thumb(self, kid: str, img_data, seq: int):
        """Aplica o limpia el poster de una tarjeta solo si esta petición sigue siendo la vigente."""
        if not shiboken.isValid(self):
            return
        if self._poster_load_seq.get(kid, 0) != seq:
            return
        card = self.cards.get(kid)
        if not card or not shiboken.isValid(card):
            return
        try:
            if not img_data:
                if shiboken.isValid(card.poster):
                    card.poster.clear()
                return
            img = QImage.fromData(img_data)
            if not img.isNull():
                card.set_pixmap(QPixmap.fromImage(img))
            elif shiboken.isValid(card.poster):
                card.poster.clear()
        except Exception:
            pass

    def update_single_card(self, item):
        """Actualiza quirúrgicamente una tarjeta específica sin recargar todo el catálogo (v3.6.7)"""
        if not shiboken.isValid(self): return
        kid = str(item.id)
        card = self.cards.get(kid)
        if card and shiboken.isValid(card):
            card.item = item
            # N1: Actualizar badges (visto/favorito) sin recrear la tarjeta
            if hasattr(card, 'refresh_badges'):
                card.refresh_badges(item)
            if hasattr(card, 'title_lbl') and shiboken.isValid(card.title_lbl):
                card.title_lbl.setText(item.titulo)
            if self._view_mode == 'list':
                return  # Lista no carga imágenes
            poster_src = (item.poster_path or "").strip()
            if getattr(item, 'tipo_contenido', '') == 'Stream/Imagen':
                if not poster_src or (
                    not poster_src.lower().startswith(("http://", "https://"))
                    and not os.path.isfile(poster_src)
                ):
                    wn = (getattr(item, 'world_name', '') or getattr(item, 'titulo', '') or '').strip()
                    if wn:
                        try:
                            cover = self.engine.img_manager.get_album_cover(wn)
                            if cover:
                                poster_src = cover
                        except Exception:
                            pass
            if poster_src:
                self._poster_load_seq[kid] = self._poster_load_seq.get(kid, 0) + 1
                seq = self._poster_load_seq[kid]
                if poster_src.lower().startswith("http://") or poster_src.lower().startswith("https://"):
                    self._fetch_thumb_qt(poster_src, kid, seq)
                else:
                    loader = ImageLoader(poster_src, item.id)
                    bridge = _ThumbBridge(self, kid, seq)
                    loader.signals.finished.connect(bridge.on_finished)
                    QThreadPool.globalInstance().start(loader)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(100, self.adjust_grid)

    def adjust_grid(self):
        """Cálculo responsivo blindado v3.6.5 + soporte N2 lista."""
        if not shiboken.isValid(self) or self._is_loading: return
        try:
            valid_cards = [c for c in self.cards.values() if shiboken.isValid(c)]
            if self._view_mode == 'list':
                # N2: Una sola columna que ocupa todo el ancho
                for i, card in enumerate(valid_cards):
                    self.grid.removeWidget(card)
                    self.grid.addWidget(card, i, 0)
                self.grid.setColumnStretch(0, 1)
            else:
                width = self.viewport().width()
                col_width = 175
                cols = max(1, width // col_width)
                for i, card in enumerate(valid_cards):
                    self.grid.removeWidget(card)
                    self.grid.addWidget(card, i // cols, i % cols)
                self.grid.setColumnStretch(0, 0)
        except Exception as e:
            logging.debug(f"Ajuste de grid diferido: {e}")
