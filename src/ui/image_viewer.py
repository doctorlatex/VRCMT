"""
image_viewer.py — Visor de imágenes de VRCMT
============================================
• Free    → pan/zoom, fit-to-window, abrir en navegador.
• Premium → además: rotar, voltear, recortar, brillo/contraste/saturación,
            filtros (B&N, Sepia, Desenfoque, Nitidez) + IA (remover fondo).
            Guardar como PNG/JPEG/WebP.

Se abre como overlay modal del mismo estilo que MediaModal.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import webbrowser
from pathlib import Path
from typing import Optional

import requests
from PySide6.QtCore import (
    QByteArray, QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
)
from PySide6.QtGui import (
    QColor, QCursor, QImage, QKeySequence, QPainter, QPixmap,
    QTransform, QWheelEvent
)
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QVBoxLayout, QWidget
)

log = logging.getLogger(__name__)

# ── helpers ──────────────────────────────────────────────────────────────────

def _pil_available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def _rembg_available() -> bool:
    try:
        import rembg  # noqa: F401
        return True
    except ImportError:
        return False


def _qpixmap_from_bytes(data: bytes) -> QPixmap:
    """Convierte bytes de imagen a QPixmap."""
    ba = QByteArray(data)
    img = QImage()
    img.loadFromData(ba)
    return QPixmap.fromImage(img)


def _bytes_from_url(url: str, timeout: int = 15) -> bytes:
    """Descarga la imagen desde una URL y devuelve los bytes."""
    resp = requests.get(url, timeout=timeout, headers={
        "User-Agent": "VRCMT/4.7 (+https://discord.gg/enKmpDQwY3)"
    })
    resp.raise_for_status()
    return resp.content


# ── worker para carga asíncrona ───────────────────────────────────────────────

class _LoadSignals(QObject):
    finished = Signal(bytes)
    error    = Signal(str)


class _LoadWorker(QRunnable):
    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.signals = _LoadSignals()

    def run(self):
        try:
            data = _bytes_from_url(self.url)
            self.signals.finished.emit(data)
        except Exception as exc:
            self.signals.error.emit(str(exc))


# ── canvas con pan / zoom ─────────────────────────────────────────────────────

class _ImageCanvas(QGraphicsView):
    """QGraphicsView con pan (botón izquierdo) y zoom (rueda)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene   = QGraphicsScene(self)
        self._pitem: Optional[QGraphicsPixmapItem] = None
        self._zoom    = 1.0
        self._dragging = False
        self._last_pos = None

        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#1a1a2e"))
        self.setStyleSheet("border: none;")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def set_pixmap(self, pix: QPixmap):
        self._scene.clear()
        self._pitem = QGraphicsPixmapItem(pix)
        self._pitem.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self._pitem)
        self._scene.setSceneRect(self._pitem.boundingRect())
        self._zoom = 1.0
        self.resetTransform()
        self.fit_in_view()

    def fit_in_view(self):
        if self._pitem:
            self.fitInView(self._pitem, Qt.KeepAspectRatio)
            self._zoom = self.transform().m11()

    def wheelEvent(self, event: QWheelEvent):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        new_z  = self._zoom * factor
        if 0.05 < new_z < 20:
            self.scale(factor, factor)
            self._zoom = new_z

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._last_pos = event.position().toPoint()
            self.setCursor(QCursor(Qt.ClosedHandCursor))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self._last_pos is not None:
            delta = event.position().toPoint() - self._last_pos
            self._last_pos = event.position().toPoint()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self.setCursor(QCursor(Qt.ArrowCursor))
        super().mouseReleaseEvent(event)


# ── panel de herramientas (premium) ──────────────────────────────────────────

def _tool_btn(text: str, color: str = "#2d3a4a") -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(
        f"QPushButton{{background:{color};color:#e8eaf6;border-radius:6px;"
        f"padding:5px 8px;font-size:12px;font-weight:600;}}"
        f"QPushButton:hover{{background:#4a6fa5;}}"
        f"QPushButton:disabled{{background:#222;color:#555;}}"
    )
    btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return btn


