# Stub yt-dlp para VRChat

Este directorio contiene el **código fuente** del ejecutable que VRCMT puede instalar en lugar del `yt-dlp.exe` de VRChat (`AppData\LocalLow\VRChat\VRChat\Tools\yt-dlp.exe`).

## Dependencias

```bash
pip install yt-dlp pyinstaller
```

## Build (Windows)

Desde esta carpeta:

```bash
pyinstaller --onefile --name yt-dlp stub_main.py
```

El binario queda en `dist/yt-dlp.exe`. Comprueba el SHA256 y publícalo junto con un `manifest.json` (ver `stub_release_template/manifest.example.json`).

## Comportamiento

1. Filtra argumentos `--exp-allow` (y `--exp-allow=...`) para compatibilidad con versiones de `yt-dlp` que no los implementan.
2. Si existe `%LOCALAPPDATA%\VRCMT\vrchat_stub.json` con `"cookies_file": "ruta\\archivo.txt"`, inyecta `--cookies` al inicio de los argumentos.

VRCMT escribe ese JSON desde Ajustes cuando configuras la ruta de cookies.
