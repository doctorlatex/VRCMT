import os
import json
import logging
import threading
import socket
import urllib.parse
import ssl
import requests
import urllib3
import cloudscraper
import httpx
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QSlider, QFrame, QWidget, QStackedWidget, QLineEdit,
                             QFileDialog, QProgressDialog, QMessageBox)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import Qt, QUrl, QTimer, Slot, QEvent, Signal, QObject


# Señales thread-safe para descarga de video / Thread-safe signals for video download
class _DownloadSignals(QObject):
    progress = Signal(int, int, str)  # downloaded, total, speed_str
    done = Signal(bool, str)          # ok, path_or_error_msg

# Desactivar advertencias de SSL para el proxy
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- MOTOR DE SIGILO VRCMT v7.0 (ProTV Native Clone) ---
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class VRCProxyHandler(BaseHTTPRequestHandler):
    """
    Servidor Proxy de Sigilo ProTV (v7.0).
    Imita exactamente el flujo de datos de AVPro Video.
    """
    _httpx_client = None

    def do_GET(self):
        try:
            # Inicializar cliente HTTP/2 una sola vez (Senior performance optimization)
            if not VRCProxyHandler._httpx_client:
                VRCProxyHandler._httpx_client = httpx.Client(
                    http2=True, 
                    verify=False, 
                    follow_redirects=True,
                    timeout=httpx.Timeout(30.0, read=None) # read=None para streaming infinito
                )

            # Extraer URL real
            parsed_path = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed_path.query)
            real_url = query.get('url', [None])[0]

            if not real_url:
                self.send_error(400, "URL Missing")
                return

            # Log sin emojis para evitar errores de consola en Windows
            logging.info(f"[Proxy] Tunel de datos para: {real_url[:60]}...")

            # Cabeceras espejo exactas de AVPro Video (VRChat) por defecto
            headers = {
                'User-Agent': 'AVProVideo/2.9.0 (Windows; 64-bit; Intel) Unity/2022.3.22f1',
                'X-Unity-Version': '2022.3.22f1',
                'Referer': 'https://vrchat.com/',
                'Accept': '*/*',
                'Connection': 'keep-alive',
                'Icy-MetaData': '1'
            }

            # Lógica de Sigilo Dinámica: Sobrescribir headers para servidores conflictivos
            domain = urllib.parse.urlparse(real_url).hostname
            if domain:
                domain = domain.lower()
                # 1. BunnyCDN rechaza AVProVideo/VRChat Referer con 404/403
                if 'bunnycdn.online' in domain:
                    headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    headers['Referer'] = 'https://bunnycdn.online/'
                # 2. Google Video (HLS de YouTube) necesita navegador real
                elif 'googlevideo.com' in domain:
                    headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    headers['Referer'] = 'https://www.youtube.com/'
                # 3. IMVRCDN (bh2.imvrcdn.com) - Requiere simulación nativa de Windows
                # Bypass avanzado de "Video Trampa" (Rickroll) v7.6 - Forensic Native
                # [ES] Mimetismo de Windows Media Foundation para evitar Rickroll
                # [EN] Windows Media Foundation mimicry to avoid Rickroll
                elif 'imvrcdn.com' in domain:
                    token = "6"
                    if "ac=" in real_url:
                        token = real_url.split("ac=")[-1].split("&")[0].split("#")[0]
                    
                    # El servidor requiere HTTP/1.1 y UA de Windows para entregar el video real (>1GB)
                    headers = {
                        'User-Agent': 'NSPlayer/12.0.22621.2506',
                        'Referer': 'https://vrchat.com/',
                        'Accept': '*/*',
                        'Connection': 'Keep-Alive',
                        'Icy-MetaData': '1'
                    }
                    
                    # Normalizar URL: Token ac= debe ser el primer parámetro
                    base_url = real_url.split("?")[0].split("#")[0]
                    real_url = f"{base_url}?ac={token}"
                    
                    # Forzar HTTP/1.1 para este dominio (Senior fix: HTTP/2 activa la trampa)
                    logging.info(f"[Proxy] Sigilo Nativo Windows activado (Bypass Rickroll)")
                    with httpx.Client(http2=False, verify=False, follow_redirects=True) as client_mf:
                        # Reenviar cabecera de Rango (Vital para evitar la trampa)
                        range_header = self.headers.get('Range', 'bytes=0-')
                        headers['Range'] = range_header
                        
                        # Senior Fix: Normalización de URL para preservar doble encoding si existe
                        safe_url = urllib.parse.quote(urllib.parse.unquote(real_url), safe=':/?&=#')
                        with client_mf.stream("GET", safe_url, headers=headers) as resp:
                            self.send_response(resp.status_code)
                            for k, v in resp.headers.items():
                                if k.lower() not in ['transfer-encoding', 'connection', 'content-encoding']:
                                    self.send_header(k, v)
                            self.end_headers()
                            for chunk in resp.iter_bytes(chunk_size=512 * 1024):
                                if not chunk: break
                                self.wfile.write(chunk)
                        return # Finalizar aquí para evitar el bloque httpx general de abajo
                
                # 4. Servidores con HTTP/2 inestable (ichinisanarigato.com)
                # [ES] Forzando HTTP/1.1 para evitar StreamReset
                # [EN] Forcing HTTP/1.1 to avoid StreamReset
                elif 'ichinisanarigato.com' in domain:
                    logging.info(f"[Proxy] Modo de compatibilidad HTTP/1.1 activado para {domain}")
                    with httpx.Client(http2=False, verify=False, follow_redirects=True) as client_legacy:
                        range_header = self.headers.get('Range')
                        if range_header: headers['Range'] = range_header
                        
                        # Senior Fix: Normalización de URL para evitar doble encoding
                        safe_url = urllib.parse.quote(urllib.parse.unquote(real_url), safe=':/?&=#')
                        with client_legacy.stream("GET", safe_url, headers=headers) as resp:
                            self.send_response(resp.status_code)
                            for k, v in resp.headers.items():
                                if k.lower() not in ['transfer-encoding', 'connection', 'content-encoding']:
                                    self.send_header(k, v)
                            self.end_headers()
                            for chunk in resp.iter_bytes(chunk_size=512 * 1024):
                                if not chunk: break
                                self.wfile.write(chunk)
                        return
            
            # Reenviar cabecera de Rango (Vital para streaming y Seek)
            range_header = self.headers.get('Range')
            if range_header:
                headers['Range'] = range_header

            # Senior Fix: Normalización de URL para evitar doble encoding
            safe_url = urllib.parse.quote(urllib.parse.unquote(real_url), safe=':/?&=#')
            logging.debug(f"[Proxy] Solicitando URL Normalizada: {safe_url}")

            # Streaming con httpx (HTTP/2 Bypass)
            with VRCProxyHandler._httpx_client.stream("GET", safe_url, headers=headers) as resp:
                self.send_response(resp.status_code)
                
                # Pasar todas las cabeceras del servidor original al reproductor nativo
                for k, v in resp.headers.items():
                    lk = k.lower()
                    if lk not in ['transfer-encoding', 'connection', 'content-encoding', 'content-length']:
                        self.send_header(k, v)
                
                # Forzar Content-Length si existe para estabilidad de seek
                cl = resp.headers.get('content-length')
                if cl: self.send_header('Content-Length', cl)
                
                self.end_headers()
                
                # Streaming binario directo (Chunk size optimizado para video)
                try:
                    for chunk in resp.iter_bytes(chunk_size=512 * 1024): 
                        if not chunk: break
                        self.wfile.write(chunk)
                except (ConnectionResetError, ConnectionAbortedError):
                    pass

        except Exception as e:
            logging.error(f"[Proxy] Error de red: {str(e)}")
            if not self.wfile.closed:
                try:
                    self.send_error(500, str(e))
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    pass

    def log_message(self, format, *args):
        pass

