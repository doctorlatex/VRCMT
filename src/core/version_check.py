import threading
import logging

# --- Flujo de versión / Release workflow (OTA) ---
# [ES] 1) Las mejoras pueden ser acumulativas en local: subes a GitHub UNA sola vez bajo un número.
#     2) Antes de publicar a usuarios: sube `version.txt` en GitHub (rama `master` para esta URL)
#        con el MISMO número que `CURRENT_VERSION` del ejecutable que vas a distribuir.
#     3) La rama `main` del repo público debe llevar el mismo `version.txt` para que la web
#        de GitHub no muestre una versión vieja (paridad con `master`).
# [EN] 1) Improvements can accumulate locally; you push once under one version number.
#     2) Before users get the update: push `version.txt` on GitHub (`master` for this URL)
#        with the SAME value as `CURRENT_VERSION` in the build you ship.
#     3) Keep public repo `main` branch `version.txt` in sync so the GitHub UI matches `master`.
CURRENT_VERSION = "2.0.15"
_DEFAULT_VERSION_URL = "https://raw.githubusercontent.com/doctorlatex/VRCMT/master/version.txt"


def _version_tuple(v):
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except Exception:
        return (0,)


def check_for_updates(callback, timeout=8, custom_url=None):
    url = (custom_url or _DEFAULT_VERSION_URL).strip()
    if not url:
        url = _DEFAULT_VERSION_URL

    def _run():
        try:
            import urllib.request
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "VRCMT-OTA/2.0",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    remote = resp.read().decode("utf-8-sig", errors="ignore").strip()
                    logging.info(
                        "[OTA] version remota: %s | version local: %s",
                        remote, CURRENT_VERSION,
                    )
                    if remote and _version_tuple(remote) > _version_tuple(CURRENT_VERSION):
                        logging.info("[OTA] Nueva version disponible: %s → activando banner", remote)
                        callback(remote)
                        return
                    else:
                        logging.info("[OTA] Version al dia. Sin actualizacion.")
        except Exception as e:
            logging.warning("[OTA] Error al verificar actualizacion: %s", e)
        callback(None)

    threading.Thread(target=_run, daemon=True, name="VRCMT-OTA").start()