def _slider(lo: int, hi: int, val: int) -> QSlider:
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(val)
    s.setStyleSheet(
        "QSlider::groove:horizontal{height:4px;background:#2d3a4a;border-radius:2px;}"
        "QSlider::handle:horizontal{width:12px;height:12px;margin:-4px 0;"
        "background:#4a9eff;border-radius:6px;}"
        "QSlider::sub-page:horizontal{background:#4a9eff;border-radius:2px;}"
    )
    return s


class _ToolPanel(QScrollArea):
    """Panel lateral con herramientas de edición (solo Premium)."""

    # señales para que el viewer reaccione
    rotate_left  = Signal()
    rotate_right = Signal()
    flip_h       = Signal()
    flip_v       = Signal()
    reset        = Signal()
    save         = Signal()
    remove_bg    = Signal()
    brightness_changed   = Signal(int)   # -100..100
    contrast_changed     = Signal(int)   # -100..100
    saturation_changed   = Signal(int)   # -100..100
    filter_selected      = Signal(str)   # 'none','grayscale','sepia','blur','sharpen'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(180)
        self.setStyleSheet(
            "QScrollArea{background:#121826;border:none;border-left:1px solid #263040;}"
            "QWidget{background:#121826;}"
            "QLabel{color:#9ba8b8;font-size:11px;font-weight:600;}"
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)

        inner = QWidget()
        self.setWidget(inner)
        vl = QVBoxLayout(inner)
        vl.setContentsMargins(8, 10, 8, 10)
        vl.setSpacing(6)

        # ── Transformar ──────────────────────────────────────────────────
        vl.addWidget(QLabel("TRANSFORMAR"))
        row1 = QHBoxLayout()
        self.btn_rl = _tool_btn("↺ Izq")
        self.btn_rr = _tool_btn("↻ Der")
        row1.addWidget(self.btn_rl)
        row1.addWidget(self.btn_rr)
        vl.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_fh = _tool_btn("⇔ H")
        self.btn_fv = _tool_btn("⇕ V")
        row2.addWidget(self.btn_fh)
        row2.addWidget(self.btn_fv)
        vl.addLayout(row2)

        self.btn_reset = _tool_btn("↩ Restablecer", "#3a2010")
        vl.addWidget(self.btn_reset)

        vl.addSpacing(6)
        vl.addWidget(QLabel("BRILLO"))
        self.sl_bright = _slider(-100, 100, 0)
        vl.addWidget(self.sl_bright)

        vl.addWidget(QLabel("CONTRASTE"))
        self.sl_contrast = _slider(-100, 100, 0)
        vl.addWidget(self.sl_contrast)

        vl.addWidget(QLabel("SATURACIÓN"))
        self.sl_sat = _slider(-100, 100, 0)
        vl.addWidget(self.sl_sat)

        vl.addSpacing(6)
        vl.addWidget(QLabel("FILTROS"))
        self.filter_btns: list[QPushButton] = []
        for key, label in [
            ('none',      '🎨 Original'),
            ('grayscale', '⬛ Blanco y Negro'),
            ('sepia',     '🟫 Sepia'),
            ('blur',      '💧 Desenfoque'),
            ('sharpen',   '🔪 Nitidez'),
        ]:
            b = _tool_btn(label)
            b.setCheckable(True)
            b.setProperty('filter_key', key)
            b.clicked.connect(lambda chk, k=key: self._on_filter(k))
            self.filter_btns.append(b)
            vl.addWidget(b)

        vl.addSpacing(6)
        vl.addWidget(QLabel("INTELIGENCIA ARTIFICIAL"))
        self.btn_rembg = _tool_btn("✂️ Quitar Fondo (IA)", "#1a3050")
        if not _rembg_available():
            self.btn_rembg.setToolTip(
                "Requiere: pip install \"rembg[cpu]\"\n"
                "Herramienta de eliminación de fondo con IA."
            )
        vl.addWidget(self.btn_rembg)

        vl.addSpacing(10)
        vl.addWidget(QLabel("GUARDAR"))
        self.btn_save = _tool_btn("💾 Guardar como…", "#0d3320")
        vl.addWidget(self.btn_save)

        vl.addStretch()

        # Conectar señales
        self.btn_rl.clicked.connect(self.rotate_left)
        self.btn_rr.clicked.connect(self.rotate_right)
        self.btn_fh.clicked.connect(self.flip_h)
        self.btn_fv.clicked.connect(self.flip_v)
        self.btn_reset.clicked.connect(self.reset)
        self.btn_save.clicked.connect(self.save)
        self.btn_rembg.clicked.connect(self.remove_bg)
        self.sl_bright.valueChanged.connect(self.brightness_changed)
        self.sl_contrast.valueChanged.connect(self.contrast_changed)
        self.sl_sat.valueChanged.connect(self.saturation_changed)

    def _on_filter(self, key: str):
        for b in self.filter_btns:
            b.setChecked(b.property('filter_key') == key)
        self.filter_selected.emit(key)


