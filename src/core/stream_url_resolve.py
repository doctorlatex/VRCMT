"""
Resolución de URLs intermedias de APIs públicas hacia el destino real del stream.
Resolution of public API wrapper URLs to the real stream destination (e.g. YouTube).

[ES] api.u2b.cx/vrcurl/... devuelve HTML de YouTube; extraemos la URL canónica o el videoId.
[EN] api.u2b.cx/vrcurl/... returns YouTube HTML; we extract canonical watch URL or videoId.
"""
from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request

# Patrones probados en respuestas HTML grandes de YouTube embebido (~1.4 MB típico).
# Patterns tested on large embedded YouTube HTML (~1.4 MB typical).
_CANONICAL_YT = re.compile(
    r'<link\s+rel="canonical"\s+href="(https://www\.youtube\.com/watch\?v=[^"]+)"',
    re.IGNORECASE,
)
_VIDEO_ID_JSON = re.compile(r'"videoId"\s*:\s*"([a-zA-Z0-9_-]{11})"')
_WATCH_V = re.compile(r"watch\?v=([a-zA-Z0-9_-]{11})")


def _looks_u2b_vrcurl(url: str) -> bool:
    u = (url or "").lower()
    return "u2b.cx" in u and "/vrcurl/" in u


def _extract_youtube_from_html(html: str) -> str | None:
    m = _CANONICAL_YT.search(html)
    if m:
        return m.group(1).strip()
    m = _VIDEO_ID_JSON.search(html)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    m = _WATCH_V.search(html)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    return None


def resolve_u2b_vrcurl_to_youtube(
    url: str,
    *,
    timeout: float = 20.0,
    max_bytes: int = 2_000_000,
    chunk: int = 131_072,
) -> str | None:
    """
    Si `url` es api.u2b.cx/vrcurl/..., descarga HTML por streaming y devuelve
    https://www.youtube.com/watch?v=... ; si falla, devuelve None.
    """
    if not _looks_u2b_vrcurl(url):
        return None
    req = urllib.request.Request(
        url.strip(),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buf = b""
            total = 0
            while total < max_bytes:
                part = resp.read(chunk)
                if not part:
                    break
                buf += part
                total += len(part)
                try:
                    text = buf.decode("utf-8", errors="replace")
                except Exception:
                    continue
                resolved = _extract_youtube_from_html(text)
                if resolved:
                    logging.info(
                        "🔗 [u2b→YouTube] Resuelto en ~%d KB: %s → %s",
                        total // 1024,
                        (url[:72] + "…") if len(url) > 72 else url,
                        resolved,
                    )
                    return resolved
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        logging.debug("resolve_u2b_vrcurl_to_youtube: %s", e)
    except Exception as e:
        logging.debug("resolve_u2b_vrcurl_to_youtube (otro): %s", e)
    logging.debug("resolve_u2b_vrcurl_to_youtube: sin resultado para %s", url[:100])
    return None


def resolve_known_stream_api_urls(url: str) -> str:
    """
    Aplica resoluciones conocidas (u2b.cx → YouTube). Si no aplica o falla, devuelve la URL original.
    """
    r = resolve_u2b_vrcurl_to_youtube(url)
    return r if r else (url or "")
