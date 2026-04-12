# VRCMT — VRChat Media Tracker

> Aplicación de escritorio para llevar el control de lo que ves en VRChat.  
> Desktop app to keep track of what you watch in VRChat.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![PySide6](https://img.shields.io/badge/PySide6-6.x-green)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Tests](https://img.shields.io/badge/tests-40%20passed-brightgreen)

---

## ¿Qué es VRCMT? / What is VRCMT?

**ES:** VRCMT detecta automáticamente los videos que reproduces en VRChat leyendo los logs de la aplicación, los identifica usando TMDB, y los guarda en un catálogo local con estado de visto, calificaciones personales y comunitarias (Firebase), progreso por capítulos, y más.

**EN:** VRCMT automatically detects videos you play in VRChat by reading the application logs, identifies them using TMDB, and saves them in a local catalog with watched status, personal and community ratings (Firebase), chapter progress, and more.

---

## Características / Features

| Feature | Descripción / Description |
|---------|--------------------------|
| 🔍 Detección automática | Lee logs de VRChat en tiempo real para detectar reproducción |
| 🎬 Catálogo | Grid / lista con póster, año, tipo y progreso |
| ✅ Regla del 90% | Marca automáticamente como visto al llegar al 90% |
| ⭐ Calificaciones | Personal (local) + VRCMT comunitaria (Firebase) |
| 🗂️ Capítulos | Gestiona temporadas y episodios de series |
| 🌍 Filtros | Por tipo, mundo, género y búsqueda de texto |
| 🏅 Badges | Indicadores de visto / favorito en tarjetas |
| 📊 Estadísticas | Tiempo total, géneros, actividad mensual |
| 🔔 OTA Updates | Notificación de actualizaciones disponibles |
| 💾 Backup | Respaldo en la nube para usuarios Premium |
| 🎨 Temas | Dark y AMOLED |
| 📤 Exportar | Catálogo en CSV o JSON |
| 🖥️ System Tray | Minimiza al área de notificaciones |

---

## Requisitos / Requirements

- **Python 3.11+** (recomendado Python 3.13)
- **VRChat** instalado con logs habilitados (por defecto en `%LOCALAPPDATA%\..\LocalLow\VRChat\VRChat\`)
- Cuenta de **TMDB** para metadatos (API key gratuita en [themoviedb.org](https://www.themoviedb.org/))
- Cuenta de **Firebase** (opcional, para calificaciones comunitarias y backup Premium)
- **Discord** instalado (opcional, para Rich Presence)

---

## Instalación / Installation

### 1. Clonar el repositorio / Clone the repository

```bash
git clone https://github.com/doctorlatex/VRCMT.git
cd VRCMT
```

### 2. Crear entorno virtual / Create virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Instalar dependencias / Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configurar credenciales / Configure credentials

Crea un archivo `.env` en la raíz del proyecto (o configura desde la app en **Configuración**):

```env
TMDB_API_KEY=tu_clave_tmdb
FIREBASE_PROJECT_ID=tu_proyecto_firebase
```

> **Nota:** Las credenciales de Firebase (`serviceAccountKey.json`) deben colocarse en `assets/`.

### 5. Ejecutar / Run

```bash
python main.py
```

---

## Compilar ejecutable / Build executable

```bash
pip install pyinstaller
pyinstaller VRCMT.spec
```

El ejecutable se generará en `dist/VRCMT.exe`.

---

## Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

**40 tests unitarios** cubriendo:
- `test_scanner.py` — Detección de eventos del log de VRChat
- `test_timer.py` — Cronómetro de reproducción y regla del 90%
- `test_i18n.py` — Cobertura de traducciones ES/EN al 100%

---

## Estructura del proyecto / Project structure

```
VRCMTapp/
├── main.py                  # Punto de entrada / Entry point
├── VRCMT.spec               # Spec PyInstaller
├── requirements.txt         # Dependencias
├── assets/
│   ├── es.json              # Traducciones español
│   ├── en.json              # Traducciones inglés
│   └── icon.ico             # Icono de la app
├── src/
│   ├── api/
│   │   ├── discord_rpc.py   # Discord Rich Presence
│   │   ├── firebase_client.py # Firebase/Firestore
│   │   └── tmdb_client.py   # The Movie Database API
│   ├── core/
│   │   ├── engine.py        # Motor principal
│   │   ├── scanner.py       # Lector de logs VRChat
│   │   ├── timer.py         # Cronómetro de reproducción
│   │   ├── config.py        # Configuración y traducciones
│   │   ├── image_manager.py # Gestión de imágenes/pósters
│   │   ├── backup_manager.py # Backups locales/nube
│   │   ├── paths.py         # Rutas del sistema
│   │   ├── themes.py        # Temas QSS
│   │   └── version_check.py # Comprobación OTA
│   ├── db/
│   │   └── models.py        # Modelos Peewee (SQLite)
│   └── ui/
│       ├── main_window.py   # Ventana principal
│       ├── catalog_view.py  # Vista de catálogo
│       ├── media_modal.py   # Modal de detalle
│       ├── settings_view.py # Configuración
│       ├── stats_view.py    # Estadísticas
│       ├── about_view.py    # Acerca de
│       ├── video_player.py  # Reproductor interno
│       ├── search_dialog.py # Búsqueda
│       ├── image_viewer.py  # Visor de imágenes
│       └── toast.py         # Notificaciones toast
└── tests/
    ├── test_scanner.py
    ├── test_timer.py
    └── test_i18n.py
```

---

## Cómo funciona / How it works

1. **VRCMT** arranca y detecta la carpeta de logs de VRChat automáticamente.
2. El **RADAR** (scanner) monitorea los archivos `output_log_*.txt` en tiempo real.
3. Cuando detecta una URL de video (AVProHQ, USharpVideo, iwaSync, etc.), lanza una búsqueda en TMDB.
4. El resultado se guarda en la base de datos SQLite local.
5. El **cronómetro** mide el tiempo de reproducción. Al alcanzar el **90%** de la duración, el contenido se marca automáticamente como visto.
6. Si el reproductor del mundo emite `Media Ready info loaded` (ProTV), la duración exacta se captura directamente del log, sin depender de TMDB.

---

## Contribuir / Contributing

Pull requests bienvenidos. Por favor:
1. Haz fork del repositorio
2. Crea una rama: `git checkout -b feature/mi-mejora`
3. Ejecuta los tests: `python -m pytest tests/ -v`
4. Envía tu PR

---

## Licencia / License

MIT License — ver [LICENSE](LICENSE) para más detalles.

---

*Hecho con ❤️ para la comunidad de VRChat / Made with ❤️ for the VRChat community*