# ── modal principal ───────────────────────────────────────────────────────────

class ImageViewerModal(QFrame):
    """
    Visor de imágenes modal.
    Se comporta como MediaModal: se inserta en el overlay de la ventana principal.
    """
    closed = Signal()

    def __init__(self, url: str, title: str, engine, parent: QWidget = None):
        super().__init__(parent)
        self.url    = url
        self.title  = title
        self.engine = engine

        self._raw_bytes: Optional[bytes] = None   # bytes originales
        self._pil_orig  = None                    # PIL.Image original
        self._pil_edit  = None                    # PIL.Image con ediciones
        self._current_filter = 'none'
        self._brightness  = 0
        self._contrast    = 0
        self._saturation  = 0
        self._rotation    = 0
        self._flip_h      = False
        self._flip_v      = False

        self._build_ui()
        self._start_loading()

    # ── propiedades de acceso ─────────────────────────────────────────────────

    @property
    def _is_premium(self) -> bool:
        return getattr(self.engine, 'is_premium', False)

    # ── construcción UI ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.setObjectName("ImageViewerModal")
        self.setStyleSheet(
            "#ImageViewerModal{"
            "background:#0e131f;"
            "border-radius:12px;"
            "border:1px solid #263040;"
            "}"
        )
        self.setMinimumSize(700, 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── header ────────────────────────────────────────────────────────────
        header = QFrame()
        header.setFixedHeight(44)
        header.setStyleSheet(
            "background:#121826;border-radius:12px 12px 0 0;"
            "border-bottom:1px solid #263040;"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 0, 10, 0)

        self.lbl_title = QLabel(self.title or "Imagen")
        self.lbl_title.setStyleSheet(
            "color:#e8eaf6;font-size:13px;font-weight:700;background:transparent;"
        )
        hl.addWidget(self.lbl_title, stretch=1)

        # Botón abrir en navegador
        btn_browser = QPushButton("🌐 Abrir en Navegador")
        btn_browser.setStyleSheet(
            "QPushButton{background:#1e3a5f;color:#e8eaf6;border-radius:6px;"
            "padding:4px 10px;font-size:11px;font-weight:600;}"
            "QPushButton:hover{background:#2a5080;}"
        )
        btn_browser.clicked.connect(lambda: webbrowser.open(self.url))
        hl.addWidget(btn_browser)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(30, 30)
        btn_close.setStyleSheet(
            "QPushButton{background:#2d1010;color:#ff5252;border-radius:6px;"
            "font-size:14px;font-weight:700;}"
            "QPushButton:hover{background:#421515;}"
        )
        btn_close.clicked.connect(self.closed.emit)
        hl.addWidget(btn_close)
        root.addWidget(header)

        # ── body: canvas + panel lateral ─────────────────────────────────────
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # canvas
        self.canvas = _ImageCanvas()
        bl.addWidget(self.canvas, stretch=1)

        # panel premium
        if self._is_premium:
            self.tool_panel = _ToolPanel()
            self._connect_tools()
            bl.addWidget(self.tool_panel)
        else:
            self.tool_panel = None
            # Banner premium sutil
            lock = QLabel("🔒 Edición disponible para usuarios Premium")
            lock.setAlignment(Qt.AlignCenter)
            lock.setStyleSheet(
                "color:#c9a227;font-size:11px;font-weight:600;"
                "background:#1a1500;padding:4px;"
                "border-left:3px solid #c9a227;"
            )
            lock.setFixedWidth(170)
            lock.setWordWrap(True)
            bl.addWidget(lock)

        root.addWidget(body, stretch=1)

        # ── footer con controles de zoom ──────────────────────────────────────
        footer = QFrame()
        footer.setFixedHeight(40)
        footer.setStyleSheet(
            "background:#121826;border-radius:0 0 12px 12px;"
            "border-top:1px solid #263040;"
        )
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(14, 0, 14, 0)

        self.lbl_status = QLabel("Cargando imagen…")
        self.lbl_status.setStyleSheet("color:#6a8caf;font-size:11px;background:transparent;")
        fl.addWidget(self.lbl_status, stretch=1)

        btn_fit = QPushButton("⊞ Ajustar")
        btn_fit.setStyleSheet(
            "QPushButton{background:#1e2d3f;color:#9ba8b8;border-radius:5px;"
            "padding:3px 10px;font-size:11px;}"
            "QPushButton:hover{background:#2d3f55;color:#e8eaf6;}"
        )
        btn_fit.clicked.connect(self.canvas.fit_in_view)
        fl.addWidget(btn_fit)

        btn_zoom_in = QPushButton("➕")
        btn_zoom_out = QPushButton("➖")
        for b in (btn_zoom_out, btn_zoom_in):
            b.setFixedSize(28, 28)
            b.setStyleSheet(
                "QPushButton{background:#1e2d3f;color:#9ba8b8;border-radius:5px;"
                "font-size:13px;}"
                "QPushButton:hover{background:#2d3f55;color:#e8eaf6;}"
            )
        btn_zoom_in.clicked.connect(lambda: self._zoom_step(1.2))
        btn_zoom_out.clicked.connect(lambda: self._zoom_step(1 / 1.2))
        fl.addWidget(btn_zoom_out)
        fl.addWidget(btn_zoom_in)

        root.addWidget(footer)

    def _zoom_step(self, factor: float):
        self.canvas.scale(factor, factor)
        self.canvas._zoom *= factor

    # ── conexión de herramientas premium ─────────────────────────────────────

    def _connect_tools(self):
        p = self.tool_panel
        p.rotate_left.connect(lambda: self._apply_rotation(-90))
        p.rotate_right.connect(lambda: self._apply_rotation(90))
        p.flip_h.connect(self._apply_flip_h)
        p.flip_v.connect(self._apply_flip_v)
        p.reset.connect(self._reset_edits)
        p.save.connect(self._save_image)
        p.remove_bg.connect(self._remove_background)
        p.brightness_changed.connect(self._on_brightness)
        p.contrast_changed.connect(self._on_contrast)
        p.saturation_changed.connect(self._on_saturation)
        p.filter_selected.connect(self._on_filter)

    # ── carga de imagen ───────────────────────────────────────────────────────

    def _start_loading(self):
        if self.url.startswith(('http://', 'https://')):
            worker = _LoadWorker(self.url)
            worker.signals.finished.connect(self._on_loaded)
            worker.signals.error.connect(self._on_load_error)
            QThreadPool.globalInstance().start(worker)
        else:
            try:
                with open(self.url, 'rb') as f:
                    self._on_loaded(f.read())
            except Exception as e:
                self._on_load_error(str(e))

    def _on_loaded(self, data: bytes):
        self._raw_bytes = data
        pix = _qpixmap_from_bytes(data)
        if pix.isNull():
            self.lbl_status.setText("⚠️ No se pudo cargar la imagen")
            return

        self.canvas.set_pixmap(pix)
        w, h = pix.width(), pix.height()
        self.lbl_status.setText(f"{w} × {h} px")

        if _pil_available():
            try:
                from PIL import Image
                self._pil_orig = Image.open(io.BytesIO(data)).convert("RGBA")
                self._pil_edit = self._pil_orig.copy()
            except Exception as e:
                log.warning("PIL no pudo abrir imagen: %s", e)

    def _on_load_error(self, err: str):
        log.error("ImageViewer: error cargando '%s': %s", self.url, err)
        self.lbl_status.setText(f"⚠️ Error: {err}")

    # ── helpers PIL ───────────────────────────────────────────────────────────

    def _rebuild_from_pil(self):
        """Aplica todos los ajustes sobre _pil_orig y actualiza el canvas."""
        if self._pil_orig is None:
            return
        try:
            from PIL import Image, ImageEnhance, ImageFilter, ImageOps

            img = self._pil_orig.copy()

            # Volteos
            if self._flip_h:
                img = ImageOps.mirror(img)
            if self._flip_v:
                img = ImageOps.flip(img)

            # Rotación
            if self._rotation != 0:
                img = img.rotate(-self._rotation, expand=True)

            # Ajustes de color (solo para RGBA/RGB)
            rgb = img.convert("RGB")

            if self._brightness != 0:
                factor = 1 + self._brightness / 100
                rgb = ImageEnhance.Brightness(rgb).enhance(max(0.0, factor))

            if self._contrast != 0:
                factor = 1 + self._contrast / 100
                rgb = ImageEnhance.Contrast(rgb).enhance(max(0.0, factor))

            if self._saturation != 0:
                factor = 1 + self._saturation / 100
                rgb = ImageEnhance.Color(rgb).enhance(max(0.0, factor))

            # Filtros
            if self._current_filter == 'grayscale':
                rgb = ImageOps.grayscale(rgb).convert("RGB")
            elif self._current_filter == 'sepia':
                gray = ImageOps.grayscale(rgb)
                rgb  = Image.merge("RGB", [
                    gray.point(lambda p: min(255, int(p * 1.08))),
                    gray.point(lambda p: min(255, int(p * 0.85))),
                    gray.point(lambda p: min(255, int(p * 0.66))),
                ])
            elif self._current_filter == 'blur':
                rgb = rgb.filter(ImageFilter.GaussianBlur(radius=2))
            elif self._current_filter == 'sharpen':
                rgb = rgb.filter(ImageFilter.UnsharpMask(radius=2, percent=150))

            self._pil_edit = rgb.convert("RGBA")

            # Mostrar en canvas
            buf = io.BytesIO()
            self._pil_edit.save(buf, format='PNG')
            pix = _qpixmap_from_bytes(buf.getvalue())
            if not pix.isNull():
                self.canvas.set_pixmap(pix)
                w, h = pix.width(), pix.height()
                self.lbl_status.setText(f"{w} × {h} px")

        except Exception as e:
            log.error("Error aplicando edición PIL: %s", e)

    # ── slots de herramientas ─────────────────────────────────────────────────

    def _apply_rotation(self, degrees: int):
        self._rotation = (self._rotation + degrees) % 360
        self._rebuild_from_pil()

    def _apply_flip_h(self):
        self._flip_h = not self._flip_h
        self._rebuild_from_pil()

    def _apply_flip_v(self):
        self._flip_v = not self._flip_v
        self._rebuild_from_pil()

    def _on_brightness(self, val: int):
        self._brightness = val
        self._rebuild_from_pil()

    def _on_contrast(self, val: int):
        self._contrast = val
        self._rebuild_from_pil()

    def _on_saturation(self, val: int):
        self._saturation = val
        self._rebuild_from_pil()

    def _on_filter(self, key: str):
        self._current_filter = key
        self._rebuild_from_pil()

    def _reset_edits(self):
        self._rotation = 0
        self._flip_h   = False
        self._flip_v   = False
        self._brightness  = 0
        self._contrast    = 0
        self._saturation  = 0
        self._current_filter = 'none'
        if self.tool_panel:
            self.tool_panel.sl_bright.setValue(0)
            self.tool_panel.sl_contrast.setValue(0)
            self.tool_panel.sl_sat.setValue(0)
            self.tool_panel._on_filter('none')
        self._rebuild_from_pil()

    def _save_image(self):
        if self._pil_edit is None and self._raw_bytes is None:
            QMessageBox.warning(self, "Sin imagen", "No hay imagen cargada para guardar.")
            return

        suggested = Path(self.title or "imagen").stem + "_edit.png"
        path, fmt = QFileDialog.getSaveFileName(
            self, "Guardar imagen como…",
            suggested,
            "PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp)"
        )
        if not path:
            return
        try:
            if self._pil_edit is not None:
                ext = Path(path).suffix.lower()
                if ext in ('.jpg', '.jpeg'):
                    self._pil_edit.convert("RGB").save(path, "JPEG", quality=92)
                elif ext == '.webp':
                    self._pil_edit.save(path, "WEBP", quality=90)
                else:
                    self._pil_edit.save(path, "PNG")
            else:
                with open(path, 'wb') as f:
                    f.write(self._raw_bytes)

            self.lbl_status.setText(f"✅ Guardado: {Path(path).name}")
            QMessageBox.information(self, "Imagen guardada",
                                    f"Se guardó correctamente:\n{path}")
        except Exception as e:
            log.error("Error guardando imagen: %s", e)
            QMessageBox.critical(self, "Error al guardar", str(e))

    def _remove_background(self):
        if not _rembg_available():
            reply = QMessageBox.question(
                self, "Instalar rembg",
                "La eliminación de fondo con IA requiere la librería <b>rembg</b>.<br><br>"
                "¿Deseas ver las instrucciones de instalación?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                webbrowser.open("https://github.com/danielgatis/rembg#installation")
            return

        if self._pil_orig is None:
            QMessageBox.warning(self, "Sin imagen", "Primero espera que cargue la imagen.")
            return

        self.lbl_status.setText("🤖 Procesando con IA… (puede tardar 5-30 seg)")
        if self.tool_panel:
            self.tool_panel.btn_rembg.setEnabled(False)

        def _worker():
            try:
                import rembg
                from PIL import Image
                buf_in = io.BytesIO()
                self._pil_orig.save(buf_in, format='PNG')
                result = rembg.remove(buf_in.getvalue())
                img_out = Image.open(io.BytesIO(result)).convert("RGBA")
                return img_out, None
            except Exception as e:
                return None, str(e)

        def _done(result):
            img_out, err = result
            if self.tool_panel:
                self.tool_panel.btn_rembg.setEnabled(True)
            if err:
                log.error("rembg error: %s", err)
                self.lbl_status.setText(f"⚠️ Error IA: {err}")
                return
            self._pil_orig = img_out
            self._pil_edit = img_out.copy()
            self._rebuild_from_pil()
            self.lbl_status.setText("✅ Fondo eliminado. Guarda para preservar los cambios.")

        class _Worker(QRunnable):
            class Sig(QObject):
                done = Signal(object)
            def __init__(self):
                super().__init__()
                self.signals = _Worker.Sig()
            def run(self):
                self.signals.done.emit(_worker())

        w = _Worker()
        w.signals.done.connect(_done)
        QThreadPool.globalInstance().start(w)


# ── función de apertura desde media_modal ─────────────────────────────────────

def open_image_viewer(
    url: str,
    title: str,
    engine,
    main_window,
) -> None:
    """
    Abre el ImageViewerModal como overlay dentro de la ventana principal,
    del mismo modo que MediaModal.
    """
    if not (url or "").strip():
        return

    try:
        create = getattr(main_window, "_create_overlay_layer", None)
        if create is None:
            raise RuntimeError("MainWindow sin _create_overlay_layer")
        new_overlay, overlay_l = create()
        modal = ImageViewerModal(url, title, engine, new_overlay)
        modal.setWindowFlags(Qt.Widget)

        from PySide6.QtCore import Qt as _Qt
        modal.closed.connect(main_window.close_modal)
        overlay_l.addWidget(modal)
        main_window.modal_stack.append(new_overlay)
        new_overlay.show()
        new_overlay.raise_()
    except Exception as e:
        log.error("No se pudo abrir ImageViewerModal: %s", e)
        webbrowser.open(url)
