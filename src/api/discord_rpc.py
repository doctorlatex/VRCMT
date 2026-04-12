import os
import json
import time
import uuid
import base64
import hashlib
import logging
import platform
import threading
import urllib.parse
import webbrowser
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from pypresence import Presence
import pypresence.exceptions
from src.core.paths import APP_DIR, resource_path

# Configuración Discord
CLIENT_ID = "1477259462589812756"
CLIENT_SECRET = "I6cq2IJ7i3J2rG80irBgc071v265i4eX"
REDIRECT_URI = "http://localhost:3000/callback"
PORT = 3000

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/callback"):
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            if 'code' in params:
                self.server.auth_code = params['code'][0]
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                
                html = """
                <!DOCTYPE html>
                <html lang="es">
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Discord - Autorización VRCMT</title>
                    <style>
                        @import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;600;700&display=swap');
                        body {
                            background-color: #313338;
                            color: #F2F3F5;
                            font-family: 'Open Sans', Helvetica, Arial, sans-serif;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            height: 100vh;
                            margin: 0;
                        }
                        .card {
                            background-color: #2B2D31;
                            padding: 32px;
                            border-radius: 8px;
                            box-shadow: 0 2px 10px rgba(0,0,0,0.2);
                            text-align: center;
                            max-width: 420px;
                            width: 90%;
                        }
                        .icon-container {
                            background-color: #23A559;
                            border-radius: 50%;
                            width: 64px;
                            height: 64px;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            margin: 0 auto 20px;
                        }
                        .icon-container svg {
                            width: 40px;
                            height: 40px;
                            fill: white;
                        }
                        h1 { font-size: 24px; margin: 0 0 10px; font-weight: 700; }
                        p { font-size: 14px; color: #B5BAC1; margin: 0 0 24px; line-height: 1.5; }
                        .btn {
                            background-color: #5865F2;
                            color: white;
                            border: none;
                            padding: 10px 24px;
                            border-radius: 3px;
                            font-size: 14px;
                            font-weight: 600;
                            cursor: pointer;
                            transition: background-color 0.2s;
                            width: 100%;
                        }
                        .btn:hover { background-color: #4752C4; }
                    </style>
                </head>
                <body>
                    <div class="card">
                        <div class="icon-container">
                            <svg viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>
                        </div>
                        <h1>Autorización Exitosa / Authorization Successful</h1>
                        <p>Tu cuenta de Discord se ha vinculado correctamente a VRChat Media Tracker.<br><br>Ya puedes volver a la aplicación.<br>You can now return to the application.</p>
                        <button id="closeBtn" class="btn" onclick="attemptClose()">Cerrar Pestaña / Close Tab</button>
                    </div>
                    <script>
                        function attemptClose() {
                            try {
                                window.open('', '_parent', '');
                                window.close();
                            } catch(e) {}
                            setTimeout(function() {
                                var btn = document.getElementById('closeBtn');
                                btn.innerText = "Por favor cierra en la X superior / Please close via the top X";
                                btn.style.backgroundColor = "#4752C4";
                                btn.style.cursor = "default";
                            }, 200);
                        }
                        setTimeout(attemptClose, 4000);
                    </script>
                </body>
                </html>
                """
                self.wfile.write(html.encode('utf-8'))
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Error: No code provided")
            threading.Thread(target=self.server.shutdown).start()
    def log_message(self, format, *args): pass

def _derive_hwid_key() -> bytes:
    """Deriva una clave Fernet de 32 bytes a partir del HWID de la máquina.
    No se escribe ningún archivo de clave; la misma máquina siempre produce
    la misma clave, pero en una máquina diferente el archivo .enc es ilegible.
    """
    # Combinar dirección MAC y nombre de host como identificador de máquina.
    hwid_material = f"{uuid.getnode()}:{platform.node()}".encode("utf-8")
    salt = b"VRCMT_HWID_SALT_v1"  # salt fijo y público; la entropía viene del HWID
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=260_000)
    return base64.urlsafe_b64encode(kdf.derive(hwid_material))


