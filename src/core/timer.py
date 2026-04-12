import time
import logging
from src.db.models import Multimedia

class PlaybackTimer:
    # [ES] Si la duración TMDB es 0, se usa este umbral mínimo (en segundos) para
    #      considerar el contenido como "visto" cuando no hay otra referencia.
    # [EN] If TMDB duration is 0, use this minimum threshold (in seconds) to
    #      consider content as "seen" when no other duration reference is available.
    FALLBACK_SEEN_SECONDS = 12 * 60   # 12 minutos / 12 minutes

    def __init__(self):
        self.current_item_id = None
        self.start_time = None
        self.accumulated_seconds = 0
        self.is_paused = False
        self.total_duration = 0
        # [ES] Indicador de si la duración fue refinada desde el log del reproductor.
        # [EN] Whether duration was refined from the player's log entry.
        self._duration_from_log = False

    def start(self, item_id: str, total_min: float):
        """Inicia el cronómetro para un nuevo video (v2.9)."""
        self.current_item_id = item_id
        self.total_duration = total_min * 60
        self._duration_from_log = False
        self.start_time = time.time()
        self.accumulated_seconds = 0
        self.is_paused = False
        logging.info(f"⏱️ Cronómetro iniciado para ID: {item_id} ({total_min} min)")

    def update_duration(self, duration_seconds: float, start_offset_seconds: float = 0.0):
        """
        [ES] Actualiza la duración total con el valor real reportado por VRChat
             (evento MEDIA_READY, ej: 'Media Ready info loaded: end=5187.384').
             Si se reporta un offset de inicio (t=X), se acumula ese tiempo como
             ya reproducido para que el ratio sea correcto desde el inicio.
        [EN] Updates total duration with the real value reported by VRChat
             (MEDIA_READY event, e.g. 'Media Ready info loaded: end=5187.384').
             If a start offset is reported (t=X), it's added as already-played
             time so the ratio is correct from the start.
        """
        if not self.current_item_id or duration_seconds <= 0:
            return
        old = self.total_duration
        self.total_duration = duration_seconds
        self._duration_from_log = True
        # [ES] El offset "t" indica desde qué segundo empieza la reproducción.
        #      Lo sumamos como tiempo ya acumulado para que el ratio sea preciso.
        # [EN] The "t" offset indicates where playback starts in seconds.
        #      We add it as already-accumulated time so the ratio stays accurate.
        if start_offset_seconds > 0 and self.accumulated_seconds == 0:
            self.accumulated_seconds = start_offset_seconds
        logging.info(
            "⏱️ [MEDIA_READY] Duración actualizada desde log: %.1fs (era %.1fs). "
            "Offset inicio: %.1fs. [Duration updated from log: %.1fs (was %.1fs). Start offset: %.1fs]",
            duration_seconds, old, start_offset_seconds,
            duration_seconds, old, start_offset_seconds,
        )

    def pause(self):
        """Detiene el tiempo momentáneamente (v2.9)."""
        if self.start_time and not self.is_paused:
            self.accumulated_seconds += (time.time() - self.start_time)
            self.is_paused = True
            self.start_time = None
            logging.info("⏱️ Cronómetro pausado.")

    def resume(self):
        """Reanuda el tiempo acumulado (v2.9)."""
        if self.is_paused:
            self.start_time = time.time()
            self.is_paused = False
            logging.info("⏱️ Cronómetro reanudado.")

    def get_current_progress(self) -> float:
        """Calcula los minutos totales reproducidos."""
        total = self.accumulated_seconds
        if self.start_time:
            total += (time.time() - self.start_time)
        return total / 60

    def _playback_progress_ratio(self) -> float:
        """
        [ES] Segundos reproducidos / duración total.
             Si la duración es 0 (no hay dato TMDB ni log), usa FALLBACK_SEEN_SECONDS:
             si el usuario lleva ≥ 12 minutos reproduciendo, retorna 0.9 (visto).
        [EN] Played seconds / total duration.
             If duration is 0 (no TMDB or log data), uses FALLBACK_SEEN_SECONDS:
             if the user has been playing for ≥ 12 minutes, returns 0.9 (seen).
        """
        played = self.accumulated_seconds
        if self.start_time:
            played += time.time() - self.start_time
        if self.total_duration <= 0:
            # [ES] Sin duración de referencia: umbral mínimo fijo como indicador de "visto".
            # [EN] No reference duration: fixed minimum threshold as "seen" indicator.
            return 0.9 if played >= self.FALLBACK_SEEN_SECONDS else played / self.FALLBACK_SEEN_SECONDS
        return min(played / self.total_duration, 1.0)

    def check_seen_status(self) -> bool:
        """Verifica la regla del 90% (Auto-Visto)."""
        return self._playback_progress_ratio() >= 0.9

    def save_to_db(self):
        """Guarda el progreso actual en la base de datos."""
        if not self.current_item_id: return
        
        minutes = self.get_current_progress()
        ratio = self._playback_progress_ratio()
        seen = ratio >= 0.9
        
        try:
            # --- MEJORA v3.4: BLINDAJE DE EXISTENCIA ---
            item = Multimedia.get_or_none(Multimedia.id == self.current_item_id)
            if not item:
                logging.warning(f"⚠️ [Timer] No se pudo guardar progreso: El ítem {self.current_item_id} ya no existe.")
                self.current_item_id = None
                return

            was_unseen = item.estado_visto != 1
            # Solo guardamos el mayor progreso alcanzado (v2.9)
            if minutes > item.minuto_actual:
                item.minuto_actual = minutes
                
            if seen:
                item.estado_visto = 1
                if was_unseen:
                    # Un solo INFO al cruzar el umbral; el autosave cada N s no debe inundar el log
                    logging.info(
                        "✅ Regla del 90%% alcanzada (%.1f%% respecto a duración en catálogo). Marcando como visto.",
                        ratio * 100,
                    )
            item.save()
            logging.debug(f"💾 Progreso guardado: {item.minuto_actual:.2f} min totales.")
        except Exception as e:
            logging.error(f"Error guardando progreso en DB: {e}")
