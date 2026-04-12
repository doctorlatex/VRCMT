from peewee import *
import os
import logging
from datetime import datetime
from src.core.paths import DB_PATH

# Configuración de base de datos robusta (thread_safe: motor en hilo + UI en main)
db = SqliteDatabase(
    DB_PATH,
    pragmas={
        'journal_mode': 'wal',
        'cache_size': -1024 * 64,
        'synchronous': 1,
        'foreign_keys': 1,
    },
    thread_safe=True,
    timeout=30,
)

class BaseModel(Model):
    class Meta:
        database = db

class Multimedia(BaseModel):
    id = CharField(primary_key=True)
    # Índices mejorados para rendimiento y consultas eficientes (Mejora v3.6.5)
    url = TextField(index=True)
    titulo = CharField(index=True)
    año = CharField(null=True)
    temporada = CharField(default='1', index=True)
    episodio = CharField(default='1', index=True)
    nombre_episodio = CharField(null=True)
    minuto_actual = FloatField(default=0.0)
    duracion_total = FloatField(default=0.0)
    estado_visto = IntegerField(default=0, index=True)
    calificacion_personal = FloatField(default=0.0)
    calificacion_global = FloatField(default=0.0)
    tipo_contenido = CharField(default='Pelicula', index=True)
    es_anime = IntegerField(default=0, index=True)
    generos = CharField(null=True)
    sinopsis = TextField(null=True)
    poster_path = CharField(null=True)
    imdb_id = CharField(null=True, index=True)
    tmdb_id = CharField(null=True, index=True)
    director = CharField(null=True)
    elenco = TextField(null=True)
    etiquetas = TextField(null=True)
    world_name = CharField(null=True, index=True)
    world_id = CharField(null=True) # --- MEJORA v2.11.0: Necesario para Triple Candado ---
    en_watchlist = IntegerField(default=0)
    es_favorito = IntegerField(default=0) # --- MEJORA v2.11.40: Fase 4 Quick Toggles ---
    metadata_lang = CharField(default='es') # --- MEJORA v2.11.60: Localización Radical ---
    coleccion = CharField(null=True)
    coleccion_id = IntegerField(default=0)
    fecha_creacion = DateTimeField(default=datetime.now)
    ultimo_visto = DateTimeField(default=datetime.now)
    ultima_actualizacion = DateTimeField(default=datetime.now)

class VRCMTRatingCache(BaseModel):
    """Cache local del promedio comunitario VRCMT por IMDb ID.

    Evita consultas repetidas a Firebase: la app lee de aquí primero.
    TTL gestionado en capa de aplicación (media_modal). Se invalida cuando
    el usuario guarda su propia calificación o cuando expira el TTL (1 hora).
    """
    imdb_id   = CharField(primary_key=True)
    avg       = FloatField(default=0.0)
    count     = IntegerField(default=0)
    updated_at = FloatField(default=0.0)   # unix timestamp (time.time())


def init_db():
    db.connect(reuse_if_open=True)
    db.create_tables([Multimedia, VRCMTRatingCache])
    logging.info("✨ Base de Datos Peewee v2.4.1 lista.")