class DiscordManager:
    def __init__(self):
        self.key_path = os.path.join(APP_DIR, 'cipher.key')  # kept for migration only
        self.id_path = os.path.join(APP_DIR, 'discord_id.enc')
        self.fernet = self._init_encryption()
        
        self.rpc = None
        self.connected = False
        self.last_reconnect_attempt = 0
        self.stop_event = threading.Event()
        
        # Estado actual en memoria
        self._state_lock = threading.Lock()
        self.current_details = None
        self.current_state_msg = None
        self.current_start_time = None
        self.needs_update = False

        # Iniciar el Worker (Hilo a prueba de balas para evitar cuelgues)
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def _init_encryption(self) -> Fernet:
        """Devuelve un Fernet cuya clave se deriva del HWID de la máquina.
        Si existe un cipher.key heredado, intenta re-cifrar discord_id.enc con la
        nueva clave derivada y luego elimina el archivo de clave para no dejarlo
        expuesto en disco.
        """
        hwid_key = _derive_hwid_key()
        new_fernet = Fernet(hwid_key)

        # --- Migración desde cipher.key heredado ---
        if os.path.exists(self.key_path):
            try:
                with open(self.key_path, 'rb') as f:
                    old_key = f.read()
                old_fernet = Fernet(old_key)

                if os.path.exists(self.id_path):
                    with open(self.id_path, 'rb') as f:
                        old_enc = f.read()
                    plaintext = old_fernet.decrypt(old_enc)
                    with open(self.id_path, 'wb') as f:
                        f.write(new_fernet.encrypt(plaintext))
                    logging.info("🔑 discord_id.enc migrado a clave derivada de HWID.")

                os.remove(self.key_path)
                logging.info("🗑️ cipher.key heredado eliminado (ya no necesario).")
            except Exception as e:
                logging.warning(f"⚠️ No se pudo migrar cipher.key: {e}. Se usará HWID directamente.")

        return new_fernet

    def login(self):
        """Lanza el portal OAuth2 para vincular la cuenta (v2.0)."""
        import urllib.parse
        server = HTTPServer(('localhost', PORT), OAuthCallbackHandler)
        auth_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={urllib.parse.quote(REDIRECT_URI)}&response_type=code&scope=identify"
        webbrowser.open(auth_url)
        server.serve_forever()
        
        if hasattr(server, 'auth_code'):
            try:
                # 1. Intercambiar código por Token
                data = {
                    'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
                    'grant_type': 'authorization_code', 'code': server.auth_code,
                    'redirect_uri': REDIRECT_URI
                }
                headers = {'Content-Type': 'application/x-www-form-urlencoded'}
                resp = requests.post('https://discord.com/api/oauth2/token', data=data, headers=headers)
                token = resp.json().get('access_token')

                if token:
                    # 2. Obtener Info del Usuario
                    import urllib.request
                    req = urllib.request.Request(
                        'https://discord.com/api/users/@me', 
                        headers={
                            'Authorization': f'Bearer {token}',
                            'User-Agent': 'DiscordBot (https://github.com/vrcmt, 2.0)'
                        }
                    )
                    with urllib.request.urlopen(req, timeout=5) as user_resp:
                        user_data = json.loads(user_resp.read().decode('utf-8'))
                    d_id = user_data.get('id')
                    username = user_data.get('username')

                    if d_id:
                        # 3. Guardar cifrado
                        payload = json.dumps({"id": d_id, "username": username}).encode()
                        with open(self.id_path, 'wb') as f:
                            f.write(self.fernet.encrypt(payload))
                        
                        logging.info(f"🎮 Discord vinculado: {username} ({d_id})")
                        return {"id": d_id, "username": username}
            except Exception as e:
                logging.error(f"Error en flujo OAuth Discord: {e}")
        return None

    def get_saved_id(self):
        """Recupera el ID de Discord guardado y descifrado."""
        if not os.path.exists(self.id_path): 
            logging.debug("🔍 Discord ID file not found.")
            return None
        try:
            with open(self.id_path, 'rb') as f:
                content = f.read()
            try:
                decrypted = self.fernet.decrypt(content)
            except InvalidToken:
                # La clave HWID no coincide: el archivo fue cifrado en otra máquina.
                # Eliminamos el archivo corrupto para que el usuario pueda re-loguearse.
                logging.warning(
                    "⚠️ [HWID] discord_id.enc fue cifrado en una máquina diferente o con una "
                    "clave antigua. Se eliminará para permitir un nuevo inicio de sesión con Discord."
                )
                try:
                    os.remove(self.id_path)
                except OSError:
                    pass
                return None
            data = json.loads(decrypted)
            d_id = data.get('id')
            logging.debug(f"🔍 Discord ID recuperado: {d_id}")
            return d_id
        except Exception as e: 
            logging.error(f"❌ Error al recuperar Discord ID: {e}")
            return None

    def get_saved_username(self, config=None):
        """Recupera el Nombre de Discord guardado."""
        guest_lbl = config.tr('lbl_guest', "Invitado") if config else "Invitado"
        if not os.path.exists(self.id_path): return guest_lbl
        try:
            with open(self.id_path, 'rb') as f:
                raw = f.read()
            try:
                decrypted = self.fernet.decrypt(raw)
            except InvalidToken:
                return guest_lbl
            return json.loads(decrypted).get('username', 'Usuario')
        except Exception:
            return "Usuario"

    def update_presence(self, details: str, state: str, start_time: float = None):
        """Método público para actualizar la presencia de forma segura desde la UI o Motor."""
        with self._state_lock:
            # --- MEJORA v2.11.18: BLINDAJE DE LONGITUD DISCORD RPC ---
            # Reducir límite a 100 caracteres para evitar crashes por bytes de emojis
            self.current_details = (details[:100] + "...") if details and len(details) > 100 else details
            self.current_state_msg = (state[:100] + "...") if state and len(state) > 100 else state
            self.current_start_time = int(start_time or time.time())
            self.needs_update = True

    def clear_presence(self):
        """Limpia el estado actual."""
        with self._state_lock:
            self.current_details = None
            self.current_state_msg = None
            self.current_start_time = None
            self.needs_update = True

    def _worker_loop(self):
        """Ciclo de vida asíncrono para gestionar la conexión y reconexión a Discord sin congelar UI."""
        while not self.stop_event.is_set():
            if not self.connected:
                self._connect()
            else:
                self._process_updates()
            
            time.sleep(2)
            
        if self.rpc and self.connected:
            try:
                self.rpc.close()
            except Exception as e:
                logging.debug(f"Discord RPC close: {e}")

    def _connect(self):
        """Intenta conectar con la app de Discord en segundo plano."""
        now = time.time()
        if now - self.last_reconnect_attempt < 15:
            return
            
        self.last_reconnect_attempt = now
        try:
            # CIRUGÍA: Aislamiento total de Event Loop para evitar colisiones
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            self.rpc = Presence(CLIENT_ID, loop=loop)
            self.rpc.connect()
            self.connected = True
            logging.info("🎮 Discord Rich Presence: Conectado y listo.")
            
            with self._state_lock:
                self.needs_update = True
                
        except pypresence.exceptions.DiscordNotFound:
            self.connected = False
            self.rpc = None
        except Exception as e:
            self.connected = False
            self.rpc = None
            logging.debug(f"Discord RPC Conexión omitida: {e}")

    def _process_updates(self):
        """Aplica las actualizaciones a Discord de manera segura."""
        with self._state_lock:
            if not self.needs_update: return
            details = self.current_details
            state = self.current_state_msg
            start_time = self.current_start_time
            self.needs_update = False
            
        try:
            if details or state:
                self.rpc.update(
                    details=details,
                    state=state,
                    start=start_time,
                    large_image="logo_tracker",
                    large_text="VRCMT v2.0"
                )
            else:
                self.rpc.clear()
        except pypresence.exceptions.InvalidPipe:
            self.connected = False
            logging.info("🎮 Discord cerrado, desconectando RPC...")
        except Exception as e:
            self.connected = False
            logging.debug(f"Discord RPC Error en actualización: {e}")

    def stop(self):
        self.stop_event.set()