class VRCMTPlayer(QDialog):
    """
    Reproductor Híbrido VRCMT v7.0 (Grado ProTV).
    Usa el motor nativo de Windows para bypass total de navegador.
    """
    _proxy_port = 0
    _proxy_server = None

    def __init__(self, url, title, parent=None, engine=None):
        super().__init__(parent)
        self.engine = engine
        self.url = self._sanitize_url(url)
        self.media_title = title
        self.is_manual_mode = (not url) # Senior Fix: Detectar si es modo manual desde el inicio
        self.setWindowTitle(f"VRCMT Player Premium - {self.media_title}")
        self.resize(1280, 720) # Senior Fix: Ventana más ancha para mejor visibilidad de links
        
        # Iniciar Proxy si es necesario
        self._start_global_proxy()

        self._duration = 0
        self._mode = "native"
        
        # --- MEJORA v4.1: AUTO-OCULTADO DE CONTROLES ---
        self.setMouseTracking(True)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._hide_controls)
        
        # Filtro de eventos para capturar mouse en hijos
        self.installEventFilter(self)

        self.setStyleSheet("""
            QDialog { background-color: #050507; border: 1px solid #22435f; }
            QLabel { color: #e8edf2; font-family: 'Segoe UI', sans-serif; }
            QPushButton {
                background-color: #131722; border: 1px solid #2a3140;
                border-radius: 6px; color: #f3f6ff; font-weight: 600;
                padding: 6px; min-width: 44px;
            }
            QPushButton:hover { background-color: #1a4f7a; border-color: #58b2ff; }
            QLineEdit { 
                background-color: #10141d; border: 1px solid #2a3140; 
                color: #edf3ff; padding: 8px; border-radius: 6px;
                font-family: 'Consolas', monospace; font-size: 13px;
            }
            QSlider::groove:horizontal { border: 1px solid #232a37; height: 6px; background: #0e1118; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #1f6aa5; border-radius: 3px; }
            QSlider::handle:horizontal { background: #fff; border: 1px solid #4dabf5; width: 14px; margin-top: -5px; margin-bottom: -5px; border-radius: 7px; }
        """)

        self.setup_ui()
        self.init_engines()
        
        if self.url:
            self.load_media()
        else:
            self.url_panel.show()
            self.url_input.setFocus()

    def _sanitize_url(self, url):
        if not url: return ""
        # Convertir token de acceso de fragmento (#) a parámetro (?) para el CDN
        if "#ac=" in url: url = url.replace("#ac=", "?ac=")
        return url.split(" (offset")[0].split(" with API")[0].strip()

    @classmethod
    def _start_global_proxy(cls):
        if cls._proxy_port != 0: return
        def run():
            try:
                cls._proxy_server = ThreadedHTTPServer(('127.0.0.1', 0), VRCProxyHandler)
                cls._proxy_port = cls._proxy_server.server_port
                logging.info(f"🛰️ Motor de Sigilo VRCMT v7.0 activo en puerto: {cls._proxy_port}")
                cls._proxy_server.serve_forever()
            except Exception as e:
                logging.error(f"❌ Error Proxy: {e}")
        t = threading.Thread(target=run, daemon=True)
        t.start()
        # Senior Fix: Eliminamos el bucle while bloqueante que causaba el freeze de la UI
        # El puerto se asignará dinámicamente en el hilo. load_media manejará el puerto 0 si es necesario.

    def setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # --- MEJORA v4.1: BARRA DE URL MANUAL ---
        self.url_panel = QFrame()
        self.url_panel.setStyleSheet("background-color: #0a0a0f; border-bottom: 1px solid #1f6aa5;")
        self.url_panel.setFixedHeight(100)
        url_l = QHBoxLayout(self.url_panel)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(self.engine.config.tr('placeholder_manual_url', "Introduce el link de video aquí...") if self.engine else "Introduce el link de video aquí...")
        btn_load = QPushButton(self.engine.config.tr('btn_load_url', "Cargar") if self.engine else "Cargar")
        btn_load.clicked.connect(self.on_manual_load)
        url_l.addWidget(self.url_input)
        url_l.addWidget(btn_load)
        self.url_panel.hide() # Oculto por defecto
        self.main_layout.addWidget(self.url_panel)

        self.stack = QStackedWidget()
        
        # 1. MOTOR NATIVO (MediaFoundation / AVPro Style)
        self.native_container = QWidget()
        self.native_container.setMouseTracking(True)
        self.native_l = QVBoxLayout(self.native_container)
        self.native_l.setContentsMargins(0,0,0,0)
        self.video_widget = QVideoWidget()
        self.video_widget.setMouseTracking(True)
        self.video_widget.setStyleSheet("background-color: black;")
        self.native_l.addWidget(self.video_widget)
        
        # 2. MOTOR WEB (Solo para YouTube)
        self.web_view = QWebEngineView()
        self.web_view.setStyleSheet("background-color: black;")
        
        self.stack.addWidget(self.native_container) # index 0
        self.stack.addWidget(self.web_view)        # index 1
        
        self.main_layout.addWidget(self.stack, 1)

        # PANEL DE CONTROLES
        self.ctrl_panel = QFrame()
        self.ctrl_panel.setStyleSheet("background-color: #0a0d14; border-top: 1px solid #1f6aa5;")
        self.ctrl_panel.setFixedHeight(92)
        ctrl_l = QVBoxLayout(self.ctrl_panel)
        ctrl_l.setContentsMargins(16, 10, 16, 10)

        # Barra de tiempo
        time_l = QHBoxLayout()
        self.lbl_curr = QLabel("00:00")
        self.slider_seek = QSlider(Qt.Horizontal)
        self.slider_seek.setRange(0, 1000)
        self.slider_seek.sliderMoved.connect(self._on_seek_moved)
        self.lbl_total = QLabel("00:00")
        time_l.addWidget(self.lbl_curr)
        time_l.addWidget(self.slider_seek)
        time_l.addWidget(self.lbl_total)
        ctrl_l.addLayout(time_l)

        # Botones
        btns_l = QHBoxLayout()
        self.btn_play = QPushButton("⏸️")
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_back10 = QPushButton("⏪10")
        self.btn_back10.clicked.connect(lambda: self._seek_relative(-10))
        self.btn_fwd10 = QPushButton("10⏩")
        self.btn_fwd10.clicked.connect(lambda: self._seek_relative(10))
        
        self.btn_vol = QPushButton("🔊")
        self.btn_vol.clicked.connect(self.toggle_mute)
        
        self.slider_vol = QSlider(Qt.Horizontal)
        self.slider_vol.setRange(0, 100)
        self.slider_vol.setValue(100)
        self.slider_vol.setFixedWidth(80)
        self.slider_vol.valueChanged.connect(self._on_volume_changed)

        self.btn_fs = QPushButton("🔲")
        self.btn_fs.clicked.connect(self.toggle_fullscreen)
        self.btn_speed = QPushButton("1.00x")
        self.btn_speed.setToolTip("Cambiar velocidad de reproducción / Change playback speed")
        self.btn_speed.clicked.connect(self._cycle_speed)
        self.btn_copy_url = QPushButton("📋 URL")
        self.btn_copy_url.setToolTip("Copiar URL actual / Copy current URL")
        self.btn_copy_url.clicked.connect(self._copy_current_url)

        # Botón descargar — visible solo para URLs de plataformas públicas
        # Download button — visible only for public platform URLs
        self.btn_download = QPushButton("⬇️ Descargar")
        self.btn_download.setToolTip(
            "Descargar este video a tu PC usando yt-dlp\n"
            "Download this video to your PC using yt-dlp"
        )
        self.btn_download.setStyleSheet(
            "QPushButton { background-color: #1a3a1a; color: #81c784; border: 1px solid #2e7d32; }"
            "QPushButton:hover { background-color: #2e7d32; color: white; }"
            "QPushButton:disabled { background-color: #1a1a1a; color: #444; border-color: #333; }"
        )
        self.btn_download.setVisible(False)
        self.btn_download.clicked.connect(self._on_download)

        self.lbl_title_display = QLabel(f"🎬 {self.media_title}")
        self.lbl_title_display.setStyleSheet("font-weight: 700; color: #ffca28; font-size: 12px;")

        btns_l.addWidget(self.btn_play)
        btns_l.addWidget(self.btn_back10)
        btns_l.addWidget(self.btn_fwd10)
        btns_l.addSpacing(8)
        btns_l.addWidget(self.btn_vol)
        btns_l.addWidget(self.slider_vol)
        btns_l.addSpacing(8)
        btns_l.addWidget(self.btn_speed)
        btns_l.addWidget(self.btn_copy_url)
        btns_l.addWidget(self.btn_download)
        btns_l.addStretch()
        btns_l.addWidget(self.lbl_title_display)
        btns_l.addStretch()
        btns_l.addWidget(self.btn_fs)
        
        ctrl_l.addLayout(btns_l)
        self.main_layout.addWidget(self.ctrl_panel)

    def on_manual_load(self):
        url = self.url_input.text().strip()
        if url:
            self.url = self._sanitize_url(url)
            self.media_title = "Manual Stream"
            self.lbl_title_display.setText(f"🎬 {self.media_title}")
            self.load_media()

    def _hide_controls(self):
        if self.isFullScreen():
            self.ctrl_panel.hide()
            self.url_panel.hide()
            self.setCursor(Qt.BlankCursor)

    def _show_controls(self):
        self.ctrl_panel.show()
        # Senior Fix: En modo manual, siempre mostrar el panel de URL cuando los controles estén visibles
        if self.is_manual_mode:
            self.url_panel.show()
        self.setCursor(Qt.ArrowCursor)
        if self.isFullScreen():
            self.hide_timer.start(3000) # Ocultar en 3 segundos de inactividad

    def mouseMoveEvent(self, event):
        self._show_controls()
        super().mouseMoveEvent(event)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseMove:
            self._show_controls()
        return super().eventFilter(obj, event)

    def init_engines(self):
        # Reproductor Nativo
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        
        self.player.positionChanged.connect(self._on_pos_changed)
        self.player.durationChanged.connect(self._on_dur_changed)
        self.player.playbackStateChanged.connect(self._on_state_changed)
        self.player.setPlaybackRate(1.0)

    def _is_downloadable_url(self, url: str) -> bool:
        """Retorna True si la URL es de una plataforma pública descargable con yt-dlp.
        Returns True if the URL is from a public platform downloadable with yt-dlp."""
        _PUBLIC = ('youtube.com', 'youtu.be', 'twitch.tv', 'kick.com',
                   'soundcloud.com', 'music.youtube.com', 'vimeo.com',
                   'dailymotion.com', 'tiktok.com')
        u = (url or "").lower()
        return any(h in u for h in _PUBLIC)

    def load_media(self):
        # Mostrar/ocultar botón de descarga según la URL
        # Show/hide download button based on URL
        is_dl = self._is_downloadable_url(self.url)
        if hasattr(self, 'btn_download'):
            self.btn_download.setVisible(is_dl)
            self.btn_download.setEnabled(is_dl)

        u = self.url.lower()
        if "youtube.com" in u or "youtu.be" in u:
            # [ES] YouTube puede bloquear embeds (Error 153). Intentar stream directo primero.
            # [EN] YouTube may block embeds (Error 153). Try direct stream first.
            stream_url = self._resolve_youtube_stream_url(self.url)
            if stream_url:
                self._mode = "native"
                self.stack.setCurrentIndex(0)
                self.btn_speed.setEnabled(True)
                encoded_url = urllib.parse.quote(stream_url, safe="")
                proxy_url = f"http://127.0.0.1:{self._proxy_port}/proxy?url={encoded_url}"
                logging.info("YouTube resuelto a stream directo (modo nativo).")
                self.player.setSource(QUrl(proxy_url))
                self.player.play()
            else:
                self._mode = "web"
                self.stack.setCurrentIndex(1)
                self.btn_speed.setEnabled(False)
                self.btn_speed.setText("1.00x")
                vid = self._extract_youtube_video_id(self.url)
                if vid:
                    # Fallback 1: página watch completa (suele evitar bloqueos de embed).
                    self.web_view.load(QUrl(f"https://www.youtube.com/watch?v={vid}"))
                else:
                    # Fallback 2: URL original
                    self.web_view.load(QUrl(self.url))
        else:
            self._mode = "native"
            self.stack.setCurrentIndex(0)
            self.btn_speed.setEnabled(True)
            # USAR TÚNEL DE SIGILO
            encoded_url = urllib.parse.quote(self.url, safe="")
            proxy_url = f"http://127.0.0.1:{self._proxy_port}/proxy?url={encoded_url}"
            logging.info(f"🛡️ Sigilo Nativo activado...")
            self.player.setSource(QUrl(proxy_url))
            self.player.play()

    def _extract_youtube_video_id(self, url: str) -> str:
        if not url:
            return ""
        try:
            p = urllib.parse.urlparse(url)
            host = (p.netloc or "").lower()
            if "youtu.be" in host:
                return (p.path or "").strip("/").split("/")[0]
            qs = urllib.parse.parse_qs(p.query or "")
            if "v" in qs and qs["v"]:
                return qs["v"][0]
            m = re.search(r"/shorts/([^/?&#]+)", url)
            if m:
                return m.group(1)
        except Exception:
            return ""
        return ""

    def _resolve_youtube_stream_url(self, url: str) -> str:
        """Resuelve URL reproducible directa usando yt-dlp si está disponible."""
        try:
            import yt_dlp  # type: ignore
        except Exception:
            logging.info("yt-dlp no disponible; fallback web para YouTube.")
            return ""
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "format": "best[ext=mp4]/best",
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return ""
                if "url" in info and info["url"]:
                    return str(info["url"])
                fmts = info.get("formats") or []
                for f in reversed(fmts):
                    fu = f.get("url")
                    if fu:
                        return str(fu)
        except Exception as e:
            logging.warning(f"No se pudo resolver stream de YouTube: {e}")
        return ""

    def toggle_play(self):
        if self._mode == "native":
            if self.player.playbackState() == QMediaPlayer.PlayingState: self.player.pause()
            else: self.player.play()

    def toggle_mute(self):
        m = self.audio_output.isMuted()
        self.audio_output.setMuted(not m)
        self.btn_vol.setText("🔇" if not m else "🔊")

    def _on_volume_changed(self, val):
        self.audio_output.setVolume(val / 100.0)

    def _seek_relative(self, seconds: int):
        if self._mode != "native" or self._duration <= 0:
            return
        cur = self.player.position()
        nxt = max(0, min(self._duration, cur + int(seconds * 1000)))
        self.player.setPosition(nxt)

    def _cycle_speed(self):
        # Velocidades útiles sin romper comportamiento normal.
        speeds = [0.75, 1.0, 1.25, 1.5, 2.0]
        current = self.player.playbackRate() if self._mode == "native" else 1.0
        if current not in speeds:
            current = 1.0
        idx = speeds.index(current)
        nxt = speeds[(idx + 1) % len(speeds)]
        if self._mode == "native":
            self.player.setPlaybackRate(nxt)
        self.btn_speed.setText(f"{nxt:.2f}x")

    def _copy_current_url(self):
        try:
            from PySide6.QtGui import QGuiApplication
            cb = QGuiApplication.clipboard()
            cb.setText(self.url or "")
        except Exception:
            pass

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.hide_timer.stop()
            self._show_controls()
        else:
            self.showFullScreen()
            self.hide_timer.start(3000)

    def _on_seek_moved(self, pos):
        if self._mode == "native" and self._duration > 0:
            self.player.setPosition(int((pos / 1000.0) * self._duration))

    def _on_pos_changed(self, pos):
        if self._duration > 0 and not self.slider_seek.isSliderDown():
            self.slider_seek.setValue(int((pos / self._duration) * 1000))
        self.lbl_curr.setText(self._format_time(pos / 1000))

    def _on_dur_changed(self, dur):
        self._duration = dur
        self.lbl_total.setText(self._format_time(dur / 1000))

    def _on_state_changed(self, state):
        self.btn_play.setText("▶️" if state != QMediaPlayer.PlayingState else "⏸️")

    def _format_time(self, seconds):
        if not seconds or seconds < 0: return "00:00"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

    def _on_download(self):
        """Descarga el video actual usando yt-dlp a una carpeta elegida por el usuario.
        Downloads the current video using yt-dlp to a user-chosen folder."""
        try:
            import yt_dlp  # type: ignore
        except ImportError:
            QMessageBox.warning(
                self, "yt-dlp no disponible",
                "yt-dlp no está disponible en este entorno.\n"
                "Descarga el video manualmente desde la plataforma original."
            )
            return

        if not self.url:
            QMessageBox.information(self, "Sin URL", "No hay URL cargada para descargar.")
            return

        # Elegir carpeta de destino / Choose destination folder
        dest_dir = QFileDialog.getExistingDirectory(
            self,
            "Elegir carpeta de descarga / Choose download folder",
            os.path.expanduser("~/Downloads"),
        )
        if not dest_dir:
            return

        self.btn_download.setEnabled(False)
        self.btn_download.setText("⏳ Descargando…")

        # Señales thread-safe para actualizar la UI / Thread-safe signals to update UI
        self._dl_signals = _DownloadSignals()

        def _on_progress_slot(downloaded: int, total: int, speed: str) -> None:
            if total > 0:
                pct = int(downloaded * 100 / total)
                self.btn_download.setText(f"⬇️ {pct}% {speed}")
            else:
                self.btn_download.setText(f"⬇️ {speed}")

        def _on_done_slot(ok: bool, msg: str) -> None:
            self.btn_download.setEnabled(True)
            self.btn_download.setText("⬇️ Descargar")
            if ok:
                QMessageBox.information(
                    self, "✅ Descarga completa",
                    f"Video guardado en:\n{msg}"
                )
                import subprocess
                try:
                    subprocess.Popen(f'explorer /select,"{os.path.abspath(msg)}"')
                except Exception:
                    pass
            else:
                QMessageBox.warning(
                    self, "Error de descarga",
                    f"No se pudo descargar el video:\n{msg}"
                )

        self._dl_signals.progress.connect(_on_progress_slot, Qt.ConnectionType.QueuedConnection)
        self._dl_signals.done.connect(_on_done_slot, Qt.ConnectionType.QueuedConnection)

        url_to_dl = self.url

        def _download_thread():
            try:
                _sigs = self._dl_signals

                def _progress_hook(d):
                    try:
                        status = d.get("status", "")
                        if status == "downloading":
                            downloaded = int(d.get("downloaded_bytes", 0))
                            total = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
                            speed_raw = d.get("speed") or 0
                            if speed_raw >= 1_000_000:
                                speed_str = f"{speed_raw/1_000_000:.1f}MB/s"
                            elif speed_raw >= 1_000:
                                speed_str = f"{speed_raw/1_000:.0f}KB/s"
                            else:
                                speed_str = ""
                            _sigs.progress.emit(downloaded, total, speed_str)
                    except Exception:
                        pass

                # Priorizar formatos progresivos (video+audio en un archivo) que no necesitan ffmpeg.
                # Para YouTube: 22=720p mp4, 18=360p mp4. Sin ffmpeg no se puede hacer merge.
                # Prioritize progressive formats (video+audio in one file) that don't need ffmpeg.
                # For YouTube: 22=720p mp4, 18=360p mp4. Without ffmpeg, merging is not possible.
                ydl_opts = {
                    "outtmpl": os.path.join(dest_dir, "%(title)s.%(ext)s"),
                    "format": "best[ext=mp4]/best[height<=720]/best",
                    "quiet": True,
                    "no_warnings": True,
                    "noplaylist": True,
                    "progress_hooks": [_progress_hook],
                    "overwrites": True,
                }
                # Añadir cookies si están configuradas / Add cookies if configured
                if self.engine:
                    cookies_path = self.engine.config.get_val("vrchat_stub_cookies_path", "") or ""
                    if cookies_path and os.path.isfile(cookies_path):
                        ydl_opts["cookiefile"] = cookies_path

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url_to_dl, download=True)
                    if info:
                        # Obtener la ruta final del archivo descargado
                        # Get the final path of the downloaded file
                        filename = ydl.prepare_filename(info)
                        # Puede que yt-dlp haya cambiado la extensión tras el merge
                        for ext in ['.mp4', '.mkv', '.webm', '.m4a', '.mp3']:
                            candidate = os.path.splitext(filename)[0] + ext
                            if os.path.isfile(candidate):
                                filename = candidate
                                break
                        _sigs.done.emit(True, filename)
                    else:
                        _sigs.done.emit(False, "No se pudo obtener información del video")
            except Exception as e:
                logging.error("Download error: %s", e)
                self._dl_signals.done.emit(False, str(e))

        threading.Thread(target=_download_thread, daemon=True, name="VRCMT-Download").start()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.isFullScreen(): self.toggle_fullscreen()
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.player.stop()
        self.web_view.setPage(None)
        super().closeEvent(event)
