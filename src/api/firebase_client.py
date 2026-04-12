import os
import json
import hmac
import uuid
import time
import queue as _queue
import hashlib
import logging
import platform
import threading
import firebase_admin
from firebase_admin import credentials, firestore, storage
from src.core.paths import APP_DIR, resource_path

# Silenciar completamente los loggers de gRPC y google.auth para
# evitar spam en el log cuando las credenciales son inválidas.
logging.getLogger("grpc").setLevel(logging.CRITICAL)
logging.getLogger("grpc._plugin_wrapping").setLevel(logging.CRITICAL)
logging.getLogger("google.auth.transport.grpc").setLevel(logging.CRITICAL)
logging.getLogger("google.auth").setLevel(logging.CRITICAL)
logging.getLogger("google.oauth2").setLevel(logging.CRITICAL)

# Tiempo de vida del caché de acceso (en segundos). 10 minutos por defecto.
_CACHE_TTL = 600
_CACHE_FILE = os.path.join(APP_DIR, 'firebase_cache.json')

class FirebaseClient:
    def __init__(self):
        self.db = None
        self._db_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        # Circuit breaker: True cuando las credenciales son inválidas/revocadas.
        # En ese estado, NINGUNA llamada Firebase se ejecuta (modo offline limpio).
        self._auth_failed: bool = False
        # Caché de resultados: { cache_key: (result: bool, timestamp: float) }
        self._access_cache: dict = {}
        # Último estado premium conocido (para el listener)
        self._last_premium_status: bool = False
        self._load_cache_from_disk()
        # Thread persistente para todas las llamadas gRPC/Firestore.
        # Evita el crash ACCESS_VIOLATION de Python 3.13 al salir un thread que usó gRPC.
        # Al ser daemon=True + os._exit() en shutdown, el cleanup gRPC nunca se ejecuta.
        self._fb_task_queue: _queue.Queue = _queue.Queue()
        self._fb_worker = threading.Thread(
            target=self._fb_persistent_worker,
            name="vrcmt-firebase-worker",
            daemon=True,
        )
        self._fb_worker.start()
        self._initialize()

    @property
    def is_available(self) -> bool:
        """True si Firebase está conectado y las credenciales son válidas."""
        return self.db is not None and not self._auth_failed

    @staticmethod
    def _harden_cred_file(cred_path: str):
        """Restringe cred.json a lectura solo por el propietario del proceso (rw-------).
        En Windows usa la API de ACL a través del módulo `stat`; en otros SO usa chmod.
        Si falla, solo se registra una advertencia (no bloquea el arranque).
        """
        try:
            import stat as _stat
            # Quitar todos los permisos y establecer rw solo para el propietario
            os.chmod(cred_path, _stat.S_IRUSR | _stat.S_IWUSR)
        except Exception as e:
            logging.warning(f"⚠️ No se pudo restringir permisos de cred.json: {e}")

    def _initialize(self):
        with self._db_lock:
            try:
                cred_path = resource_path('cred.json')
                if not os.path.exists(cred_path):
                    logging.warning("⚠️ Archivo cred.json no encontrado. Funciones de nube desactivadas.")
                    return

                self._harden_cred_file(cred_path)
                if not firebase_admin._apps:
                    cred = credentials.Certificate(cred_path)
                    firebase_admin.initialize_app(cred, {
                        'storageBucket': 'vrcmt-75823.firebasestorage.app'
                    })

                # ── Prueba de credenciales vía HTTP antes de crear el cliente gRPC ──
                # Esto detecta "invalid_grant" sin disparar el spam del retry de gRPC.
                try:
                    import google.auth.transport.requests as _http_req
                    _app = firebase_admin.get_app()
                    _google_cred = _app.credential.get_credential()
                    _google_cred.refresh(_http_req.Request())
                except Exception as _auth_err:
                    _msg = str(_auth_err).lower()
                    if any(k in _msg for k in ('invalid_grant', 'invalid jwt', 'refresherror', 'jwt signature')):
                        self._auth_failed = True
                        logging.warning(
                            "⚠️ Firebase OFFLINE — credenciales inválidas o revocadas.\n"
                            "   → Genera una nueva clave en Firebase Console:\n"
                            "     Configuración del proyecto → Cuentas de servicio → Generar nueva clave privada\n"
                            "   → Reemplaza el archivo cred.json local con la nueva clave.\n"
                            "   Error técnico: %s", _auth_err
                        )
                        return  # No crear cliente Firestore; db permanece None
                    # Errores de red transitorios → intentar igualmente
                    logging.debug("Firebase credential test (transitorio): %s", _auth_err)

                self.db = firestore.client()
                logging.info("☁️ Firebase Cloud v2.0 (Firestore + Storage) conectado exitosamente.")
            except Exception as e:
                logging.error(f"❌ Error conectando Firebase: {e}")

    def _fb_persistent_worker(self):
        """Thread persistente que ejecuta TODAS las operaciones gRPC/Firestore.
        Nunca termina (daemon). Al salir la app, os._exit() omite el cleanup de gRPC,
        evitando el crash ACCESS_VIOLATION de Python 3.13."""
        while True:
            try:
                fn, callback = self._fb_task_queue.get(block=True)
                # Circuit breaker: si las credenciales fallaron, no ejecutar más tareas
                if self._auth_failed:
                    if callback:
                        callback(None, RuntimeError("Firebase offline — credenciales inválidas"))
                    continue
                try:
                    result = fn()
                    if callback:
                        callback(result, None)
                except Exception as exc:
                    # Detectar fallo de autenticación en tareas ya encoladas
                    _emsg = str(exc).lower()
                    if any(k in _emsg for k in ('invalid_grant', 'invalid jwt', 'jwt signature',
                                                 'unauthenticated', 'permission_denied')):
                        if not self._auth_failed:
                            self._auth_failed = True
                            self.db = None
                            logging.warning("⚠️ Firebase OFFLINE — credenciales revocadas detectadas en worker.")
                    else:
                        logging.error("☁️ [Firebase] Worker task error: %s", exc)
                    if callback:
                        callback(None, exc)
            except Exception as loop_exc:
                logging.error("☁️ [Firebase] Worker loop error: %s", loop_exc)

    def run_firebase_async(self, fn, callback=None):
        """Encola una operación Firebase en el thread persistente.
        callback(result, error) se llama desde el worker thread al terminar.
        Thread-safe; se puede llamar desde cualquier thread."""
        if self._auth_failed:
            if callback:
                callback(None, RuntimeError("Firebase offline — credenciales inválidas"))
            return
        self._fb_task_queue.put((fn, callback))

    def register_user_if_not_exists(self, discord_id: str, username: str = "Desconocido"):
        """Crea o actualiza el perfil del usuario en Firestore (Fase 3)."""
        if not self.db or not discord_id: return
        
        try:
            user_ref = self.db.collection('Usuarios').document(discord_id)
            doc = user_ref.get()
            
            if not doc.exists:
                user_ref.set({
                    'vip_global': False,
                    'premium_global': False,
                    'username': username,
                    'fecha_registro': firestore.SERVER_TIMESTAMP
                })
                logging.info(f"☁️ [Nube] Nuevo usuario registrado: {username}")
            else:
                # Sincronizar nombre si cambió
                current_data = doc.to_dict()
                if current_data.get('username') != username:
                    user_ref.update({'username': username})
                    logging.info(f"☁️ [Nube] Perfil sincronizado: {username}")
        except Exception as e:
            logging.error(f"Error registrando usuario en Firebase: {e}")

    @staticmethod
    def _cache_hmac_key() -> bytes:
        """Deriva una clave HMAC de 32 bytes ligada a esta máquina (misma lógica que HWID del cifrado).
        Evita que un archivo firebase_cache.json manipulado sea aceptado."""
        material = f"{uuid.getnode()}:{platform.node()}".encode("utf-8")
        return hashlib.sha256(material + b"VRCMT_CACHE_HMAC_v1").digest()

    def _load_cache_from_disk(self):
        """Carga el caché persistido en disco al iniciar (útil para arrancar sin conexión).
        Verifica la firma HMAC antes de aceptar el contenido."""
        try:
            if not os.path.exists(_CACHE_FILE):
                return
            with open(_CACHE_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)

            if not isinstance(raw, dict) or 'data' not in raw or 'sig' not in raw:
                logging.debug("☁️ [Cache] Formato de caché sin firma (heredado). Descartando.")
                return

            # Verificar firma HMAC
            payload = json.dumps(raw['data'], sort_keys=True, separators=(',', ':')).encode('utf-8')
            expected_sig = hmac.new(self._cache_hmac_key(), payload, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected_sig, raw['sig']):
                logging.warning("⚠️ [Cache] Firma HMAC inválida en firebase_cache.json. Descartando.")
                return

            data = raw['data']
            now = time.time()
            self._access_cache = {
                k: (v['result'], v['ts'])
                for k, v in data.items()
                if isinstance(v, dict) and (now - v.get('ts', 0)) < _CACHE_TTL
            }
            logging.debug(f"☁️ [Cache] {len(self._access_cache)} entradas verificadas y cargadas desde disco.")
        except Exception as e:
            logging.debug(f"☁️ [Cache] No se pudo cargar caché desde disco: {e}")

    def _save_cache_to_disk(self):
        """Persiste el caché actual firmado (HMAC) en disco en un hilo separado."""
        def _write():
            try:
                with self._cache_lock:
                    data = {k: {'result': v[0], 'ts': v[1]} for k, v in self._access_cache.items()}
                payload = json.dumps(data, sort_keys=True, separators=(',', ':')).encode('utf-8')
                sig = hmac.new(self._cache_hmac_key(), payload, hashlib.sha256).hexdigest()
                with open(_CACHE_FILE, 'w', encoding='utf-8') as f:
                    json.dump({'data': data, 'sig': sig}, f)
            except Exception as e:
                logging.debug(f"☁️ [Cache] No se pudo guardar caché en disco: {e}")
        threading.Thread(target=_write, daemon=True).start()

    def _cache_get(self, key: str):
        """Devuelve (result, valid) desde el caché si existe y no ha expirado."""
        entry = self._access_cache.get(key)
        if entry is not None:
            result, ts = entry
            if (time.time() - ts) < _CACHE_TTL:
                return result, True
        return None, False

    def _cache_set(self, key: str, result: bool):
        with self._cache_lock:
            self._access_cache[key] = (result, time.time())
        self._save_cache_to_disk()

    def verificar_triple_candado(self, discord_id: str, world_id: str) -> bool:
        """
        Valida si el usuario tiene acceso PREMIUM (Global o Local por Mapa).
        Retorna True si tiene acceso, False si está bloqueado.
        En errores de red usa el último resultado cacheado; si no hay caché devuelve False.
        """
        if not self.db:
            logging.debug("☁️ Firebase no inicializado, denegando acceso (Modo Offline).")
            return False

        if not discord_id:
            logging.warning("⚠️ Intento de validación PREMIUM sin Discord ID vinculado.")
            return False

        cache_key = f"{discord_id}:{world_id or ''}"
        try:
            # 1. ¿Es PREMIUM Global? (Pase Maestro)
            user_ref = self.db.collection('Usuarios').document(discord_id)
            user_doc = user_ref.get()
            
            if user_doc.exists:
                data = user_doc.to_dict()
                is_global = bool(data.get('vip_global', False) or data.get('premium_global', False))
                logging.debug(f"☁️ [Firebase] Data Usuario: {data}")
                if is_global:
                    logging.info(f"💎 Acceso PREMIUM Global concedido para: {data.get('username', discord_id)}")
                    self._cache_set(cache_key, True)
                    return True
            else:
                logging.debug(f"☁️ Usuario {discord_id} no encontrado en Firebase.")
            
            # Si no hay world_id, solo dependemos del PREMIUM Global
            if not world_id:
                logging.debug("🔒 No hay World ID para validar PREMIUM Local.")
                self._cache_set(cache_key, False)
                return False

            # 2. ¿Es un Mapa Aliado y tiene PREMIUM Local?
            logging.debug(f"🔍 Validando Mapa Aliado: {world_id}")
            world_ref = self.db.collection('Mapas_Aliados').document(world_id).get()
            if world_ref.exists:
                logging.debug(f"🗺️ Mapa aliado detectado: {world_id}")
                premium_ref = self.db.collection('Mapas_Aliados').document(world_id).collection('Premium').document(discord_id).get()
                if premium_ref.exists:
                    v_data = premium_ref.to_dict()
                    logging.debug(f"💎 Data PREMIUM Local: {v_data}")
                    if v_data.get('activo', False):
                        logging.info(f"💎 Acceso PREMIUM Local (Mapa Aliado) concedido para {discord_id}")
                        self._cache_set(cache_key, True)
                        return True

            logging.debug(f"🔒 Acceso denegado para {discord_id} en el mapa {world_id}")
            self._cache_set(cache_key, False)
            return False
        except Exception as e:
            logging.error(f"❌ Error validando PREMIUM en Firestore: {e}")
            cached, valid = self._cache_get(cache_key)
            if valid:
                logging.info(f"⚡ Usando caché de acceso para {discord_id} (error de red): {cached}")
                return cached
            # Sin caché disponible → denegar por seguridad
            logging.warning("🔒 Sin caché disponible; denegando acceso por seguridad.")
            return False

    def get_premium_status(self, discord_id: str) -> bool:
        """Lee el estatus PREMIUM del usuario con una consulta puntual (sin streaming gRPC).
        Reemplaza al antiguo listen_premium_status para evitar el crash de Python 3.13
        causado por Thread-ConsumeBidirectionalStream al usar on_snapshot."""
        if not self.db or not discord_id:
            return False
        try:
            doc = self.db.collection('Usuarios').document(discord_id).get()
            if doc.exists:
                data = doc.to_dict() or {}
                is_premium = bool(data.get('vip_global', False) or data.get('premium_global', False))
                self._last_premium_status = is_premium
                self._cache_set(f"premium:{discord_id}", is_premium)
                logging.debug(f"☁️ [Premium] Estado leído: {'PREMIUM' if is_premium else 'FREE'}")
                return is_premium
        except Exception as e:
            logging.error(f"get_premium_status error: {e}")
            cached, valid = self._cache_get(f"premium:{discord_id}")
            # _cache_get retorna (result, valid); bool sobre la tupla siempre seria True.
            if valid:
                return bool(cached)
        return False

    def listen_premium_status(self, discord_id: str, callback):
        """OBSOLETO — usaba on_snapshot (gRPC bidireccional) que crashea en Python 3.13.
        Mantenido solo por retrocompatibilidad; no crea ningún listener real.
        Usar engine._setup_premium_polling() en su lugar."""
        logging.warning("⚠️ listen_premium_status llamado (obsoleto). Usar get_premium_status + polling.")
        return None

    def get_community_rating(self, media_id: str) -> float:
        if not self.db: return 0.0
        try:
            doc = self.db.collection('Global_Ratings').document(media_id).get()
            if doc.exists:
                return doc.to_dict().get('average', 0.0)
        except Exception as e:
            logging.debug(f"get_community_rating: {e}")
        return 0.0

    # ── Calificaciones comunitarias VRCMT ─────────────────────────────────────────
    # Estructura en Firestore:
    #   VRCMTRatings/{imdb_id}/votes/{discord_id}  → {rating, local_db_id, updated_at}
    #   VRCMTRatings/{imdb_id}/aggregate           → {average, count, sum, updated_at}
    #
    # Reglas:
    #  · Solo se sube si rating > 0 (0 = sin voto, se elimina si existía).
    #  · El aggregate se recalcula atómicamente en cada escritura/borrado.
    #  · Si el imdb_id cambia (Fix), el voto migra: se borra del antiguo y se escribe en el nuevo.

    def _ratings_vote_ref(self, imdb_id: str, discord_id: str):
        return self.db.collection('VRCMTRatings').document(imdb_id)\
                      .collection('votes').document(discord_id)

    def _ratings_aggregate_ref(self, imdb_id: str):
        return self.db.collection('VRCMTRatings').document(imdb_id)\
                      .collection('_meta').document('aggregate')

    def _read_and_write_aggregate(self, txn, imdb_id: str,
                                    override_id: str = None,
                                    override_rating: float = None):
        """Lee TODOS los votos primero (transaccional, antes de cualquier write)
        y luego escribe el aggregate correcto con el valor nuevo/borrado incluido.

        override_id:     discord_id cuyo voto se va a reemplazar/borrar.
        override_rating: nuevo valor para ese usuario (None o 0 = borrar su voto).

        IMPORTANTE: llama a este método ANTES de txn.set/txn.delete en la transacción,
        porque Firestore requiere reads antes de writes.
        """
        votes_ref = (self.db.collection('VRCMTRatings')
                         .document(imdb_id)
                         .collection('votes'))
        all_docs = votes_ref.get(transaction=txn)   # lectura transaccional

        total = 0.0
        count = 0
        user_found = False
        for doc in all_docs:
            d = doc.to_dict() or {}
            if override_id and doc.id == override_id:
                user_found = True
                r = float(override_rating) if override_rating and override_rating > 0 else 0.0
            else:
                r = float(d.get('rating', 0.0))
            if r > 0:
                total += r
                count += 1

        # Voto nuevo que aún no existe en Firestore
        if override_id and not user_found and override_rating and override_rating > 0:
            total += float(override_rating)
            count += 1

        avg = round(total / count, 2) if count > 0 else 0.0
        agg_ref = self._ratings_aggregate_ref(imdb_id)
        txn.set(agg_ref, {
            'average': avg,
            'count': count,
            'sum': round(total, 2),
            'updated_at': firestore.SERVER_TIMESTAMP,
        })
        return avg, count

    def sync_rating(self, discord_id: str, imdb_id: str, rating: float,
                    local_db_id: str = '', old_imdb_id: str = ''):
        """Sube o migra la calificación personal de un título a Firestore.

        Args:
            discord_id:   ID de Discord del usuario (requerido).
            imdb_id:      IMDb ID actual del título (ej. "tt0133093").
            rating:       Calificación personal (0.0–10.0). 0 = borrar voto.
            local_db_id:  ID de la BD local (metadato de auditoría).
            old_imdb_id:  IMDb ID anterior. Si difiere de imdb_id y el usuario
                          tenía un voto guardado, se migra al nuevo ID.
        """
        if not self.db or not discord_id or not imdb_id:
            return
        try:
            old = (old_imdb_id or '').strip()
            new = imdb_id.strip()

            # ── Migración: imdb_id cambió → borrar voto del ID anterior ────
            if old and old != new:
                try:
                    @firestore.transactional
                    def _delete_old(txn):
                        old_ref = self._ratings_vote_ref(old, discord_id)
                        # LECTURA primero (dentro de transacción)
                        snap = old_ref.get(transaction=txn)
                        if snap.exists:
                            # Recalcula aggregate SIN este usuario (lectura de todos los votos)
                            self._read_and_write_aggregate(txn, old,
                                                           override_id=discord_id,
                                                           override_rating=0.0)
                            txn.delete(old_ref)  # ESCRITURA después

                    _delete_old(self.db.transaction())
                    logging.info("🗳️ [Rating] Voto migrado: %s → %s (discord=%s)", old, new, discord_id[:8])
                except Exception as e:
                    logging.warning("sync_rating: error borrando voto viejo (%s): %s", old, e)

            # ── Borrar voto si rating == 0 ───────────────────────────────────
            if rating <= 0:
                try:
                    @firestore.transactional
                    def _delete_vote(txn):
                        ref = self._ratings_vote_ref(new, discord_id)
                        # LECTURA primero
                        snap = ref.get(transaction=txn)
                        if snap.exists:
                            # Recalcula aggregate SIN este voto
                            self._read_and_write_aggregate(txn, new,
                                                           override_id=discord_id,
                                                           override_rating=0.0)
                            txn.delete(ref)  # ESCRITURA después

                    _delete_vote(self.db.transaction())
                    logging.info("🗳️ [Rating] Voto eliminado: %s (discord=%s)", new, discord_id[:8])
                except Exception as e:
                    logging.warning("sync_rating: error borrando voto (rating=0): %s", e)
                return

            # ── Escribir / actualizar voto ───────────────────────────────────
            @firestore.transactional
            def _write_vote(txn):
                ref = self._ratings_vote_ref(new, discord_id)
                # LECTURAS primero: calcula aggregate con el nuevo valor ya incluido
                agg = self._read_and_write_aggregate(txn, new,
                                                     override_id=discord_id,
                                                     override_rating=round(float(rating), 1))
                # ESCRITURA después
                txn.set(ref, {
                    'rating': round(float(rating), 1),
                    'local_db_id': str(local_db_id or ''),
                    'updated_at': firestore.SERVER_TIMESTAMP,
                })
                return agg  # (avg, count) calculado en la transacción

            agg = _write_vote(self.db.transaction())
            logging.info("🗳️ [Rating] Voto guardado: %s = %.1f (discord=%s)", new, rating, discord_id[:8])
            return agg  # devuelve (avg, count) al caller

        except Exception as e:
            logging.error("sync_rating error: %s", e)
        return None  # error o paths de borrado/migración

    def get_vrcmt_rating(self, imdb_id: str) -> tuple[float, int]:
        """Retorna (promedio, cantidad_votos) de la calificación comunitaria VRCMT.
        Retorna (0.0, 0) si no hay datos o falla la conexión."""
        if not self.db or not imdb_id:
            return 0.0, 0
        try:
            doc = self._ratings_aggregate_ref(imdb_id).get()
            if doc.exists:
                d = doc.to_dict() or {}
                return float(d.get('average', 0.0)), int(d.get('count', 0))
        except Exception as e:
            logging.debug("get_vrcmt_rating: %s", e)
        return 0.0, 0

    # --- MEJORA v2.11.19: BUCKET STORAGE (BACKUP CLOUD) ---
    def upload_backup(self, discord_id: str, local_zip_path: str) -> bool:
        """Sube el archivo ZIP de respaldo al Storage privado del usuario."""
        if not discord_id or not os.path.exists(local_zip_path): 
            logging.error("❌ Fallo en subida: Falta Discord ID o archivo ZIP no existe.")
            return False
            
        try:
            bucket = storage.bucket()
            # Estructura: Backups/{Discord_ID}/vrcmt_backup.zip
            blob = bucket.blob(f"Backups/{discord_id}/vrcmt_backup.zip")
            blob.upload_from_filename(local_zip_path)
            logging.info(f"☁️ [Storage] Respaldo subido a la nube con éxito para el usuario {discord_id}.")
            return True
        except Exception as e:
            logging.error(f"❌ Error al subir backup a Firebase: {e}")
            return False

    def download_backup(self, discord_id: str, destination_path: str) -> bool:
        """Descarga el último backup del usuario desde la nube."""
        if not discord_id: return False
        
        try:
            bucket = storage.bucket()
            blob = bucket.blob(f"Backups/{discord_id}/vrcmt_backup.zip")
            
            if not blob.exists():
                logging.warning(f"⚠️ [Storage] No se encontró ningún backup en la nube para {discord_id}.")
                return False
                
            blob.download_to_filename(destination_path)
            logging.info(f"☁️ [Storage] Respaldo descargado con éxito para el usuario {discord_id}.")
            return True
        except Exception as e:
            logging.error(f"❌ Error al descargar backup de Firebase: {e}")
            return False
