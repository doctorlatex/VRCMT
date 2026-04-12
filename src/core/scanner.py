import os
import glob
import time
import re
import logging
import urllib.parse
from datetime import datetime
from typing import Generator, Dict

class VRChatLogScanner:
    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        # --- MEJORA v2.10.5: Prioridad de Títulos desde Reproductores (iwaSync, TVManager, Udon) ---
        # [ES] Sincronizado con Lógica de Oro del proyecto anterior
        # [EN] Synchronized with Golden Logic from previous project
        self.player_title_patterns = [
            re.compile(r"\[(?:iwaSync|AT INFO TVManager.*?|TVManager.*?)\] (?:Playing|Selected|Now Playing): (.+)"),
            re.compile(r"Video Playback - Attempting to resolve URL '(.+)'"), # Legacy format support
            re.compile(r"\[Video Playback\]\s+Playing\s+'(.*?)'"),
            re.compile(r"\[UdonBehaviour\].*?Playing: (.+)"),
            re.compile(r"\[USharpVideo.*?\] Opening (.+)")
        ]
        self.pause_patterns = [
            # [ES] ProTV/TVManager (ProTV) usa formato "[AT DEBUG TVManager ...]"
            #      por lo que buscar "Forwarding event _TvPause" directamente es más fiable.
            #      Se mantiene el patrón legacy "[TVManager] Player Paused" para mundos
            #      que usen TVManager simple.
            # [EN] ProTV/TVManager uses "[AT DEBUG TVManager ...]" format,
            #      so matching "Forwarding event _TvPause" directly is more reliable.
            #      Legacy "[TVManager] Player Paused" kept for simple TVManager worlds.
            re.compile(r"Forwarding event _TvPause\b"),
            re.compile(r"\[TVManager.*?\] Player Paused"),
            re.compile(r"\[USharpVideo.*?\] Pausing"),
        ]
        self.resume_patterns = [
            # [ES] _TvPlay se emite tanto en inicio como en reanudación; el timer.resume()
            #      solo actúa si estaba en pausa, por lo que es seguro usarlo aquí.
            # [EN] _TvPlay fires on both start and resume; timer.resume() only acts
            #      if paused, so it's safe to use here.
            re.compile(r"Forwarding event _TvPlay\b"),
            re.compile(r"\[TVManager.*?\] Player Resumed"),
            re.compile(r"\[USharpVideo.*?\] Resuming"),
        ]
        self.stop_patterns = [
            # [ES] ProTV emite "_TvStop" como parte de "Forwarding event _TvStop".
            # [EN] ProTV emits "_TvStop" as part of "Forwarding event _TvStop".
            re.compile(r"Forwarding event _TvStop\b"),
            re.compile(r"\[TVManager.*?\] Player Stopped"),
            re.compile(r"\[USharpVideo.*?\] (?:Stopping|Video Finished)"),
        ]

        # --- NUEVO v5.0: PATRÓN DE DURACIÓN REAL DESDE EL LOG (ProTV / TVManager) ---
        # [ES] "Media Ready info loaded: start=0, end=5187.384, t=0, ..."
        #      Captura la duración real del reproductor de VRChat sin necesitar TMDB.
        # [EN] Captures actual video duration from VRChat's player, no TMDB needed.
        self.media_ready_pattern = re.compile(
            r"Media Ready info loaded:\s*start=(\d+(?:\.\d+)?),\s*end=(\d+(?:\.\d+)?),\s*t=(\d+(?:\.\d+)?)",
            re.IGNORECASE,
        )
        # --- MEJORA v3.4: PATRÓN DE URL ULTRA-ROBUSTO ---
        # [ES] Restringido para no capturar espacios (evita basura de logs como "offset 0")
        # [EN] Restricted to not capture spaces (avoids log junk like "offset 0")
        self.url_pattern = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
        
        # --- MEJORA v4.5: PATRONES DE VIDEO DE ALTA PRIORIDAD ---
        # --- MEJORA v4.8: URL GARANTIZADA VÍDEO (pipeline nativo VRChat / ProTV AVProHQ) ---
        # [ES] Prioridad a la URL final tras resolución de VRChat y a la carga explícita por AVProHQ.
        # [EN] Prefer the final URL after VRChat resolution and ProTV AVProHQ user load lines.
        self.video_url_patterns = [
            re.compile(r"\[Video Playback\]\s+URL\s+'[^']*'\s+resolved\s+to\s+'(https?://[^']+)'"),
            # [ES] Algunos mundos insertan espacios en el path (ej. /SERIES/THE ROOKIE).
            # [EN] Some worlds include spaces in path segments (e.g. /SERIES/THE ROOKIE).
            re.compile(r"\[AVProHQ\]\s+loading URL by user '[^']*':\s*(https?://.+)$"),
            re.compile(r"\[AVProVideo\] Opening (https?://.+)$"),
            re.compile(r"\[Video Playback\] Attempting to resolve URL '(https?://[^']+)'"),
            re.compile(r"\[Compat\] loading URL by user '.*?':\s*(https?://.+)$"),
            re.compile(r"\[(?:iwaSync|USharpVideo.*?|TVManager.*?)\] (?:Opening|Selected):\s*(https?://.+)$")
        ]

        # --- MEJORA v4.9: DETECCIÓN NATIVA DE IMÁGENES ---
        # VRChat usa la etiqueta "[Image Download] Attempting to load image from URL '...'"
        # para distinguir imágenes de vídeos en el log. Esto es autoritativo: si VRChat
        # lo registró como Image Download, es una imagen sin importar la extensión de la URL.
        self.image_url_patterns = [
            re.compile(r"\[Image Download\]\s+Attempting to load image from URL\s+'(https?://[^']+)'"),
            re.compile(r"\[Image Download\]\s+Attempting to load image from URL\s+\"(https?://[^\"]+)\""),
        ]

        self.world_pattern = re.compile(r"Entering Room: (.+)")
        self.world_id_pattern = re.compile(r"Joining (wrld_[a-f0-9-]+)") # --- MEJORA v2.11.0 ---
        # Marca de tiempo al inicio de línea típica de output_log VRChat
        self._vrchat_line_ts = re.compile(
            r"^(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2}):(\d{2})"
        )
        
        # --- MEJORA v4.7: SOPORTE MULTI-INSTANCIA ---
        self.active_logs = {} # path -> file_handle
        self.current_states = {} # path -> {'world': str, 'world_id': str, 'last_title': str}
        # Mundo inicial inyectado por el engine al recuperar la sesión; los nuevos logs
        # arrancan con este valor en lugar de 'Mundo Desconocido'.
        self._initial_world: str = 'Mundo Desconocido'

    def set_initial_world(self, world: str) -> None:
        """Inyecta el mundo conocido para que los estados de nuevos logs lo hereden."""
        if world and world != 'Mundo Desconocido':
            self._initial_world = world
            # Actualizar también los estados de logs ya abiertos que siguen sin mundo.
            for path, st in self.current_states.items():
                if st.get('world') == 'Mundo Desconocido':
                    st['world'] = world

    def _clean_line(self, line: str) -> str:
        return re.sub(r'<color=#[A-Fa-f0-9]{6}>|</color>', '', line).strip()

    def _sanitize_raw_url(self, raw_url: str) -> str:
        """
        Limpia ruido típico que VRChat/AVPro añade al final de la URL:
        - " (offset 0)"
        - " with API MediaFoundation"
        """
        u = (raw_url or "").strip()
        # Recorta desde el primer bloque de metadatos agregado por AVPro/MediaFoundation.
        u = re.sub(r"\s+\(offset\s+\d+\).*?$", "", u, flags=re.IGNORECASE)
        u = re.sub(r"\s+with\s+API\s+.+?$", "", u, flags=re.IGNORECASE)
        return u.strip()

    def _process_line(self, line: str, log_path: str):
        clean_line = self._clean_line(line)
        state = self.current_states.setdefault(log_path, {
            'world': self._initial_world,
            'world_id': None,
            'last_title': None
        })
        
        # 1. Detección de Mundo (Nombre e ID)
        world_match = self.world_pattern.search(clean_line)
        if world_match:
            state['world'] = world_match.group(1)
            return {'event': 'WORLD_CHANGE', 'name': state['world'], 'world_id': state['world_id']}

        world_id_match = self.world_id_pattern.search(clean_line)
        if world_id_match:
            state['world_id'] = world_id_match.group(1)

        # 2. Detección de Título (Semilla de metadatos)
        for pt in self.player_title_patterns:
            t_match = pt.search(clean_line)
            if t_match:
                potential = t_match.group(1)
                if not potential.startswith("http"):
                    state['last_title'] = potential
        
        # 3a-pre. MEDIA_READY: duración real del video (ProTV "Media Ready info loaded")
        # [ES] Captura duración y offset de inicio para el cronómetro preciso.
        # [EN] Captures duration and start offset for precise playback timer.
        mr_match = self.media_ready_pattern.search(clean_line)
        if mr_match:
            try:
                start_s  = float(mr_match.group(1))
                end_s    = float(mr_match.group(2))
                offset_s = float(mr_match.group(3))
                duration_s = end_s - start_s
                if duration_s > 0:
                    return {
                        'event': 'MEDIA_READY',
                        'duration_seconds': duration_s,
                        'start_offset_seconds': offset_s,
                    }
            except (ValueError, IndexError):
                pass

        # 3a. Detección de Imagen Nativa (etiqueta autoritativa del motor de VRChat)
        # "[Image Download] Attempting to load image from URL '...'"
        # Si VRChat ya lo etiquetó como Image Download, es una imagen con certeza;
        # no necesita ir por el pipeline de video ni la clasificación TMDB.
        for ip in self.image_url_patterns:
            img_match = ip.search(clean_line)
            if img_match:
                return self._create_play_event(
                    img_match.group(1), log_path,
                    confirmed_video=False, confirmed_image=True
                )

        # 3b. Detección de URL de Alta Prioridad (Video Players)
        for vp in self.video_url_patterns:
            url_match = vp.search(clean_line)
            if url_match:
                return self._create_play_event(url_match.group(1), log_path, confirmed_video=True)

        # 4. Detección de URL Genérica (Fallback)
        url_match = self.url_pattern.search(clean_line)
        if url_match:
            return self._create_play_event(url_match.group(0), log_path, confirmed_video=False)

        # 5. Detección de Control de Reproducción
        for p in self.pause_patterns:
            if p.search(clean_line): return {'event': 'PAUSE'}
        for r in self.resume_patterns:
            if r.search(clean_line): return {'event': 'RESUME'}
        for s in self.stop_patterns:
            if s.search(clean_line): return {'event': 'STOP'}

        return None

    def _create_play_event(self, raw_url: str, log_path: str,
                           confirmed_video: bool = False, confirmed_image: bool = False):
        """Helper para centralizar la creación de eventos de reproducción (Senior Fix)"""
        url = urllib.parse.unquote(self._sanitize_raw_url(raw_url)).strip()
        state = self.current_states.get(log_path, {})
        
        # Lista Negra Forense (Evitar assets de VRChat y basura de YouTube)
        # [ES] Se añade 'googlevideo.com' y 'Video Playback' para mantener el catálogo limpio
        # [EN] Added 'googlevideo.com' and 'Video Playback' to keep the catalog clean
        blacklist = [
            'vrchat.cloud', 'amplitude', 'analytics', 'assets.vrchat.com', 
            'api.vrchat.cloud', 'googlevideo.com', 'Video Playback'
        ]
        
        if any(x.lower() in url.lower() for x in blacklist):
            return None
            
        title = state.get('last_title')
        if title and any(x.lower() in title.lower() for x in ['Video Playback']):
            title = None # Limpiar título si es basura conocida

        event = {
            'event': 'PLAY',
            'url': url,
            'confirmed_video': confirmed_video,
            'confirmed_image': confirmed_image,  # True = VRChat lo etiquetó como [Image Download]
            'player_title': title,
            'world': state.get('world', 'Mundo Desconocido'),
            'world_id': state.get('world_id')
        }
        state['last_title'] = None 
        return event

    @staticmethod
    def _read_log_tail_lines(path: str, max_bytes: int) -> list:
        """Lee solo el final del log (evita cargar archivos de VRChat de cientos de MB en RAM)."""
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            sz = f.tell()
            if sz == 0:
                return []
            start = max(0, sz - max_bytes)
            f.seek(start)
            raw = f.read()
        if start > 0 and raw:
            nl = raw.find(b'\n')
            if nl != -1:
                raw = raw[nl + 1:]
        return raw.decode('utf-8', errors='ignore').splitlines()

    def _parse_vrchat_ts(self, line: str):
        m = self._vrchat_line_ts.match(line)
        if not m:
            return None
        try:
            return datetime(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
                int(m.group(4)),
                int(m.group(5)),
                int(m.group(6)),
            )
        except ValueError:
            return None

    def _ingest_world_from_lines(self, lines: list, state: dict) -> None:
        """Actualiza world / world_id con la última aparición al recorrer en orden cronológico."""
        for line in lines:
            clean = self._clean_line(line)
            wm = self.world_pattern.search(clean)
            if wm:
                state["world"] = wm.group(1).strip()
                ts = self._parse_vrchat_ts(line)
                if ts:
                    state["_world_line_ts"] = ts
            wid = self.world_id_pattern.search(clean)
            if wid:
                state["world_id"] = wid.group(1)

    def seed_state_from_tail(self, log_path: str, max_bytes: int = 900_000) -> None:
        """Al enganchar un log nuevo (seek EOF): recupera mundo desde la cola por si ya estabas dentro."""
        try:
            lines = self._read_log_tail_lines(log_path, max_bytes)
            st = self.current_states.setdefault(
                log_path,
                {"world": "Mundo Desconocido", "world_id": None, "last_title": None},
            )
            self._ingest_world_from_lines(lines, st)
            st.pop("_world_line_ts", None)
        except Exception as e:
            logging.debug(f"seed_state_from_tail {os.path.basename(log_path)}: {e}")

    def _session_snapshot_from_tail(self, log_path: str, max_bytes: int) -> dict:
        lines = self._read_log_tail_lines(log_path, max_bytes)
        st = {
            "world": "Mundo Desconocido",
            "world_id": None,
            "_world_line_ts": None,
        }
        self._ingest_world_from_lines(lines, st)
        play_event = None
        for line in reversed(lines):
            clean = self._clean_line(line)
            url_match = self.url_pattern.search(clean)
            if not url_match:
                continue
            url = urllib.parse.unquote(url_match.group(0))
            if any(
                x in url.lower()
                for x in [
                    "vrchat.cloud",
                    "amplitude",
                    "analytics",
                    "googlevideo.com",
                ]
            ):
                continue
            play_event = {"url": url, "world": st["world"]}
            break
        return {
            "world": st["world"],
            "world_id": st["world_id"],
            "last_enter_ts": st.get("_world_line_ts"),
            "play_event": play_event,
        }

    def find_last_session_state(self):
        """Último mundo y vídeo: cruza varios output_log y elige la sesión más reciente por Entering Room + mtime."""
        files = glob.glob(os.path.join(self.log_dir, "output_log_*.txt"))
        if not files:
            return None

        files.sort(key=lambda x: (os.path.getmtime(x), x), reverse=True)
        best = None
        best_key = None

        try:
            for path in files[:12]:
                mtime = os.path.getmtime(path)
                snap = None
                for max_bytes in (2 * 1024 * 1024, 5 * 1024 * 1024):
                    snap = self._session_snapshot_from_tail(path, max_bytes)
                    if snap["last_enter_ts"] or snap["play_event"]:
                        break
                ts = snap["last_enter_ts"] or datetime.fromtimestamp(mtime)
                key = (ts, mtime)
                if best is None or key > best_key:
                    best = {
                        "world": snap["world"],
                        "world_id": snap["world_id"],
                        "play_event": snap["play_event"],
                    }
                    best_key = key

            if best and (
                best["play_event"] or best["world"] != "Mundo Desconocido" or best["world_id"]
            ):
                return best

            logging.debug(
                "Recuperación de sesión: sin Entering Room / URL útil en colas revisadas."
            )
            return None
        except Exception as e:
            logging.error(f"Error recuperando sesión: {e}")
            return None

    def scan(self) -> Generator[Dict[str, any], None, None]:
        logging.info(f"📡 [RADAR v2.4.0] Vigilancia Multilog activa (hasta 8 instancias / logs)")
        
        while True:
            try:
                # 1. Identificar logs recientes
                files = glob.glob(os.path.join(self.log_dir, "output_log_*.txt"))
                if not files:
                    time.sleep(2)
                    continue
                
                files.sort(key=os.path.getmtime, reverse=True)
                recent_files = files[:8]  # Varias instancias / sesiones VRChat a la vez

                # 2. Abrir nuevos logs detectados
                for path in recent_files:
                    if path not in self.active_logs:
                        try:
                            self.seed_state_from_tail(path)
                            f = open(path, 'r', encoding='utf-8', errors='ignore')
                            # Ir al final para no procesar historial antiguo
                            f.seek(0, os.SEEK_END)
                            self.active_logs[path] = f
                            logging.info(f"🔥 [RADAR] Enganchado a nueva instancia: {os.path.basename(path)}")
                        except Exception as e:
                            logging.error(f"❌ Error abriendo log {path}: {e}")

                # 3. Leer líneas de todos los logs activos
                # [ES] VRChat escribe muchas líneas/s; 1 readline por log + sleep(0.5) dejaba el puntero
                # minutos atrás → eventos PLAY (p. ej. [Compat]) nunca llegaban al motor a tiempo.
                # [EN] One readline per log per 0.5s cannot keep up with VRChat; drain buffered lines per file.
                any_line_read = False
                _burst = 400  # tope por archivo por vuelta (evita acaparar un solo log infinito)
                for path, file_handle in list(self.active_logs.items()):
                    if path not in recent_files:
                        # Log antiguo, cerrar
                        file_handle.close()
                        del self.active_logs[path]
                        if path in self.current_states: del self.current_states[path]
                        continue

                    try:
                        n = 0
                        while n < _burst:
                            line = file_handle.readline()
                            if not line:
                                break
                            any_line_read = True
                            n += 1
                            res = self._process_line(line, path)
                            if res:
                                yield res
                    except Exception as e:
                        logging.error(f"⚠️ [RADAR] Error de lectura en {os.path.basename(path)}: {e}")

                time.sleep(0.02 if any_line_read else 0.35)

            except Exception as e:
                logging.error(f"⚠️ [RADAR] Error crítico en bucle: {e}")
                time.sleep(1)

