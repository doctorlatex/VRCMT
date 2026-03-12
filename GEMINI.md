# 🤖 VRChat Media Tracker - Mapa de Desarrollo

## 🚀 Versión Actual: v1.16 (Release Candidate)

### 🛠 Mejoras Críticas Implementadas (Marzo 2026)

#### 1. El Actualizador Indestructible (v1.16)
- **Nombres Versionados:** El ejecutable se descarga con su versión (`VRCMT_v1.16.exe`), evitando conflictos de archivos bloqueados por Windows.
- **Limpieza Asíncrona (Background):** La limpieza de versiones antiguas ocurre 10 segundos después del arranque en un hilo separado. Esto evita el error de "No responde" en la interfaz.
- **Buscador Flexible:** El sistema ahora busca cualquier archivo `.exe` en los assets de GitHub, eliminando la dependencia de un nombre de archivo rígido.

#### 2. Blindaje de Detección (v1.14 - v1.16)
- **Escaneo de Emergencia:** Detección agresiva de patrones `Txx EPxx`, `Sxx Exx` y `Cap.x`.
- **Extractor por Ruta:** Inteligencia para leer nombres de carpetas en la URL si el archivo es genérico (ej: detecta "TED" desde la ruta).
- **Type Shielding:** Protección total para que las series no sean clasificadas como películas por error de la API.

#### 3. Estabilidad y Seguridad
- **Escudo de 50MB:** Prevención de crashes por lectura de logs masivos de VRChat.
- **Mutex de Instancia Única:** Garantiza que solo un proceso del tracker esté activo a la vez.
- **Rotación de Logs:** Mantenimiento automático de logs del proyecto (7 días).

### 📁 Estructura del Proyecto
- `launcher.py`: Gestión de actualizaciones OTA y arranque unificado.
- `main.py`: Orquestador del radar y la UI (Modo Unificado).
- `parser_handler.py`: Motor de extracción de metadatos.
- `api_handler.py`: Enriquecimiento bilingüe con blindaje de tipo.

---

## 📜 Declaración de Autoría (Mandato Permanente)
Este proyecto ha sido **desarrollado 100% mediante Inteligencia Artificial**, bajo la visión, dirección y supervisión técnica de **doctorlatex**. 

- **Código:** Generado íntegramente por IA (Gemini CLI).
- **Dirección Artística y Funcional:** doctorlatex.
- **Crédito de Programación:** 100% IA. 
- **Ubicación del Crédito:** Este aviso debe incluirse en la documentación (`GEMINI.md`) y en las notas de cada versión (*Release Notes*) en GitHub. La interfaz de la aplicación (GUI) debe mantenerse limpia de este aviso por elección del autor.

---
*Documento actualizado v1.16 - Gemini CLI.*
