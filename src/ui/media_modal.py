from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QTextEdit, QFrame, QScrollArea, QWidget, QLineEdit,
                             QSlider, QGridLayout, QMessageBox, QTabWidget, QSizePolicy)
from PySide6.QtCore import Qt, Signal, Slot, QTimer, QThreadPool, QRunnable, QByteArray, QObject
from PySide6.QtGui import QPixmap, QImage, QPainter, QCursor
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtCore import QUrl
import logging
import os
import sys
import time
import webbrowser
import requests
import shiboken6 as shiboken
from collections import deque
from functools import reduce
import operator

# ── Cache VRCMT en memoria (L1) ────────────────────────────────────────────────
# L1: dict en memoria, compartido entre instancias, TTL 10 min
# L2: tabla VRCMTRatingCache en SQLite local, TTL 1 hora
# L3: Firebase (solo si L1 y L2 miss o expirados)
_VRCMT_SHARED_CACHE: dict[str, tuple[float, int, float]] = {}
_VRCMT_L1_TTL  = 600    # 10 min en memoria
_VRCMT_L2_TTL  = 3600   # 1 hora en SQLite


def _vrcmt_cache_read_db(imdb_id: str) -> tuple[float, int] | None:
    """Lee el promedio VRCMT de la BD local. Retorna (avg, count) o None si expirado/ausente."""
    try:
        row = VRCMTRatingCache.get_or_none(VRCMTRatingCache.imdb_id == imdb_id)
        if row and (time.time() - row.updated_at) < _VRCMT_L2_TTL:
            return float(row.avg), int(row.count)
    except Exception:
        pass
    return None


def _vrcmt_cache_write_db(imdb_id: str, avg: float, count: int) -> None:
    """Guarda o actualiza el promedio VRCMT en la BD local (SQLite)."""
    try:
        VRCMTRatingCache.insert(
            imdb_id=imdb_id, avg=avg, count=count, updated_at=time.time()
        ).on_conflict(
            conflict_target=[VRCMTRatingCache.imdb_id],
            update={VRCMTRatingCache.avg: avg,
                    VRCMTRatingCache.count: count,
                    VRCMTRatingCache.updated_at: time.time()},
        ).execute()
    except Exception as e:
        logging.debug("_vrcmt_cache_write_db: %s", e)


def _vrcmt_cache_invalidate(imdb_id: str) -> None:
    """Invalida el cache L1 y L2 para un imdb_id (ej. al guardar calificación propia)."""
    _VRCMT_SHARED_CACHE.pop(imdb_id, None)
    try:
        VRCMTRatingCache.delete().where(VRCMTRatingCache.imdb_id == imdb_id).execute()
    except Exception:
        pass
from peewee import fn
from src.db.models import Multimedia, VRCMTRatingCache
from src.core.paths import resolve_local_existing_path

from src.ui.search_dialog import SearchDialog


def _img_data_to_bytes(img_data) -> bytes:
    if img_data is None:
        return b""
    if isinstance(img_data, (bytes, bytearray, memoryview)):
        return bytes(img_data)
    try:
        from PySide6.QtCore import QByteArray

        if isinstance(img_data, QByteArray):
            return bytes(img_data)
    except Exception:
        pass
    return bytes(img_data)


def _tipo_normalizado_es_stream_imagen(tipo) -> bool:
    """True si el tipo en DB equivale a Stream/Imagen (espacios, barra ancha, etc.)."""
    if tipo is None:
        return False
    x = (
        str(tipo)
        .strip()
        .lower()
        .replace(" ", "")
        .replace("／", "/")
        .replace("\uff0f", "/")
        .replace("\\", "/")
    )
    return x == "stream/imagen"


def _poster_path_usable(pp: str) -> bool:
    """poster_path en DB: HTTP(S) o file: siempre; ruta local solo si el archivo existe."""
    pp = (pp or "").strip()
    if not pp:
        return False
    pl = pp.lower()
    if pl.startswith("http://") or pl.startswith("https://"):
        return True
    if pl.startswith("file:"):
        return True
    return resolve_local_existing_path(pp) is not None


