import threading
import logging

CURRENT_VERSION = "2.0.6"
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
            req = urllib.request.Request(url, headers={"User-Agent": "VRCMT-OTA/2.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    remote = resp.read().decode("utf-8-sig", errors="ignore").strip()
                    if remote and _version_tuple(remote) > _version_tuple(CURRENT_VERSION):
                        logging.info("OTA: nueva version %s disponible", remote)
                        callback(remote)
                        return
        except Exception as e:
            logging.debug("OTA check: %s", e)
        callback(None)

    threading.Thread(target=_run, daemon=True, name="VRCMT-OTA").start()