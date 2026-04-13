import logging
import threading
import hashlib
import os
import time
import re
import guessit
import wordninja
from datetime import datetime, timedelta
from typing import Optional
from src.db.models import Multimedia, init_db
from src.core.scanner import VRChatLogScanner
from src.core.image_manager import ImageManager
from src.core.timer import PlaybackTimer
from src.core.config import ConfigManager
from src.core.version_check import CURRENT_VERSION as _APP_VERSION
from src.api.tmdb_client import TMDBClient
from src.api.discord_rpc import DiscordManager
from src.api.firebase_client import FirebaseClient
from PySide6.QtCore import QObject, Signal

class EngineSignals(QObject):
    media_added = Signal()
    premium_updated = Signal(bool)
    language_changed = Signal(str)
    api_error = Signal(str)       # errores de API (429/401)
    update_available = Signal(str) # N5: nueva version disponible

class VRCMTEngine:
    # Idioma, calidad y metadatos típicos en slugs de cine/archivos (v4.10)
    _CINEMA_STOPWORDS = frozenset({
        'castellano', 'latino', 'latinos', 'español', 'espanol', 'ingles', 'inglesa', 'english',
        'inglessub', 'japonessub', 'inglessubtitulado', 'japonessubtitulado', 'vose', 'vos',
        'subtitulado', 'subtitulada', 'subbed', 'subs', 'sub', 'forced', 'doblaje', 'dual',
        'audio', 'lat', 'extended', 'extendida', 'unrated', 'remastered', 'proper', 'repack',
        'hdts', 'hdcam', 'hdtc', 'ts', 'tc', 'cam', 'dvdscr', 'dvdrip', 'bdrip', 'brrip',
        'webrip', 'webdl', 'web-dl', 'bluray', 'blu-ray', 'hdr', 'uhd', '2160p', '1080p',
        '720p', '480p', '4k', '8k', 'x264', 'x265', 'hevc', 'h264', 'h265', 'aac', 'ac3',
        'eac3', 'dts', 'truehd', 'atmos',         'nf', 'amzn', 'dsnp', 'hulu', 'hmax', 'pcok',
        'peliculas', 'pelicula', 'pelis', 'movie', 'movies', 'capitulo', 'temporada',
        'old', 'new', 'final', 'fixed', 'internal', 'nfo', 'sample', 'trailer',
        'vosi', 'fansub', 'microhd', 'bdremux', 'web', 'dl', 'complete', 'disc',
    })

    def __init__(self):
        init_db()
        self.config = ConfigManager()
        self.signals = EngineSignals()
        
        # Cargar valores desde configuración
        user_key = self.config.get_val('tmdb_api_key', '')
        log_dir = self.config.get_val('log_dir', os.path.expandvars(r'%USERPROFILE%\AppData\LocalLow\VRChat\VRChat'))
        
        # Senior Logic: Pasar clave de usuario y clave maestra
        self.tmdb = TMDBClient(user_key, self.config.MASTER_KEY)
        self.tmdb.error_signal = self.signals.api_error # Vincular señales
        
        self.firebase = FirebaseClient()
        self.img_manager = ImageManager()
        self.discord = DiscordManager()
        self.timer = PlaybackTimer()
        self.scanner = VRChatLogScanner(log_dir)
        self.running = False
        
        # --- ESTADO DE PRESENCIA ---
        self.current_world = "Mundo Desconocido"
        self.current_media_title = None
        
        self.is_premium = False
        self._setup_premium_listener()

        # --- MEJORA v2.9: DEBOUNCE ANTI-CRASH ---
        self.last_url = ""
        self.last_process_time = 0

        # Registro de sesión: url_real → m_id
        # Permite que usuarios Free detecten re-capturas del mismo contenido sin necesidad
        # de buscar por URL en la BD (que tendría url="" y no encontraría nada).
        # Se descarta al cerrar la aplicación; es solo para esta sesión.
        self._session_url_map: dict = {}

    def _setup_premium_listener(self):
        """Inicia el polling periódico de estatus PREMIUM via el thread Firebase persistente.
        Reemplaza el antiguo on_snapshot listener que causaba crash Thread-ConsumeBidirectionalStream
        en Python 3.13 (gRPC bidireccional + thread cleanup = ACCESS_VIOLATION)."""
        import threading as _t

        # Cancelar polling anterior: poner flag a False y despertar el evento para que
        # el thread viejo salga de su wait() inmediatamente (sin esperar 5 min).
        self._premium_poll_active = False
        if hasattr(self, '_premium_poll_event'):
            self._premium_poll_event.set()  # despierta el wait() del loop anterior

        d_id = self.discord.get_saved_id()
        if not d_id:
            self.is_premium = False
            if hasattr(self, 'signals'):
                self.signals.premium_updated.emit(False)
            logging.info("🚪 Estatus PREMIUM: FREE (Modo Desconectado)")
            return

        # Nuevo evento compartido para este ciclo de polling.
        poll_event = _t.Event()
        self._premium_poll_event = poll_event
        self._premium_poll_active = True
        self._premium_poll_discord_id = d_id

        # Contador de errores consecutivos: tras 3 fallos seguidos, baja a FREE
        self._premium_poll_errors = 0
        _MAX_POLL_ERRORS = 3

        def _poll_once(bypass_cache: bool = False):
            """Ejecuta UNA consulta de premium via el thread persistente Firebase."""
            if not getattr(self, '_premium_poll_active', False):
                return
            if getattr(self, '_premium_poll_discord_id', '') != d_id:
                return  # Discord ID cambió; este poll está obsoleto

            def _op():
                return self.firebase.get_premium_status(d_id, bypass_cache=bypass_cache)

            def _cb(result, error):
                if not getattr(self, '_premium_poll_active', False):
                    return
                if error is None and result is not None:
                    self._premium_poll_errors = 0
                    status = bool(result)
                else:
                    self._premium_poll_errors = getattr(self, '_premium_poll_errors', 0) + 1
                    if self._premium_poll_errors >= _MAX_POLL_ERRORS:
                        # Tras 3 errores consecutivos, bajar a FREE por seguridad
                        logging.warning("⚠️ %d errores seguidos en poll premium → bajando a FREE", _MAX_POLL_ERRORS)
                        status = False
                    else:
                        status = self.is_premium  # mantener mientras sean pocos errores
                self.is_premium = status
                if hasattr(self, 'signals'):
                    self.signals.premium_updated.emit(status)
                logging.info(f"💎 Estatus PREMIUM poll: {'ACTIVO' if status else 'FREE'}")

            self.firebase.run_firebase_async(_op, _cb)

        # Primera consulta inmediata
        _poll_once()

        # Polling periódico cada 30 segundos para detección casi en tiempo real de
        # cambios de rol (asignación/revocación de premium desde el servidor).
        _POLL_INTERVAL = 30
        def _poll_loop():
            while getattr(self, '_premium_poll_active', False):
                # wait() retorna True si el event fue set() (stop), False por timeout.
                poll_event.wait(_POLL_INTERVAL)
                if poll_event.is_set():
                    break  # evento señalizado: salir limpiamente
                if getattr(self, '_premium_poll_active', False):
                    _poll_once()

        _t.Thread(target=_poll_loop, daemon=True, name="vrcmt-premium-poll").start()

        # Guardar referencia a _poll_once para que force_premium_refresh la invoque
        self._poll_once_ref = _poll_once

    def force_premium_refresh(self):
        """Borra la caché premium y fuerza una nueva lectura inmediata de Firestore."""
        d_id = getattr(self, '_premium_poll_discord_id', '') or self.discord.get_saved_id()
        if not d_id:
            logging.warning("force_premium_refresh: sin Discord ID guardado")
            return
        self.firebase.force_refresh_premium(d_id)
        if callable(getattr(self, '_poll_once_ref', None)):
            self._poll_once_ref(bypass_cache=True)
        else:
            # Fallback: reiniciar el listener desde cero
            self._setup_premium_listener()

    def start(self):
        self.running = True
        self._normalize_social_types()
        
        # --- MEJORA v2.11.60: SINCRONIZACIÓN RADICAL DE IDIOMA ---
        threading.Thread(target=self._sync_metadata_language, daemon=True).start()

        # --- MEJORA v2.11.51: RECUPERACIÓN DE SESIÓN COMPLETA ---
        session = self.scanner.find_last_session_state()
        if session:
            self.current_world = session['world']

            # Inyectar el mundo recuperado en el estado inicial del scanner para que
            # los primeros eventos (imágenes, play) ya hereden el mundo correcto.
            self.scanner.set_initial_world(self.current_world)

            if session['play_event']:
                url = session['play_event']['url']
                existing = Multimedia.get_or_none(Multimedia.url == url)
                if existing:
                    self.current_media_title = existing.titulo
                    self.timer.start(existing.id, existing.duracion_total)
                else:
                    self.current_media_title = self._title_from_url(url)

            # Corregir capturas recientes guardadas como "Mundo Desconocido" antes del inicio.
            if self.current_world and self.current_world != 'Mundo Desconocido':
                try:
                    from datetime import timedelta
                    cutoff = datetime.now() - timedelta(minutes=30)
                    n = (Multimedia.update(titulo=self.current_world, world_name=self.current_world)
                         .where(
                             (Multimedia.tipo_contenido == 'Stream/Imagen') &
                             (Multimedia.titulo == 'Mundo Desconocido') &
                             (Multimedia.world_name == 'Mundo Desconocido') &
                             (Multimedia.fecha_creacion >= cutoff)
                         ).execute())
                    if n:
                        logging.info("🌍 Startup: corregidas %d capturas recientes → '%s'", n, self.current_world)
                except Exception as _e:
                    logging.debug("startup world-fix: %s", _e)

            logging.info(f"🌍 Sesión recuperada: {self.current_world} | {'🎬 ' + self.current_media_title if self.current_media_title else 'Explorando'}")
            self._update_rpc()
        else:
            self.discord.update_presence(details=f"VRCMT v{_APP_VERSION}", state="Esperando actividad...")

        threading.Thread(target=self._main_loop, daemon=True).start()

        def _ensure_vrchat_stub():
            try:
                from src.core.vrchat_ytdlp_stub import VRChatYtStubManager
                VRChatYtStubManager(self.config).ensure_stub_if_enabled()
            except Exception as e:
                logging.debug("VRChat yt-dlp stub ensure: %s", e)

        threading.Thread(target=_ensure_vrchat_stub, daemon=True).start()
        
        # Hilo persistente para guardar progreso cada X segundos (según config)
        interval = self.config.get_val('auto_save_interval', 30)
        def auto_save():
            while self.running:
                time.sleep(interval)
                self.timer.save_to_db()
        threading.Thread(target=auto_save, daemon=True).start()
        
        # N5: Comprobación OTA en segundo plano (5 s después del inicio para no bloquear)
        def _ota_launch():
            import time as _t
            _t.sleep(5)
            try:
                from src.core.version_check import check_for_updates
                def _ota_cb(ver):
                    if ver:
                        self.signals.update_available.emit(ver)
                # P4: Usar URL OTA configurada por el usuario si existe
                custom_ota = self.config.get_val('ota_url', '').strip() or None
                check_for_updates(_ota_cb, custom_url=custom_ota)
            except Exception as _e:
                logging.debug("OTA launch: %s", _e)
        threading.Thread(target=_ota_launch, daemon=True, name="VRCMT-OTA-launcher").start()

        logging.info(f"🚀 Motor VRCMT v{_APP_VERSION} iniciado. (API: {self.tmdb.api_key[:4]}... | Log: {os.path.basename(self.scanner.log_dir)})")

    def _update_rpc(self):
        """Sincroniza el estado de Discord con el mundo y el contenido actual (v2.11.51)"""
        watching_lbl = self.config.tr('lbl_watching', "🎬 Viendo")
        world_lbl = self.config.tr('lbl_world', "🌍 En")
        
        details = f"{watching_lbl}: {self.current_media_title}" if self.current_media_title else self.config.tr('lbl_exploring', "Explorando VRChat")
        state = f"{world_lbl}: {self.current_world}"
        self.discord.update_presence(details=details, state=state)

    def _main_loop(self):
        for event in self.scanner.scan():
            if not self.running: break
            try:
                ev_type = event['event']
                if ev_type == 'PLAY':
                    self._handle_play(event)
                elif ev_type == 'PAUSE':
                    self.timer.pause()
                elif ev_type == 'RESUME':
                    self.timer.resume()
                elif ev_type == 'STOP':
                    self.timer.save_to_db()
                    self.timer.current_item_id = None
                    self.current_media_title = None # Limpiar título al detener
                    self._update_rpc()
                elif ev_type == 'MEDIA_READY':
                    # [ES] El reproductor de VRChat confirmó la duración real del video.
                    #      Actualizamos el cronómetro sin resetear el progreso acumulado.
                    # [EN] VRChat's player reported the actual video duration.
                    #      Update the timer without resetting accumulated progress.
                    dur  = event.get('duration_seconds', 0.0)
                    offs = event.get('start_offset_seconds', 0.0)
                    self.timer.update_duration(dur, offs)
                elif ev_type == 'WORLD_CHANGE':
                    self.current_world = event['name']
                    self.scanner.set_initial_world(self.current_world)
                    self._update_rpc()
                    # Corregir capturas que llegaron justo antes de detectar el mundo
                    if self.current_world != 'Mundo Desconocido':
                        try:
                            from datetime import timedelta
                            cutoff = datetime.now() - timedelta(minutes=5)
                            n = (Multimedia.update(
                                    titulo=self.current_world,
                                    world_name=self.current_world
                                ).where(
                                    (Multimedia.tipo_contenido == 'Stream/Imagen') &
                                    (Multimedia.titulo == 'Mundo Desconocido') &
                                    (Multimedia.fecha_creacion >= cutoff)
                                ).execute())
                            if n:
                                logging.info("🌍 WORLD_CHANGE: corregidas %d capturas recientes → '%s'", n, self.current_world)
                                if hasattr(self, 'signals'):
                                    self.signals.media_added.emit()
                        except Exception as _e:
                            logging.debug("world_change fix: %s", _e)
            except Exception as e:
                logging.error(f"Error en motor: {e}")

    def _video_filename_fingerprint(self, url: str):
        """Último segmento de ruta con extensión de vídeo (minúsculas). Sirve para unificar worker vs CDN resuelto."""
        import urllib.parse
        try:
            decoded = urllib.parse.unquote(url or "")
            path = urllib.parse.urlparse(decoded).path
            for seg in reversed([p for p in path.split("/") if p]):
                low = seg.lower()
                if low.endswith(
                    (".webm", ".mp4", ".m3u8", ".mkv", ".mov", ".avi", ".flv", ".m4v", ".ogv", ".ts")
                ):
                    return low
        except Exception:
            pass
        return None

    def _url_free_fp(self, url: str) -> str:
        """Fingerprint de URL para usuarios Free con contenido privado.

        En vez de guardar "" o la URL completa, guardamos solo el último segmento
        significativo del path (ej. "2020_elanimal.mp4").  Esto permite:
          - Que el campo url nunca quede vacío (mejora el nombre extraído).
          - Deduplicar en BD buscando Multimedia.url == fp (sin ventana de tiempo).
          - No exponer dominio, CDN ni estructura de directorios.

        Prioridad: extensión de vídeo conocida → último segmento de ruta largo.
        """
        fp = self._video_filename_fingerprint(url)
        if fp:
            return fp
        try:
            import urllib.parse
            path = urllib.parse.urlparse(urllib.parse.unquote(url or "")).path.rstrip("/")
            segs = [s for s in path.split("/") if s and len(s) > 4]
            if segs:
                return segs[-1][:120].lower()
        except Exception:
            pass
        return ""

    def _is_url_fingerprint(self, url: str) -> bool:
        """True si 'url' almacenada en BD es un fingerprint Free (sin schema http/https).

        Permite saber si hay que "promover" ese valor a la URL real cuando el
        usuario pasa de Free a Premium y vuelve a reproducir el mismo contenido.
        """
        if not url:
            return True
        u = url.lower()
        return not (u.startswith("http://") or u.startswith("https://") or u.startswith("file://"))

    def _is_social_stream_url(self, url: str) -> bool:
        u = (url or "").lower()
        return any(
            x in u
            for x in (
                "youtube.com",
                "music.youtube.com",
                "youtu.be",
                "googlevideo.com",
                "manifest.googlevideo.com",
                "twitch.tv",
                "kick.com",
                "soundcloud.com",
                "soundcloud.app",
                "/audio/",
                "/audio_assets/",
                "api/vrc/assetstreaming",
            )
        )

    def _normalize_social_types(self) -> None:
        """Corrige histórico: enlaces sociales deben vivir en Stream/Imagen."""
        try:
            social_q = (
                (Multimedia.url.contains("youtube.com"))
                | (Multimedia.url.contains("music.youtube.com"))
                | (Multimedia.url.contains("youtu.be"))
                | (Multimedia.url.contains("googlevideo.com"))
                | (Multimedia.url.contains("manifest.googlevideo.com"))
                | (Multimedia.url.contains("twitch.tv"))
                | (Multimedia.url.contains("kick.com"))
                | (Multimedia.url.contains("soundcloud.com"))
                | (Multimedia.url.contains("soundcloud.app"))
                | (Multimedia.url.contains("/audio_assets/"))
                | (Multimedia.url.contains("assetstreaming/audio"))
                | (Multimedia.titulo.startswith("YouTube:"))
            )
            n = (
                Multimedia.update(tipo_contenido="Stream/Imagen")
                .where(social_q & (Multimedia.tipo_contenido != "Stream/Imagen"))
                .execute()
            )
            if n:
                logging.info("🧹 Normalización social: %s registros movidos a Stream/Imagen.", n)
        except Exception as e:
            logging.debug("normalize_social_types: %s", e)

    def _canonical_title_for_match(self, s: str) -> str:
        """Quita año inicial, idioma (latino/castellano/…) y basura de release para comparar o buscar en TMDb."""
        if not s:
            return ""
        t = s.lower().strip()
        for glued in (
            "inglessubtitulado",
            "japonessubtitulado",
            "latinoforced",
            "latinohdts",
            "inglessub",
            "japonessub",
            "latinoaudio",
            "castellanoaudio",
        ):
            t = t.replace(glued, " ")
        parts = re.split(r"[\s._\-:]+", t)
        out = []
        for p in parts:
            if not p or len(p) < 2:
                continue
            pl = p.lower()
            if pl in self._CINEMA_STOPWORDS:
                continue
            out.append(pl)
        joined = " ".join(out)
        joined = re.sub(r"^\d{4}\s+", "", joined).strip()
        return joined

    def _titles_match_canonical(self, a: str, b: str) -> bool:
        ca, cb = self._canonical_title_for_match(a), self._canonical_title_for_match(b)
        if not ca or not cb:
            return (a or "").strip().lower() == (b or "").strip().lower()
        if ca == cb:
            return True
        if ca in cb or cb in ca:
            return True
        sa, sb = set(ca.split()), set(cb.split())
        if len(sa) >= 2 and len(sb) >= 2 and (sa <= sb or sb <= sa):
            return True
        if len(sa & sb) >= max(2, min(len(sa), len(sb)) - 1):
            return True
        return False

    def _find_recent_same_file_play(self, fingerprint: str, title_search: str, window_sec: int = 180):
        if not fingerprint:
            return None
        cutoff = datetime.now() - timedelta(seconds=window_sec)
        rows = list(
            Multimedia.select()
            .where((Multimedia.ultimo_visto >= cutoff) & (Multimedia.url.contains(fingerprint)))
            .order_by(Multimedia.ultimo_visto.desc())
            .limit(8)
        )
        for row in rows:
            if self._titles_match_canonical(title_search, row.titulo or ""):
                return row
        if len(rows) == 1:
            return rows[0]
        return None

    def _merge_stream_url_if_better(self, row, new_url: str) -> None:
        # [ES] Cine/series: conservar siempre la URL tal como llegó del log de VRChat (p. ej. workers.dev).
        # El reproductor usa proxy httpx con follow_redirects; no hace falta guardar pixeldrain en BD.
        # [ES] Stream/Imagen: se permite seguir sustituyendo por URL "mejor" si el flujo lo necesita.
        tipo = (getattr(row, "tipo_contenido", None) or "").strip()
        if tipo in ("Pelicula", "Serie", "Video"):
            return
        if not new_url or row.url == new_url:
            return
        o, n = (row.url or "").lower(), new_url.lower()
        if "pixeldrain" in n and "pixeldrain" not in o:
            row.url = new_url
        elif "workers.dev" in o and "pixeldrain" in n:
            row.url = new_url

    @staticmethod
    def _youtube_video_id(url: str) -> Optional[str]:
        if not url:
            return None
        m = re.search(
            r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/live/)([A-Za-z0-9_-]{11})",
            url,
        )
        if m:
            return m.group(1)
        m2 = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", url)
        return m2.group(1) if m2 else None

    def _find_existing_media_by_url(self, url: str):
        """Misma entrada guardada aunque la URL varíe un poco (/, YouTube corto vs watch)."""
        if not url:
            return None
        ex = Multimedia.get_or_none(Multimedia.url == url)
        if ex:
            return ex
        alt = url.rstrip("/")
        if alt != url:
            ex = Multimedia.get_or_none(Multimedia.url == alt)
            if ex:
                return ex
        if not url.endswith("/"):
            ex = Multimedia.get_or_none(Multimedia.url == url + "/")
            if ex:
                return ex
        yid = self._youtube_video_id(url)
        if yid:
            rows = list(
                Multimedia.select()
                .where(Multimedia.url.contains(yid))
                .order_by(Multimedia.ultima_actualizacion.desc())
                .limit(25)
            )
            for row in rows:
                if self._youtube_video_id(row.url or "") == yid:
                    return row

        # Búsqueda por fingerprint Free: usuarios Free guardan solo el último segmento del
        # path (ej. "2020_elanimal.mp4") en vez de la URL completa.  Si la búsqueda exacta
        # falló arriba, extraemos el mismo fingerprint de la URL entrante y buscamos la
        # coincidencia exacta en BD.  Funciona cross-sesión sin ventana de tiempo.
        fp = self._url_free_fp(url)
        if fp:
            ex = Multimedia.get_or_none(Multimedia.url == fp)
            if ex:
                return ex

        return None

    def _refresh_existing_play(
        self,
        existing,
        url: str,
        world,
        w_id,
        media_info,
        forced_type,
        is_social_stream: bool,
        direct_img_url,
        emit_signal: bool = True,
    ) -> None:
        """Actualiza visto reciente y metadatos al repetir un enlace ya catalogado."""
        dt_now = datetime.now()

        # Promover fingerprint Free a URL real cuando el usuario es ahora Premium.
        if self.is_premium and self._is_url_fingerprint(existing.url) and url and not self._is_url_fingerprint(url):
            existing.url = url
            logging.info("⬆️ [Free→Premium] URL promovida de fingerprint a real: %s", url)

        self._merge_stream_url_if_better(existing, url)
        existing.ultimo_visto = dt_now
        existing.ultima_actualizacion = dt_now
        existing.world_name = world
        existing.world_id = w_id
        if media_info.get("season"):
            existing.temporada = media_info["season"]
        if media_info.get("episode"):
            existing.episodio = media_info["episode"]
        if forced_type:
            existing.tipo_contenido = forced_type
        if is_social_stream:
            existing.tipo_contenido = "Stream/Imagen"

        # Auto-corrección: si el registro fue clasificado como Pelicula/Serie pero la URL
        # es en realidad una imagen (direct_img_url detectado), corregir tipo y título.
        if direct_img_url and existing.tipo_contenido in ('Pelicula', 'Serie', 'Video'):
            existing.tipo_contenido = "Stream/Imagen"
            # Si el título no coincide con el mundo, sincronizar (clave para _is_world_capture_album)
            wn = (world or "").strip()
            if wn and (existing.titulo or "").strip() != wn:
                existing.titulo = wn
            logging.info(
                "🔧 [Auto-fix] '%s' corregido a Stream/Imagen (URL de imagen detectada en re-scan)",
                existing.titulo,
            )

        existing.save()
        logging.info(f"♻️ Re-detectado: {existing.titulo} (T{existing.temporada} E{existing.episodio})")
        self.current_media_title = existing.titulo
        if not direct_img_url:
            self.timer.start(existing.id, existing.duracion_total)
        self._update_rpc()
        if emit_signal and hasattr(self, "signals"):
            self.signals.media_added.emit()

    # Free-URL whitelist: URLs from these hosts are always stored regardless of premium status.
    _FREE_HOSTS = ('youtube.com', 'youtu.be', 'twitch.tv', 'kick.com', 'soundcloud.com')

    def _is_free_url(self, url: str) -> bool:
        """Returns True if the URL is from a public/free platform (YouTube, Twitch, Kick…)."""
        u = (url or "").lower()
        return any(h in u for h in self._FREE_HOSTS)

    def _handle_play(self, event):
        try:
            url = event['url']
            confirmed_video = event.get('confirmed_video', False)

            # --- MEJORA v4.9: IMAGEN NATIVA (fuente autoritativa: etiqueta [Image Download] del log) ---
            # Si el scanner detectó la línea "[Image Download] Attempting to load image from URL",
            # VRChat ya sabe que es una imagen; omitimos toda la clasificación de vídeo/TMDB y
            # guardamos directamente como imagen. Esto resuelve el problema de mundos que muestran
            # imágenes cuyo path contiene palabras como "Cinema" o "Clasica" y se clasificaban
            # erróneamente como Pelicula.
            if event.get('confirmed_image', False):
                # Descartar miniaturas de plataformas aunque vengan por [Image Download]
                if self.img_manager.is_poster_cdn_url(url):
                    logging.debug("🖼️ [Poster CDN] Miniatura de plataforma ignorada (confirmed_image): %s…", url[:80])
                    return
                state = getattr(self, '_current_state_for_img', {})
                world  = event.get('world', '') or ''
                # Fallback: si el scanner aún no detectó el mundo, usar el conocido por el engine
                if not world or world == 'Mundo Desconocido':
                    world = getattr(self, 'current_world', '') or 'Mundo Desconocido'
                w_id   = event.get('world_id')
                local_path = self.img_manager.download_capture(url, world)
                if local_path:
                    self._save_image(url, world, local_path)
                    if hasattr(self, 'signals'):
                        self.signals.media_added.emit()
                logging.info("📸 [Image Download nativo] %s → %s", url[:80], world)
                return

            # --- MEJORA v4.6: DISCRIMINACIÓN DE CONTENIDO (Senior Logic) ---
            # [ES] AVPro marca "vídeo" incluso con PNG/posters; HEAD solo si no parece .mp4/.webm/… (evita latencia).
            # [EN] AVPro flags "video" for PNGs too; HEAD only when URL does not look like a video file.
            direct_img_url = None
            path_lower = url.split("?", 1)[0].split("#", 1)[0].lower()
            _video_suffixes = (
                ".mp4", ".webm", ".m3u8", ".mkv", ".mov", ".avi", ".flv", ".ts", ".m4v", ".ogv",
            )
            looks_video = any(path_lower.endswith(s) for s in _video_suffixes)

            if self.img_manager.is_image_url(url):
                direct_img_url = url
            elif not looks_video or not confirmed_video:
                probed_img = self._probe_is_image(url)
                if probed_img:
                    direct_img_url = probed_img
                    url = direct_img_url

            if not direct_img_url and not confirmed_video:
                if looks_video:
                    confirmed_video = True
                else:
                    logging.debug(f"🗑️ Ignorando enlace no multimedia: {url[:60]}...")
                    return

            is_social_stream = self._is_social_stream_url(url)

            player_title = event.get('player_title')
            world = event.get('world', '') or ''
            if not world or world == 'Mundo Desconocido':
                world = getattr(self, 'current_world', '') or 'Mundo Desconocido'
            w_id = event.get('world_id')
            self.current_world = world 

            # --- MEJORA v3.5: PIPELINE DE ANÁLISIS FORENSE MAESTRO ---
            media_info = self._analyze_media_source(player_title, url)
            title_search = media_info['clean_title']
            forced_type = media_info.get('forced_type')
            if is_social_stream:
                forced_type = 'Stream/Imagen'
                media_info['forced_type'] = 'Stream/Imagen'
            
            # --- MEJORA v2.5: BLINDAJE ANTI-DUPLICADOS ---
            existing = self._find_existing_media_by_url(url)
            if not existing:
                fp = self._video_filename_fingerprint(url)
                if fp:
                    twin = self._find_recent_same_file_play(fp, title_search)
                    if twin:
                        self._merge_stream_url_if_better(twin, url)
                        existing = twin

            # Registro de sesión: cubre el caso de usuarios Free cuya URL se guardó como ""
            # en la BD. _find_existing_media_by_url falla pero el registro de sesión lo recuerda.
            if not existing and url in self._session_url_map:
                existing = Multimedia.get_or_none(Multimedia.id == self._session_url_map[url])

            # Debounce: evita trabajo duplicado, pero SIEMPRE refresca visto reciente si el enlace ya está guardado.
            now_mono = time.time()
            if url == self.last_url and (now_mono - self.last_process_time) < 10:
                if existing:
                    self._refresh_existing_play(
                        existing, url, world, w_id, media_info, forced_type, is_social_stream, direct_img_url
                    )
                    self.last_process_time = now_mono
                return

            self.last_url = url
            self.last_process_time = now_mono

            if existing:
                self._refresh_existing_play(
                    existing, url, world, w_id, media_info, forced_type, is_social_stream, direct_img_url
                )
                return

            # --- MEJORA v3.5: SOPORTE SOCIAL AVANZADO (YouTube oEmbed) ---
            # [ES] Categorización automática como Stream/Imagen para redes sociales
            # [EN] Automatic categorization as Stream/Image for social media
            if confirmed_video or is_social_stream:
                social_info = self._resolve_social_media(url)
                if social_info:
                    m_id = self._save_basic(url, social_info['title'], world, w_id, media_info)
                    Multimedia.update(
                        tipo_contenido='Stream/Imagen',
                        sinopsis=social_info.get('synopsis', ''),
                        poster_path=social_info.get('thumbnail', '')
                    ).where(Multimedia.id == m_id).execute()
                    self._session_url_map[url] = m_id
                    self.timer.start(m_id, 120.0)
                    self.current_media_title = social_info['title']
                    self._update_rpc()
                    if hasattr(self, 'signals'): self.signals.media_added.emit()
                    return
                if is_social_stream:
                    # Incluso si falla oEmbed, YouTube/Twitch/Kick siempre van en Stream/Imagen.
                    fallback_title = self._title_from_url(url)
                    m_id = self._save_basic(url, fallback_title, world, w_id, media_info)
                    Multimedia.update(tipo_contenido='Stream/Imagen').where(Multimedia.id == m_id).execute()
                    self._session_url_map[url] = m_id
                    self.timer.start(m_id, 120.0)
                    self.current_media_title = fallback_title
                    self._update_rpc()
                    if hasattr(self, 'signals'): self.signals.media_added.emit()
                    return

            # --- CLASIFICACIÓN Y BÚSQUEDA ---
            if direct_img_url or self.img_manager.is_image_url(url):
                # Miniaturas de YouTube/Twitch/Kick son posters de videos, no capturas del mundo.
                # Si la URL es un CDN de posters se ignora silenciosamente para que no
                # aparezca como entrada independiente en el catálogo de imágenes.
                if self.img_manager.is_poster_cdn_url(url):
                    logging.debug("🖼️ [Poster CDN] Ignorando miniatura de plataforma: %s…", url[:80])
                    return
                local_path = self.img_manager.download_capture(url, world)
                if local_path:
                    self._save_image(url, world, local_path)
                    self._session_url_map[url] = f"IMG_{hashlib.md5(url.encode()).hexdigest()[:10]}"
                    if hasattr(self, 'signals'): self.signals.media_added.emit()
                return

            tmdb_q = (media_info.get('canonical_title') or '').strip() or title_search
            if len(tmdb_q) < 2:
                tmdb_q = title_search

            logging.info(
                f"🔎 Nueva detección forense: {title_search} | TMDb≈{tmdb_q} | Año: {media_info['year']} | T: {media_info['season']} E: {media_info['episode']}"
            )

            # Búsqueda TMDb con Inteligencia NLP
            # Forzar tipo en la búsqueda si las reglas de oro lo detectaron
            search_type = 'tv' if forced_type == 'Serie' else 'multi'

            results = self.tmdb.search(tmdb_q, language=self._get_tmdb_lang(), year=media_info['year'], media_type=search_type)
            if not results and media_info['year']:
                results = self.tmdb.search(tmdb_q, language=self._get_tmdb_lang(), media_type=search_type)

            # --- SEGURIDAD FREE: no almacenar URLs privadas para usuarios sin premium ---
            # Si el usuario es FREE y la URL no es pública (YouTube/Twitch/Kick…),
            # guardamos solo el fingerprint del último segmento de ruta en vez de la
            # URL completa.  Ventajas:
            #  · El campo url nunca queda vacío → mejor extracción de nombre.
            #  · El fingerprint sirve para deduplicar en sesiones futuras (BD lookup).
            #  · No expone dominio, CDN ni path del servidor privado.
            is_private_url = not self._is_free_url(url) and not is_social_stream and not direct_img_url
            if self.is_premium or not is_private_url:
                store_url = url
            else:
                store_url = self._url_free_fp(url) or ""

            if results:
                best = results[0]
                # Si las reglas de oro dicen Serie pero TMDb dice Película (o viceversa), confiar en Reglas de Oro si es muy específico
                res_type = best.get('media_type', 'movie')
                if forced_type == 'Serie' and res_type == 'movie':
                    # Re-buscar específicamente como TV (v4.7)
                    tv_results = self.tmdb._call("GET", "search/tv", {'query': tmdb_q, 'language': self._get_tmdb_lang()})
                    if tv_results and tv_results.get('results'):
                        best = tv_results['results'][0]
                        best['media_type'] = 'tv'

                details = self.tmdb.get_details(best.get('media_type', 'movie'), best['id'], language=self._get_tmdb_lang())
                m_id = self._save_media(store_url, details, world, w_id, media_info)
                self._session_url_map[url] = m_id  # registrar para re-detecciones intra-sesión
                runtime = details.get('runtime') or (details.get('episode_run_time', [0])[0] if details.get('episode_run_time') else 0)
                self.timer.start(m_id, float(runtime or 0))
                self.current_media_title = details.get('title') or details.get('name')
                self._update_rpc()
            else:
                # Fallback al nombre limpio extraído
                m_id = self._save_basic(store_url, title_search, world, w_id, media_info)
                self._session_url_map[url] = m_id  # registrar para re-detecciones intra-sesión
                if forced_type:
                    Multimedia.update(tipo_contenido=forced_type).where(Multimedia.id == m_id).execute()
                self.timer.start(m_id, 120.0)
                self.current_media_title = title_search
                self._update_rpc()
        except Exception as e:
            logging.error(f"💥 Error crítico en _handle_play: {e}")

    def _resolve_social_media(self, url):
        """Resuelve miniaturas e info enriquecida para plataformas sociales (v4.8)"""
        u = url.lower()
        try:
            if any(x in u for x in ['youtube.com', 'youtu.be']):
                v_id = url.split('v=')[-1] if 'v=' in url else url.split('/')[-1]
                v_id = v_id.split('&')[0].split('?')[0]
                # Intento de oEmbed para obtener título real y canal
                try:
                    import urllib.request, json
                    o_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={v_id}&format=json"
                    req = urllib.request.Request(o_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        r = json.loads(resp.read().decode('utf-8'))
                    return { 
                        'title': r.get('title', f"YouTube: {v_id}"), 
                        'thumbnail': f"https://img.youtube.com/vi/{v_id}/maxresdefault.jpg",
                        'synopsis': f"Canal: {r.get('author_name', 'Desconocido')}\nPlataforma: YouTube",
                        'type': 'Stream/Imagen'
                    }
                except Exception:
                    return { 'title': f"YouTube: {v_id}", 'thumbnail': f"https://img.youtube.com/vi/{v_id}/maxresdefault.jpg", 'type': 'Stream/Imagen' }
            
            if 'twitch.tv' in u:
                user = url.split('/')[-1].split('?')[0]
                return { 
                    'title': f"Twitch: {user}", 
                    'thumbnail': f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{user}-440x248.jpg",
                    'synopsis': f"Stream en vivo de {user} en Twitch.",
                    'type': 'Stream/Imagen'
                }
            
            if 'kick.com' in u:
                user = url.split('/')[-1].split('?')[0]
                return { 
                    'title': f"Kick Live: {user}", 
                    'thumbnail': "https://kick.com/favicon.ico",
                    'synopsis': f"Canal de Kick: {user}",
                    'type': 'Stream/Imagen'
                }
        except Exception as e:
            logging.debug(f"_resolve_social_media: {e}")
        return None

    def _split_letter_digit_boundaries(self, s: str) -> str:
        """Separa letras y dígitos pegados (the10commandments, show2024) para GuessIt / TMDB."""
        s = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', s)
        s = re.sub(r'(\d)([a-zA-Z])', r'\1 \2', s)
        return re.sub(r'\s+', ' ', s).strip()

    def _drop_cinema_noise_tokens(self, tokens: list) -> list:
        """Quita idioma/calidad y sufijos tipo byDirector al final del slug."""
        if not tokens:
            return tokens
        out = [t for t in tokens if t and t.lower() not in self._CINEMA_STOPWORDS]
        while out:
            low = out[-1].lower()
            if low in self._CINEMA_STOPWORDS:
                out.pop()
                continue
            if low.startswith('by') and len(low) > 3:
                out.pop()
                continue
            break
        return out

    def _analyze_media_source(self, player_title, url):
        """Inteligencia Forense Radical: Disección Multinivel (v4.10)"""
        import urllib.parse
        import wordninja
        info = {'clean_title': "Video Desconocido", 'season': "", 'episode': "", 'year': "", 'is_link': False, 'forced_type': 'Pelicula'}

        u_social = url.lower()
        is_social = any(
            x in u_social
            for x in (
                "youtube.com",
                "youtu.be",
                "googlevideo.com",
                "manifest.googlevideo.com",
                "twitch.tv",
                "kick.com",
                "soundcloud.com",
                "soundcloud.app",
                "/audio/",
                "/audio_assets/",
                "api/vrc/assetstreaming",
            )
        )
        if is_social:
            info['forced_type'] = 'Stream/Imagen'

        try:
            decoded_url = urllib.parse.unquote(url)
            if "pixeldrain.com/api/file/" in decoded_url:
                file_id = decoded_url.rstrip('/').split('/')[-1]
                try:
                    import requests
                    resp = requests.get(f"https://pixeldrain.com/api/file/{file_id}/info", timeout=3)
                    filename = resp.json().get('name', url) if resp.status_code == 200 else url
                except Exception:
                    filename = url
            else:
                path_parts = [p for p in decoded_url.split('?')[0].split('/') if p]
                filename = path_parts[-1] if path_parts else url
                generic_pats = re.compile(r'(?i)^(\d+|t\d+|e\d+|s\d+|cap\d+|episodio\d+|part\d+|temp\d+|untitled|video|file)\b')
                if (len(filename) < 8 or generic_pats.match(filename)) and len(path_parts) > 1:
                    parent = path_parts[-2]
                    if parent.upper() not in ['SERIES', 'PELICULAS', 'ANIME', 'MOVIES', 'FILES', '🎬∞OLD', '∞🎬']:
                        filename = f"{parent} {filename}"
            info['is_link'] = True
        except Exception:
            filename = url

        source_text = filename
        is_generic = any(x in str(player_title).lower() for x in ['http', 'www', 'video', 'cine', 'playback', 'player'])
        if player_title and not is_generic:
            source_text = str(player_title)

        source_text = re.sub(r'\.(webm|mp4|mkv|avi|mov|flv|wmv|ts|m3u8)$', '', source_text, flags=re.I)
        source_text = source_text.replace('🎬', '').replace('∞', '').replace('+', ' ')
        source_text = source_text.replace('%20', ' ')

        # Extraer año al inicio (ej. 1998_enemigopublico) antes de quitar guiones bajos
        year_prefix_match = re.match(r'^([12]\d{3})[_\-\s]+(.+)', source_text)
        if year_prefix_match:
            info['year'] = year_prefix_match.group(1)
            source_text = year_prefix_match.group(2)

        source_text = source_text.replace('_', ' ').replace('.', ' ')
        source_text = re.sub(r'\s+', ' ', source_text).strip()
        # Quitar resolución (720p, 1080p) antes de patrones T/E para no confundirlos con temporada/episodio
        tv_scan_text = re.sub(r'(?i)\b\d{3,4}\s*p\b', ' ', source_text)
        tv_scan_text = re.sub(r'\s+', ' ', tv_scan_text).strip()

        guessit_title = None
        g_info = {}
        primed = source_text

        if not is_social:
            golden_tv_pats = [
                re.compile(r'(?i)(.*?)\b(?:T|S|TEM|SEASON|TEMPORADA)\s*(\d{1,2})?\s*(?:EP|E|CAP|EPISODE|EPISODIO|CAPITULO|C)\s*(\d{1,3})\b'),
                re.compile(r'(?i)(.*?)\b(\d{1,2})x(\d{1,3})\b'),
                re.compile(r'(?i)(.*?)\b(?:S|T)(\d{1,2})(?:E|EP|C)(\d{1,3})\b'),
                re.compile(r'(?i)(.*?)\b(?:EP|E|CAP|EPISODIO|CAPITULO|CAP\.|C|EP\.)\s*(\d{1,3})\b'),
                re.compile(r'(?i)(.*?)[-_\s]\[?(\d{2,3})\]?(?:\.|$| )'),
            ]

            for pat in golden_tv_pats:
                m = pat.search(tv_scan_text)
                if m:
                    info['forced_type'] = 'Serie'
                    if m.lastindex >= 3:
                        info['season'] = info['season'] or m.group(2) or "1"
                        info['episode'] = info['episode'] or m.group(3)
                    elif m.lastindex >= 2:
                        info['episode'] = info['episode'] or m.group(2)
                        info['season'] = info['season'] or "1"
                    if m.group(1) and len(m.group(1).strip()) > 2:
                        source_text = m.group(1).strip()
                    break

            url_up = url.upper()
            series_keywords = ['/SERIES/', '/TV/', '/TEMPORADA', '/SEASON', '/EPISODIO', '/EPISODE', '/SHOWS/', '/ANIME/']
            movie_keywords = ['/PELICULAS/', '/MOVIES/', '/CINE/']
            in_movie_folder = any(kw in url_up for kw in movie_keywords)

            if in_movie_folder:
                # Si la URL indica claramente que es una película, forzamos Pelicula
                # y solo lo cambiamos si hay un patrón de temporada/episodio muy explícito
                info['forced_type'] = 'Pelicula'
            elif info['forced_type'] != 'Serie' and any(kw in url_up for kw in series_keywords):
                info['forced_type'] = 'Serie'
                if not info['season']:
                    info['season'] = "1"

            if '/ANIME/' in url_up:
                info['is_anime'] = 1

            st = self._split_letter_digit_boundaries(source_text)
            primed = ' '.join(self._drop_cinema_noise_tokens(st.split()))
            if not primed:
                primed = st
            # Sufijo tipo bycecilb_demille que a veces queda tras quitar idioma
            primed = re.sub(r'(?i)\s+\bby[a-z0-9]{3,}(?:\s+[a-z]{2,24}){0,3}\s*$', '', primed).strip()
            primed = re.sub(r'(?i)\b\d{3,4}\s*p\b', ' ', primed)
            primed = re.sub(r'\s+', ' ', primed).strip()

            try:
                g_in = re.sub(r'\s+', '.', primed)
                g_info = guessit.guessit(g_in)
                g_season = g_info.get('season')
                g_episode = g_info.get('episode')
                g_type = g_info.get('type')
                if info['forced_type'] != 'Serie' and (g_season or g_episode or g_type == 'episode'):
                    info['forced_type'] = 'Serie'
                if g_season and not info['season']:
                    info['season'] = str(g_season[0] if isinstance(g_season, list) else g_season)
                if g_episode and not info['episode']:
                    info['episode'] = str(g_episode[0] if isinstance(g_episode, list) else g_episode)
                gy = g_info.get('year')
                if gy and not info['year']:
                    yv = gy[0] if isinstance(gy, list) else gy
                    info['year'] = str(yv)
                g_title = g_info.get('title')
                if isinstance(g_title, list):
                    g_title = g_title[0] if g_title else None
                if g_title and len(str(g_title).strip()) > 1:
                    guessit_title = str(g_title).strip()
            except Exception as e:
                logging.debug(f"guessit en _analyze_media_source: {e}")

            # Guessit puede marcar S/E ficticios (ej. "365 días" → 3x65); la ruta PELICULAS manda.
            if in_movie_folder:
                info['forced_type'] = 'Pelicula'
                info['season'] = ''
                info['episode'] = ''

        yr = re.search(r'\b(19|20)\d{2}\b', primed)
        if yr and not info['year']:
            info['year'] = yr.group(0)

        if guessit_title:
            clean_tmp = guessit_title
            if clean_tmp.islower():
                clean_tmp = clean_tmp.title()
        else:
            clean_tmp = primed if not is_social else source_text
            noise = ['old', 'peliculas', 'pelicula', 'inglessubtitulado', 'japonessubtitulado', 'latinoforced', 'latinohdts']
            for n in noise:
                clean_tmp = re.sub(r'\b' + re.escape(n) + r'\b', '', clean_tmp, flags=re.I)
            subs = ('inglessubtitulado', 'japonessubtitulado', 'latinoforced', 'latinohdts')
            for sub in subs:
                clean_tmp = re.sub(re.escape(sub), '', clean_tmp, flags=re.I)
            if ' ' not in clean_tmp and len(clean_tmp) > 10:
                clean_tmp = ' '.join(wordninja.split(clean_tmp))

        corrections = {
            'an ios': 'años', 'des pu es': 'despues', 'onepiece': 'One Piece',
            'one piece': 'One Piece', 'mut a fu kaz': 'Mutafukaz',
            'yel': 'y el', 'ti tula do': 'titulado', 'sub ti tula do': 'subtitulado',
            'robot ech': 'Robotech', 'hero es': 'Heroes',
        }
        for wrong, right in corrections.items():
            clean_tmp = re.sub(r'(?i)\b' + re.escape(wrong) + r'\b', right, clean_tmp)

        clean_tmp = re.sub(r'[-\s]+', ' ', clean_tmp).strip()
        if len(clean_tmp) > 2:
            info['clean_title'] = clean_tmp if guessit_title else clean_tmp.title()
        else:
            info['clean_title'] = source_text.strip().title() if source_text.strip() else 'Video Desconocido'

        info['canonical_title'] = self._canonical_title_for_match(info['clean_title']) or self._canonical_title_for_match(
            source_text
        )

        return info

    def _save_media(self, url, details, world, w_id=None, media_info=None):
        imdb_id = details.get('external_ids', {}).get('imdb_id')
        m_id = f"{details['id']}_{imdb_id}" if imdb_id else str(details['id'])
        
        genres = ", ".join([g['name'] for g in details.get('genres', [])])
        runtime = details.get('runtime') or (details.get('episode_run_time', [0])[0] if details.get('episode_run_time') else 0)
        current_lang_code = 'es' if self.config.get_val('language', 'Español') == 'Español' else 'en'
        
        season = str(media_info['season']) if media_info and media_info['season'] else ""
        episode = str(media_info['episode']) if media_info and media_info['episode'] else ""

        tipo_sugerido = 'Serie' if 'first_air_date' in details else 'Pelicula'
        if media_info and media_info.get('forced_type') == 'Serie':
            tipo_sugerido = 'Serie'

        # Senior Fix: Extraer Director y Elenco
        credits = details.get('credits', {})
        cast = ", ".join([c['name'] for c in credits.get('cast', [])[:10]])
        director = next((c['name'] for c in credits.get('crew', []) if c['job'] == 'Director'), "")
        if not director and details.get('created_by'):
            director = ", ".join(
                [c.get("name") for c in (details.get("created_by") or [])[:2] if c.get("name")]
            )
        
        # Senior Fix: Extraer Colección
        belongs_to_collection = details.get('belongs_to_collection')
        collection_name = belongs_to_collection.get('name') if belongs_to_collection else None
        collection_id = belongs_to_collection.get('id', 0) if belongs_to_collection else 0

        # Senior Fix: Usar Upsert (Update or Create) para corregir registros mal clasificados
        item, created = Multimedia.get_or_create(id=m_id, defaults={
            'url': url,
            'titulo': details.get('title') or details.get('name'),
            'año': (details.get('release_date') or details.get('first_air_date', ''))[:4] or (media_info.get('year') if media_info else ""),
            'temporada': season,
            'episodio': episode,
            'sinopsis': details.get('overview'),
            'generos': genres,
            'imdb_id': imdb_id,
            'tmdb_id': details['id'],
            'duracion_total': float(runtime or 0),
            'calificacion_global': float(details.get('vote_average', 0.0)),
            'tipo_contenido': tipo_sugerido,
            'es_anime': media_info.get('is_anime', 0) if media_info else 0,
            'director': director,
            'elenco': cast,
            'coleccion': collection_name,
            'coleccion_id': collection_id,
            'world_name': world,
            'world_id': w_id, 
            'metadata_lang': current_lang_code,
            'poster_path': f"https://image.tmdb.org/t/p/w500{details.get('poster_path')}" if details.get('poster_path') else ""
        })

        if not created:
            now = datetime.now()
            # Actualizar URL cuando:
            #   · La nueva URL es real (no vacía), Y
            #   · La URL almacenada está vacía O es un fingerprint Free (sin schema).
            # Esto permite que un usuario Free que se vuelve Premium vea su link al
            # reproducir de nuevo, sin sobreescribir URLs completas ya guardadas.
            if url and self._is_url_fingerprint(item.url):
                item.url = url
            item.ultimo_visto = now
            item.ultima_actualizacion = now
            item.tipo_contenido = tipo_sugerido
            if media_info and media_info.get('is_anime'):
                item.es_anime = 1
            item.calificacion_global = float(details.get('vote_average', 0.0))
            item.director = director
            item.elenco = cast
            item.coleccion = collection_name
            item.coleccion_id = collection_id
            if season: item.temporada = season
            if episode: item.episodio = episode
            item.save()

        logging.info(f"✅ Registrado Forense: {details.get('title') or details.get('name')} (T{season} E{episode}) [{tipo_sugerido}]")
        if hasattr(self, 'signals'): self.signals.media_added.emit()
        return m_id

    def _save_basic(self, url, title, world, w_id=None, media_info=None):
        # Cuando url="" (usuario Free con contenido privado), usamos el título + tipo como
        # fuente del hash para evitar que todos esos registros colisionen en el mismo ID.
        # Con URL real seguimos usando la URL como siempre.
        tipo_final = media_info.get('forced_type', 'Pelicula') if media_info else 'Pelicula'
        id_source = url if url else f"{title}_{tipo_final}"
        m_id = f"BASIC_{hashlib.md5(id_source.encode('utf-8', errors='replace')).hexdigest()[:10]}"

        season = str(media_info['season']) if media_info and media_info['season'] else ""
        episode = str(media_info['episode']) if media_info and media_info['episode'] else ""

        item, created = Multimedia.get_or_create(id=m_id, defaults={
            'url': url, 'titulo': title, 'año': media_info.get('year') if media_info else "",
            'temporada': season, 'episodio': episode, 'tipo_contenido': tipo_final, 
            'es_anime': media_info.get('is_anime', 0) if media_info else 0,
            'world_name': world, 'world_id': w_id, 'duracion_total': 120.0
        })

        if not created:
            now = datetime.now()
            # Actualizar URL cuando la nueva no está vacía y la almacenada es un
            # fingerprint Free o está vacía; nunca sobreescribir una URL real con otra URL.
            if url and self._is_url_fingerprint(item.url):
                item.url = url
            item.ultimo_visto = now
            item.ultima_actualizacion = now
            item.tipo_contenido = tipo_final
            if media_info and media_info.get('is_anime'):
                item.es_anime = 1
            if season: item.temporada = season
            if episode: item.episodio = episode
            item.save()

        logging.info(f"✅ Registrado (Básico Forense): {title} (T{season} E{episode}) [{tipo_final}]")
        if hasattr(self, 'signals'): self.signals.media_added.emit()
        return m_id

    def _save_image(self, url, world, path):
        """Gestión Forense de Imágenes: Agrupación por Mundo (v4.9)"""
        # [ES] Las imágenes ahora funcionan como 'Series' para mostrar historial por mundo.
        # [EN] Images now work as 'Series' to show history per world.
        #
        # Formato de episodio: DD-HH:MM:SS (24 h) → ej. "22-15:30:45"
        # La temporada agrupa por Año-Mes: "2026-03"
        m_id = f"IMG_{hashlib.md5(url.encode()).hexdigest()[:10]}"
        now = datetime.now()
        season = now.strftime("%Y-%m")
        episode = now.strftime("%d-%H:%M:%S")   # Día-Hora:Min:Seg en 24 h

        existing = Multimedia.get_or_none(Multimedia.url == url)
        if existing:
            # Actualizar fecha de vista más reciente y el timestamp del episodio.
            existing.ultimo_visto = now
            existing.ultima_actualizacion = now
            existing.temporada = season
            existing.episodio = episode
            # Si el mundo guardado era 'Mundo Desconocido' y ahora tenemos el real, corregir.
            if world and world != 'Mundo Desconocido':
                if not existing.world_name or existing.world_name == 'Mundo Desconocido':
                    existing.world_name = world
                    existing.titulo = world
                    logging.info("📸 Imagen corregida: 'Mundo Desconocido' → '%s'", world)
                elif world != existing.world_name:
                    logging.info(
                        "📸 Imagen re-detectada desde mundo diferente: original='%s' actual='%s'. "
                        "Preservando origen.",
                        existing.world_name, world
                    )
            if path:
                existing.poster_path = path
            existing.save()
            logging.info("📸 Imagen re-detectada en %s. Episodio actualizado: %s", world, episode)
        else:
            Multimedia.create(
                id=m_id,
                url=url,
                titulo=world,
                tipo_contenido='Stream/Imagen',
                temporada=season,
                episodio=episode,
                poster_path=path,
                world_name=world,
                fecha_creacion=now,
                ultimo_visto=now
            )
            logging.info("📸 Nueva captura guardada: %s (T.%s Ep.%s)", world, season, episode)
        
        if hasattr(self, 'signals'): self.signals.media_added.emit()

    def _title_from_url(self, url):
        # 1. Extraer nombre base de la URL
        import urllib.parse
        try:
            decoded_url = urllib.parse.unquote(url)
            path_parts = [p for p in decoded_url.split('?')[0].split('/') if p]
            if not path_parts: 
                filename = url.split('/')[-1]
            else:
                filename = path_parts[-1]
        except:
            filename = url.split('/')[-1]

        # 2. Limpieza inicial
        raw_name = filename.replace('∞', ' ').replace('🎬', ' ').replace('%20', ' ').replace('+', ' ')
        name_no_ext = re.sub(r'\.[a-zA-Z0-9]{3,4}$', '', raw_name)
        titulo_procesado = name_no_ext.replace('.', ' ').replace('_', ' ').replace('-', ' ').strip()
        
        # 3. Limpiar basura con Regex (Filtro v1.17.9)
        garbage_pattern = re.compile(r'(?i)(\b(latinoforced|inglessubtitulado|japonessubtitulado|ingles subtitulado|japones subtitulado|inglessub|japonessub|latinohdts|ingles|japones|coreano|korean|tomo|1080p|720p|4k|latino|castellano|español|audio|dual|subtitulado|sub|web-dl|bluray|mkv|mp4|webm|forced|hd|ts|rip|antigua|doblaje|lat|mb|gb|kb|x264|x265|aac|pelicula|peliculas|peli cula)\b|\[.*?\]|\(.*?y\))')
        titulo_limpio_pre = garbage_pattern.sub('', titulo_procesado).strip()

        # 4. Usar GuessIt para extraer el título base ignorando codecs y años
        try:
            g_info = guessit.guessit(titulo_limpio_pre)
            base_title = str(g_info.get('title', titulo_limpio_pre)).strip()
        except Exception:
            base_title = titulo_limpio_pre

        # Limpiar años colados al final
        base_title = re.sub(r'\b(?:19|20)\d{2}\b', '', base_title).strip()

        # 5. NLP: Si el título no tiene espacios (ej. residentevildeathisland), usar WordNinja
        if base_title.count(' ') == 0 and len(base_title) > 5:
            # WordNinja no es perfecto con español, pero ayuda mucho con nombres en inglés
            # Proteger algunas letras como la ñ
            protected = base_title.replace('ñ', 'qnq').replace('Ñ', 'QNQ')
            reconstructed = " ".join(wordninja.split(protected))
            reconstructed = reconstructed.replace('q n q', 'ñ').replace('Q N Q', 'Ñ').replace('qnq', 'ñ').replace('QNQ', 'Ñ')
            
            # Ajustes comunes post-ninja
            reconstructed = reconstructed.replace('an ios', 'años').replace('des pu es', 'despues').replace('onepiece', 'One Piece')
            nlp_title = reconstructed.strip()
        else:
            nlp_title = base_title
            
        nlp_title = re.sub(r'\s+', ' ', nlp_title).strip()
        
        # 6. Evitar nombres genéricos
        if nlp_title.lower() in ['index', 'playlist', 'chunk', 'stream', 'video', 'untitled']:
            return "Video de VRChat"
            
        return nlp_title.title() if nlp_title else "Video Desconocido"

    def _get_tmdb_lang(self):
        """Mapea el idioma actual a códigos ISO de TMDb (v2.11.57)"""
        lang = self.config.get_val('language', 'Español')
        return 'es-MX' if lang == 'Español' else 'en-US'

    def _sync_metadata_language(self):
        """Recorre la DB y re-descarga info si el idioma no coincide (v2.11.60)"""
        current_lang_code = 'es' if self.config.get_val('language', 'Español') == 'Español' else 'en'
        tmdb_lang = self._get_tmdb_lang()
        
        # Buscar ítems que necesiten actualización
        to_update = Multimedia.select().where((Multimedia.metadata_lang != current_lang_code) & (Multimedia.tmdb_id.is_null(False)))
        
        if to_update.count() > 0:
            logging.info(f"🔄 [LOCALIZACIÓN RADICAL] Actualizando {to_update.count()} títulos al idioma: {current_lang_code}")
            for item in to_update:
                try:
                    details = self.tmdb.get_details(
                        'tv' if item.tipo_contenido == 'Serie' else 'movie', 
                        item.tmdb_id, 
                        language=tmdb_lang
                    )
                    if details:
                        item.titulo = details.get('title') or details.get('name')
                        item.sinopsis = details.get('overview')
                        genres_list = [g.get('name') for g in details.get('genres', []) if g.get('name')]
                        item.generos = ", ".join(genres_list)
                        item.metadata_lang = current_lang_code
                        item.save()
                        logging.debug(f"  ✅ Actualizado: {item.titulo}")
                except Exception as e:
                    logging.error(f"  ❌ Error actualizando {item.titulo}: {e}")
            
            # Avisar a la UI para refrescar si es necesario
            if hasattr(self, 'signals'): self.signals.media_added.emit()

    def stop(self):
        self.running = False
        if self.config.get_val("vrchat_stub_restore_on_exit", False):
            try:
                from src.core.vrchat_ytdlp_stub import VRChatYtStubManager
                ok, msg = VRChatYtStubManager(self.config).restore_original()
                logging.info("VRChat yt-dlp restaurado al salir: %s — %s", ok, msg)
            except Exception as e:
                logging.debug("VRChat stub restore on exit: %s", e)
        # Detener el polling de premium y despertar el thread bloqueado en wait().
        self._premium_poll_active = False
        if hasattr(self, '_premium_poll_event'):
            self._premium_poll_event.set()
                
        if hasattr(self, 'discord'):
            self.discord.stop()
        
        # --- MEJORA v2.11.16: ESPERA DE HILOS ---
        from PySide6.QtCore import QThreadPool
        logging.info("⏳ Esperando a que terminen los hilos de fondo...")
        QThreadPool.globalInstance().waitForDone(2000) # Máximo 2 segundos de espera

    def _probe_is_image(self, url: str):
        """Sonda forense para determinar si un enlace es una imagen real y obtener su URL directa (v4.8)"""
        import requests
        try:
            # Petición HEAD para no descargar el archivo, solo leer cabeceras
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.head(url, headers=headers, timeout=5, allow_redirects=True)
            ct = resp.headers.get('Content-Type', '').lower()
            if 'image/' in ct:
                return resp.url # Devolver la URL real después de redirecciones
            return None
        except:
            return None
