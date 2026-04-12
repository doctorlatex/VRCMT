import webbrowser
import logging
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QProgressBar, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QThreadPool, QRunnable, QObject


class _OTASignals(QObject):
    """Señales thread-safe para el descargador OTA. / Thread-safe signals for the OTA downloader."""
    progress = Signal(int, int)   # bytes_downloaded, total_bytes
    done = Signal(bool, str)      # ok, message_or_path

APP_VERSION   = "2.0.9"
APP_NAME      = "VRCMT — VRChat Media Tracker"
APP_AUTHOR    = "DoctorLatex"
APP_CONTACT   = "Discord: DoctorLatex"
DISCORD_INVITE = "https://discord.gg/enKmpDQwY3"
GITHUB_RELEASES = "https://github.com/doctorlatex/VRCMT/releases/latest"

# ---------------------------------------------------------------------------
# Modal de instrucciones
# ---------------------------------------------------------------------------

class InstructionsModal(QFrame):
    """Ventana flotante de instrucciones de uso (mismo sistema que MediaModal)."""

    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InstructionsModal")
        self.setFixedSize(720, 560)
        self.setStyleSheet("""
            QFrame#InstructionsModal {
                background-color: #141414;
                border-radius: 16px;
                border: 1px solid #2a2a2a;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Cabecera ────────────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(56)
        header.setStyleSheet("background-color: #1a1a1a; border-radius: 16px 16px 0 0;")
        hdr_l = QHBoxLayout(header)
        hdr_l.setContentsMargins(20, 0, 12, 0)

        ico = QLabel("📖")
        ico.setStyleSheet("font-size: 20px;")
        title_lbl = QLabel("Instrucciones de uso")
        title_lbl.setStyleSheet(
            "color: #f1f3f4; font-size: 16px; font-weight: bold; margin-left: 8px;"
        )

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(32, 32)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet("""
            QPushButton {
                background: #2a2a2a; color: #aaa; border-radius: 16px;
                font-size: 14px; font-weight: bold; border: none;
            }
            QPushButton:hover { background: #c0392b; color: #fff; }
        """)
        btn_close.clicked.connect(self.closed.emit)

        hdr_l.addWidget(ico)
        hdr_l.addWidget(title_lbl)
        hdr_l.addStretch()
        hdr_l.addWidget(btn_close)
        root.addWidget(header)

        # ── Área de scroll con el contenido ─────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: #1a1a1a; width: 6px; border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #333; border-radius: 3px; min-height: 30px;
            }
        """)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        vbox = QVBoxLayout(content)
        vbox.setContentsMargins(28, 20, 28, 28)
        vbox.setSpacing(18)

        sections = [
            # ── Qué es ──────────────────────────────────────────────────────
            ("🎯 ¿Qué es VRCMT?",
             "VRCMT es una herramienta de <b>seguimiento personal</b> de las películas, series, "
             "anime, streams e imágenes que ves mientras estás en <b>VRChat</b>.<br><br>"
             "Su objetivo es ayudarte a llevar un registro ordenado de tu catálogo visual "
             "dentro del metaverso, similar a lo que hace Letterboxd o Trakt pero para VRChat.<br><br>"
             "<b>La herramienta por sí sola NO reproduce ni detecta películas.</b> Solo escucha "
             "lo que VRChat reproduce y trata de identificarlo."),

            # ── Cómo funciona ───────────────────────────────────────────────
            ("⚙️ ¿Cómo funciona?",
             "VRChat genera automáticamente <b>archivos de log</b> en tu PC cada vez que "
             "un reproductor de un mundo (AVPro, ProTV, etc.) carga un vídeo.<br><br>"
             "VRCMT lee esos logs en tiempo real y extrae la URL del enlace que se está "
             "reproduciendo. Con ese enlace intenta:<br><br>"
             "1. Extraer el nombre del archivo o título del path.<br>"
             "2. Buscar ese nombre en <b>TMDB</b> (The Movie Database).<br>"
             "3. Si lo encuentra: guardar póster, sinopsis, año, director, elenco, etc.<br>"
             "4. Si no lo encuentra: guardar solo el título extraído del enlace.<br><br>"
             "Todo esto ocurre en segundo plano sin interrumpir tu experiencia en VRChat."),

            # ── Marca automática como visto ─────────────────────────────────
            ("✅ Marcado automático como visto (regla del 90%)",
             "VRCMT registra cuánto tiempo llevas reproduciendo cada contenido en VRChat.<br><br>"
             "Si llevas visto el <b>90% o más de la duración total</b> del video, "
             "la app lo marca automáticamente como <i>Visto</i> en tu catálogo.<br><br>"
             "• La duración real se obtiene del log de VRChat cuando el reproductor informa "
             "<i>«Media Ready»</i>.<br>"
             "• Si no hay duración disponible, se usa un umbral mínimo de <b>12 minutos</b> "
             "continuos como referencia.<br>"
             "• Los eventos de pausa y reanudación se detectan automáticamente desde los logs.<br>"
             "• El progreso se guarda cada 30 segundos y también al detenerse el video.<br><br>"
             "También puedes marcarlo manualmente usando el botón <b>⭕ Marcar Visto</b> en la ficha."),

            # ── Fichas de contenido ─────────────────────────────────────────
            ("🃏 ¿Qué puedo hacer en la ficha de un título?",
             "Al hacer clic en cualquier tarjeta del catálogo se abre su <b>ficha detallada</b> "
             "donde puedes:<br><br>"
             "• <b>Ver el póster</b>, sinopsis, año, géneros, director y elenco (si TMDB lo encontró).<br>"
             "• <b>Dar una calificación</b> personal (1–10) con las estrellas.<br>"
             "• <b>Escribir notas</b> propias sobre ese título.<br>"
             "• <b>Abrir el enlace</b> de reproducción directamente en el reproductor interno.<br>"
             "• <b>Descargar el video</b> a tu PC (solo para YouTube, Twitch, Vimeo y plataformas públicas) "
             "usando el botón <b>⬇️ Descargar</b> en el reproductor.<br>"
             "• <b>Corregir el contenido</b> si la detección fue incorrecta usando el botón "
             "<b>🔧 Fix</b>.<br>"
             "• <b>Eliminar</b> el registro si no quieres que aparezca en tu catálogo.<br><br>"
             "ℹ️ El botón <b>Marcar Anime</b> solo aparece en contenido detectado como película "
             "o serie (no en videos públicos de YouTube/Twitch/Kick)."),

            # ── Reproductor Premium ─────────────────────────────────────────
            ("📺 Reproductor VRCMT (Premium)",
             "El reproductor interno VRCMT (disponible en <i>Herramientas Premium</i>) permite "
             "ver cualquier video directamente desde la app, sin abrir VRChat:<br><br>"
             "• <b>Motor nativo</b> con proxy de sigilo para evitar restricciones de CDN.<br>"
             "• <b>Velocidad de reproducción</b> ajustable (0.75× — 2×).<br>"
             "• <b>Pantalla completa</b> con ocultado automático de controles.<br>"
             "• <b>Copiar URL</b> para compartir el enlace fácilmente.<br>"
             "• <b>⬇️ Descargar</b> — para YouTube, Twitch, Vimeo y otras plataformas públicas, "
             "descarga el archivo MP4 directamente a tu PC usando yt-dlp. "
             "Puedes añadir cookies exportadas desde tu navegador en "
             "<i>Ajustes → YouTube en VRChat → Opciones avanzadas</i> para descargar "
             "contenido con restricción de edad."),

            # ── YouTube en VRChat (Stub) ────────────────────────────────────
            ("📺 YouTube en VRChat — Mejora del reproductor",
             "VRChat usa un pequeño programa interno (<i>yt-dlp</i>) para abrir videos de YouTube. "
             "La sección <b>«YouTube en VRChat»</b> en Ajustes te permite reemplazarlo por una "
             "versión más reciente con soporte de cookies.<br><br>"
             "<b>Pasos para activar:</b><br>"
             "1. Cierra VRChat completamente.<br>"
             "2. En Ajustes, activa la casilla <b>«Activar mejora de YouTube para VRChat»</b>.<br>"
             "3. Haz clic en <b>«⬇️ Instalar / Actualizar desde internet»</b>.<br>"
             "4. Abre VRChat — los videos de YouTube deberían funcionar mejor.<br><br>"
             "<b>Cookies (opcional):</b> en <i>Opciones avanzadas</i> puedes añadir un archivo "
             "de cookies exportado con la extensión <i>«Get cookies.txt»</i> de tu navegador. "
             "Necesario para videos con restricción de edad o contenido privado.<br><br>"
             "<b>Quitar la mejora:</b> haz clic en <b>«↩️ Quitar mejora»</b> para volver al "
             "yt-dlp original de VRChat."),

            # ── Detección y cómo mejorarla ──────────────────────────────────
            ("⚠️ Limitaciones de la detección automática",
             "La precisión depende <b>completamente del nombre que el creador del mundo "
             "le dio al archivo o enlace</b>. VRCMT no puede saber qué película es si el enlace "
             "no tiene información legible:<br><br>"
             "• <code>https://cdn.ejemplo.com/v2/a1b2c3.mp4</code> → ❌ imposible de detectar.<br>"
             "• <code>https://cdn.ejemplo.com/PELICULAS/El_Padrino_1972.mp4</code> → ✅ detectable.<br>"
             "• <code>https://cdn.ejemplo.com/s02e05_breaking_bad.mkv</code> → ✅ detectable con T y E.<br><br>"
             "Si la detección falla o es incorrecta, usa el botón <b>🔧 Fix</b> dentro de la "
             "ficha para corregirlo manualmente."),

            # ── Cómo ayudar a mejorar la detección ─────────────────────────
            ("💡 ¿Cómo corregir un título mal detectado? — Botón 🔧 Fix",
             "El botón <b>🔧 Fix</b> es la herramienta principal para reparar cualquier "
             "contenido que se detectó incorrectamente o que no se pudo identificar.<br><br>"
             "<b>Caso 1: el contenido no se detectó (sin póster ni datos)</b><br>"
             "El campo <b>🆔 ID IMDb</b> estará vacío. Pulsa <b>🔧 Fix</b> directamente → "
             "se abre el buscador → escribe el nombre de la película o serie → selecciona "
             "el resultado correcto. La app descarga el póster, sinopsis y todos los metadatos "
             "desde TMDB.<br><br>"
             "<b>Caso 2: se detectó la película/serie equivocada</b><br>"
             "El campo <b>🆔 ID IMDb</b> tendrá un valor incorrecto (p. ej. <code>tt1234567</code>). "
             "Primero <b>borra el ID IMDb</b> para que quede vacío, luego pulsa <b>🔧 Fix</b> → "
             "se abre el buscador → busca y selecciona el título correcto.<br><br>"
             "<b>Caso 3: tienes el ID IMDb correcto</b><br>"
             "Escribe el ID IMDb en el campo (formato <code>tt0000000</code>) y pulsa "
             "<b>🔧 Fix</b>. La app busca directamente ese ID en TMDB y actualiza la ficha.<br><br>"
             "✅ <b>Al corregir con Fix</b>, el tipo de contenido se ajusta automáticamente: "
             "si TMDB lo reconoce como serie quedará en la sección <i>Series</i>; si es "
             "película, en <i>Películas</i>. No necesitas cambiarlo manualmente.<br><br>"
             "<b>Eliminar falsos positivos:</b> si VRCMT capturó una URL que no era contenido "
             "real (menús, imágenes de fondo, etc.), puedes eliminar esa entrada desde la ficha."),

            # ── Free vs Premium ─────────────────────────────────────────────
            ("🔒 Usuarios Free — almacenamiento de enlaces",
             "En modo <b>Free</b>, los enlaces de contenido privado (películas y series en "
             "servidores de los mundos) <b>no se guardan completos</b> en tu base de datos. "
             "Solo se guarda el nombre del archivo para identificar el título.<br><br>"
             "• En tu catálogo verás las tarjetas con un candado 🔒 naranja.<br>"
             "• Al abrir la ficha <b>no verás el enlace de reproducción</b>.<br>"
             "• YouTube, Twitch, Kick y servicios públicos <b>sí se guardan siempre</b>, "
             "independientemente del plan."),

            ("💎 Al volverte Premium",
             "Cuando activas Premium, los <b>nuevos</b> contenidos que captures guardarán "
             "el enlace completo automáticamente.<br><br>"
             "El contenido que ya tenías en modo Free <b>no mostrará el enlace de forma automática</b>. "
             "Para que aparezca debes volver a reproducir ese contenido en VRChat mientras tu "
             "cuenta esté en modo Premium. En ese momento el enlace se detecta y actualiza "
             "en tu catálogo."),

            # ── Nota legal ──────────────────────────────────────────────────
            ("ℹ️ Nota importante",
             "VRCMT es exclusivamente una herramienta de <b>organización personal</b>. "
             "No distribuye, almacena ni reproduce contenido multimedia de ningún tipo. "
             "Únicamente guarda metadatos como título, año, póster y tu historial de "
             "visualización en una base de datos local en tu PC.<br><br>"
             "El acceso a cualquier contenido dentro de VRChat es responsabilidad exclusiva "
             "de los creadores de los mundos que visitas. VRCMT no tiene control ni "
             "conocimiento del contenido que los mundos reproducen."),
        ]

        for sec_title, sec_body in sections:
            self._add_section(vbox, sec_title, sec_body)

        vbox.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)

    # ------------------------------------------------------------------
    def _add_section(self, layout, title: str, body: str):
        """Agrega un bloque título + texto al layout dado."""
        # Título de sección
        lbl_title = QLabel(title)
        lbl_title.setStyleSheet(
            "color: #1f6aa5; font-size: 14px; font-weight: bold; "
            "padding-bottom: 4px; border-bottom: 1px solid #222;"
        )
        lbl_title.setWordWrap(True)
        layout.addWidget(lbl_title)

        # Cuerpo con HTML
        lbl_body = QLabel(body)
        lbl_body.setStyleSheet(
            "color: #ccc; font-size: 13px; line-height: 1.5;"
            "background: transparent; padding: 4px 0;"
        )
        lbl_body.setWordWrap(True)
        lbl_body.setTextFormat(Qt.RichText)
        lbl_body.setOpenExternalLinks(False)
        layout.addWidget(lbl_body)


