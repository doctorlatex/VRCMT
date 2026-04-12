"""
Gestión del stub yt-dlp para VRChat: respaldo, instalación, restauración y actualización vía manifest.
El ejecutable stub (PyInstaller) vive en stub_src/; el binario publicado se descarga desde manifest.json.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import tempfile
import urllib.request
from typing import Any, Callable, Optional, Tuple

from src.core.paths import APP_DIR

# URL pública del manifest del stub — repositorio dedicado VRCMT-stub
# (puede sobreescribirse desde Ajustes → URL manifest stub)
_DEFAULT_STUB_MANIFEST_URL = (
    "https://raw.githubusercontent.com/doctorlatex/VRCMT-stub/main/manifest.json"
)

# Ruta típica del yt-dlp que VRChat invoca (Windows)
def default_vrchat_tools_ytdlp_path() -> str:
    local_low = os.environ.get("LOCALAPPDATA", "")
    if not local_low:
        return ""
    # LOCALAPPDATA = ...\AppData\Local → LocalLow es hermano
    base = os.path.dirname(local_low)
    return os.path.join(base, "LocalLow", "VRChat", "VRChat", "Tools", "yt-dlp.exe")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def _cmp_version(a: str, b: str) -> int:
    """-1 si a<b, 0 igual, 1 si a>b (solo prefijo semver mayor.menor.patch)."""
    def parse(v: str) -> Tuple[int, int, int]:
        v = (v or "").strip()
        m = _SEMVER_RE.match(v)
        if not m:
            return (0, 0, 0)
        return int(m.group(1)), int(m.group(2)), int(m.group(3))

    pa, pb = parse(a), parse(b)
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


class VRChatYtStubManager:
    STATE_NAME = "vrchat_ytdlp_stub_state.json"
    SIDECAR_NAME = "vrchat_stub.json"
    CACHE_NAME = "vrchat_stub_last.exe"

    def __init__(self, config):
        self.config = config
        self.app_dir = APP_DIR
        self.backups_dir = os.path.join(self.app_dir, "backups")
        os.makedirs(self.backups_dir, exist_ok=True)
        self.state_path = os.path.join(self.app_dir, self.STATE_NAME)
        self.sidecar_path = os.path.join(self.app_dir, self.SIDECAR_NAME)
        self.cache_path = os.path.join(self.app_dir, self.CACHE_NAME)
        self.target_exe = self.config.get_val("vrchat_ytdlp_target_path", "") or default_vrchat_tools_ytdlp_path()

    def _load_state(self) -> dict:
        if not os.path.isfile(self.state_path):
            return {}
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("No se pudo leer estado del stub VRChat: %s", e)
            return {}

    def _save_state(self, data: dict) -> None:
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logging.error("No se pudo guardar estado del stub VRChat: %s", e)

    def write_sidecar_from_config(self) -> None:
        """El stub lee cookies_file desde este JSON (misma carpeta VRCMT)."""
        cookies = (self.config.get_val("vrchat_stub_cookies_path", "") or "").strip()
        payload = {"cookies_file": cookies} if cookies else {"cookies_file": ""}
        try:
            with open(self.sidecar_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logging.warning("No se pudo escribir sidecar del stub: %s", e)

    def is_enabled(self) -> bool:
        return bool(self.config.get_val("vrchat_stub_enabled", False))

    def get_status_text(self) -> str:
        st = self._load_state()
        tgt = self.target_exe
        if not tgt or not os.path.isfile(tgt):
            return "yt-dlp VRChat: ruta no encontrada o archivo ausente."
        h = _sha256_file(tgt)
        ver = st.get("installed_version", "—")
        active = st.get("stub_active", False)
        match = st.get("installed_sha256") == h if st.get("installed_sha256") else False
        return (
            f"Activo (config): {'sí' if self.is_enabled() else 'no'} | "
            f"Stub instalado (estado): {'sí' if active else 'no'} | "
            f"Versión manifest: {ver} | "
            f"SHA256 actual del exe: {h[:16]}… | "
            f"Coincide con última instalación: {'sí' if match else 'no'}"
        )

    def get_friendly_status_summary(self) -> str:
        """Una línea clara para la UI (usa config.tr si existe)."""
        tr = getattr(self.config, "tr", None)
        if not callable(tr):
            def tr(key, default=None):
                return default or key

        if not self.is_enabled():
            return tr("stub_sum_disabled", "La mejora está desactivada. Activa la casilla de arriba y pulsa «Guardar» si quieres usarla.")

        tgt = self.target_exe
        if not tgt or not os.path.isfile(tgt):
            return tr("stub_sum_no_exe", "No encuentro el programa de VRChat (yt-dlp). Abre VRChat al menos una vez o revisa la ruta en opciones avanzadas.")

        st = self._load_state()
        active = st.get("stub_active", False)
        expected = st.get("installed_sha256")
        h = _sha256_file(tgt).lower()
        match = bool(expected and expected.lower() == h)

        if active and match:
            return tr("stub_sum_ok", "Estado: todo listo. La mejora está instalada.")
        if active and not match:
            return tr("stub_sum_reswap", "Atención: VRChat pudo cambiar el archivo. Pulsa «Descargar e instalar» o reinicia VRCMT para intentar corregirlo.")
        return tr("stub_sum_not_installed", "Has activado la mejora pero falta instalarla. Pulsa «Descargar e instalar» o «Ya tengo el archivo…».")

    def backup_original_if_needed(self) -> Tuple[bool, str]:
        if not self.target_exe or not os.path.isfile(self.target_exe):
            return False, "No existe yt-dlp.exe de VRChat en la ruta esperada."
        st = self._load_state()
        if st.get("backup_filename"):
            bp = os.path.join(self.backups_dir, st["backup_filename"])
            if os.path.isfile(bp):
                return True, "Respaldo ya existente."

        digest = _sha256_file(self.target_exe)[:16]
        name = f"yt-dlp_vrchat_original_{digest}.exe"
        dest = os.path.join(self.backups_dir, name)
        try:
            shutil.copy2(self.target_exe, dest)
        except Exception as e:
            return False, f"No se pudo copiar respaldo: {e}"
        st["backup_filename"] = name
        st.setdefault("stub_active", False)
        self._save_state(st)
        return True, f"Respaldo guardado: {name}"

    def _backup_path(self) -> Optional[str]:
        st = self._load_state()
        fn = st.get("backup_filename")
        if not fn:
            return None
        p = os.path.join(self.backups_dir, fn)
        return p if os.path.isfile(p) else None

    def _atomic_replace(self, src_file: str, dst_file: str) -> None:
        ddir = os.path.dirname(dst_file) or "."
        fd, tmp = tempfile.mkstemp(suffix=".exe.tmp", dir=ddir)
        os.close(fd)
        try:
            shutil.copy2(src_file, tmp)
            os.replace(tmp, dst_file)
        finally:
            if os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def install_stub_from_file(self, src_exe: str) -> Tuple[bool, str]:
        if not os.path.isfile(src_exe):
            return False, "El archivo del stub no existe."
        ok, msg = self.backup_original_if_needed()
        if not ok:
            return False, msg
        try:
            self._atomic_replace(src_exe, self.target_exe)
        except Exception as e:
            return False, f"No se pudo reemplazar yt-dlp.exe (¿VRChat en uso?): {e}"
        h = _sha256_file(self.target_exe)
        st = self._load_state()
        st["stub_active"] = True
        st["installed_sha256"] = h
        st["installed_version"] = st.get("installed_version", "local")
        self._save_state(st)
        try:
            shutil.copy2(self.target_exe, self.cache_path)
        except Exception as e:
            logging.debug("Cache stub: %s", e)
        self.write_sidecar_from_config()
        return True, "Stub instalado."

    def install_stub_from_bytes(self, data: bytes, version: str = "") -> Tuple[bool, str]:
        if not self.target_exe:
            return False, "Ruta destino no configurada."
        ddir = os.path.dirname(self.target_exe)
        if not os.path.isdir(ddir):
            try:
                os.makedirs(ddir, exist_ok=True)
            except Exception as e:
                return False, f"No se puede crear carpeta Tools: {e}"
        fd, tmp = tempfile.mkstemp(suffix=".exe", dir=ddir)
        os.close(fd)
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            ok, msg = self.backup_original_if_needed()
            if not ok:
                return False, msg
            os.replace(tmp, self.target_exe)
        except Exception as e:
            if os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return False, f"Instalación fallida: {e}"
        h = _sha256_bytes(data)
        st = self._load_state()
        st["stub_active"] = True
        st["installed_sha256"] = h
        if version:
            st["installed_version"] = version
        self._save_state(st)
        try:
            with open(self.cache_path, "wb") as f:
                f.write(data)
        except Exception as e:
            logging.debug("Cache stub: %s", e)
        self.write_sidecar_from_config()
        return True, f"Stub instalado (v{version or 'desconocida'})."

    def restore_original(self) -> Tuple[bool, str]:
        bp = self._backup_path()
        if not bp:
            return False, "No hay respaldo del yt-dlp original."
        if not self.target_exe:
            return False, "Ruta destino no configurada."
        try:
            self._atomic_replace(bp, self.target_exe)
        except Exception as e:
            return False, f"No se pudo restaurar (¿VRChat en uso?): {e}"
        st = self._load_state()
        st["stub_active"] = False
        st["installed_sha256"] = _sha256_file(self.target_exe)
        self._save_state(st)
        return True, "yt-dlp original de VRChat restaurado."

    def _http_headers(self) -> dict:
        token = (self.config.get_val("vrchat_stub_github_token", "") or "").strip()
        h = {
            "User-Agent": "VRCMT-StubUpdater/1.0",
            "Accept": "application/vnd.github+json",
        }
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _fetch_bytes(self, url: str) -> bytes:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers=self._http_headers())
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
            return resp.read()

    def fetch_manifest(self, manifest_url: str) -> dict:
        raw = self._fetch_bytes(manifest_url.strip())
        return json.loads(raw.decode("utf-8"))

    def update_from_manifest(self, force: bool = False) -> Tuple[bool, str]:
        url = (self.config.get_val("vrchat_stub_manifest_url", "") or "").strip()
        if not url:
            url = _DEFAULT_STUB_MANIFEST_URL  # usar URL pública por defecto
        try:
            man = self.fetch_manifest(url)
        except Exception as e:
            return False, f"No se pudo leer manifest: {e}"
        version = str(man.get("version", "")).strip()
        dl = (man.get("download_url") or man.get("url") or "").strip()
        expect_hash = (man.get("sha256") or "").strip().lower()
        if not dl or not expect_hash:
            return False, "Manifest incompleto (falta download_url/url o sha256)."
        st = self._load_state()
        if not force and version and _cmp_version(version, str(st.get("installed_version", ""))) <= 0:
            return True, f"Ya tienes la versión {st.get('installed_version')} (manifest {version})."
        try:
            data = self._fetch_bytes(dl)
        except Exception as e:
            return False, f"Descarga fallida: {e}"
        got = _sha256_bytes(data).lower()
        if got != expect_hash:
            return False, "SHA256 no coincide; abortado por seguridad."
        return self.install_stub_from_bytes(data, version=version)

    def ensure_stub_if_enabled(self) -> None:
        """Si el modo está activo y VRChat sustituyó el exe, reinstalar desde caché o manifest."""
        if not self.is_enabled():
            return
        self.write_sidecar_from_config()
        if not self.target_exe or not os.path.isfile(self.target_exe):
            return
        st = self._load_state()
        if not st.get("stub_active"):
            return
        expected = st.get("installed_sha256")
        if not expected:
            return
        current = _sha256_file(self.target_exe).lower()
        if current == expected.lower():
            return
        if os.path.isfile(self.cache_path):
            ch = _sha256_file(self.cache_path).lower()
            if ch == expected.lower():
                try:
                    ok, msg = self.backup_original_if_needed()
                    if not ok and "No existe" not in msg:
                        logging.warning("Stub reswap: %s", msg)
                    self._atomic_replace(self.cache_path, self.target_exe)
                    logging.info("Stub VRChat reinstalado desde caché (VRChat había sustituido el exe).")
                    return
                except Exception as e:
                    logging.warning("No se pudo reinstalar stub desde caché: %s", e)
        manifest = (self.config.get_val("vrchat_stub_manifest_url", "") or "").strip()
        if manifest:
            ok, msg = self.update_from_manifest(force=True)
            logging.info("Stub VRChat re-descargado: %s — %s", ok, msg)
