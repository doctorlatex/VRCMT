# 🗺️ Hoja de Ruta Maestra - VRCMT v2.0

## 🟢 Fases 1 y 2: Motor y Núcleo (¡COMPLETADAS!)
- [x] Mutex (Anti-doble instancia) y rotación de logs.
- [x] Interfaz OAuth2 y Cifrado local.
- [x] Discord Rich Presence dinámico.
- [x] Procesamiento de enlaces nativo y capturas de imágenes.
- [x] Cerebro NLP (WordNinja + GuessIt + Regex) para limpieza de nombres.

## 🟡 Fase 3: Integración Cloud y Seguridad PREMIUM (¡COMPLETADA!)
- [x] Triple Candado Premium (Global, Aliado y Local).
- [x] Listener en tiempo real de Firebase para estatus PREMIUM (`vip_global` y `premium_global` alineados en validación y registro de usuario).
- [x] **Backup Universal en la Nube:** Subida/Descarga de `catalogo_v2.db` a la nube segura (Firebase Storage).
- [x] **UX de Respaldo:** Barras de carga animadas y transiciones fluidas.
- [x] Refinar el cambio visual automático del botón de Discord y apagado seguro de hilos.
- [x] **Restricción de UI FREE:** Ocultar botones de Copiar y Reproducir enlaces en el modal para usuarios no premium.

## 🔧 Mantenimiento reciente (abril 2026)
- [x] **Anti-trabo al arrancar:** recuperación de sesión ya no lee el log completo de VRChat (`readlines()`); solo los últimos 2–8 MB del `output_log` más reciente.
- [x] SQLite **thread_safe** + **timeout** para acceso concurrente (hilo del motor + interfaz) y menos bloqueos “database is locked”.
- [x] Catálogo: representante por título/tipo = registro **más reciente** (`ultima_actualizacion`), sin depender de `GROUP BY` ambiguo en SQLite.
- [x] TMDB: evitar recursión infinita si la clave de usuario coincide con la clave maestra y la API devuelve 401/429.
- [x] Limpieza de código: eliminado `media_modal_fix.py` (fragmento huérfano que rompía `compileall`); sustitución de `except:` silenciosos por logging donde aplica.
- [x] **Calificaciones en nube:** `sync_rating` persiste en Firestore con transacción atómica y devuelve el promedio actualizado al modal en tiempo real. Caché multi-capa (L1/L2/L3) con debounce 20 s.

## 🔵 Fase 4: Rescate de Lógicas Nativas (¡COMPLETADA!)
Migración de funciones críticas desde `CatalogoPeliculasVistas`:
- [x] **Módulo de Idiomas (Traducción Dinámica):** Selector en Configuración; cambio en tiempo real sin reiniciar.
- [x] **Gestor de Episodios:** Lista en el modal, borrado y marcar visto por fila (✓/○).
- [x] **Interruptores rápidos:** "👁️ Ya la ví" y "❤️ Favorito" con badges visuales en la tarjeta del catálogo.
- [x] **Vistas expandidas:** Cuadrícula ↔ Lista con botón toggle; ordenamiento por fecha/A-Z/nota.
- [x] **Colecciones por Mundo:** Filtro lateral por `world_name` en Streams/Imágenes.

## 🟣 Fase 5: Estética Suprema y Despliegue (¡COMPLETADA!)
- [x] **Scroll Infinito (N8):** Carga fluida de páginas adicionales al acercarse al fondo; sin reset al subir.
- [x] **Sistema OTA (N5):** Comprobación de versión al inicio con toast de aviso; URL configurable en Ajustes.
- [x] **Temas Visuales (N6):** Oscuro y AMOLED; selector en Configuración con aplicación inmediata.
- [x] **Compilación .EXE (N7):** `VRCMT.spec` para PyInstaller listo para usar.

## 🟤 Fase 6: Calidad de Vida (¡COMPLETADA — Abril 2026!)
- [x] **F1 Filtro por Género:** Combo con géneros extraídos de la BD en todas las vistas de catálogo.
- [x] **F2 Progreso de Serie:** Barra "X/Y eps" en la tarjeta del catálogo para series/anime.
- [x] **F3 Toast Notifications:** Notificaciones flotantes no intrusivas (info/success/error/warning).
- [x] **F4 Exportar Catálogo:** Botones CSV y JSON en Configuración.
- [x] **F5 System Tray:** Minimizar a bandeja; menú con Mostrar/Salir.
- [x] **F6 Atajos de Teclado:** Esc, Ctrl+F, Ctrl+1-6.
- [x] **F7 Estadísticas Ampliadas:** Progreso de series y actividad mensual en la vista Estadísticas.
- [x] **F8 Sidebar Colapsable:** Modo solo-icono con botón ◀/▶.
- [x] **P2 Watchlist en Sidebar:** Filtro dedicado para items en lista de pendientes.
- [x] **T2 on_media_added:** Refresco diferido al volver al catálogo desde otra vista.
- [x] **T3 Hard cap:** Límite aumentado a 500; N8 maneja el display progresivo.

## 📌 Pendiente futuro
- [ ] **Barra de progreso global de Serie** visible en la tarjeta incluso al scannear (requiere BD adicional de `episodios_totales` por serie).
- [ ] **Icono `.ico`** para el ejecutable (`assets/icon.ico`).
- [ ] **URL OTA real** apuntando al repositorio de distribución definitivo.
- [ ] **Estadísticas: Top mundos VRChat** (por número de sesiones de streaming).
- [ ] **Modo compacto de sidebar** con solo iconos (ya implementado con F8).

---
*Última actualización: 12 de Abril de 2026*
