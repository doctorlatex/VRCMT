import sys
import os
import logging
import ctypes
import faulthandler
from logging.handlers import TimedRotatingFileHandler
from PySide6.QtWidgets import QApplication, QMessageBox
from src.core.engine import VRCMTEngine
from src.ui.main_window import MainWindow

# --- MEJORA v2.11.16: BLINDAJE ABSOLUTO (FAULTHANDLER) ---
# Se activa tras crear log_dir para escribir volcados nativos (segfault) en disco.

# --- MEJORA v2.10.0: BLOQUEO DE INSTANCIA ÚNICA (MUTEX) ---
APP_MUTEX_NAME = "Global\\VRCMediaTracker_v2_SingleInstance_Mutex"
kernel32 = ctypes.windll.kernel32
mutex = kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
last_error = kernel32.GetLastError()

if last_error == 183:  # ERROR_ALREADY_EXISTS
    app_dummy = QApplication(sys.argv)
    QMessageBox.warning(None, "VRChat Media Tracker", "¡La aplicación ya está abierta!\nRevisa tu bandeja de sistema o barra de tareas.\n\nThe application is already running!")
    sys.exit(0)

# --- MEJORA v2.11.11: OPTIMIZACIÓN DE RENDIMIENTO Y LOGS ---
log_dir = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'VRCMT')
os.makedirs(log_dir, exist_ok=True)

try:
    _fh_native = open(os.path.join(log_dir, "native_trace.log"), "a", encoding="utf-8")
    faulthandler.enable(file=_fh_native, all_threads=True)
except Exception:
    faulthandler.enable(all_threads=True)

# Forzar salida en UTF-8 para evitar errores de codificación en consola de Windows (UnicodeEncodeError)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

log_level = logging.DEBUG if os.environ.get('DEBUG') == '1' else logging.INFO

logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] %(message)s',
    handlers=[
        TimedRotatingFileHandler(os.path.join(log_dir, 'tracker_v2.log'), when="D", interval=1, backupCount=7, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logging.getLogger('guessit').setLevel(logging.WARNING)
logging.getLogger('rebulk').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('firebase_admin').setLevel(logging.WARNING)
logging.getLogger('google').setLevel(logging.WARNING)


def _global_excepthook(exc_type, exc, tb):
    try:
        logging.critical(
            "Excepción no capturada en hilo principal",
            exc_info=(exc_type, exc, tb),
        )
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _global_excepthook


def _resolve_icon_path() -> str:
    """Resuelve la ruta del ícono tanto en modo script como en ejecutable PyInstaller.
    Resolves icon path both in script mode and as a PyInstaller executable."""
    candidates = []
    # Cuando se ejecuta como exe PyInstaller (MEIPASS = directorio temporal con datos)
    # When running as PyInstaller exe (MEIPASS = temporary directory with data files)
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        candidates.append(os.path.join(sys._MEIPASS, 'logo_tracker.ico'))
    # Cuando se ejecuta como script Python
    # When running as Python script
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logo_tracker.ico'))
    candidates.append(os.path.join(os.path.abspath('.'), 'logo_tracker.ico'))
    for p in candidates:
        if os.path.isfile(p):
            return p
    return ""


def main():
    from src.core.version_check import CURRENT_VERSION as _VER
    logging.info("🏁 Iniciando VRCMT v%s - Next Generation", _VER)

    # Establecer AppUserModelID para que Windows muestre el ícono correcto en la barra de tareas
    # (Debe llamarse ANTES de crear QApplication)
    # Set AppUserModelID so Windows shows the correct icon in the taskbar
    # (Must be called BEFORE creating QApplication)
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "VRCMT.VRChatMediaTracker.App.2"
        )
    except Exception:
        pass
    
    # 1. Inicializar la Interfaz Gráfica (Debe ser lo primero)
    from PySide6.QtCore import Qt

    app = QApplication.instance()
    if not app:
        # Reduce HWND hermanos nativos (artefactos en Windows junto al cartel / scrolls).
        QApplication.setAttribute(
            Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings, True
        )
        app = QApplication(sys.argv)

    # Tope de hilos del pool global: carteles/modal usan QRunnable + OpenSSL; demasiada
    # paralelización en Windows/Python 3.13 coincide con access violation en native_trace.log.
    from PySide6.QtCore import QThreadPool

    _tp = QThreadPool.globalInstance()
    _tp.setMaxThreadCount(max(2, min(4, _tp.maxThreadCount())))
    
    app.setStyle("Fusion")

    # Ícono de la aplicación en ventana y barra de tareas de Windows
    # Application icon for window title bar and Windows taskbar
    try:
        from PySide6.QtGui import QIcon
        _icon_path = _resolve_icon_path()
        if _icon_path:
            app.setWindowIcon(QIcon(_icon_path))
            logging.debug("Ícono de app cargado desde: %s", _icon_path)
        else:
            logging.debug("logo_tracker.ico no encontrado; se usará ícono genérico.")
    except Exception as _ie:
        logging.debug("No se pudo aplicar ícono de app: %s", _ie)

    # N6: Aplicar tema desde configuración
    try:
        from src.core.config import ConfigManager as _CM
        from src.core.themes import get_theme as _get_theme
        _theme_name = _CM().get_val('theme', 'Oscuro')
        _qss = _get_theme(_theme_name)
        if _qss:
            app.setStyleSheet(_qss)
    except Exception as _te:
        logging.debug("Tema no aplicado: %s", _te)

    # 2. Inicializar el motor y la ventana principal
    # Mantener referencias globales para evitar que el Garbage Collector las limpie (v2.11.16)
    global engine, main_window
    engine = VRCMTEngine()
    engine.start()
    
    main_window = MainWindow(engine)
    main_window.show()
    
    # 3. Ciclo de vida de la aplicación
    try:
        exit_code = app.exec()
    except Exception as e:
        logging.critical(f"💥 Error fatal en el bucle principal: {e}")
        exit_code = 1
    
    logging.info("🔌 Cerrando VRCMT v%s...", _VER)
    
    # Limpieza Quirúrgica Pro activa (GitHub Best Practices)
    if hasattr(engine, 'stop'):
        engine.stop()
        
    # --- MEJORA v3.6.8: APAGADO DE EMERGENCIA ---
    # En Python 3.13, los hilos de gRPC (Firebase) causan Access Violation si se limpian convencionalmente
    # No forzamos el garbage collector (gc.collect()) porque destruye objetos en uso por hilos C++ de fondo.
    logging.info("🔌 Forzando cierre limpio de procesos...")
    os._exit(exit_code)

if __name__ == "__main__":
    main()
