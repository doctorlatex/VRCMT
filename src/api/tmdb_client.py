import requests
import logging

class TMDBClient:
    def __init__(self, api_key: str, internal_key: str = None):
        self.user_key = api_key
        self.internal_key = internal_key
        self.api_key = api_key or internal_key
        self.base_url = "https://api.themoviedb.org/3"
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        self.session = requests.Session() 
        self.error_signal = None # Se vinculará desde el motor

    def _call(self, method: str, path: str, params: dict, timeout: int = 5):
        """Manejador de llamadas con lógica de fallback y detección de cuota (Senior)"""
        url = f"{self.base_url}/{path}"
        
        # 1. Intentar con clave actual (Usuario o Maestra)
        try:
            params['api_key'] = self.api_key
            resp = self.session.request(method, url, params=params, headers=self.headers, timeout=timeout)
            
            if resp.status_code == 200:
                return resp.json()
            
            # 2. Si hay error de cuota (429) o clave inválida (401)
            if resp.status_code in [401, 429]:
                # Si falló la del usuario, intentar con la maestra (solo si es distinta; evita recursión infinita)
                if (
                    self.api_key == self.user_key
                    and self.internal_key
                    and self.internal_key != self.user_key
                ):
                    logging.warning(f"⚠️ Clave personal falló ({resp.status_code}). Usando clave maestra...")
                    self.api_key = self.internal_key
                    return self._call(method, path, params, timeout)
                
                # Si falló la maestra, notificar al usuario
                if self.error_signal:
                    self.error_signal.emit("API_QUOTA_EXCEEDED")
                logging.error(f"❌ TMDb Error Fatal ({resp.status_code}): Límite alcanzado o clave inválida.")
                
        except Exception as e:
            logging.error(f"❌ TMDb Connection Error: {e}")
        return None

    def search(self, query: str, language: str = 'es-MX', year: str = None, media_type: str = 'multi') -> list:
        """Búsqueda inteligente con idioma dinámico."""
        params = {
            'query': query,
            'language': language,
            'include_adult': 'true'
        }
        if year: params['year'] = year
        
        logging.debug(f"📡 TMDb Search ({language}) [{media_type}]: {query}")
        data = self._call("GET", f"search/{media_type}", params, timeout=10)
        return data.get('results', []) if data else []

    def get_details(self, media_type: str, tmdb_id: int, language: str = 'es-MX') -> dict:
        """Detalles en el idioma seleccionado."""
        params = {
            'language': language,
            'append_to_response': 'external_ids,credits,videos'
        }
        return self._call("GET", f"{media_type}/{tmdb_id}", params) or {}

    def find_by_imdb_id(self, imdb_id: str, language: str = 'es-MX') -> dict:
        """Búsqueda por ID de IMDb con idioma dinámico."""
        params = {
            'language': language,
            'external_source': 'imdb_id'
        }
        data = self._call("GET", f"find/{imdb_id}", params)
        if data:
            movies = data.get('movie_results', [])
            tv = data.get('tv_results', [])
            if movies: return {'media_type': 'movie', 'id': movies[0]['id']}
            elif tv: return {'media_type': 'tv', 'id': tv[0]['id']}
        return None

    def get_collection(self, collection_id: int, language: str = 'es-MX') -> list:
        """Obtiene todas las partes (películas) de una colección/saga."""
        params = {'language': language}
        data = self._call("GET", f"collection/{collection_id}", params)
        if data:
            parts = data.get('parts', [])
            # Inyectar media_type para compatibilidad con el buscador
            for p in parts: p['media_type'] = 'movie'
            return parts
        return []