# ---------------------------------------------------------------------------
# Página "Acerca de"
# ---------------------------------------------------------------------------

class AboutView(QWidget):
    """Página principal de 'Acerca de', visible desde la barra lateral."""

    show_instructions_requested = Signal()

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._pending_version = ""
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignTop)

        # ── Tarjeta central ─────────────────────────────────────────────────
        card = QFrame()
        card.setObjectName("AboutCard")
        card.setMaximumWidth(640)
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        card.setStyleSheet("""
            QFrame#AboutCard {
                background-color: #141414;
                border-radius: 16px;
                border: 1px solid #222;
            }
        """)
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(36, 32, 36, 36)
        card_l.setSpacing(16)

        # Logo + nombre
        logo_row = QHBoxLayout()
        logo_row.setSpacing(14)
        logo_row.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        lbl_icon = QLabel("🎬")
        lbl_icon.setStyleSheet("font-size: 44px;")
        logo_row.addWidget(lbl_icon)

        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        lbl_name = QLabel("VRCMT")
        lbl_name.setStyleSheet(
            "color: #1f6aa5; font-size: 26px; font-weight: bold; letter-spacing: 2px;"
        )
        lbl_subname = QLabel("VRChat Media Tracker")
        lbl_subname.setStyleSheet("color: #888; font-size: 13px;")
        name_col.addWidget(lbl_name)
        name_col.addWidget(lbl_subname)
        logo_row.addLayout(name_col)
        card_l.addLayout(logo_row)

        # Separador
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #222; background: #222; max-height: 1px;")
        card_l.addWidget(sep)

        # Datos de versión / info
        info_grid = [
            ("Versión",      APP_VERSION),
            ("Desarrollado por", APP_AUTHOR),
            ("Contacto",     APP_CONTACT),
            ("Base de datos", "TMDB — The Movie Database"),
            ("Framework",    "Python · PySide6 (Qt6)"),
            ("Plataforma",   "Windows 10 / 11"),
        ]
        for label, value in info_grid:
            row = QHBoxLayout()
            lbl_k = QLabel(f"{label}:")
            lbl_k.setStyleSheet("color: #666; font-size: 12px; min-width: 140px;")
            lbl_v = QLabel(value)
            lbl_v.setStyleSheet("color: #ddd; font-size: 12px; font-weight: bold;")
            row.addWidget(lbl_k)
            row.addWidget(lbl_v)
            row.addStretch()
            card_l.addLayout(row)

        # Descripción breve
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #222; background: #222; max-height: 1px;")
        card_l.addWidget(sep2)

        lbl_desc = QLabel(
            "Herramienta personal para registrar y organizar las películas, "
            "series, anime y streams que ves en los mundos de VRChat."
        )
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color: #999; font-size: 12px; line-height: 1.6;")
        card_l.addWidget(lbl_desc)

        # Aviso TMDB
        lbl_tmdb = QLabel(
            '🎞️  Este producto usa la API de TMDB pero no está respaldado ni certificado por TMDB.'
        )
        lbl_tmdb.setWordWrap(True)
        lbl_tmdb.setStyleSheet(
            "color: #555; font-size: 11px; font-style: italic;"
        )
        card_l.addWidget(lbl_tmdb)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.HLine)
        sep3.setStyleSheet("color: #222; background: #222; max-height: 1px;")
        card_l.addWidget(sep3)

        # ── Banner de actualización disponible (oculto por defecto) ──────────
        self._update_banner = QFrame()
        self._update_banner.setStyleSheet(
            "background-color: #1b3a1b; border-radius: 10px; border: 1px solid #2e7d32; padding: 2px;"
        )
        update_banner_l = QVBoxLayout(self._update_banner)
        update_banner_l.setContentsMargins(14, 10, 14, 10)
        update_banner_l.setSpacing(8)

        self._lbl_update_title = QLabel("🆕 Nueva versión disponible")
        self._lbl_update_title.setStyleSheet("color: #81c784; font-size: 14px; font-weight: bold;")
        update_banner_l.addWidget(self._lbl_update_title)

        self._lbl_update_desc = QLabel(
            "Haz clic en «Instalar actualización» para descargar e instalar automáticamente.\n"
            "La app se cerrará, se actualizará y volverá a abrirse sola."
        )
        self._lbl_update_desc.setWordWrap(True)
        self._lbl_update_desc.setStyleSheet("color: #aaa; font-size: 12px;")
        update_banner_l.addWidget(self._lbl_update_desc)

        self._update_progress = QProgressBar()
        self._update_progress.setRange(0, 100)
        self._update_progress.setValue(0)
        self._update_progress.setFixedHeight(8)
        self._update_progress.setStyleSheet(
            "QProgressBar { border-radius: 4px; background: #333; }"
            "QProgressBar::chunk { background: #4caf50; border-radius: 4px; }"
        )
        self._update_progress.setVisible(False)
        update_banner_l.addWidget(self._update_progress)

        update_btn_row = QHBoxLayout()
        self._btn_install_update = QPushButton("⬇️  Instalar actualización")
        self._btn_install_update.setFixedHeight(40)
        self._btn_install_update.setCursor(Qt.PointingHandCursor)
        self._btn_install_update.setStyleSheet("""
            QPushButton {
                background-color: #2e7d32; color: #fff;
                border-radius: 8px; font-size: 13px; font-weight: bold;
                border: none; padding: 0 18px;
            }
            QPushButton:hover   { background-color: #388e3c; }
            QPushButton:pressed { background-color: #1b5e20; }
            QPushButton:disabled { background-color: #333; color: #666; }
        """)
        self._btn_install_update.clicked.connect(self._on_install_update)

        btn_github = QPushButton("🌐  Ver en GitHub")
        btn_github.setFixedHeight(40)
        btn_github.setCursor(Qt.PointingHandCursor)
        btn_github.setStyleSheet("""
            QPushButton {
                background-color: #333; color: #ccc;
                border-radius: 8px; font-size: 13px;
                border: none; padding: 0 14px;
            }
            QPushButton:hover { background-color: #444; }
        """)
        btn_github.clicked.connect(lambda: webbrowser.open(GITHUB_RELEASES))

        update_btn_row.addWidget(self._btn_install_update, 1)
        update_btn_row.addWidget(btn_github)
        update_banner_l.addLayout(update_btn_row)

        self._update_banner.setVisible(False)
        card_l.addWidget(self._update_banner)

        # ── Fila de botones: Instrucciones | Discord ─────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        btn_instr = QPushButton("📖  Instrucciones de uso")
        btn_instr.setFixedHeight(44)
        btn_instr.setCursor(Qt.PointingHandCursor)
        btn_instr.setStyleSheet("""
            QPushButton {
                background-color: #1f6aa5; color: #fff;
                border-radius: 10px; font-size: 14px; font-weight: bold;
                border: none; padding: 0 20px;
            }
            QPushButton:hover   { background-color: #2980b9; }
            QPushButton:pressed { background-color: #1a5276; }
        """)
        btn_instr.clicked.connect(self.show_instructions_requested.emit)

        btn_discord = QPushButton("💬  Unirse al servidor")
        btn_discord.setFixedHeight(44)
        btn_discord.setCursor(Qt.PointingHandCursor)
        btn_discord.setToolTip(DISCORD_INVITE)
        btn_discord.setStyleSheet("""
            QPushButton {
                background-color: #5865f2; color: #fff;
                border-radius: 10px; font-size: 14px; font-weight: bold;
                border: none; padding: 0 20px;
            }
            QPushButton:hover   { background-color: #6b77f5; }
            QPushButton:pressed { background-color: #4752c4; }
        """)
        btn_discord.clicked.connect(lambda: webbrowser.open(DISCORD_INVITE))

        btn_row.addWidget(btn_instr)
        btn_row.addWidget(btn_discord)
        card_l.addLayout(btn_row)

        root.addWidget(card)
        root.addStretch()

    # ── API pública ──────────────────────────────────────────────────────────

    def notify_update(self, version: str) -> None:
        """Llamado desde MainWindow cuando el OTA detecta una nueva versión."""
        self._pending_version = version
        self._lbl_update_title.setText(f"🆕 Nueva versión disponible: v{version}")
        self._update_banner.setVisible(True)

    def _on_install_update(self) -> None:
        from src.core.self_updater import download_update, apply_update_and_restart
        import sys

        if not getattr(sys, "frozen", False):
            QMessageBox.information(
                self, "Solo en .exe",
                "La actualización automática solo está disponible en el ejecutable .exe.\n"
                "En modo desarrollo, descarga manualmente desde GitHub."
            )
            webbrowser.open(GITHUB_RELEASES)
            return

        # Confirmación antes de descargar — evita que el usuario piense que es un crash
        # Confirmation before downloading — prevents user from thinking it's a crash
        confirm = QMessageBox.question(
            self,
            "Instalar actualización",
            f"Se descargará la nueva versión de VRCMT.\n\n"
            f"⚠️ La app se cerrará automáticamente al finalizar la descarga,\n"
            f"se reemplazará el ejecutable y se reabrirá sola.\n\n"
            f"¿Continuar?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            return

        self._btn_install_update.setEnabled(False)
        self._btn_install_update.setText("⏳  Descargando...")
        self._update_progress.setVisible(True)
        self._update_progress.setValue(0)

        # Usar señales Qt para garantizar que los callbacks se ejecuten en el hilo GUI
        # Use Qt signals to guarantee callbacks run on the GUI thread
        self._ota_signals = _OTASignals()

        def _on_progress_slot(downloaded: int, total: int) -> None:
            if total > 0:
                pct = int(downloaded * 100 / total)
                self._update_progress.setValue(pct)
                mb = downloaded / 1_048_576
                self._btn_install_update.setText(f"⏳  Descargando… {mb:.0f} MB")

        def _on_done_slot(ok: bool, msg: str) -> None:
            if ok:
                self._btn_install_update.setText("✅  Instalando y reiniciando…")
                apply_update_and_restart(msg)
            else:
                self._btn_install_update.setEnabled(True)
                self._btn_install_update.setText("⬇️  Instalar actualización")
                self._update_progress.setVisible(False)
                QMessageBox.warning(
                    self, "Error de descarga",
                    f"No se pudo descargar la actualización:\n{msg}\n\n"
                    "Puedes descargarla manualmente desde GitHub."
                )
                webbrowser.open(GITHUB_RELEASES)

        # QueuedConnection garantiza que los slots corran en el hilo principal (GUI)
        # QueuedConnection guarantees slots run on the main (GUI) thread
        self._ota_signals.progress.connect(_on_progress_slot, Qt.ConnectionType.QueuedConnection)
        self._ota_signals.done.connect(_on_done_slot, Qt.ConnectionType.QueuedConnection)

        def on_progress(downloaded: int, total: int) -> None:
            self._ota_signals.progress.emit(downloaded, total)

        def on_done(ok: bool, msg: str) -> None:
            self._ota_signals.done.emit(ok, msg)

        download_update(on_progress=on_progress, on_done=on_done)
