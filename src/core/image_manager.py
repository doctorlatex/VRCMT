import os
import re
import time
import requests
import logging
import hashlib
from PIL import Image
from io import BytesIO
from src.core.paths import CAPTURES_DIR

class ImageManager:
    def __init__(self):
        self.image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.img')
        self.trusted_domains = [
            'discordapp.com', 'discord.com',
            'media.discordapp.net', 'cdn.discordapp.com',
            'imgur.com', 'pixeldrain.com',
        ]
        # Dominios de miniaturas/posters de plataformas sociales.
        # Estas URLs son carátulas de videos (YouTube, Twitch, Kick…), NO capturas
        # del mundo de VRChat. is_image_url() devuelve False para ellas aunque
        # terminen en .jpg/.png, evitando que aparezcan en el catálogo de imágenes.
        self._poster_cdn_hosts = (
            'img.youtube.com',
            'i.ytimg.com',
            'i1.ytimg.com',
            'i2.ytimg.com',
            'i3.ytimg.com',
            'static-cdn.jtvnw.net',   # Twitch preview thumbnails
            'clips-media-assets2.twitch.tv',
            'vod-secure.twitch.tv',
            'kick.com/favicon',
            'assets.kick.com',
        )

    # ── Detección de URLs expiradas de Discord CDN ──────────────────────────
    @staticmethod
    def is_discord_cdn_url(url: str) -> bool:
        u = (url or "").lower()
        return "discordapp.net" in u or "cdn.discordapp.com" in u

    @staticmethod
    def discord_cdn_is_expired(url: str) -> bool:
        """Devuelve True si la URL de Discord CDN ya venció (parámetro ex= en epoch hex)."""
        m = re.search(r"[?&]ex=([0-9a-fA-F]+)", url)
        if not m:
            return False
        try:
            expiry = int(m.group(1), 16)
            return time.time() > expiry
        except Exception:
            return False

    def is_poster_cdn_url(self, url: str) -> bool:
        """Retorna True si la URL es una miniatura/poster de plataforma social.

        Estas URLs (YouTube thumbnails, Twitch previews…) son carátulas de videos
        y nunca deben clasificarse como capturas del mundo de VRChat.
        """
        u = (url or "").lower()
        return any(host in u for host in self._poster_cdn_hosts)

    def is_image_url(self, url: str) -> bool:
        """Determina si una URL es una imagen sin descargarla (v2.6)."""
        url_lower = (url or "").lower()

        # Miniaturas de YouTube/Twitch/Kick → son posters de videos, no capturas.
        if self.is_poster_cdn_url(url):
            return False

        path_only = url_lower.split("?")[0].split("#")[0]

        # 1. Por extensión (sin query; ej. twimg …/photo.jpg?format=jpg)
        if any(path_only.endswith(ext) for ext in self.image_extensions):
            return True

        # 1a. .png/.jpg en medio del path (Discord, CDNs) antes de ?…
        if re.search(r"\.(png|jpe?g|gif|webp|bmp)(\?|#|$)", url_lower):
            return True

        # Avatares / repos: casi siempre imagen
        if "avatars.githubusercontent.com" in url_lower:
            return True

        # 1b. Twimg /media/… suele ser imagen aunque la ruta no termine en extensión
        if "twimg.com" in url_lower and "/media/" in url_lower:
            return True

        # 2. Por dominio confiable (HEAD request)
        if any(td in url_lower for td in self.trusted_domains):
            try:
                resp = requests.head(url, timeout=3, allow_redirects=True)
                content_type = resp.headers.get('Content-Type', '')
                return content_type.startswith('image/')
            except Exception:
                return False
        return False

    def download_capture(self, url: str, world_name: str) -> str:
        """Descarga la imagen y retorna la ruta local (v2.5).
        
        Manejo especial de Discord CDN:
        - Las URLs llevan el parámetro ex= (epoch hex) que indica su vencimiento.
        - Si ya venció, no se intenta descargar (evita errores 403 que bloquean el thread).
        - El hash del nombre de archivo se calcula sobre la parte del path (sin query)
          para que, si la URL se renueva, se reutilice el archivo ya descargado.
        """
        try:
            # Advertir si la URL de Discord CDN ya venció
            if self.is_discord_cdn_url(url) and self.discord_cdn_is_expired(url):
                logging.warning(
                    "⚠️ URL de Discord CDN expirada, no se puede descargar: %s…", url[:80]
                )
                return ""

            # Carpeta del mundo (Álbum)
            safe_name = "".join(x for x in (world_name or "mundo") if x.isalnum() or x in " -_")
            world_dir = os.path.join(CAPTURES_DIR, safe_name)
            os.makedirs(world_dir, exist_ok=True)

            # Hash sobre el path sin query (así URLs renovadas de Discord CDN reutilizan la misma imagen)
            path_part = url.split("?")[0]
            file_hash = hashlib.md5(path_part.encode()).hexdigest()
            file_ext  = path_part.split('.')[-1]
            if len(file_ext) > 5 or not file_ext.isalpha():
                file_ext = "jpg"

            filename   = f"capture_{file_hash}.{file_ext}"
            local_path = os.path.join(world_dir, filename)

            if os.path.exists(local_path):
                return local_path

            import urllib.request
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    data = resp.read()
                    with open(local_path, 'wb') as f:
                        f.write(data)
                    logging.info("📸 Imagen guardada en álbum '%s': %s", world_name, filename)
                    return local_path
        except Exception as e:
            logging.error("Error descargando captura '%s…': %s", (url or "")[:60], e)
        return ""

    def get_album_cover(self, world_name: str) -> str:
        """Retorna la última imagen descargada de un mundo (Dynamic Cover)."""
        world_dir = os.path.join(CAPTURES_DIR, "".join(x for x in world_name if x.isalnum() or x in " -_"))
        if not os.path.exists(world_dir): return ""
        
        files = [os.path.join(world_dir, f) for f in os.listdir(world_dir)]
        if not files: return ""
        
        # El más reciente por fecha de creación
        files.sort(key=os.path.getmtime, reverse=True)
        return files[0]