class _PosterCanvas(QWidget):
    """Cartel sin QLabel.setPixmap: en Windows eso suele crear un HWND hijo (ventanita suelta)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pm = QPixmap()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def clear(self):
        self._pm = QPixmap()
        self.update()

    def setPixmap(self, pm: QPixmap):
        self._pm = QPixmap(pm) if pm is not None and not pm.isNull() else QPixmap()
        self.update()

    def paintEvent(self, event):
        # #region agent log
        try:
            from src.debug_ac5f85 import dbg, flow_current

            rr = event.rect()
            dbg(
                "H-RENDER",
                "ui.media_modal._PosterCanvas.paintEvent",
                "enter",
                {
                    "flow": flow_current(),
                    "has_pm": bool(not self._pm.isNull()),
                    "size": [int(self.width()), int(self.height())],
                    "dirty": [int(rr.x()), int(rr.y()), int(rr.width()), int(rr.height())],
                    "vis": bool(self.isVisible()),
                },
            )
        except Exception:
            pass
        # #endregion
        if self._pm.isNull():
            return
        tw, th = self.width(), self.height()
        if tw < 2 or th < 2:
            return
        scaled = self._pm.scaled(
            tw,
            th,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if scaled.isNull():
            return
        p = QPainter(self)
        x = (tw - scaled.width()) // 2
        y = (th - scaled.height()) // 2
        p.drawPixmap(x, y, scaled)


class AccessCheckerSignals(QObject):
    """Emite el resultado en el hilo del pool; el slot debe ir con QueuedConnection al GUI."""
    finished = Signal(bool)


class AccessChecker(QRunnable):
    """Validador asíncrono de Triple Candado para evitar bloqueos de UI (v3.3).
    No llama callbacks que toquen widgets desde run(): solo emite finished."""
    def __init__(self, firebase, discord_id, world_id, signal_parent=None):
        super().__init__()
        self.firebase = firebase
        self.discord_id = discord_id
        self.world_id = world_id
        # Sin parent: evita Access Violation si el modal se destruye mientras
        # el hilo de Firebase sigue corriendo y emite la señal.
        self.signals = AccessCheckerSignals()

    def run(self):
        # #region agent log
        import json as _j, time as _t, threading as _th
        open('debug-6ee757.log','a').write(_j.dumps({"sessionId":"6ee757","timestamp":int(_t.time()*1000),"location":"media_modal:AccessChecker.run:start","message":"access checker start (queue-based)","data":{"world_id":str(self.world_id)[:40],"thread":_th.current_thread().name},"hypothesisId":"H-AC"})+'\n')
        # #endregion
        import queue as _q
        result_q = _q.Queue()
        discord_id = self.discord_id
        world_id = self.world_id

        def _op():
            return self.firebase.verificar_triple_candado(discord_id, world_id)

        def _cb(result, error):
            result_q.put((result, error))

        self.firebase.run_firebase_async(_op, _cb)

        try:
            result, error = result_q.get(timeout=30)
            has_access = bool(result) if error is None else True
        except Exception as e:
            logging.debug(f"AccessChecker: {e}")
            has_access = True

        # #region agent log
        open('debug-6ee757.log','a').write(_j.dumps({"sessionId":"6ee757","timestamp":int(_t.time()*1000),"location":"media_modal:AccessChecker.run:done","message":"access checker done (no gRPC in this thread)","data":{"has_access":has_access,"thread":_th.current_thread().name},"hypothesisId":"H-AC"})+'\n')
        # #endregion
        self.signals.finished.emit(has_access)

class _StarRatingBar(QWidget):
    """Barra de calificación con 10 estrellas interactivas (escala 0.0–10.0).

    - Hover: muestra estrellas provisionales en amarillo claro.
    - Click izquierdo: fija la calificación y emite rating_changed.
    - Click derecho o clic en estrella ya seleccionada: resetea a 0.
    - La puntuación se muestra como "8.0 ★" debajo de las estrellas.
    """
    rating_changed = Signal(float)

    _STAR_FULL  = "★"
    _STAR_EMPTY = "☆"
    _COLOR_ACTIVE  = "#FFD700"   # dorado
    _COLOR_HOVER   = "#FFE566"   # amarillo claro
    _COLOR_EMPTY   = "#555555"   # gris
    _COLOR_LABEL   = "#cccccc"

    def __init__(self, parent=None, max_stars: int = 10):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
        self._max = max_stars
        self._value = 0.0     # calificación actual (0–10)
        self._hover = -1      # índice hover (-1 = ninguno)

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(4)

        # Fila de estrellas
        star_row = QHBoxLayout()
        star_row.setContentsMargins(0, 0, 0, 0)
        star_row.setSpacing(2)
        self._star_labels: list[QLabel] = []
        for i in range(self._max):
            lbl = QLabel(self._STAR_EMPTY)
            lbl.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFixedSize(24, 24)
            lbl.setStyleSheet(f"color: {self._COLOR_EMPTY}; font-size: 18px;")
            lbl.setCursor(QCursor(Qt.PointingHandCursor))
            lbl.setToolTip(f"{i + 1}.0")
            star_row.addWidget(lbl)
            self._star_labels.append(lbl)
        star_row.addStretch()
        main_lay.addLayout(star_row)

        # Label de valor numérico
        self._val_lbl = QLabel("Toca para calificar")
        self._val_lbl.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
        self._val_lbl.setAlignment(Qt.AlignCenter)
        self._val_lbl.setStyleSheet(f"color: {self._COLOR_LABEL}; font-size: 11px;")
        main_lay.addWidget(self._val_lbl)

        # Instalar filtro de eventos en las estrellas
        for i, lbl in enumerate(self._star_labels):
            lbl.installEventFilter(self)

    # ── API pública ──────────────────────────────────────────────────────────
    def set_value(self, v: float):
        self._value = max(0.0, min(float(v), float(self._max)))
        self._hover = -1
        self._refresh_stars(filled_up_to=int(self._value) - 1)
        self._refresh_label(self._value)

    def get_value(self) -> float:
        return self._value

    # ── Eventos ──────────────────────────────────────────────────────────────
    def eventFilter(self, obj, event):
        try:
            idx = self._star_labels.index(obj)
        except ValueError:
            return super().eventFilter(obj, event)

        et = event.type()
        if et == event.Type.Enter:
            self._hover = idx
            self._refresh_stars(filled_up_to=idx, hover=True)
            self._refresh_label(float(idx + 1), provisional=True)

        elif et == event.Type.Leave:
            self._hover = -1
            self._refresh_stars(filled_up_to=int(self._value) - 1)
            self._refresh_label(self._value)

        elif et == event.Type.MouseButtonPress:
            btn = event.button()
            if btn == Qt.RightButton:
                # Botón derecho → resetear
                self._value = 0.0
                self._hover = -1
                self._refresh_stars(filled_up_to=-1)
                self._refresh_label(0.0)
                self.rating_changed.emit(0.0)
            elif btn == Qt.LeftButton:
                new_val = float(idx + 1)
                if new_val == self._value:
                    # Clic en estrella ya activa → resetear
                    new_val = 0.0
                self._value = new_val
                self._hover = -1
                self._refresh_stars(filled_up_to=int(self._value) - 1)
                self._refresh_label(self._value)
                self.rating_changed.emit(self._value)

        return super().eventFilter(obj, event)

    # ── Internos ─────────────────────────────────────────────────────────────
    def _refresh_stars(self, filled_up_to: int, hover: bool = False):
        color_filled = self._COLOR_HOVER if hover else self._COLOR_ACTIVE
        for i, lbl in enumerate(self._star_labels):
            if i <= filled_up_to:
                lbl.setText(self._STAR_FULL)
                lbl.setStyleSheet(f"color: {color_filled}; font-size: 18px;")
            else:
                lbl.setText(self._STAR_EMPTY)
                lbl.setStyleSheet(f"color: {self._COLOR_EMPTY}; font-size: 18px;")

    def _refresh_label(self, v: float, provisional: bool = False):
        if v <= 0:
            txt = "Toca para calificar"
            style = f"color: {self._COLOR_LABEL}; font-size: 11px; font-style: italic;"
        else:
            txt = f"{v:.1f} / 10  ★"
            style = f"color: {self._COLOR_ACTIVE}; font-size: 12px; font-weight: bold;"
            if provisional:
                style = f"color: {self._COLOR_HOVER}; font-size: 12px;"
        self._val_lbl.setText(txt)
        self._val_lbl.setStyleSheet(style)


class _RatingUploadSignals(QObject):
    finished = Signal(str)   # emite imdb_id cuando el upload termina (éxito o error)


class _RatingUploadWorker(QRunnable):
    """Sube una calificación a Firebase en background sin bloquear la UI."""
    def __init__(self, firebase, discord_id: str, imdb_id: str, rating: float,
                 local_db_id: str = '', old_imdb_id: str = ''):
        super().__init__()
        self.setAutoDelete(True)
        self.signals = _RatingUploadSignals()
        self.firebase = firebase
        self.discord_id = discord_id
        self.imdb_id = imdb_id
        self.rating = rating
        self.local_db_id = local_db_id
        self.old_imdb_id = old_imdb_id

    def run(self):
        # #region agent log
        import json as _j, time as _t, threading as _th
        open('debug-6ee757.log','a').write(_j.dumps({"sessionId":"6ee757","timestamp":int(_t.time()*1000),"location":"media_modal:_RatingUploadWorker.run:start","message":"upload worker start (queue-based)","data":{"imdb":self.imdb_id,"thread":_th.current_thread().name},"hypothesisId":"H5"})+'\n')
        # #endregion
        import queue as _q
        result_q = _q.Queue()
        discord_id = self.discord_id
        imdb_id = self.imdb_id
        rating = self.rating
        local_db_id = self.local_db_id
        old_imdb_id = self.old_imdb_id

        def _op():
            self.firebase.sync_rating(
                discord_id, imdb_id, rating,
                local_db_id=local_db_id, old_imdb_id=old_imdb_id,
            )
            return 'ok'

        def _cb(result, error):
            result_q.put((result, error))

        self.firebase.run_firebase_async(_op, _cb)

        try:
            result, error = result_q.get(timeout=60)
            if error:
                import logging
                logging.error("_RatingUploadWorker: %s", error)
            # #region agent log
            open('debug-6ee757.log','a').write(_j.dumps({"sessionId":"6ee757","timestamp":int(_t.time()*1000),"location":"media_modal:_RatingUploadWorker.run:sync_done","message":"sync_rating returned OK (no gRPC in this thread)","data":{"imdb":imdb_id},"hypothesisId":"H5"})+'\n')
            # #endregion
        except Exception as e:
            import logging
            logging.error("_RatingUploadWorker timeout/error: %s", e)

        # #region agent log
        open('debug-6ee757.log','a').write(_j.dumps({"sessionId":"6ee757","timestamp":int(_t.time()*1000),"location":"media_modal:_RatingUploadWorker.run:finally_before_emit","message":"about to emit finished signal","data":{"imdb":imdb_id},"hypothesisId":"H5"})+'\n')
        # #endregion
        self.signals.finished.emit(imdb_id)
        # #region agent log
        open('debug-6ee757.log','a').write(_j.dumps({"sessionId":"6ee757","timestamp":int(_t.time()*1000),"location":"media_modal:_RatingUploadWorker.run:finally_after_emit","message":"emit done - thread about to exit (no gRPC state)","data":{"imdb":imdb_id},"hypothesisId":"H5"})+'\n')
        # #endregion


class _VRCMTRatingFetcherSignals(QObject):
    finished = Signal(float, int)   # (average, count)


class _VRCMTRatingFetcher(QRunnable):
    """Obtiene el rating comunitario VRCMT desde Firebase en background."""
    def __init__(self, firebase, imdb_id: str, signal_parent: QObject = None):
        super().__init__()
        self.setAutoDelete(True)
        self.firebase = firebase
        self.imdb_id = imdb_id
        # Sin parent: el QObject de signals NO debe ser hijo del modal.
        # Si fuera hijo, Qt lo destruiría cuando el modal se cierra/reemplaza,
        # causando un Access Violation cuando el hilo de fondo intenta emitir.
        self.signals = _VRCMTRatingFetcherSignals()

    def run(self):
        # #region agent log
        import json as _j, time as _t, threading as _th
        open('debug-6ee757.log','a').write(_j.dumps({"sessionId":"6ee757","timestamp":int(_t.time()*1000),"location":"media_modal:_VRCMTRatingFetcher.run:start","message":"fetcher run start (queue-based)","data":{"imdb_id":self.imdb_id,"thread":_th.current_thread().name},"hypothesisId":"H1,H3,H4,H5"})+'\n')
        # #endregion
        import queue as _q
        result_q = _q.Queue()
        imdb = self.imdb_id

        def _op():
            return self.firebase.get_vrcmt_rating(imdb)

        def _cb(result, error):
            result_q.put((result, error))

        self.firebase.run_firebase_async(_op, _cb)

        try:
            result, error = result_q.get(timeout=30)
            if error:
                raise error
            avg, count = result if result else (0.0, 0)
        except Exception as e:
            import logging
            logging.debug("_VRCMTRatingFetcher: %s", e)
            avg, count = 0.0, 0

        # #region agent log
        open('debug-6ee757.log','a').write(_j.dumps({"sessionId":"6ee757","timestamp":int(_t.time()*1000),"location":"media_modal:_VRCMTRatingFetcher.run:before_emit","message":"fetcher about to emit (no gRPC in this thread)","data":{"imdb_id":imdb,"avg":avg,"count":count},"hypothesisId":"H1,H3"})+'\n')
        # #endregion
        self.signals.finished.emit(float(avg), int(count))


class TrailerCheckerSignals(QObject):
    """Emite el YouTube key del tráiler encontrado (str) o cadena vacía si no existe."""
    finished = Signal(str)   # youtube_key o ""


class TrailerChecker(QRunnable):
    """Busca en TMDB si existe tráiler para un tmdb_id dado, sin bloquear la UI.

    Prueba idiomas en orden: primero el del usuario, luego 'en-US' como fallback
    universal (donde más trailers existen).  Acepta Trailer → Teaser → cualquier video.
    """
    _FALLBACK_LANGS = ('en-US',)

    def __init__(self, tmdb_client, media_type: str, tmdb_id: int,
                 primary_lang: str, signal_parent: QObject = None):
        super().__init__()
        self.setAutoDelete(True)
        self.tmdb = tmdb_client
        self.media_type = media_type
        self.tmdb_id = tmdb_id
        self.primary_lang = primary_lang
        # Sin parent: igual que _VRCMTRatingFetcher — evita Access Violation
        # cuando el modal se destruye mientras el hilo de TMDB sigue corriendo.
        self.signals = TrailerCheckerSignals()

    def _best_key(self, videos: list) -> str:
        return (
            next((x['key'] for x in videos if x.get('type') == 'Trailer' and x.get('key')), None)
            or next((x['key'] for x in videos if x.get('type') == 'Teaser' and x.get('key')), None)
            or next((x['key'] for x in videos if x.get('key')), None)
            or ''
        )

    def run(self):
        try:
            langs = [self.primary_lang] + [l for l in self._FALLBACK_LANGS if l != self.primary_lang]
            for lang in langs:
                try:
                    d = self.tmdb.get_details(self.media_type, self.tmdb_id, language=lang)
                    videos = d.get('videos', {}).get('results', [])
                    key = self._best_key(videos)
                    if key:
                        self.signals.finished.emit(key)
                        return
                except Exception as e:
                    logging.debug("TrailerChecker lang=%s err=%s", lang, e)
            self.signals.finished.emit('')
        except Exception as e:
            logging.debug("TrailerChecker.run error: %s", e)
            self.signals.finished.emit('')


class MediaModal(QFrame):
    """Panel de detalle incrustado en overlay. QDialog creaba en Windows una capa nativa
    extra (ventanita/icono de vídeo) aunque se usara como hijo; QFrame evita esa ventana."""
    data_changed = Signal(object)
    # Cada fila de versión añade varios QWidget; títulos genéricos (p. ej. "Video Playback" del log de VRChat)
    # pueden agrupar decenas de filas → muchos HWND en Windows y ventanas mini / cierre del proceso.
    _MAX_VERSION_ROWS = 15

    def __init__(self, item, engine, parent=None):
        super().__init__(parent)
        self.setObjectName("MediaModalShell")
        self.setWindowFlags(Qt.Widget)
        self.item = item
        self.engine = engine
        self._access_cache = {}
        self._access_pending = {}
        # Caché de tráiler: tmdb_id → youtube_key (str) o None (sin tráiler).
        # Evita rellamar a TMDB cada vez que se navega entre capítulos del mismo título.
        self._trailer_cache: dict = {}
        self._trailer_pending: set = set()
        # Caché de rating VRCMT: imdb_id → (average: float, count: int)
        self._vrcmt_rating_cache: dict = {}
        self._vrcmt_rating_pending: set = set()

        # Cola unificada GUI ← Firebase/TMDB worker threads.
        # Todos los callbacks de run_firebase_async depositan aquí; el timer de 50ms
        # los procesa en el hilo GUI sin emitir señales desde threads del pool (evita
        # el ACCESS_VIOLATION de Python 3.13 al destruir QObject desde thread no-GUI).
        import queue as _pyq
        self._fb_ui_q: _pyq.SimpleQueue = _pyq.SimpleQueue()
        self._fb_drain_timer = QTimer(self)
        self._fb_drain_timer.setInterval(50)
        self._fb_drain_timer.timeout.connect(self._drain_fb_queue)
        self._fb_drain_timer.start()

        # ── Sistema de calificación a Firebase ──────────────────────────────────
        # Flujo: star_click → guarda en BD local INMEDIATO → debounce 20 s → Firebase upload
        #        → (4 uploads/min máx.) → si se supera: freeze 5 min → desbloqueo.
        #
        # Debounce de 10 s: se reinicia en cada cambio de estrella.
        self._rating_debounce_timer = QTimer(self)
        self._rating_debounce_timer.setSingleShot(True)
        self._rating_debounce_timer.setInterval(20_000)
        self._rating_debounce_timer.timeout.connect(self._do_upload_rating)
        # Snapshots capturados en save_rating para que _do_upload_rating use
        # el ítem correcto aunque el usuario haya navegado a otro durante el debounce.
        self._pending_rating_imdb: str = ''
        self._pending_rating_value: float = 0.0
        self._pending_rating_local_id: str = ''
        # Rate-limit: máx. 4 uploads en una ventana de 60 s.
        self._rating_upload_times: deque = deque(maxlen=4)
        self._last_uploaded_rating: float | None = None
        self._last_uploaded_imdb: str = ''
        # Freeze: si se supera el rate-limit, bloquear 5 min.
        self._rating_frozen: bool = False
        self._rating_freeze_timer = QTimer(self)
        self._rating_freeze_timer.setSingleShot(True)
        self._rating_freeze_timer.setInterval(5 * 60 * 1000)   # 5 minutos
        self._rating_freeze_timer.timeout.connect(self._on_rating_unfreeze)
        # True tras guardar nota en BD local hasta que el catálogo reciba data_changed (o flush al cerrar).
        self._rating_catalog_pending_notify: bool = False
        # No setWindowTitle: en hijos incrustados en Windows a veces dispara HWND/título erróneo.
        self.setMinimumSize(950, 750)
        self._is_refreshing = False # Bandera anti-bucle
        self._ep_page_size = 20
        self._ep_page = 1
        self._eps_cache = []
        # Collapsible sections state / Estado de secciones colapsables
        self._links_collapsed = False
        self._eps_collapsed   = False
        self._links_section_visible = False
        self._eps_section_visible   = False
        self._nam = QNetworkAccessManager(self)
        self._poster_reply = None
        
        try:
            from src.core.themes import get_modal_stylesheet as _gms
            _active_theme = engine.config.get_val('theme', 'Oscuro') if engine else 'Oscuro'
            self.setStyleSheet(_gms(_active_theme))
        except Exception:
            pass

        self.setup_ui()
        self.connect_actions()
        QTimer.singleShot(0, self._deferred_initial_refresh)
        # #region agent log
        try:
            import logging

            from src.debug_ac5f85 import dbg, flow_current

            logging.info("[VRCMT-UI] MediaModal.__init__ done id=%s", str(getattr(item, "id", "")))
            dbg(
                "H4",
                "MediaModal.__init__",
                "after_setup",
                {
                    "flow": flow_current(),
                    "item_id": str(getattr(item, "id", "")),
                    "parent_cls": parent.metaObject().className()
                    if parent and shiboken.isValid(parent)
                    else None,
                },
            )
        except Exception:
            pass
        # #endregion

    def _request_access_once(self, discord_id, world_id, callback):
        """Single-flight access check per world usando run_firebase_async + cola GUI.
        No crea QRunnable: evita el ACCESS_VIOLATION de Python 3.13 al emitir Signal
        desde el thread del pool al destruirse el QObject de señales."""
        key = str(world_id or "").strip()
        if not key:
            callback(False)
            return
        if key in self._access_cache:
            callback(bool(self._access_cache.get(key)))
            return
        pending = self._access_pending.get(key)
        if pending is not None:
            pending.append(callback)
            return
        self._access_pending[key] = [callback]

        snap_key = key
        snap_discord = str(discord_id or '')

        def _op():
            return self.engine.firebase.verificar_triple_candado(snap_discord, snap_key)

        def _cb(result, error):
            ok = bool(result) if error is None and result is not None else False
            self._fb_ui_q.put(('access', snap_key, ok))

        self.engine.firebase.run_firebase_async(_op, _cb)

    def _embeddings_avoid_extra_hwnd(self):
        """Windows: viewport de QScrollArea, QTextEdit, QSlider y scrollbars → HWND extra."""
        if sys.platform != "win32":
            return
        na = Qt.WidgetAttribute
        self.setAttribute(na.WA_NativeWindow, False)
        mf = getattr(self, "main_frame", None)
        if mf is not None:
            mf.setAttribute(na.WA_NativeWindow, False)
        pl = getattr(self, "poster_label", None)
        if pl is not None:
            pl.setAttribute(na.WA_NativeWindow, False)
        sr = getattr(self, "star_rating", None)
        if sr is not None:
            sr.setAttribute(na.WA_NativeWindow, False)
        for sa in (getattr(self, "ver_scroll", None), getattr(self, "ep_scroll", None)):
            if sa is None:
                continue
            sa.setAttribute(na.WA_NativeWindow, False)
            vp = sa.viewport()
            vp.setAttribute(na.WA_NativeWindow, False)
            try:
                vp.setAttribute(na.WA_DontCreateNativeAncestors, True)
            except AttributeError:
                pass
            for _sb in (sa.horizontalScrollBar(), sa.verticalScrollBar()):
                _sb.setAttribute(na.WA_NativeWindow, False)
            inner = sa.widget()
            if inner is not None:
                inner.setAttribute(na.WA_NativeWindow, False)
        syn = getattr(self, "synopsis", None)
        if syn is not None:
            syn.setAttribute(na.WA_NativeWindow, False)

    def _get_clean_text(self, key, default):
        text = self.engine.config.tr(key, default)
        if text.endswith(':'): return text[:-1]
        return text

    def _theme_name(self) -> str:
        """Devuelve el nombre del tema activo para estilizar elementos inline."""
        try:
            return self.engine.config.get_val('theme', 'Oscuro') or 'Oscuro'
        except Exception:
            return 'Oscuro'

    def _tipo_stream_imagen_ui(self):
        return _tipo_normalizado_es_stream_imagen(getattr(self.item, "tipo_contenido", None))

    def _clear_poster_label(self):
        if shiboken.isValid(self):
            self.poster_label.clear()

    def _set_poster_pixmap_scaled(self, pm: QPixmap):
        """Asigna pixmap al lienzo; el escalado ocurre en paintEvent (evita QLabel nativo en Windows)."""
        if not shiboken.isValid(self) or pm.isNull():
            self._clear_poster_label()
            return
        if pm.width() < 2 or pm.height() < 2:
            self._clear_poster_label()
            return
        self.poster_label.setPixmap(pm)

    def setup_ui(self):
        main_v = QVBoxLayout(self)
        main_v.setContentsMargins(0, 0, 0, 0)
        
        self.main_frame = QFrame()
        self.main_frame.setObjectName("ModalMain")
        main_v.addWidget(self.main_frame)
        
        outer_l = QVBoxLayout(self.main_frame)
        
        # Top Close Button — estilo fijo independiente del tema para garantizar visibilidad
        top_row = QHBoxLayout()
        top_row.addStretch()
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(36, 36)
        self.btn_close.setObjectName("CloseButton")
        self.btn_close.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.75);
                border-radius: 18px;
                font-size: 17px;
                font-weight: bold;
                border: 1px solid rgba(255,255,255,0.18);
            }
            QPushButton:hover {
                background: #e53935;
                color: white;
                border-color: #e53935;
            }
            QPushButton:pressed { background: #b71c1c; }
        """)
        top_row.addWidget(self.btn_close)
        outer_l.addLayout(top_row)

        content_l = QHBoxLayout()
        content_l.setContentsMargins(30, 0, 30, 30)
        content_l.setSpacing(30)
        outer_l.addLayout(content_l)

        # 1. IZQUIERDA
        left_panel = QWidget()
        left_panel.setFixedWidth(280)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        left_layout.setAlignment(Qt.AlignTop)

        self.poster_label = _PosterCanvas()
        self.poster_label.setFixedSize(260, 390)
        self.poster_label.setStyleSheet("border-radius: 15px; border: 1px solid #333; background-color: #111;")
        left_layout.addWidget(self.poster_label, 0, Qt.AlignCenter)

        left_layout.addSpacing(10)

        # ── Sección de calificación — solo visible si hay sesión Discord ──────
        self.rating_container = QWidget()
        self.rating_container.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
        rc_lay = QVBoxLayout(self.rating_container)
        rc_lay.setContentsMargins(0, 0, 0, 4)
        rc_lay.setSpacing(4)

        lbl_rating_title = QLabel(f"⭐ {self._get_clean_text('lbl_rating', 'Mi Nota')}")
        lbl_rating_title.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
        lbl_rating_title.setObjectName("SectionTitle")
        lbl_rating_title.setAlignment(Qt.AlignCenter)
        rc_lay.addWidget(lbl_rating_title)

        self.star_rating = _StarRatingBar(max_stars=10)
        rc_lay.addWidget(self.star_rating, 0, Qt.AlignCenter)

        # _lbl_rating_hint existe solo en memoria para que _update_rating_hint
        # no falle; no se agrega al layout (no visible en UI).
        self._lbl_rating_hint = QLabel("")
        self._lbl_rating_hint.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)

        left_layout.addWidget(self.rating_container)

        # Atributos de compatibilidad para código que referencia rating_slider/lbl_rating_val
        self.rating_slider = None   # ya no existe; se mantiene el atributo para evitar AttributeError
        self.lbl_rating_val = None

        self.btn_trailer = QPushButton(self._get_clean_text('btn_trailer', '▶️ Ver Tráiler Oficial'))
        self.btn_trailer.setObjectName("ActionButton")
        left_layout.addWidget(self.btn_trailer)

        row_btns = QHBoxLayout()
        self.btn_seen = QPushButton()
        self.btn_seen.setObjectName("SecondaryButton")
        self.btn_fav = QPushButton()
        self.btn_fav.setObjectName("SecondaryButton")
        row_btns.addWidget(self.btn_seen)
        row_btns.addWidget(self.btn_fav)
        left_layout.addLayout(row_btns)

        self.btn_delete = QPushButton(self._get_clean_text('btn_delete_cat', '🗑️ Eliminar del Catálogo'))
        self.btn_delete.setObjectName("DeleteButton")
        self.btn_anime = QPushButton()
        self.btn_anime.setObjectName("SecondaryButton")
        self.btn_anime.setMinimumHeight(35)
        left_layout.addWidget(self.btn_anime)
        left_layout.addWidget(self.btn_delete)

        content_l.addWidget(left_panel)

        # 2. DERECHA
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        # Sin Qt.AlignTop para que el QTabWidget (stretch=1) llene todo el alto disponible
        # No Qt.AlignTop so QTabWidget (stretch=1) fills all available height

        self.lbl_title = QLabel()
        self.lbl_title.setObjectName("Title")
        self.lbl_title.setWordWrap(True)
        right_layout.addWidget(self.lbl_title)

        self.lbl_meta = QLabel()
        self.lbl_meta.setObjectName("Meta")
        right_layout.addWidget(self.lbl_meta)

        # ── TABS: Detalles | Capítulos ─────────────────────────────────────
        # El tab de Capítulos ocupa todo el alto disponible para ver más filas.
        # Chapters tab fills full available height for better episode visibility.
        self.detail_tabs = QTabWidget()
        self.detail_tabs.setDocumentMode(False)
        try:
            from src.core.themes import get_tabs_stylesheet as _gts
            _active_theme = self.engine.config.get_val('theme', 'Oscuro')
            self.detail_tabs.setStyleSheet(_gts(_active_theme))
        except Exception:
            pass
        right_layout.addWidget(self.detail_tabs, 1)   # stretch=1 → llena el espacio restante

        # Tab 1: Detalles / Info
        _tab1 = QWidget()
        from PySide6.QtWidgets import QSizePolicy as _SP
        _tab1.setSizePolicy(_SP.Policy.Expanding, _SP.Policy.Expanding)
        tab1_layout = QVBoxLayout(_tab1)
        tab1_layout.setContentsMargins(0, 8, 0, 0)
        tab1_layout.setSpacing(10)
        self.detail_tabs.addTab(_tab1, self._get_clean_text('tab_details', '📋 Detalles / Info'))

        # Tab 2: Capítulos / Episodes (lista completa de capítulos a altura total)
        _tab2 = QWidget()
        _tab2.setSizePolicy(_SP.Policy.Expanding, _SP.Policy.Expanding)
        tab2_layout = QVBoxLayout(_tab2)
        tab2_layout.setContentsMargins(0, 8, 0, 0)
        tab2_layout.setSpacing(6)
        self._tab2_ref = _tab2   # referencia para setTabVisible
        self.detail_tabs.addTab(_tab2, self._get_clean_text('tab_episodes', '▶️ Reproducción / Watch'))
        # ──────────────────────────────────────────────────────────────────

        # Senior Fix: Colección, Director y Elenco
        self.lbl_collection = QLabel()
        self.lbl_collection.setCursor(Qt.PointingHandCursor)
        self.lbl_collection.setStyleSheet("color: #ffca28; font-weight: bold; margin-top: 5px;")
        self.lbl_collection.mousePressEvent = self._on_collection_clicked
        tab1_layout.addWidget(self.lbl_collection)

        self.lbl_credits = QLabel()
        self.lbl_credits.setWordWrap(True)
        self.lbl_credits.setStyleSheet("color: #aaa; font-size: 13px; margin-top: 5px;")
        tab1_layout.addWidget(self.lbl_credits)

        tab1_layout.addWidget(QLabel(f"📖 {self._get_clean_text('lbl_synopsis', 'Sinopsis')}", objectName="SectionTitle"))
        self.synopsis = QTextEdit()
        self.synopsis.setReadOnly(True)
        self.synopsis.setMinimumHeight(80)
        self.synopsis.setMaximumHeight(120)
        tab1_layout.addWidget(self.synopsis)

        # Fixers Grid
        # Fixer de metadata (IMDb + Etiquetas) → vive en Tab 1 (Detalles)
        fixer_frame = QFrame()
        fixer_frame.setStyleSheet("QFrame { background-color: #0f0f0f; border-radius: 8px; }")
        fix_l = QGridLayout(fixer_frame)
        fix_l.setContentsMargins(10, 10, 10, 8)
        fix_l.setHorizontalSpacing(10)
        fix_l.setVerticalSpacing(6)
        fix_l.setColumnStretch(1, 1)
        fix_l.setColumnMinimumWidth(0, 90)
        fix_l.setColumnMinimumWidth(2, 88)
        fix_l.setRowMinimumHeight(0, 30)
        fix_l.setRowMinimumHeight(1, 30)

        fix_l.addWidget(QLabel(f"🆔 {self._get_clean_text('lbl_imdb', 'ID IMDb')}"), 0, 0)
        self.entry_imdb = QLineEdit()
        self.entry_imdb.setMinimumWidth(150)
        self.entry_imdb.setFixedHeight(22)
        self.entry_imdb.setStyleSheet("min-height: 22px; max-height: 22px; padding: 1px 6px;")
        fix_l.addWidget(self.entry_imdb, 0, 1)
        self.btn_fix = QPushButton("🔧 Fix")
        self.btn_fix.setObjectName("ActionButton")
        self.btn_fix.setFixedSize(88, 28)
        fix_l.addWidget(self.btn_fix, 0, 2, Qt.AlignVCenter)

        fix_l.addWidget(QLabel(f"🏷️ {self._get_clean_text('lbl_tags', 'Etiquetas')}"), 1, 0)
        self.entry_tags = QLineEdit()
        self.entry_tags.setMinimumWidth(150)
        self.entry_tags.setFixedHeight(22)
        self.entry_tags.setStyleSheet("min-height: 22px; max-height: 22px; padding: 1px 6px;")
        fix_l.addWidget(self.entry_tags, 1, 1)
        self.btn_save_tags = QPushButton("💾 Guardar")
        self.btn_save_tags.setObjectName("SecondaryButton")
        self.btn_save_tags.setFixedSize(88, 28)
        fix_l.addWidget(self.btn_save_tags, 1, 2, Qt.AlignVCenter)

        tab1_layout.addWidget(fixer_frame)

        # Temporada/Episodio → vive en Tab 2 (Reproducción / Capítulos)
        # Season/Episode fixer lives in Tab 2 alongside the chapter list
        self.te_frame = QFrame()
        self.te_frame.setStyleSheet("QFrame { background-color: #0f0f0f; border-radius: 8px; }")
        te_grid = QGridLayout(self.te_frame)
        te_grid.setContentsMargins(10, 8, 10, 8)
        te_grid.setHorizontalSpacing(10)
        te_grid.setVerticalSpacing(0)
        te_grid.setColumnStretch(1, 1)
        te_grid.setColumnMinimumWidth(0, 90)
        te_grid.setColumnMinimumWidth(2, 88)
        te_grid.setRowMinimumHeight(0, 30)

        self.te_container = QWidget()
        te_box = QHBoxLayout(self.te_container)
        te_box.setContentsMargins(0, 0, 0, 0)
        te_box.setSpacing(8)

        self.entry_temp = QLineEdit()
        self.entry_temp.setFixedSize(92, 22)
        self.entry_temp.setStyleSheet("min-height: 22px; max-height: 22px; padding: 1px 4px;")
        self.entry_temp.setAlignment(Qt.AlignCenter)
        self.entry_ep = QLineEdit()
        self.entry_ep.setFixedSize(92, 22)
        self.entry_ep.setStyleSheet("min-height: 22px; max-height: 22px; padding: 1px 4px;")
        self.entry_ep.setAlignment(Qt.AlignCenter)
        te_box.addStretch(1)
        te_box.addWidget(self.entry_temp)
        te_box.addSpacing(8)
        te_box.addWidget(self.entry_ep)
        te_box.addStretch(1)

        self.lbl_te_tag = QLabel(f"📺 {self._get_clean_text('lbl_season', 'Temporada')}")
        self.btn_save_te = QPushButton("✅ Act.")
        self.btn_save_te.setObjectName("SecondaryButton")
        self.btn_save_te.setFixedSize(88, 28)
        te_grid.addWidget(self.lbl_te_tag, 0, 0)
        te_grid.addWidget(self.te_container, 0, 1)
        te_grid.addWidget(self.btn_save_te, 0, 2, Qt.AlignVCenter)
        # te_frame se añade en Tab 2 más abajo (después de construir los tabs)

        # Versiones — se oculta para usuarios Free si no hay links accesibles
        self.lbl_versions_title = QLabel(
            f"▼ 🔗 {self._get_clean_text('lbl_links_title', 'ENLACES DE REPRODUCCIÓN')}",
            objectName="SectionTitle"
        )
        self.lbl_versions_title.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_versions_title.setToolTip(
            self._get_clean_text('tooltip_collapse', 'Clic para colapsar / expandir · Click to collapse / expand')
        )
        self.lbl_versions_title.mousePressEvent = lambda e: self._toggle_links_section()
        # lbl_versions_title y ver_scroll se agregan en Tab 2 (más abajo)
        # lbl_versions_title and ver_scroll are added in Tab 2 below
        self.ver_scroll = QScrollArea()
        self.ver_scroll.setWidgetResizable(True)
        self.ver_scroll.setMinimumHeight(40)
        # Sin setMaximumHeight para expandirse cuando la otra sección se colapsa
        from PySide6.QtWidgets import QSizePolicy as _SP
        self.ver_scroll.setSizePolicy(_SP.Policy.Expanding, _SP.Policy.Expanding)
        self.ver_scroll.setStyleSheet("background-color: #111; border-radius: 8px; border: 1px solid #222;")
        self.ver_content = QWidget()
        self.ver_vbox = QVBoxLayout(self.ver_content)
        self.ver_vbox.setContentsMargins(4,4,4,4)
        self.ver_vbox.setSpacing(4)
        self.ver_vbox.setAlignment(Qt.AlignTop)
        self.ver_scroll.setWidget(self.ver_content)
        # La sección de enlaces va en Tab 2 (junto a los capítulos).
        # Para películas Tab 2 siempre es visible y sólo muestra los enlaces.
        # Links section lives in Tab 2 (alongside chapters).
        # For movies Tab 2 is always visible and shows only links.
        tab2_layout.addWidget(self.lbl_versions_title)
        tab2_layout.addWidget(self.ver_scroll)   # sin stretch aquí; crece solo cuando no hay capítulos
        self.ver_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.ver_content.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Indicador Premium: visible para usuarios Free en Películas/Series sin enlaces
        self.lbl_premium_hint = QLabel(
            f"💎 {self._get_clean_text('msg_premium_hint', 'Hazte Premium para acceder a los enlaces de reproducción')}"
        )
        self.lbl_premium_hint.setWordWrap(True)
        self.lbl_premium_hint.setAlignment(Qt.AlignCenter)
        self.lbl_premium_hint.setStyleSheet(
            "color: #ff9800; font-size: 12px; font-weight: bold; "
            "background: #1a1000; border-radius: 6px; padding: 8px 12px;"
        )
        self.lbl_premium_hint.setVisible(False)
        tab2_layout.addWidget(self.lbl_premium_hint)

        # Fila Temporada/Episodio en Tab 2, entre los enlaces y la lista de capítulos
        tab2_layout.addWidget(self.te_frame)

        tab1_layout.addStretch(0)   # empuja los widgets de Detalles hacia arriba

        # Capítulos (paginado) — en Tab 2 debajo de la fila de temporada
        # Episodes (paginated) — in Tab 2 below the links
        self.lbl_episodes_title = QLabel(
            f"🗂️ {self._get_clean_text('lbl_episodes_list', 'Capítulos')}",
            objectName="SectionTitle",
        )
        self.lbl_episodes_title.setContentsMargins(0, 4, 0, 0)
        self.lbl_episodes_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #1f6aa5; margin-top: 4px; text-transform: uppercase;")
        tab2_layout.addWidget(self.lbl_episodes_title)

        self.ep_pager = QWidget()
        self.ep_pager.setFixedHeight(30)
        ep_pager_l = QHBoxLayout(self.ep_pager)
        ep_pager_l.setContentsMargins(0, 0, 0, 0)
        ep_pager_l.setSpacing(3)

        self.btn_ep_prev = QPushButton("◀")
        self.btn_ep_prev.setFixedSize(38, 26)
        self.btn_ep_next = QPushButton("▶")
        self.btn_ep_next.setFixedSize(38, 26)
        self.lbl_ep_page = QLabel("Página 1/1")
        self.lbl_ep_page.setStyleSheet("color: #888; font-size: 11px;")
        self.entry_ep_page = QLineEdit()
        self.entry_ep_page.setPlaceholderText("Ir a…")
        self.entry_ep_page.setFixedWidth(72)
        self.entry_ep_page.setFixedHeight(20)
        self.entry_ep_page.setStyleSheet("min-height: 20px; max-height: 20px; padding: 1px 6px;")
        self.btn_ep_go = QPushButton("Ir")
        self.btn_ep_go.setFixedSize(48, 26)

        ep_pager_l.addWidget(self.btn_ep_prev)
        ep_pager_l.addWidget(self.btn_ep_next)
        ep_pager_l.addSpacing(10)
        ep_pager_l.addWidget(self.lbl_ep_page)
        ep_pager_l.addStretch(1)
        ep_pager_l.addWidget(self.entry_ep_page)
        ep_pager_l.addWidget(self.btn_ep_go)
        tab2_layout.addWidget(self.ep_pager)
        self.ep_scroll = QScrollArea()
        self.ep_scroll.setWidgetResizable(True)
        self.ep_scroll.setMinimumHeight(40)
        self.ep_scroll.setSizePolicy(_SP.Policy.Expanding, _SP.Policy.Expanding)
        self.ep_scroll.setStyleSheet("background-color: #111; border-radius: 8px; border: 1px solid #222;")
        self.ep_content = QWidget()
        self.ep_vbox = QVBoxLayout(self.ep_content)
        self.ep_vbox.setContentsMargins(4,4,4,4)
        self.ep_vbox.setSpacing(4)
        self.ep_vbox.setAlignment(Qt.AlignTop)
        self.ep_scroll.setWidget(self.ep_content)
        tab2_layout.addWidget(self.ep_scroll, 2)   # stretch=2 → ocupa el grueso del alto del tab
        self.ep_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.ep_content.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        content_l.addWidget(right_panel, 2)
        self._embeddings_avoid_extra_hwnd()
        # refresh_modal_view() se difiere a _deferred_initial_refresh: ejecutarlo aquí
        # (durante __init__, antes de show) dispara red/DB/setPixmap en Windows y puede cerrar el proceso.

    def connect_actions(self):
        try:
            self.btn_close.clicked.connect(self.close_modal)
            self.btn_fix.clicked.connect(self.on_fix_clicked)
            self.btn_save_tags.clicked.connect(self.save_tags)
            self.btn_save_te.clicked.connect(self.save_temp_ep)
            self.btn_anime.clicked.connect(self.toggle_anime)
            self.btn_delete.clicked.connect(self.on_delete)
            self.btn_trailer.clicked.connect(self.on_trailer_clicked)
            self.btn_seen.clicked.connect(self.toggle_seen)
            self.btn_fav.clicked.connect(self.toggle_fav)
            # QueuedConnection: save_rating corre en el siguiente tick del event-loop,
            # DESPUÉS de que el MouseButtonPress del star_rating haya terminado.
            # Evita re-entrada en eventFilter que causaba crash.
            self.star_rating.rating_changed.connect(self.save_rating,
                                                    Qt.ConnectionType.QueuedConnection)
            self.btn_ep_prev.clicked.connect(lambda: self._set_ep_page(self._ep_page - 1))
            self.btn_ep_next.clicked.connect(lambda: self._set_ep_page(self._ep_page + 1))
            self.btn_ep_go.clicked.connect(self._go_to_ep_page)
            self.entry_ep_page.returnPressed.connect(self._go_to_ep_page)
        except Exception as e:
            logging.error(f"Error conectando señales del modal: {e}")

    @Slot()
    def _deferred_initial_refresh(self):
        if not shiboken.isValid(self):
            return
        import logging

        logging.info(
            "[VRCMT-UI] deferred_refresh id=%s visible=%s",
            str(getattr(self.item, "id", "")),
            self.isVisible(),
        )
        # #region agent log
        try:
            from src.debug_ac5f85 import dbg, flow_click_trace, flow_current

            flow_click_trace(
                "modal_deferred_initial_refresh",
                item_id=str(getattr(self.item, "id", "")),
                visible=self.isVisible(),
            )
            dbg(
                "H6",
                "MediaModal._deferred_initial_refresh",
                "run",
                {
                    "flow": flow_current(),
                    "item_id": str(getattr(self.item, "id", "")),
                    "visible": self.isVisible(),
                },
            )
        except Exception:
            pass
        # #endregion
        self.refresh_modal_view()

    def _adj_val(self, line_edit, delta):
        try:
            val = int(line_edit.text() or 0)
            line_edit.setText(str(max(1, val + delta)))
        except (TypeError, ValueError):
            line_edit.setText("1")

    def _is_world_capture_album(self):
        """Capturas por mundo: titulo == world_name (engine._save_image). No son 'versiones' de un mismo vídeo."""
        if not _tipo_normalizado_es_stream_imagen(self.item.tipo_contenido):
            return False
        wn = (self.item.world_name or "").strip()
        tt = (self.item.titulo or "").strip()
        return bool(wn) and tt == wn

    # Hosts de plataformas sociales/streaming: sus URLs son videos, no capturas del mundo.
    # Se excluyen de los catálogos de imágenes para que solo aparezcan fotos reales de VRChat.
    _SOCIAL_VIDEO_HOSTS = (
        'youtube.com', 'youtu.be', 'googlevideo.com',
        'twitch.tv', 'kick.com', 'soundcloud.com', 'soundcloud.app',
        'tiktok.com', 'vimeo.com',
    )

    def _is_social_video_url(self, url: str) -> bool:
        u = (url or '').lower()
        return any(h in u for h in self._SOCIAL_VIDEO_HOSTS)

    def _siblings_base_query(self):
        """Misma obra / mismo mundo: acota por tipo y world_name para no mezclar mundos con el mismo título."""
        if self._is_world_capture_album():
            # Catálogo de mundo: devuelve SOLO las capturas de imagen reales del mismo mundo
            # (excluyendo URLs de YouTube/Twitch/Kick/etc. que son videos, no fotos de VRChat).
            wn = (self.item.world_name or "").strip()
            q = (
                Multimedia.select()
                .where(
                    (Multimedia.world_name == wn) &
                    (fn.REPLACE(fn.REPLACE(Multimedia.tipo_contenido, ' ', ''), '\uff0f', '/') == 'Stream/Imagen')
                )
                .order_by(Multimedia.ultimo_visto.desc())
            )
            # Excluir URLs de plataformas de video social con NOT LIKE a nivel SQL.
            for host in self._SOCIAL_VIDEO_HOSTS:
                q = q.where(~Multimedia.url.contains(host))
            return q
        q = Multimedia.select().where(
            Multimedia.titulo == self.item.titulo,
            Multimedia.tipo_contenido == self.item.tipo_contenido,
        )
        if self.item.world_name:
            q = q.where(Multimedia.world_name == self.item.world_name)
        if self.item.tipo_contenido == "Serie":
            q = q.where(
                Multimedia.temporada == self.item.temporada,
                Multimedia.episodio == self.item.episodio,
            )
        return q
        q = Multimedia.select().where(
            Multimedia.titulo == self.item.titulo,
            Multimedia.tipo_contenido == self.item.tipo_contenido,
        )
        if self.item.world_name:
            q = q.where(Multimedia.world_name == self.item.world_name)
        if self.item.tipo_contenido == "Serie":
            q = q.where(
                Multimedia.temporada == self.item.temporada,
                Multimedia.episodio == self.item.episodio,
            )
        return q

    def refresh_modal_view(self):
        if not shiboken.isValid(self) or self._is_refreshing:
            return
        # #region agent log
        try:
            from src.debug_ac5f85 import dbg, flow_click_trace, flow_current

            flow_click_trace(
                "modal_refresh_modal_view_start",
                item_id=str(getattr(self.item, "id", "")),
            )
            dbg(
                "H4",
                "MediaModal.refresh_modal_view",
                "start",
                {
                    "flow": flow_current(),
                    "item_id": str(getattr(self.item, "id", "")),
                },
            )
        except Exception:
            pass
        # #endregion
        self._is_refreshing = True
        try:
            self.lbl_title.setText(self.item.titulo)
            
            # Metadatos enriquecidos: Año | TMDb | VRCMT (async) | Géneros
            self.lbl_meta.setText(self._build_meta_line())
            imdb_id_now = (self.item.imdb_id or '').strip()
            tipo_meta = (getattr(self.item, 'tipo_contenido', '') or '').strip()
            if imdb_id_now and tipo_meta in ('Pelicula', 'Serie'):
                self._fetch_vrcmt_rating_async(imdb_id_now)
            
            # Senior Fix: Mostrar Director y Elenco
            credits_text = ""
            if self.item.director:
                credits_text += f"🎬 <b>Director:</b> {self.item.director}<br>"
            if self.item.elenco:
                credits_text += f"👥 <b>Cast:</b> {self.item.elenco}"
            self.lbl_credits.setText(credits_text if credits_text else "No credits available")
            self.lbl_credits.setVisible(bool(credits_text))

            # Senior Fix: Enlace de Colección (Star Wars, Marvel, etc)
            if self.item.coleccion:
                self.lbl_collection.setText(f"📁 Pertenece a: <u>{self.item.coleccion}</u>")
                self.lbl_collection.show()
            else:
                self.lbl_collection.hide()

            self.synopsis.setPlainText(self.item.sinopsis or "")
            self.entry_imdb.setText(self.item.imdb_id or "")
            self.entry_tags.setText(self.item.etiquetas or "")
            self.entry_temp.setText(str(self.item.temporada or ""))
            self.entry_ep.setText(str(self.item.episodio or ""))
            # Para Series: mostrar la calificación del primer episodio con nota > 0, o la del item actual.
            _rating_val = float(self.item.calificacion_personal or 0.0)
            if self.item.tipo_contenido == 'Serie' and _rating_val == 0.0:
                # Buscar si algún episodio ya tiene nota guardada
                _ep_with_rating = (
                    Multimedia.select(Multimedia.calificacion_personal)
                    .where(
                        (Multimedia.titulo == self.item.titulo) &
                        (Multimedia.tipo_contenido == 'Serie') &
                        (Multimedia.calificacion_personal > 0)
                    )
                    .limit(1)
                    .first()
                )
                if _ep_with_rating:
                    _rating_val = float(_ep_with_rating.calificacion_personal)
            self.star_rating.set_value(_rating_val)

            # Mostrar rating_container solo si:
            #   1. Hay sesión Discord activa
            #   2. El contenido es Película o Serie (no Videos, Streams, capturas…)
            #   3. Tiene IMDb ID definido (sin él no se puede vincular el voto en Firebase)
            _discord_logged = bool(self.engine.discord.get_saved_id() if self.engine.discord else '')
            _es_calificable  = self.item.tipo_contenido in ('Pelicula', 'Serie')
            _tiene_imdb      = bool((self.item.imdb_id or '').strip())
            self.rating_container.setVisible(_discord_logged and _es_calificable and _tiene_imdb)
            
            is_serie = (self.item.tipo_contenido == 'Serie')
            is_stream_img = self._tipo_stream_imagen_ui()
            
            # Senior Fix: Solo mostrar episodios para Series o Capturas de Imagen (que tienen fecha en Temp/Ep)
            # Los Streams sociales (YouTube/Twitch) ahora usan el formato limpio de Película
            show_episodes = bool(is_serie or (is_stream_img and self.item.temporada and len(self.item.temporada) > 2))
            
            # Ocultar área de capítulos si es Película o Stream sin episodios
            self._apply_eps_visibility(show_episodes)
            # te_frame (Temporada/Episodio) solo se muestra cuando hay capítulos
            if hasattr(self, 'te_frame') and shiboken.isValid(self.te_frame):
                self.te_frame.setVisible(show_episodes)

            self.btn_anime.setText("Quitar Anime" if self.item.es_anime else "Marcar Anime")
            # Ocultar botón Anime para videos públicos (YouTube, Twitch, Kick, etc.)
            # Hide Anime button for public platform videos (YouTube, Twitch, Kick, etc.)
            _PUBLIC_HOSTS = ('youtube.com', 'youtu.be', 'twitch.tv', 'kick.com', 'soundcloud.com', 'music.youtube.com')
            _url_lower = str(self.item.url or "").lower()
            _is_public_video = any(h in _url_lower for h in _PUBLIC_HOSTS)
            self.btn_anime.setVisible(not _is_public_video)
            
            # Botón principal izquierdo: texto y visibilidad según tipo de contenido
            urow = str(self.item.url or "")
            tipo_btn = (getattr(self.item, 'tipo_contenido', '') or '').strip()
            if is_stream_img:
                if self.engine.img_manager.is_image_url(urow):
                    self.btn_trailer.setText("🖼️ Ver Imagen")
                else:
                    self.btn_trailer.setText("🌐 Ver Video")
                self.btn_trailer.setVisible(bool(urow))
            elif tipo_btn == 'Video':
                self.btn_trailer.setText("🌐 Ver Video")
                self.btn_trailer.setVisible(bool(urow))
            else:
                # Película / Serie: buscar tráiler en background.
                # El botón se muestra SOLO si TMDB confirma que existe al menos un video.
                self.btn_trailer.setText("▶️ Ver Tráiler")
                tid = getattr(self.item, 'tmdb_id', None)
                if tid:
                    m_type = 'tv' if tipo_btn == 'Serie' else 'movie'
                    self._fetch_trailer_async(tid, m_type)
                else:
                    self.btn_trailer.setVisible(False)

            self.btn_seen.setText("👁️ Ya la ví" if self.item.estado_visto else "⭕ Marcar Visto")
            self.btn_fav.setText("❤️ Favorito" if self.item.es_favorito else "🤍 Añadir Favorito")
            # Texto personalizado del botón de eliminación según tipo de contenido
            if shiboken.isValid(self.btn_delete):
                self.btn_delete.setText(self._delete_btn_label())
            # Cartel primero: si refresh_episodes_list / refresh_versions_list fallan, el póster ya se pidió.
            self._load_poster()
            if is_serie or is_stream_img:
                self.refresh_episodes_list()
            self.refresh_versions_list()
        except Exception as e:
            logging.error(f"Error refrescando modal: {e}")
        finally:
            self._is_refreshing = False
            # #region agent log
            try:
                from src.debug_ac5f85 import dbg, flow_click_trace, flow_current

                flow_click_trace(
                    "modal_refresh_modal_view_end",
                    item_id=str(getattr(self.item, "id", "")),
                )
                dbg(
                    "H4",
                    "MediaModal.refresh_modal_view",
                    "end",
                    {
                        "flow": flow_current(),
                        "item_id": str(getattr(self.item, "id", "")),
                    },
                )
            except Exception:
                pass
            # #endregion

    def _on_collection_clicked(self, event):
        """Redirigir a la búsqueda de la colección completa (Senior Navigation)"""
        if not shiboken.isValid(self) or not self.item.coleccion:
            QLabel.mousePressEvent(self.lbl_collection, event)
            return
        # Abrir diálogo de búsqueda con el nombre de la colección
        main_win = self.window()
        sd = SearchDialog(self.engine.tmdb, main_win)
        sd.setWindowFlags(Qt.Widget)
        sd.setModal(False)
        sd.setWindowModality(Qt.WindowModality.NonModal)

        # Senior Fix: Bloquear señales mientras seteamos el texto para no disparar el timer de búsqueda
        sd.search_input.blockSignals(True)
        sd.search_input.setText(self.item.coleccion)
        sd.search_input.blockSignals(False)
        
        # Senior Fix: No usar apply_search_result (que hace el Fix), sino abrir en navegador
        sd.result_selected.connect(self._open_result_in_browser)
        
        if hasattr(main_win, '_build_modal_overlay'):
            main_win._build_modal_overlay(sd)
        else:
            sd.show()
        
        # Senior Fix: Usar búsqueda profunda por ID de colección si está disponible
        if getattr(self.item, 'coleccion_id', 0) > 0:
            sd.perform_collection_search(self.item.coleccion_id)
        else:
            sd.perform_search() # Fallback a búsqueda por texto
        event.accept()

    def _open_result_in_browser(self, r):
        """Abre la saga completa o el resultado en el navegador para evitar sobrescribir (v5.3)"""
        if not shiboken.isValid(self): return
        
        # Senior Fix: Priorizar abrir la COLECCIÓN (Saga) completa en el navegador
        # [ES] Esto permite ver la lista original que el usuario solicitó sin alterar la DB
        # [EN] This allows viewing the original list the user requested without altering the DB
        if getattr(self.item, 'coleccion_id', 0) > 0:
            url = f"https://www.themoviedb.org/collection/{self.item.coleccion_id}"
        elif r and 'id' in r:
            # Fallback a la película específica si no hay ID de saga
            m_type = r.get('media_type', 'movie')
            url = f"https://www.themoviedb.org/{m_type}/{r['id']}"
        else:
            return
            
        import webbrowser
        webbrowser.open(url)
        logging.info(f"🌐 Navegación externa de SAGA activada: {url}")

    def _poster_load_source(self):
        """Ruta/URL para el cartel: Stream/Imagen usa captura local si existe, luego URLs de imagen (hermanos/mundo)."""
        if not self.item:
            return "", "none"
        im = self.engine.img_manager

        def _first_image_url(rows, prefer_current_first=True):
            rows = list(rows)
            if prefer_current_first:
                rows.sort(key=lambda r: (0 if r.id == self.item.id else 1, str(r.id)))
            seen = set()
            for row in rows:
                u = (row.url or "").strip()
                if not u or u in seen:
                    continue
                seen.add(u)
                if im.is_image_url(u):
                    return u
            return None

        if self._tipo_stream_imagen_ui():
            # Captura en disco / poster_path HTTP tiene prioridad sobre item.url (p. ej. Discord ?format=webp):
            # el motor guarda PNG local mientras url sigue siendo remota; cargar primero el archivo evita WebP/Qt.
            pp = (self.item.poster_path or "").strip()
            if pp and _poster_path_usable(pp):
                pl = pp.lower()
                if pl.startswith("http://") or pl.startswith("https://") or pl.startswith("file:"):
                    return pp, "poster_path"
                resolved_pp = resolve_local_existing_path(pp)
                if resolved_pp:
                    return resolved_pp, "poster_path"
            if pp and not _poster_path_usable(pp):
                logging.warning(
                    "[poster] poster_path en DB pero no usable (archivo ausente o ruta inválida); se intenta URL. prefix=%s",
                    pp[:200],
                )
            # Fallback local: archivo más reciente del álbum del mundo en disco.
            # Tiene prioridad sobre URLs remotas que pueden haber expirado (Discord CDN).
            wn_album = (self.item.world_name or self.item.titulo or "").strip()
            if wn_album:
                try:
                    cover = im.get_album_cover(wn_album)
                    if cover and os.path.isfile(cover):
                        return cover, "album_cover"
                except Exception:
                    pass
            u = _first_image_url(self._siblings_base_query())
            if u:
                return u, "stream_image_url"
            wn = (self.item.world_name or "").strip()
            wid = (self.item.world_id or "").strip()
            clauses = []
            if wn:
                clauses.append(Multimedia.world_name == wn)
            if wid:
                clauses.append(Multimedia.world_id == wid)
            if clauses:
                or_world = reduce(operator.or_, clauses)
                norm_t_row = fn.LOWER(
                    fn.REPLACE(fn.REPLACE(Multimedia.tipo_contenido, " ", ""), "\uff0f", "/")
                )
                broad = (
                    Multimedia.select()
                    .where(
                        or_world,
                        norm_t_row == "stream/imagen",
                    )
                    .order_by(Multimedia.ultima_actualizacion.desc())
                    .limit(150)
                )
                u = _first_image_url(broad)
                if u:
                    return u, "stream_world_image_url"
            u0 = (self.item.url or "").strip()
            if u0 and im.is_image_url(u0):
                return u0, "stream_self_url"
            return "", "none"

        pp = (self.item.poster_path or "").strip()
        if pp and _poster_path_usable(pp):
            pl = pp.lower()
            if pl.startswith("http://") or pl.startswith("https://") or pl.startswith("file:"):
                return pp, "poster_path"
            resolved_pp = resolve_local_existing_path(pp)
            if resolved_pp:
                return resolved_pp, "poster_path"
        if pp and not _poster_path_usable(pp):
            logging.warning(
                "[poster] poster_path no usable; fallback a url si es imagen. prefix=%s",
                pp[:200],
            )
        u0 = (self.item.url or "").strip()
        if u0 and im.is_image_url(u0):
            return u0, "item_url_direct"
        return "", "none"

    def _load_poster(self):
        src, kind = self._poster_load_source()
        # #region agent log
        try:
            from src.debug_ac5f85 import dbg, flow_current

            dbg(
                "H5",
                "MediaModal._load_poster",
                "start",
                {
                    "flow": flow_current(),
                    "kind": kind,
                    "src_len": len(src) if src else 0,
                    "src_prefix": (src[:48] + "…") if isinstance(src, str) and len(src) > 48 else (src if isinstance(src, str) else type(src).__name__),
                },
            )
        except Exception:
            pass
        # #endregion
        if not shiboken.isValid(self):
            return
        if not src:
            self._clear_poster_label()
            return
        self._clear_poster_label()
        s = str(src)
        if s.lower().startswith("http://") or s.lower().startswith("https://"):
            # QtNetwork en hilo GUI (evita SSL en hilos Python)
            try:
                if self._poster_reply is not None:
                    self._poster_reply.abort()
                    self._poster_reply.deleteLater()
            except Exception:
                pass
            req = QNetworkRequest(QUrl(s))
            req.setRawHeader(b"User-Agent", b"Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            reply = self._nam.get(req)
            self._poster_reply = reply
            mid = str(self.item.id)

            def _done():
                try:
                    if reply is not self._poster_reply:
                        return
                    raw = reply.readAll()
                    self._on_poster_loaded(raw, mid)
                finally:
                    try:
                        reply.deleteLater()
                    except Exception:
                        pass
                    if self._poster_reply is reply:
                        self._poster_reply = None

            reply.finished.connect(_done)
        else:
            from src.ui.catalog_view import ImageLoader
            loader = ImageLoader(s, str(self.item.id))
            loader.signals.finished.connect(
                self._on_poster_loaded,
                Qt.ConnectionType.QueuedConnection,
            )
            from PySide6.QtCore import QThreadPool
            QThreadPool.globalInstance().start(loader)

    @Slot(QByteArray, str)
    def _on_poster_loaded(self, img_data, mid):
        raw_len = -1
        if not shiboken.isValid(self) or str(mid) != str(self.item.id):
            return
        try:
            raw = _img_data_to_bytes(img_data)
            raw_len = len(raw) if raw else 0
            if not raw:
                self._clear_poster_label()
                return
            img = QImage.fromData(raw)
            if img.isNull():
                for fmt in ("JPEG", "PNG", "WEBP", "GIF"):
                    img = QImage.fromData(raw, fmt)
                    if not img.isNull():
                        break
            pm = QPixmap.fromImage(img) if not img.isNull() else QPixmap()
            if pm.isNull() and raw:
                pm.loadFromData(raw)
            if pm.isNull() and raw:
                try:
                    from io import BytesIO
                    from PIL import Image

                    pil = Image.open(BytesIO(raw))
                    pil.load()
                    if pil.mode not in ("RGB", "RGBA"):
                        pil = pil.convert("RGBA")
                    buf = BytesIO()
                    pil.save(buf, format="PNG")
                    pm.loadFromData(buf.getvalue())
                except Exception as e:
                    logging.debug("[poster] PIL fallback PNG: %s", e)
            if not pm.isNull():
                self._set_poster_pixmap_scaled(pm)
            else:
                pre = raw[:64]
                logging.warning(
                    "[poster] bytes no decodifican como imagen mid=%s len=%s prefix=%r",
                    mid,
                    len(raw),
                    pre,
                )
                self._clear_poster_label()
        except Exception as e:
            logging.warning("[poster] _on_poster_loaded excepción: %s", e)
            self._clear_poster_label()
        # #region agent log
        try:
            from src.debug_ac5f85 import dbg, flow_current

            dbg(
                "H5",
                "MediaModal._on_poster_loaded",
                "after_apply",
                {
                    "flow": flow_current(),
                    "mid": str(mid),
                    "raw_len": raw_len,
                },
            )
        except Exception:
            pass
        # #endregion

    def refresh_versions_list(self):
        if not shiboken.isValid(self): return
        while self.ver_vbox.count():
            child = self.ver_vbox.takeAt(0)
            if child and child.widget(): child.widget().deleteLater()
        try:
            # #region agent log
            try:
                from src.debug_ac5f85 import dbg, flow_current, win32_foreground

                dbg(
                    "DB",
                    "MediaModal.refresh_versions_list",
                    "before_query",
                    {
                        "flow": flow_current(),
                        "item_id": str(getattr(self.item, "id", "")),
                        "titulo": (self.item.titulo or "")[:80],
                        "tipo": getattr(self.item, "tipo_contenido", None),
                        "fg": win32_foreground(),
                    },
                )
            except Exception:
                pass
            # #endregion
            query = self._siblings_base_query().order_by(
                Multimedia.ultima_actualizacion.desc()
            )
            all_rows = list(query)
            # #region agent log
            try:
                from src.debug_ac5f85 import dbg, flow_current

                max_ul = 0
                n_nul = 0
                for r in all_rows:
                    u = r.url or ""
                    lu = len(u)
                    if lu > max_ul:
                        max_ul = lu
                    if "\0" in u:
                        n_nul += 1
                iu = (self.item.url or "")
                dbg(
                    "H-DB",
                    "media_modal.refresh_versions_list",
                    "siblings_query_shape",
                    {
                        "flow": flow_current(),
                        "item_id": str(getattr(self.item, "id", "")),
                        "titulo": (self.item.titulo or "")[:120],
                        "tipo_contenido": self.item.tipo_contenido,
                        "world_name": (self.item.world_name or "")[:80],
                        "world_id": (self.item.world_id or "")[:48],
                        "n_raw_rows": len(all_rows),
                        "item_url_len": len(iu),
                        "item_poster_len": len((self.item.poster_path or "")),
                        "max_sibling_url_len": max_ul,
                        "n_urls_with_nul_byte": n_nul,
                    },
                )
            except Exception:
                pass
            # #endregion
            seen_url = set()
            deduped = []
            for r in all_rows:
                u = (r.url or "").strip()
                if not u or u in seen_url:
                    continue
                seen_url.add(u)
                deduped.append(r)
            total_u = len(deduped)

            # ── Catálogo de mundo: separar conteo total de la lista a mostrar ──
            # El título siempre refleja el total de imágenes del álbum.
            # La sección de links muestra SOLO el link del capítulo seleccionado
            # (self.item), no todos los del mundo — cada capítulo tiene su propia imagen.
            if self._is_world_capture_album():
                versions = [self.item] if (self.item.url or "").strip() else []
                omitted = 0
            else:
                cap = self._MAX_VERSION_ROWS
                versions = deduped[:cap]
                omitted = total_u - len(versions)

            d_id = self.engine.discord.get_saved_id()
            is_premium = getattr(self.engine, 'is_premium', False)

            for ver in versions:
                self._add_version_row(ver, d_id, is_premium)

            # Mostrar/ocultar la sección entera según si hay filas visibles.
            # Para usuarios Free con contenido Pelicula/Serie, _add_version_row devuelve sin añadir
            # ningún widget; en ese caso ocultamos el título y el scroll completo.
            has_visible_rows = self.ver_vbox.count() > 0
            # Construir el texto base del título (sin flecha, se añade en _apply_links_visibility)
            # Build base title text (without arrow; arrow added by _apply_links_visibility)
            if self._is_world_capture_album() and total_u > 0:
                raw_title = f"🖼️ IMÁGENES DEL CATÁLOGO  ({total_u} {'imagen' if total_u == 1 else 'imágenes'})"
            else:
                raw_title = f"🔗 {self._get_clean_text('lbl_links_title', 'ENLACES DE REPRODUCCIÓN')}"
            if hasattr(self, 'lbl_versions_title') and shiboken.isValid(self.lbl_versions_title):
                arrow = "▶" if self._links_collapsed else "▼"
                self.lbl_versions_title.setText(f"{arrow} {raw_title}")
            self._apply_links_visibility(has_visible_rows)

            # Mensaje "Hazte Premium": visible solo para Free en Películas/Series sin enlaces.
            # NO mostrar si el ítem principal es en realidad una imagen (URL con extensión de
            # imagen o poster local): las imágenes son accesibles para usuarios Free aunque
            # tipo_contenido haya sido clasificado erróneamente como Pelicula/Serie.
            is_movie_or_series = getattr(self.item, 'tipo_contenido', '') in ('Pelicula', 'Serie')
            is_item_image = (
                self.engine.img_manager.is_image_url(self.item.url or "")
                or _tipo_normalizado_es_stream_imagen(getattr(self.item, 'tipo_contenido', ''))
            )
            show_hint = not has_visible_rows and not is_premium and is_movie_or_series and not is_item_image
            if hasattr(self, 'lbl_premium_hint') and shiboken.isValid(self.lbl_premium_hint):
                self.lbl_premium_hint.setVisible(show_hint)

            if omitted > 0:
                tpl = self.engine.config.tr(
                    "msg_versions_truncated",
                    "… y {n} enlaces más no mostrados (límite {m}). Orden: más recientes primero; URLs repetidas ocultas.",
                )
                try:
                    msg = tpl.format(n=omitted, m=cap)
                except (KeyError, ValueError):
                    msg = f"… y {omitted} enlaces más no mostrados (límite {cap})."
                note = QLabel(msg)
                note.setWordWrap(True)
                note.setStyleSheet("color: #888; font-size: 11px; padding: 6px;")
                self.ver_vbox.addWidget(note)

            # #region agent log
            try:
                from src.debug_ac5f85 import dbg, flow_current

                dbg(
                    "H3",
                    "media_modal.refresh_versions_list",
                    "done",
                    {
                        "flow": flow_current(),
                        "n_versions": len(versions),
                        "n_versions_total_unique": total_u,
                        "n_raw_rows": len(all_rows),
                        "omitted": omitted,
                        "is_premium": bool(is_premium),
                    },
                )
            except Exception:
                pass
            # #endregion
            # #region agent log
            try:
                from src.debug_ac5f85 import dbg, flow_current, win32_foreground

                dbg(
                    "DB",
                    "MediaModal.refresh_versions_list",
                    "after_render",
                    {
                        "flow": flow_current(),
                        "item_id": str(getattr(self.item, "id", "")),
                        "n_rows": len(all_rows),
                        "n_shown": len(versions),
                        "n_ver_widgets": int(self.ver_content.findChildren(QWidget).__len__()) if hasattr(self, "ver_content") else -1,
                        "n_ver_native_widgets": int(
                            sum(
                                1
                                for _w in (self.ver_content.findChildren(QWidget) if hasattr(self, "ver_content") else [])
                                if _w.testAttribute(Qt.WidgetAttribute.WA_NativeWindow)
                            )
                        ),
                        "fg": win32_foreground(),
                    },
                )
            except Exception:
                pass
            # #endregion
            # #region agent log
            try:
                # Captura de píxeles del MainWindow para validar si el artefacto vive dentro del buffer Qt.
                if (self.item and (self.item.titulo or "") == "Video Playback") or (
                    str(getattr(self.item, "id", "")).startswith("BASIC_18da3e9fe4")
                ):
                    from src.debug_ac5f85 import dbg_capture_active_window

                    dbg_capture_active_window("video_after_versions_render")
            except Exception:
                pass
            # #endregion
        except Exception as e:
            logging.error(f"Error refrescando lista de versiones: {e}")

    def _add_version_row(self, ver, d_id, is_premium):
        u = (ver.url or "").lower()
        is_public = any(x in u for x in ['youtube.com', 'youtu.be', 'twitch.tv', 'kick.com'])
        is_private = not is_public

        # --- Usuarios FREE: ocultar filas de URLs privadas en Peliculas/Series ---
        # Las plataformas públicas (YouTube/Twitch/Kick) siempre se muestran.
        # Las imágenes (extensión .jpg/.png/etc. o reconocidas por img_manager) SIEMPRE
        # se muestran para usuarios Free: no son contenido premium.
        is_movie_or_series = getattr(self.item, 'tipo_contenido', '') in ('Pelicula', 'Serie')
        is_image_url = self.engine.img_manager.is_image_url(ver.url or "")
        if is_private and not is_premium and is_movie_or_series and not is_image_url:
            # No renderizar la fila: el contenido premium no debe ser visible para usuarios Free
            return

        row = QFrame(self.ver_content)
        row.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
        try:
            from src.core.themes import get_ep_row_style as _gers
            row.setStyleSheet(_gers(self._theme_name(), False))
        except Exception:
            row.setStyleSheet("background-color: #1a1a1a; border-radius: 5px;")
        row.setMinimumHeight(32)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 4, 8, 4)
        rl.setSpacing(6)

        # Indicador de carga: solo para contenido privado no-imagen que requiere Firebase
        lbl_loading = QLabel("⏳", row)
        lbl_loading.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
        lbl_loading.setFixedSize(20, 22)
        lbl_loading.setAlignment(Qt.AlignCenter)
        lbl_loading.setToolTip("Verificando acceso…")
        _needs_firebase = is_private and not is_premium and bool(d_id) and not is_image_url
        lbl_loading.setVisible(_needs_firebase)
        rl.addWidget(lbl_loading)

        # Etiqueta de URL (mostrar de inmediato, luego ajustar por acceso)
        display_url = ver.url if len(ver.url) < 50 else ver.url[:47] + "..."
        url_lbl = QLabel(display_url, row)
        url_lbl.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
        url_lbl.setTextFormat(Qt.TextFormat.PlainText)
        url_lbl.setStyleSheet("color: #888; font-size: 11px;")
        url_lbl.setMinimumWidth(120)
        url_lbl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        url_lbl.setToolTip(ver.url)
        rl.addWidget(url_lbl, 1)

        # Botón Copiar — visible para premium, y también para imágenes (contenido libre)
        btn_copy = QPushButton("🔗", row)
        btn_copy.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
        btn_copy.setFixedSize(30, 26)
        btn_copy.setToolTip(self.engine.config.tr('btn_copy_link', "Copiar Link"))
        btn_copy.setStyleSheet("""
            QPushButton { background: rgba(255,255,255,0.08); color: #aaa;
                          border-radius: 5px; border: 1px solid rgba(255,255,255,0.1); font-size: 13px; }
            QPushButton:hover { background: rgba(255,255,255,0.16); color: #fff; }
        """)
        btn_copy.clicked.connect(lambda: self._on_copy_link(ver.url))
        btn_copy.setVisible(is_premium or is_image_url)
        rl.addWidget(btn_copy)

        # Botón Reproducir/Ver imagen — visible para premium y también para imágenes
        _play_icon = "👁" if is_image_url else "▶"
        _play_tip  = "Ver imagen en el visor" if is_image_url else "Reproducir en el reproductor"
        btn_play = QPushButton(_play_icon, row)
        btn_play.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
        btn_play.setFixedSize(30, 26)
        btn_play.setEnabled(False)
        btn_play.setToolTip(_play_tip)
        btn_play.setVisible(is_premium or is_image_url)
        btn_play.setStyleSheet("""
            QPushButton { background: rgba(255,255,255,0.05); color: #555;
                          border-radius: 5px; border: 1px solid rgba(255,255,255,0.06); font-size: 14px; }
            QPushButton:enabled { background: #27ae60; color: white; border-color: #27ae60; }
            QPushButton:enabled:hover { background: #2ecc71; border-color: #2ecc71; }
            QPushButton:disabled { background: rgba(255,255,255,0.04); color: #444; }
        """)
        rl.addWidget(btn_play)

        # Botón Eliminar Link
        btn_del_link = QPushButton("✕", row)
        btn_del_link.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
        btn_del_link.setFixedSize(30, 26)
        btn_del_link.setToolTip(self.engine.config.tr('btn_delete_link', "Eliminar este enlace"))
        btn_del_link.setStyleSheet("""
            QPushButton { background: rgba(198,40,40,0.25); color: #ef9a9a;
                          border-radius: 5px; border: 1px solid rgba(198,40,40,0.4); font-size: 12px; font-weight: bold; }
            QPushButton:hover { background: #c62828; color: white; border-color: #c62828; }
        """)
        btn_del_link.clicked.connect(lambda: self._on_delete_link(ver))
        rl.addWidget(btn_del_link)

        self.ver_vbox.addWidget(row)

        def apply_access(has_access):
            if not shiboken.isValid(self) or not shiboken.isValid(row):
                return
            lbl_loading.setVisible(False)
            if has_access:
                url_lbl.setText(display_url)
                url_lbl.setToolTip(ver.url)
                url_lbl.setStyleSheet("color: #4dabf5; font-size: 11px;")
                btn_play.setVisible(True)
                btn_play.setEnabled(True)
                btn_copy.setVisible(True)
                try:
                    btn_play.clicked.disconnect()
                except (TypeError, RuntimeError):
                    pass
                btn_play.clicked.connect(lambda chk, _u=ver.url, _t=self.item.titulo: self._play_video(_u, _t))
            else:
                # Acceso denegado por Firebase → ocultar fila completa para no exponer URL
                row.setVisible(False)

        # Las imágenes son contenido libre: siempre se muestra el enlace sin
        # consultar Firebase ni requerir premium.
        if is_image_url or _tipo_normalizado_es_stream_imagen(getattr(self.item, 'tipo_contenido', '')):
            apply_access(True)
        elif is_private and not is_premium:
            if not d_id:
                row.setVisible(False)
            else:
                self._request_access_once(d_id, ver.world_id, apply_access)
        else:
            apply_access(True)

    def _on_copy_link(self, url):
        """Copia el link al portapapeles (v5.4)"""
        from PySide6.QtGui import QGuiApplication
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(url)
        logging.info(f"📋 Link copiado al portapapeles: {url[:50]}...")

    def _on_delete_link(self, ver_item):
        """Elimina un link específico de la base de datos (v5.4)"""
        if not shiboken.isValid(self): return
        
        confirm = QMessageBox.question(
            self, 
            self.engine.config.tr('lbl_delete_record', "Eliminar"),
            self.engine.config.tr('msg_confirm_delete_link', "¿Borrar solo este enlace?"),
            QMessageBox.Yes | QMessageBox.No
        )
        
        if confirm == QMessageBox.Yes:
            try:
                # Borrar el registro específico de la DB
                ver_item.delete_instance()
                logging.info(f"🗑️ Enlace eliminado quirúrgicamente: {ver_item.url[:50]}")
                
                # Si era el único link, cerrar el modal o refrescar
                rq = Multimedia.select().where(
                    Multimedia.titulo == self.item.titulo,
                    Multimedia.tipo_contenido == self.item.tipo_contenido,
                )
                if self.item.world_name:
                    rq = rq.where(Multimedia.world_name == self.item.world_name)
                remaining = rq.count()
                if remaining == 0:
                    self.data_changed.emit(self.item)
                    self.close_modal()
                else:
                    self.refresh_versions_list()
                    self.data_changed.emit(self.item)
            except Exception as e:
                logging.error(f"❌ Error eliminando enlace: {e}")

    def refresh_episodes_list(self):
        if not shiboken.isValid(self): return
        while self.ep_vbox.count():
            child = self.ep_vbox.takeAt(0)
            if child and child.widget(): child.widget().deleteLater()
        try:
            # #region agent log
            try:
                from src.debug_ac5f85 import dbg, flow_current, win32_foreground

                dbg(
                    "DB",
                    "MediaModal.refresh_episodes_list",
                    "before_query",
                    {
                        "flow": flow_current(),
                        "item_id": str(getattr(self.item, "id", "")),
                        "titulo": (self.item.titulo or "")[:80],
                        "tipo": getattr(self.item, "tipo_contenido", None),
                        "fg": win32_foreground(),
                    },
                )
            except Exception:
                pass
            # #endregion
            q = Multimedia.select().where(
                Multimedia.titulo == self.item.titulo,
                Multimedia.tipo_contenido == self.item.tipo_contenido,
            )
            if self.item.world_name:
                q = q.where(Multimedia.world_name == self.item.world_name)

            # Ordenar por última actualización descendente para que al deduplicar
            # nos quedemos con la entrada MÁS RECIENTE de cada (temporada, episodio).
            all_eps = list(q.order_by(
                Multimedia.ultima_actualizacion.desc()
            ).limit(500))

            # Para películas: múltiples links son VARIANTES de la misma película, no capítulos.
            # Deduplicar todos a UN solo ítem (el más reciente). No se deben contar como episodios.
            # For movies: multiple links are VARIANTS of the same movie, not chapters.
            # Deduplicate all to ONE item (most recent). They must not be counted as episodes.
            is_movie = getattr(self.item, 'tipo_contenido', '') == 'Pelicula'
            if is_movie:
                # Todas las variantes de una película = 1 sola entrada en la lista
                # All movie variants = 1 single entry in the list
                _deduped = all_eps[:1] if all_eps else []
                # Si alguna variante está marcada como vista, usar esa
                # If any variant is marked as seen, use that one
                seen_variants = [e for e in all_eps if getattr(e, 'estado_visto', 0) == 1]
                if seen_variants:
                    _deduped = seen_variants[:1]
            else:
                # Deduplicar por (temporada, episodio) — keep most-recent
                # Deduplicate by (season, episode) — keep most-recent
                _seen_ep_keys = set()
                _deduped = []
                for _ep in all_eps:
                    _key = (str(_ep.temporada), str(_ep.episodio))
                    if _key not in _seen_ep_keys:
                        _seen_ep_keys.add(_key)
                        _deduped.append(_ep)

            # Reordenar el resultado final por temporada / episodio para el display
            eps = sorted(_deduped, key=lambda e: (str(e.temporada), str(e.episodio)))
            self._eps_cache = eps
            self._ep_page = 1
            self._apply_ep_page()
            # #region agent log
            try:
                from src.debug_ac5f85 import dbg, flow_current, win32_foreground

                dbg(
                    "DB",
                    "MediaModal.refresh_episodes_list",
                    "after_query",
                    {
                        "flow": flow_current(),
                        "item_id": str(getattr(self.item, "id", "")),
                        "n_eps": len(eps),
                        "fg": win32_foreground(),
                    },
                )
            except Exception:
                pass
            # #endregion
        except Exception as e:
            logging.error(f"Error refrescando lista de episodios: {e}")

    def _ep_total_pages(self) -> int:
        n = len(self._eps_cache or [])
        if n <= 0:
            return 1
        return max(1, (n + self._ep_page_size - 1) // self._ep_page_size)

    def _apply_ep_page(self):
        if not shiboken.isValid(self):
            return
        while self.ep_vbox.count():
            child = self.ep_vbox.takeAt(0)
            if child and child.widget():
                child.widget().deleteLater()
        total = self._ep_total_pages()
        p = max(1, min(int(self._ep_page or 1), total))
        self._ep_page = p
        i0 = (p - 1) * self._ep_page_size
        i1 = i0 + self._ep_page_size
        page_eps = (self._eps_cache or [])[i0:i1]
        for ep in page_eps:
                row = QFrame()
                row.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
                row.setCursor(Qt.PointingHandCursor)
                row.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                row.setMinimumHeight(30)
                is_curr = (ep.id == self.item.id)
                try:
                    from src.core.themes import get_ep_row_style as _gers2
                    row.setStyleSheet(_gers2(self._theme_name(), is_curr))
                except Exception:
                    row.setStyleSheet(f"background-color: {'#252525' if is_curr else '#1a1a1a'}; border-radius: 5px; border: {'1px solid #1f6aa5' if is_curr else 'none'};")
                rl = QHBoxLayout(row)
                rl.setContentsMargins(10, 4, 10, 4)
                ep_lbl = QLabel(f"T.{ep.temporada} Ep.{ep.episodio}")
                ep_lbl.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
                ep_lbl.setTextFormat(Qt.TextFormat.PlainText)
                ep_lbl.setStyleSheet("font-weight: bold; font-size: 12px;")
                rl.addWidget(ep_lbl, 1)
                # N4: Botón para marcar episodio como visto/no visto
                ep_seen = bool(getattr(ep, 'estado_visto', 0))
                btn_seen_ep = QPushButton("✓" if ep_seen else "○")
                btn_seen_ep.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
                btn_seen_ep.setFixedSize(30, 26)
                btn_seen_ep.setToolTip("Desmarcar visto" if ep_seen else "Marcar como visto")
                try:
                    from src.core.themes import get_ep_seen_btn_style as _gsbs
                    btn_seen_ep.setStyleSheet(_gsbs(self._theme_name(), ep_seen))
                except Exception:
                    btn_seen_ep.setStyleSheet(
                        "background: rgba(39,174,96,0.25); color: #2ecc71; border-radius: 5px; border: 1px solid rgba(39,174,96,0.5); font-weight: bold;"
                        if ep_seen else
                        "background: rgba(255,255,255,0.05); color: #666; border-radius: 5px; border: 1px solid rgba(255,255,255,0.08);"
                    )
                btn_seen_ep.clicked.connect(lambda _=False, itm=ep, btn=btn_seen_ep: self._toggle_ep_seen(itm, btn))
                rl.addWidget(btn_seen_ep, 0, Qt.AlignRight | Qt.AlignVCenter)

                btn_del_ep = QPushButton("✕")
                btn_del_ep.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
                btn_del_ep.setFixedSize(30, 26)
                btn_del_ep.setToolTip(self.engine.config.tr('btn_delete_ep', "Eliminar capítulo"))
                try:
                    from src.core.themes import get_ep_del_btn_style as _gdbs
                    btn_del_ep.setStyleSheet(_gdbs())
                except Exception:
                    btn_del_ep.setStyleSheet("background: rgba(198,40,40,0.2); color: #ef9a9a; border-radius: 5px; border: 1px solid rgba(198,40,40,0.35);")
                btn_del_ep.clicked.connect(lambda _=False, itm=ep: self._on_delete_episode_item(itm))
                rl.addWidget(btn_del_ep, 0, Qt.AlignRight | Qt.AlignVCenter)

                def _make_ep_press(itm):
                    def _on_press(ev):
                        if not shiboken.isValid(self):
                            return
                        self.load_specific_item(itm)
                        ev.accept()

                    return _on_press

                row.mousePressEvent = _make_ep_press(ep)
                self.ep_vbox.addWidget(row)
        self.lbl_ep_page.setText(f"Página {p}/{total}")
        self.btn_ep_prev.setEnabled(p > 1)
        self.btn_ep_next.setEnabled(p < total)
        # #region agent log
        try:
            from src.debug_ac5f85 import dbg, flow_current, win32_foreground

            dbg(
                "DB",
                "MediaModal._apply_ep_page",
                "after_render",
                {
                    "flow": flow_current(),
                    "item_id": str(getattr(self.item, "id", "")),
                    "n_eps_total": len(self._eps_cache or []),
                    "n_eps_shown": len(page_eps),
                    "n_ep_widgets": int(self.ep_content.findChildren(QWidget).__len__()) if hasattr(self, "ep_content") else -1,
                    "n_ep_native_widgets": int(
                        sum(
                            1
                            for _w in (self.ep_content.findChildren(QWidget) if hasattr(self, "ep_content") else [])
                            if _w.testAttribute(Qt.WidgetAttribute.WA_NativeWindow)
                        )
                    ),
                    "fg": win32_foreground(),
                },
            )
        except Exception:
            pass
        # #endregion

    def _set_ep_page(self, page: int):
        self._ep_page = int(page)
        self._apply_ep_page()

    def _go_to_ep_page(self):
        txt = (self.entry_ep_page.text() or "").strip()
        if not txt:
            return
        try:
            p = int(txt)
        except ValueError:
            return
        self.entry_ep_page.clear()
        self._set_ep_page(p)

    def _toggle_ep_seen(self, ep_item, btn):
        """N4: Alterna el estado visto/no visto de un episodio individual."""
        if not shiboken.isValid(self) or ep_item is None:
            return
        try:
            ep_item.estado_visto = 0 if ep_item.estado_visto else 1
            ep_item.save()
            try:
                from src.core.themes import get_ep_seen_btn_style as _gsbs3
                _ts = self._theme_name()
            except Exception:
                _gsbs3 = None; _ts = 'Oscuro'
            if ep_item.estado_visto:
                btn.setText("✓")
                btn.setStyleSheet(_gsbs3(_ts, True) if _gsbs3 else
                    "background: rgba(39,174,96,0.25); color: #2ecc71; border-radius: 5px; border: 1px solid rgba(39,174,96,0.5); font-weight: bold;")
                btn.setToolTip("Desmarcar visto")
            else:
                btn.setText("○")
                btn.setStyleSheet(_gsbs3(_ts, False) if _gsbs3 else
                    "background: rgba(255,255,255,0.05); color: #666; border-radius: 5px; border: 1px solid rgba(255,255,255,0.08);")
                btn.setToolTip("Marcar como visto")
        except Exception as e:
            logging.error("Error alternando visto del episodio: %s", e)

    def _on_delete_episode_item(self, ep_item):
        if not shiboken.isValid(self):
            return
        if ep_item is None:
            return
        confirm = QMessageBox.question(
            self,
            self.engine.config.tr('lbl_delete_record', "Eliminar"),
            self.engine.config.tr('msg_confirm_delete_ep', "¿Borrar este capítulo/entrada?"),
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            deleted_id = str(getattr(ep_item, "id", ""))
            ep_item.delete_instance()
            self.refresh_episodes_list()
            # Si borramos el item actual del modal, saltar al primero disponible o cerrar.
            if deleted_id == str(getattr(self.item, "id", "")):
                if self._eps_cache:
                    self.item = self._eps_cache[0]
                    self.refresh_modal_view()
                else:
                    self.data_changed.emit(self.item)
                    self.close_modal()
                    return
            self.data_changed.emit(self.item)
        except Exception as e:
            logging.error(f"Error eliminando capítulo/entrada: {e}")

    def load_specific_item(self, new_item):
        if not shiboken.isValid(self):
            return
        # #region agent log
        try:
            from src.debug_ac5f85 import dbg, flow_click_trace, flow_click_trace_reset, flow_current

            flow_click_trace_reset()
            flow_click_trace(
                "episode_row_click",
                new_id=str(getattr(new_item, "id", "")),
                titulo=(getattr(new_item, "titulo", None) or "")[:80],
            )
            dbg(
                "H0",
                "MediaModal.load_specific_item",
                "episode_row_click",
                {
                    "flow": flow_current(),
                    "new_id": str(getattr(new_item, "id", "")),
                    "titulo": (getattr(new_item, "titulo", None) or "")[:80],
                },
            )
        except Exception:
            pass
        # #endregion
        self.item = new_item
        try:
            from src.debug_ac5f85 import flow_click_trace

            flow_click_trace("episode_item_assigned")
        except Exception:
            pass
        self.refresh_modal_view()

    def on_rating_change(self, value):
        """Mantenido por compatibilidad."""
        pass

    def save_rating(self, new_rating: float = None):
        """Guarda la calificación personal en la BD local y programa el upload a Firebase.

        Flujo:
          star_click → BD local inmediato → debounce 20 s (se reinicia en cada clic)
          → _do_upload_rating → Firebase (máx. 4/min) → si supera: freeze 5 min.

        Llamada con QueuedConnection desde rating_changed para no crashear dentro
        del eventFilter (re-entrada en el event-loop de Qt en Windows).
        """
        if not shiboken.isValid(self):
            return

        if new_rating is None:
            new_rating = self.star_rating.get_value() if hasattr(self, 'star_rating') else 0.0
        new_rating = float(new_rating)

        # ── 1. Guardar en BD local de inmediato ──────────────────────────────
        self.item.calificacion_personal = new_rating
        self.item.save()

        if self.item.tipo_contenido == 'Serie':
            try:
                Multimedia.update(calificacion_personal=new_rating).where(
                    (Multimedia.titulo == self.item.titulo) &
                    (Multimedia.tipo_contenido == 'Serie')
                ).execute()
                logging.info("⭐ Calificación %.1f propagada a '%s'", new_rating, self.item.titulo)
            except Exception as e:
                logging.error("save_rating serie propagation: %s", e)

        # Diferir data_changed al siguiente tick para no interrumpir el eventFilter.
        self._rating_catalog_pending_notify = True
        item_ref = self.item
        QTimer.singleShot(0, lambda: self._emit_rating_catalog_deferred(item_ref))

        # ── 2. Programar upload a Firebase (debounce 10 s) ───────────────────
        imdb_id = (self.item.imdb_id or '').strip()
        discord_id = self.engine.discord.get_saved_id() if self.engine.discord else ''

        if not imdb_id or not discord_id:
            return

        # Si la interfaz está congelada por rate-limit, no reprogramar.
        if self._rating_frozen:
            logging.debug("⭐ [Rating] Interfaz congelada — cambio local guardado, Firebase en espera.")
            return

        # Actualizar snapshots (siempre reflejan el último valor elegido).
        self._pending_rating_imdb = imdb_id
        self._pending_rating_value = new_rating
        self._pending_rating_local_id = str(getattr(self.item, 'id', '') or '')

        # Reiniciar el debounce de 20 s en cada clic.
        self._rating_debounce_timer.start()
        self._update_rating_hint("✅ Guardado local · ☁️ Sincronizando con Firebase en 20 s…")

    def _do_upload_rating(self):
        """Ejecutado por el debounce timer (20 s sin cambios): sube a Firebase."""
        # #region agent log
        import json as _j, time as _t
        open('debug-6ee757.log','a').write(_j.dumps({"sessionId":"6ee757","timestamp":int(_t.time()*1000),"location":"media_modal:_do_upload_rating:entry","message":"upload timer fired","data":{"valid":shiboken.isValid(self),"imdb":self._pending_rating_imdb,"val":self._pending_rating_value,"uploads_in_window":len(self._rating_upload_times)},"hypothesisId":"H1,H4"})+'\n')
        # #endregion
        if not shiboken.isValid(self):
            return

        imdb_id  = self._pending_rating_imdb
        rating   = self._pending_rating_value
        local_id = self._pending_rating_local_id
        discord_id = self.engine.discord.get_saved_id() if self.engine.discord else ''

        if not imdb_id or not discord_id:
            return

        # Deduplicación
        if imdb_id == self._last_uploaded_imdb and rating == self._last_uploaded_rating:
            logging.debug("⭐ [Rating] Sin cambios — omitido.")
            self._update_rating_hint("")
            return

        # ── Rate-limit ───────────────────────────────────────────────────────
        now = time.monotonic()
        while self._rating_upload_times and now - self._rating_upload_times[0] > 60:
            self._rating_upload_times.popleft()

        if len(self._rating_upload_times) >= 4:
            logging.warning("⭐ [Rating] Rate-limit (4/min) — congelando 5 min.")
            self._rating_frozen = True
            self._rating_freeze_timer.start()
            self._update_rating_hint("🔒 Demasiadas calificaciones. Bloqueado 5 min.")
            return

        # ── Upload real via run_firebase_async + cola GUI (sin QRunnable/Signal) ──
        self._rating_upload_times.append(now)
        self._last_uploaded_rating = rating
        self._last_uploaded_imdb   = imdb_id
        # Invalidar L1, L1-shared y L2 (SQLite) para que el resultado de la transacción
        # (rating_done) actualice todos los niveles con el nuevo promedio.
        self._vrcmt_rating_cache.pop(imdb_id, None)
        _vrcmt_cache_invalidate(imdb_id)

        self._update_rating_hint("☁️ Subiendo calificación…")

        snap_imdb  = imdb_id
        snap_rating = rating
        snap_discord = discord_id
        snap_local   = local_id

        def _op():
            # sync_rating retorna (avg, count) calculado en la transacción Firestore,
            # o None en caso de error / borrado de voto.
            return self.engine.firebase.sync_rating(
                snap_discord, snap_imdb, snap_rating,
                local_db_id=snap_local,
            )

        def _cb(result, error):
            ok = error is None and result is not None
            # result es (avg, count) devuelto por sync_rating desde _read_and_write_aggregate
            agg_avg, agg_count = result if ok and isinstance(result, tuple) else (0.0, 0)
            self._fb_ui_q.put(('rating_done', snap_imdb, snap_rating, ok, agg_avg, agg_count))

        self.engine.firebase.run_firebase_async(_op, _cb)
        logging.info("⭐ [Rating] Upload encolado: %s = %.1f (%d/4 en ventana)", imdb_id, rating,
                     len(self._rating_upload_times))

    def _drain_fb_queue(self):
        """Drena la cola _fb_ui_q en el hilo GUI (llamado cada 50ms por QTimer).
        Los callbacks de run_firebase_async depositan aquí; nunca emiten señales Qt
        desde threads del pool, eliminando el ACCESS_VIOLATION de Python 3.13."""
        if not shiboken.isValid(self):
            return
        while True:
            try:
                msg = self._fb_ui_q.get_nowait()
            except Exception:
                break
            tag = msg[0]
            try:
                if tag in ('vrcmt', 'vrcmt_post_rating'):
                    _, snapshot_id, avg, count = msg
                    self._vrcmt_rating_pending.discard(snapshot_id)
                    # Poblar L1 (instancia) y L1-shared (memoria)
                    self._vrcmt_rating_cache[snapshot_id] = (avg, count)
                    _VRCMT_SHARED_CACHE[snapshot_id] = (avg, count, time.monotonic())
                    # Persistir en L2 (SQLite local) — próximas aperturas no consultan Firebase
                    _vrcmt_cache_write_db(snapshot_id, avg, count)
                    if (shiboken.isValid(self.lbl_meta)
                            and (self.item.imdb_id or '').strip() == snapshot_id):
                        self.lbl_meta.setText(self._build_meta_line(avg, count))
                    if tag == 'vrcmt_post_rating':
                        vrcmt_str = f"{avg:.1f} VRCMT ({count})" if avg > 0 else "sin votos aún"
                        self._update_rating_hint(f"🎮 Promedio VRCMT actualizado: {vrcmt_str}")

                elif tag == 'rating_done':
                    _, finished_imdb, snap_rating, ok, agg_avg, agg_count = msg
                    if ok:
                        logging.info("⭐ [Rating] Subido OK: %s = %.1f", finished_imdb, snap_rating)
                        if agg_avg > 0:
                            # Aggregate viene directamente de la transacción Firestore —
                            # sin consulta extra. Actualizar L1, L1-shared y L2 (SQLite).
                            self._vrcmt_rating_cache[finished_imdb] = (agg_avg, agg_count)
                            _VRCMT_SHARED_CACHE[finished_imdb] = (agg_avg, agg_count, time.monotonic())
                            _vrcmt_cache_write_db(finished_imdb, agg_avg, agg_count)
                            self._vrcmt_rating_pending.discard(finished_imdb)
                            if (shiboken.isValid(self.lbl_meta)
                                    and (self.item.imdb_id or '').strip() == finished_imdb):
                                self.lbl_meta.setText(self._build_meta_line(agg_avg, agg_count))
                            self._update_rating_hint(
                                f"✅ Guardado · 🎮 {agg_avg:.1f} VRCMT ({agg_count} voto{'s' if agg_count != 1 else ''})"
                            )
                        else:
                            self._update_rating_hint("✅ Calificación sincronizada con Firebase")
                            self._vrcmt_rating_cache.pop(finished_imdb, None)
                            _VRCMT_SHARED_CACHE.pop(finished_imdb, None)
                    else:
                        self._update_rating_hint("⚠️ Error al sincronizar con Firebase")
                        logging.error("⭐ [Rating] Error al subir: %s", finished_imdb)
                        self._vrcmt_rating_cache.pop(finished_imdb, None)
                        _VRCMT_SHARED_CACHE.pop(finished_imdb, None)

                elif tag == 'access':
                    _, key, ok = msg
                    self._access_cache[key] = ok
                    cbs = self._access_pending.pop(key, [])
                    for cb in cbs:
                        try:
                            cb(ok)
                        except Exception:
                            pass

                elif tag == 'trailer':
                    _, snap_tmdb, key = msg
                    self._trailer_pending.discard(snap_tmdb)
                    key_or_none = key if key else None
                    self._trailer_cache[snap_tmdb] = key_or_none
                    if (shiboken.isValid(self.btn_trailer)
                            and getattr(self.item, 'tmdb_id', None) == snap_tmdb):
                        self.btn_trailer.setVisible(bool(key_or_none))

            except Exception as e:
                logging.debug("_drain_fb_queue tag=%s: %s", tag, e)

    def _safe_fetch_vrcmt(self, imdb_id: str, post_rating: bool = False):
        """Lanza el fetch del promedio comunitario con guard de validez.

        post_rating=True indica que es un re-fetch inmediatamente después de
        guardar la calificación; usará el tag 'vrcmt_post_rating' para que
        _drain_fb_queue actualice el hint al recibir el resultado.
        """
        # #region agent log
        import json as _j, time as _t
        _valid = shiboken.isValid(self)
        _cur = (self.item.imdb_id or '').strip() if _valid else 'INVALID'
        open('debug-6ee757.log','a').write(_j.dumps({"sessionId":"6ee757","timestamp":int(_t.time()*1000),"location":"media_modal:_safe_fetch_vrcmt","message":"safe_fetch called","data":{"imdb":imdb_id,"valid":_valid,"cur_imdb":_cur,"pending":list(self._vrcmt_rating_pending) if _valid else []},"hypothesisId":"H1,H4,H5"})+'\n')
        # #endregion
        if not shiboken.isValid(self):
            return
        if (self.item.imdb_id or '').strip() != imdb_id:
            return
        self._vrcmt_rating_cache.pop(imdb_id, None)
        self._vrcmt_rating_pending.discard(imdb_id)
        self._fetch_vrcmt_rating_async(imdb_id, post_rating=post_rating)

    def _on_rating_unfreeze(self):
        """Llamado cuando el freeze de 5 min expira: desbloquea y reintenta."""
        if not shiboken.isValid(self):
            return
        self._rating_frozen = False
        self._rating_upload_times.clear()
        logging.info("⭐ [Rating] Freeze terminado — calificación desbloqueada.")
        self._update_rating_hint("🔓 Desbloqueado. Puedes calificar de nuevo.")
        # Si hay un voto pendiente, subirlo ahora
        if self._pending_rating_imdb and self._pending_rating_imdb != self._last_uploaded_imdb:
            self._do_upload_rating()

    def _update_rating_hint(self, msg: str):
        """Actualiza el label de hint de la sección de calificación (si existe)."""
        if not shiboken.isValid(self):
            return
        try:
            if hasattr(self, '_lbl_rating_hint') and shiboken.isValid(self._lbl_rating_hint):
                self._lbl_rating_hint.setText(msg)
        except Exception:
            pass

    def save_tags(self):
        if not shiboken.isValid(self): return
        self.item.etiquetas = self.entry_tags.text()
        self.item.save()
        QMessageBox.information(self, "Éxito", "Notas guardadas.")

    def save_temp_ep(self):
        if not shiboken.isValid(self): return
        self.item.temporada = self.entry_temp.text()
        self.item.episodio = self.entry_ep.text()
        self.item.save()
        self.refresh_episodes_list()

    def toggle_anime(self):
        if not shiboken.isValid(self): return
        self.item.es_anime = 1 if self.item.es_anime == 0 else 0
        self.item.save()
        self.btn_anime.setText("Quitar Anime" if self.item.es_anime else "Marcar Anime")
        self.data_changed.emit(self.item)

    def toggle_seen(self):
        if not shiboken.isValid(self): return
        self.item.estado_visto = 1 if self.item.estado_visto == 0 else 0
        self.item.save()
        self.btn_seen.setText("👁️ Ya la ví" if self.item.estado_visto else "⭕ Marcar Visto")
        self.data_changed.emit(self.item)

    def toggle_fav(self):
        if not shiboken.isValid(self): return
        self.item.es_favorito = 1 if self.item.es_favorito == 0 else 0
        self.item.save()
        self.btn_fav.setText("❤️ Favorito" if self.item.es_favorito else "🤍 Añadir Favorito")
        self.data_changed.emit(self.item)

    # ------------------------------------------------------------------
    # Helpers para eliminación masiva
    # ------------------------------------------------------------------

    def _delete_scope(self):
        """Devuelve la lista de todos los registros que deben eliminarse.

        · Película / Video  → todos los registros con mismo título, tipo y mundo.
        · Serie             → todos los episodios con mismo título (todas las temporadas).
        · Catálogo imágenes → todas las imágenes del mismo mundo (Stream/Imagen).
        · Imagen individual → solo el registro actual.
        """
        tipo = (getattr(self.item, 'tipo_contenido', '') or '').strip()
        titulo = (self.item.titulo or '').strip()
        world  = (self.item.world_name or '').strip()

        if tipo == 'Serie':
            rows = list(
                Multimedia.select()
                .where(
                    (Multimedia.titulo == titulo) &
                    (Multimedia.tipo_contenido == 'Serie')
                )
            )
        elif _tipo_normalizado_es_stream_imagen(tipo):
            if self._is_world_capture_album():
                # Catálogo: solo capturas reales del mundo (excluyendo streams de YouTube/Twitch/Kick)
                q = (
                    Multimedia.select()
                    .where(
                        (Multimedia.world_name == world) &
                        (fn.REPLACE(fn.REPLACE(Multimedia.tipo_contenido, ' ', ''), '\uff0f', '/') == 'Stream/Imagen')
                    )
                )
                for host in self._SOCIAL_VIDEO_HOSTS:
                    q = q.where(~Multimedia.url.contains(host))
                rows = list(q)
            else:
                rows = [self.item]
        else:
            # Película, Video u otro: todos los registros con mismo título + tipo + mundo
            rows = list(
                Multimedia.select()
                .where(
                    (Multimedia.titulo == titulo) &
                    (Multimedia.tipo_contenido == tipo) &
                    (Multimedia.world_name == world)
                )
            )
            # Si la consulta devuelve 0 (mismatch de world), incluir al menos el item actual
            if not rows:
                rows = [self.item]

        return rows

    _SOCIAL_HOSTS = ('youtube.com', 'youtu.be', 'twitch.tv', 'kick.com',
                     'soundcloud.com', 'googlevideo.com', 'manifest.googlevideo.com')

    def _is_social_url(self, url: str) -> bool:
        u = (url or '').lower()
        return any(h in u for h in self._SOCIAL_HOSTS)

    def _delete_btn_label(self):
        """Texto del botón Eliminar según tipo de contenido."""
        tipo = (getattr(self.item, 'tipo_contenido', '') or '').strip()
        if tipo == 'Serie':
            return '🗑️ Eliminar Serie Completa'
        if tipo == 'Pelicula':
            return '🗑️ Eliminar Película'
        if tipo == 'Video':
            return '🗑️ Eliminar Video'
        if _tipo_normalizado_es_stream_imagen(tipo):
            if self._is_world_capture_album():
                return '🗑️ Eliminar Catálogo'
            url = (self.item.url or '')
            if self.engine.img_manager.is_image_url(url):
                return '🗑️ Eliminar Imagen'
            return '🗑️ Eliminar Video'
        return '🗑️ Eliminar del Catálogo'

    def _delete_confirm_text(self, rows):
        """Genera el mensaje de confirmación personalizado."""
        tipo   = (getattr(self.item, 'tipo_contenido', '') or '').strip()
        titulo = (self.item.titulo or '').strip()
        world  = (self.item.world_name or '').strip()
        n      = len(rows)

        if tipo == 'Serie':
            return (
                f"¿Eliminar la serie completa <b>{titulo}</b>?<br><br>"
                f"Se borrarán <b>{n} episodio{'s' if n != 1 else ''}</b> y todos sus "
                f"enlaces guardados. Esta acción no se puede deshacer."
            )
        if _tipo_normalizado_es_stream_imagen(tipo) and self._is_world_capture_album():
            return (
                f"¿Eliminar el catálogo <b>{world}</b>?<br><br>"
                f"Se borrarán <b>{n} imagen{'es' if n != 1 else ''}</b> y todos sus "
                f"enlaces guardados. Esta acción no se puede deshacer."
            )
        # Etiqueta amigable según tipo
        if _tipo_normalizado_es_stream_imagen(tipo):
            url = (self.item.url or '')
            if self.engine.img_manager.is_image_url(url):
                tipo_label, genero = 'imagen', 'esta'
            else:
                tipo_label, genero = 'video', 'este'
        else:
            tipo_label = {'Pelicula': 'película', 'Video': 'video'}.get(tipo, 'registro')
            genero     = 'este' if tipo_label == 'video' else 'esta'
        if n > 1:
            return (
                f"¿Eliminar <b>{titulo}</b> y sus <b>{n} registros</b> del catálogo?<br><br>"
                f"Se borrarán todos los enlaces y datos guardados. Esta acción no se puede deshacer."
            )
        return (
            f"¿Eliminar {genero} <b>{tipo_label}</b> "
            f"<b>{titulo}</b> del catálogo?<br><br>"
            f"Esta acción no se puede deshacer."
        )

    def on_delete(self):
        if not shiboken.isValid(self):
            return

        rows  = self._delete_scope()
        msg   = self._delete_confirm_text(rows)

        dlg = QMessageBox(self)
        dlg.setWindowTitle("Confirmar eliminación")
        dlg.setTextFormat(Qt.RichText)
        dlg.setText(msg)
        dlg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        dlg.setDefaultButton(QMessageBox.No)
        dlg.button(QMessageBox.Yes).setText("Sí, eliminar")
        dlg.button(QMessageBox.No).setText("Cancelar")
        dlg.setIcon(QMessageBox.Warning)

        if dlg.exec() != QMessageBox.Yes:
            return

        deleted = 0
        for row in rows:
            try:
                row.delete_instance()
                deleted += 1
            except Exception as e:
                logging.error("Error eliminando registro %s: %s", getattr(row, 'id', '?'), e)

        logging.info("🗑️ Eliminados %d registros del catálogo (%s).", deleted, self.item.titulo)
        self.data_changed.emit(self.item)
        self.close_modal()

    def _build_meta_line(self, vrcmt_avg: float = -1.0, vrcmt_count: int = 0) -> str:
        """Construye la línea de metadatos del título incluyendo rating VRCMT si existe."""
        tmdb_r = float(self.item.calificacion_global or 0.0)
        parts = [
            f"📅 {self.item.año or '????'}",
            f"⭐ {tmdb_r:.1f} TMDb",
        ]
        if vrcmt_avg > 0:
            parts.append(f"🎮 {vrcmt_avg:.1f} VRCMT ({vrcmt_count})")
        parts.append(f"🎭 {self.item.generos or 'N/A'}")
        return "  |  ".join(parts)

    def _fetch_vrcmt_rating_async(self, imdb_id: str, post_rating: bool = False):
        """Obtiene el rating comunitario VRCMT y actualiza lbl_meta.

        Orden de prioridad (todas las lecturas van a local primero):
        L1 – Cache de instancia (_vrcmt_rating_cache): sin TTL, sin I/O.
        L2 – Tabla VRCMTRatingCache en SQLite local: TTL 1 hora, sin red.
        L3 – Firebase Firestore: solo si L1 y L2 miss/expirados.

        post_rating=True: usa tag 'vrcmt_post_rating' para que _drain_fb_queue
        actualice también el hint cuando llegue el resultado de Firebase.
        """
        # ── L1: Cache de instancia (sin TTL, sin I/O) ─────────────────────────
        cached = self._vrcmt_rating_cache.get(imdb_id, ...)
        if cached is not ...:
            avg, count = cached
            if shiboken.isValid(self.lbl_meta):
                self.lbl_meta.setText(self._build_meta_line(avg, count))
            return

        # ── L2: Cache en memoria compartido (TTL L1=10 min) ───────────────────
        shared = _VRCMT_SHARED_CACHE.get(imdb_id)
        if shared is not None:
            s_avg, s_count, s_ts = shared
            if time.monotonic() - s_ts < _VRCMT_L1_TTL:
                self._vrcmt_rating_cache[imdb_id] = (s_avg, s_count)
                if shiboken.isValid(self.lbl_meta):
                    self.lbl_meta.setText(self._build_meta_line(s_avg, s_count))
                return
            else:
                _VRCMT_SHARED_CACHE.pop(imdb_id, None)

        # ── L2b: SQLite local (TTL 1 hora, sin consultar Firebase) ────────────
        db_cached = _vrcmt_cache_read_db(imdb_id)
        if db_cached is not None:
            db_avg, db_count = db_cached
            self._vrcmt_rating_cache[imdb_id] = (db_avg, db_count)
            _VRCMT_SHARED_CACHE[imdb_id] = (db_avg, db_count, time.monotonic())
            if shiboken.isValid(self.lbl_meta):
                self.lbl_meta.setText(self._build_meta_line(db_avg, db_count))
            return

        # ── L3: Firebase (solo si no hay ningún cache válido) ─────────────────
        if imdb_id in self._vrcmt_rating_pending:
            return

        self._vrcmt_rating_pending.add(imdb_id)
        snapshot_id = imdb_id
        tag_name = 'vrcmt_post_rating' if post_rating else 'vrcmt'

        def _op():
            return self.engine.firebase.get_vrcmt_rating(snapshot_id)

        def _cb(result, error):
            avg, count = (result if result else (0.0, 0)) if not error else (0.0, 0)
            self._fb_ui_q.put((tag_name, snapshot_id, float(avg), int(count)))

        self.engine.firebase.run_firebase_async(_op, _cb)

    def _fetch_trailer_async(self, tmdb_id: int, media_type: str):
        """Busca tráiler TMDB en background usando threading.Thread + _fb_ui_q.

        - Si ya está en caché (hit o miss) aplica al instante.
        - Si ya hay un fetch en vuelo para ese tmdb_id, no lanza otro.
        Usa threading.Thread en lugar de QRunnable para evitar el ACCESS_VIOLATION
        de Python 3.13 que ocurre cuando Qt destruye el QRunnable desde el pool thread.
        """
        cached = self._trailer_cache.get(tmdb_id, ...)  # ... = "no en caché"
        if cached is not ...:
            if shiboken.isValid(self.btn_trailer):
                self.btn_trailer.setVisible(bool(cached))
            return

        if tmdb_id in self._trailer_pending:
            return

        self._trailer_pending.add(tmdb_id)
        if shiboken.isValid(self.btn_trailer):
            self.btn_trailer.setVisible(False)

        snap_tmdb = tmdb_id
        snap_media = media_type
        snap_lang = getattr(self.engine, '_get_tmdb_lang', lambda: 'es-MX')()
        snap_tmdb_client = self.engine.tmdb
        snap_queue = self._fb_ui_q

        def _thread_target():
            key = ''
            try:
                langs = [snap_lang] + (['en-US'] if snap_lang != 'en-US' else [])
                for lang in langs:
                    try:
                        d = snap_tmdb_client.get_details(snap_media, snap_tmdb, language=lang)
                        videos = d.get('videos', {}).get('results', [])
                        found = (
                            next((x['key'] for x in videos if x.get('type') == 'Trailer' and x.get('key')), None)
                            or next((x['key'] for x in videos if x.get('type') == 'Teaser' and x.get('key')), None)
                            or next((x['key'] for x in videos if x.get('key')), None)
                        )
                        if found:
                            key = found
                            break
                    except Exception as e:
                        logging.debug("_fetch_trailer_async lang=%s err=%s", lang, e)
            except Exception as e:
                logging.debug("_fetch_trailer_async error: %s", e)
            try:
                snap_queue.put(('trailer', snap_tmdb, key))
            except Exception:
                pass

        import threading as _threading
        t = _threading.Thread(target=_thread_target, daemon=True, name=f"TrailerFetch-{snap_tmdb}")
        t.start()

    def on_trailer_clicked(self):
        if not shiboken.isValid(self):
            return
        uitem = str(self.item.url or "").strip()
        tipo  = (getattr(self.item, 'tipo_contenido', '') or '').strip()

        # ── Imagen → abrir URL en navegador (visor interno pendiente de desarrollar) ──
        if self._tipo_stream_imagen_ui() and self.engine.img_manager.is_image_url(uitem):
            if uitem:
                webbrowser.open(uitem)
            return

        # ── Video / Stream social (YouTube, Twitch, Kick…) → abrir en navegador ──────
        if tipo == 'Video' or self._tipo_stream_imagen_ui():
            if uitem:
                webbrowser.open(uitem)
            return

        # ── Película / Serie → abrir tráiler desde caché (ya verificado en background) ──
        tid = getattr(self.item, 'tmdb_id', None)
        if tid:
            cached_key = self._trailer_cache.get(tid)
            if cached_key:
                webbrowser.open(f"https://www.youtube.com/watch?v={cached_key}")
            else:
                # Fallback: el botón no debería ser visible si no hay tráiler, pero por
                # seguridad ofrecemos intentarlo de nuevo si el caché falla.
                logging.warning("on_trailer_clicked: sin key en caché para tmdb_id=%s", tid)
                QMessageBox.information(
                    self, "Sin tráiler",
                    f"No se encontró tráiler disponible para\n<b>{self.item.titulo}</b>.",
                )

    def on_fix_clicked(self):
        if not shiboken.isValid(self) or not self.isVisible(): return
        i = self.entry_imdb.text().strip()
        if i.startswith('tt'):
            r = self.engine.tmdb.find_by_imdb_id(i, language=self.engine._get_tmdb_lang())
            if r: self.apply_search_result(r)
        else: self.open_manual_search()

    def open_manual_search(self):
        if not shiboken.isValid(self): return
        main_win = self.window()
        if hasattr(main_win, '_build_modal_overlay'):
            sd = SearchDialog(self.engine.tmdb, main_win)
            sd.setWindowFlags(Qt.Widget)
            sd.setModal(False)
            sd.setWindowModality(Qt.WindowModality.NonModal)
            sd.result_selected.connect(self.apply_search_result)
            main_win._build_modal_overlay(sd)
        else:
            sd = SearchDialog(self.engine.tmdb, self)
            sd.result_selected.connect(self.apply_search_result)
            sd.exec()

    def apply_search_result(self, r):
        if not shiboken.isValid(self): return
        # Guardar el imdb_id ANTES del Fix para detectar si cambia (migración de voto).
        _old_imdb_id = (self.item.imdb_id or '').strip()

        d = self.engine.tmdb.get_details(r['media_type'], r['id'], language=self.engine._get_tmdb_lang())
        self.item.titulo = d.get('title') or d.get('name')
        self.item.sinopsis = d.get('overview') or ""
        ext = d.get("external_ids") or {}
        self.item.imdb_id = ext.get("imdb_id") or self.item.imdb_id
        self.item.tmdb_id = str(d.get("id") or self.item.tmdb_id or "")
        pp = d.get("poster_path")
        self.item.poster_path = f"https://image.tmdb.org/t/p/w500{pp}" if pp else (self.item.poster_path or "")
        genres = ", ".join([g["name"] for g in (d.get("genres") or [])])
        self.item.generos = genres or self.item.generos
        rd = d.get("release_date") or d.get("first_air_date") or ""
        if rd:
            self.item.año = rd[:4]
        runtime = d.get("runtime") or (
            (d.get("episode_run_time") or [0])[0] if d.get("episode_run_time") else 0
        )
        self.item.duracion_total = float(runtime or 0)

        # Senior Fix: Sincronizar Metadatos Extendidos (Director, Elenco, Colección)
        credits = d.get("credits") or {}
        self.item.elenco = ", ".join([c["name"] for c in credits.get("cast", [])[:10]])
        director = next((c["name"] for c in credits.get("crew", []) if c.get("job") == "Director"), "")
        if not director and d.get("created_by"):
            director = ", ".join(
                [c.get("name") for c in (d.get("created_by") or [])[:2] if c.get("name")]
            )
        self.item.director = director
        
        belongs_to_collection = d.get('belongs_to_collection')
        self.item.coleccion = belongs_to_collection.get('name') if belongs_to_collection else None
        self.item.coleccion_id = belongs_to_collection.get('id', 0) if belongs_to_collection else 0
        self.item.calificacion_global = float(d.get('vote_average', 0.0))

        # Senior Fix: Sincronizar tipo de contenido con la elección manual
        res_type = r.get('media_type', 'movie')
        self.item.tipo_contenido = 'Serie' if res_type == 'tv' else 'Pelicula'
        
        # [ES] Detección automática de Anime basada en géneros
        genres_lower = [g['name'].lower() for g in d.get('genres', [])]
        if 'animation' in genres_lower or 'animación' in genres_lower:
            countries = d.get('origin_country', [])
            if 'JP' in countries:
                self.item.es_anime = 1

        self.item.save()

        # Migración de voto comunitario si el imdb_id cambió con el Fix.
        # Si el usuario tenía calificación > 0, moverla al nuevo imdb_id en Firebase.
        _new_imdb_id = (self.item.imdb_id or '').strip()
        discord_id = self.engine.discord.get_saved_id() if self.engine.discord else ''
        if _new_imdb_id and discord_id:
            personal_rating = float(self.item.calificacion_personal or 0.0)
            if personal_rating > 0 or (_old_imdb_id and _old_imdb_id != _new_imdb_id):
                _migrate_imdb  = _new_imdb_id
                _migrate_old   = _old_imdb_id if _old_imdb_id != _new_imdb_id else ''
                _migrate_lid   = str(getattr(self.item, 'id', '') or '')
                _migrate_disc  = discord_id
                _migrate_rating = personal_rating

                def _op_migrate():
                    self.engine.firebase.sync_rating(
                        _migrate_disc, _migrate_imdb, _migrate_rating,
                        local_db_id=_migrate_lid,
                        old_imdb_id=_migrate_old,
                    )
                    return 'ok'

                self.engine.firebase.run_firebase_async(_op_migrate)

        self.refresh_modal_view()
        self.data_changed.emit(self.item)

    def _emit_rating_catalog_deferred(self, item_ref):
        """Emite data_changed en el siguiente tick; evita doble emisión si flush ya notificó."""
        if not shiboken.isValid(self) or not self._rating_catalog_pending_notify:
            return
        self._rating_catalog_pending_notify = False
        try:
            self.data_changed.emit(item_ref)
        except Exception:
            pass

    # ── Secciones colapsables / Collapsible sections ────────────────────────

    def _toggle_links_section(self):
        """Alterna colapso del área de enlaces/imágenes. Toggle links/images section."""
        self._links_collapsed = not self._links_collapsed
        self._apply_links_visibility(self._links_section_visible)

    def _toggle_eps_section(self):
        """Reservado / Reserved — episodios ahora en tab dedicado, no colapsable."""
        pass

    def _apply_links_visibility(self, visible: bool):
        """Centraliza setVisible del área de enlaces respetando el estado de colapso.
        Centralizes setVisible for the links area, respecting the collapse state."""
        self._links_section_visible = visible
        if hasattr(self, 'lbl_versions_title') and shiboken.isValid(self.lbl_versions_title):
            arrow = "▶" if self._links_collapsed else "▼"
            txt = self.lbl_versions_title.text()
            # Quitar prefijo de flecha anterior / Strip existing arrow prefix
            if len(txt) >= 2 and txt[:2] in ("▼ ", "▶ "):
                txt = txt[2:]
            self.lbl_versions_title.setText(f"{arrow} {txt}")
            self.lbl_versions_title.setVisible(visible)
        if hasattr(self, 'ver_scroll') and shiboken.isValid(self.ver_scroll):
            self.ver_scroll.setVisible(visible and not self._links_collapsed)

    def _apply_eps_visibility(self, visible: bool):
        """Oculta/muestra solo la parte de episodios en Tab 2.
        Tab 2 siempre es visible (muestra los enlaces incluso para películas).
        Hides/shows only the episodes portion in Tab 2.
        Tab 2 is always visible (shows links even for movies)."""
        self._eps_section_visible = visible
        # Ocultar solo los widgets de capítulos, no el tab completo.
        # El tab siempre está disponible para ver los enlaces de reproducción.
        # Hide only the episode widgets, not the whole tab.
        # The tab always stays available to show playback links.
        if hasattr(self, 'lbl_episodes_title') and shiboken.isValid(self.lbl_episodes_title):
            self.lbl_episodes_title.setVisible(bool(visible))
        if hasattr(self, 'ep_pager') and shiboken.isValid(self.ep_pager):
            self.ep_pager.setVisible(bool(visible))
        if hasattr(self, 'ep_scroll') and shiboken.isValid(self.ep_scroll):
            self.ep_scroll.setVisible(bool(visible))
            # Cuando no hay capítulos, el ver_scroll puede crecer para llenar el espacio
            # When no episodes, ver_scroll can grow to fill the tab space
            if hasattr(self, 'ver_scroll') and shiboken.isValid(self.ver_scroll):
                tab2_stretch = 0 if bool(visible) else 1
                tab2_l = self.ver_scroll.parent()
                if tab2_l and hasattr(tab2_l, 'layout') and tab2_l.layout():
                    lay = tab2_l.layout()
                    idx = lay.indexOf(self.ver_scroll)
                    if idx >= 0:
                        try:
                            lay.setStretch(idx, tab2_stretch)
                        except Exception:
                            pass

    # ── Fin secciones colapsables ────────────────────────────────────────────

    def flush_rating_state_before_close(self):
        """Antes de destruir el overlay: sube nota pendiente a Firebase y sincroniza el catálogo.

        - Si el debounce de 20s seguía activo, dispara la subida en caliente (no se pierde al cerrar).
        - Si data_changed aún no se emitió (p. ej. self inválido en el singleShot), notifica aquí.
        """
        if not shiboken.isValid(self):
            return
        debounce_was_active = self._rating_debounce_timer.isActive()
        self._rating_debounce_timer.stop()
        if debounce_was_active and not self._rating_frozen:
            self._do_upload_rating()
        if self._rating_catalog_pending_notify and self.item is not None:
            self._rating_catalog_pending_notify = False
            try:
                self.data_changed.emit(self.item)
            except Exception:
                pass

    def close_modal(self):
        main_win = self.window()
        if hasattr(main_win, 'close_modal'): main_win.close_modal()
        else: self.close()

    def _play_video(self, url, title):
        if not shiboken.isValid(self):
            return
        # Si la URL es una imagen → abrir visor de imágenes interno
        if url and self.engine.img_manager.is_image_url(url):
            try:
                from src.ui.image_viewer import open_image_viewer, _is_discord_cdn_url, _is_discord_cdn_expired, _local_cache_path

                # Resolver la mejor fuente disponible para la imagen:
                # 1. Si hay caché local en el viewer cache → usarla
                # 2. Si URL Discord CDN expirada → intentar poster_path local del ítem
                # 3. En otro caso → usar la URL remota normalmente
                # Resolve best available source for the image:
                # 1. If local viewer cache exists → use it
                # 2. If Discord CDN URL expired → try item's local poster_path
                # 3. Otherwise → use remote URL normally
                resolved_url = url
                if url.startswith(('http://', 'https://')):
                    cached = _local_cache_path(url)
                    if os.path.isfile(cached):
                        resolved_url = cached
                    elif _is_discord_cdn_url(url) and _is_discord_cdn_expired(url):
                        # URL de Discord expirada → buscar alternativa local
                        # Expired Discord URL → look for local alternative
                        local_poster = (getattr(self.item, 'poster_path', '') or '').strip()
                        if local_poster and os.path.isfile(local_poster):
                            resolved_url = local_poster
                            logging.info(
                                "ImageViewer: URL Discord expirada, usando poster_path local: %s",
                                local_poster,
                            )
                        else:
                            # Buscar en captures dir por hash del path sin query
                            # Search captures dir by hash of path without query
                            from src.core.paths import CAPTURES_DIR
                            import hashlib
                            path_part = url.split("?")[0]
                            world = (getattr(self.item, 'world_name', '') or getattr(self.item, 'titulo', '') or '').strip()
                            if world:
                                safe = "".join(x for x in world if x.isalnum() or x in " -_")
                                world_dir = os.path.join(CAPTURES_DIR, safe)
                                file_hash = hashlib.md5(path_part.encode()).hexdigest()
                                ext = path_part.rsplit(".", 1)[-1]
                                if len(ext) > 5 or not ext.isalpha():
                                    ext = "jpg"
                                candidate = os.path.join(world_dir, f"capture_{file_hash}.{ext}")
                                if os.path.isfile(candidate):
                                    resolved_url = candidate
                                    logging.info(
                                        "ImageViewer: usando captura local de '%s': %s",
                                        world, candidate,
                                    )

                open_image_viewer(resolved_url, title, self.engine, self.window())
            except Exception as e:
                logging.error("Error abriendo visor de imágenes: %s", e)
                webbrowser.open(url)
            return

        from src.ui.video_player import VRCMTPlayer
        try:
            player = VRCMTPlayer(url, title, self.window(), self.engine)
            player.exec()
        except Exception as e:
            logging.error("Error abriendo reproductor nativo: %s", e)
            webbrowser.open(url)
