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


def main():
    logging.info("🏁 Iniciando VRCMT v2.0 - Next Generation")
    
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
    
    logging.info("🔌 Cerrando VRCMT v2.0...")
    
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
