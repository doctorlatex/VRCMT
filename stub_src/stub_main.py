"""
Entry point para el ejecutable que sustituye a yt-dlp.exe en VRChat/Tools.
- Quita flags --exp-allow que el yt-dlp embebido de VRChat puede pasar y que PyPI yt-dlp antiguo no entiende.
- Lee cookies desde %LOCALAPPDATA%\\VRCMT\\vrchat_stub.json (clave cookies_file).
Empaquetar con PyInstaller apuntando a este módulo; el nombre del .exe debe ser yt-dlp.exe.
"""
from __future__ import annotations

import json
import os
import sys


def _sidecar_path() -> str:
    la = os.environ.get("LOCALAPPDATA", "")
    if not la:
        return ""
    return os.path.join(la, "VRCMT", "vrchat_stub.json")


def _filter_exp_allow(argv: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    n = len(argv)
    while i < n:
        a = argv[i]
        if a == "--exp-allow":
            i += 1
            if i < n and not argv[i].startswith("-"):
                i += 1
            continue
        if a.startswith("--exp-allow="):
            i += 1
            continue
        out.append(a)
        i += 1
    return out


def _inject_cookies(argv: list[str], cookies_path: str) -> list[str]:
    if not cookies_path or not os.path.isfile(cookies_path):
        return argv
    return [argv[0], "--cookies", cookies_path] + argv[1:]


def main() -> int:
    argv = _filter_exp_allow(list(sys.argv))
    cookies = ""
    sp = _sidecar_path()
    if sp and os.path.isfile(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                data = json.load(f)
            cookies = (data.get("cookies_file") or "").strip()
        except Exception:
            pass
    argv = _inject_cookies(argv, cookies)
    sys.argv = argv
    import yt_dlp

    return yt_dlp.main()


if __name__ == "__main__":
    raise SystemExit(main())
