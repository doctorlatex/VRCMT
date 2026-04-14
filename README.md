# VRCMT — VRChat Media Tracker

**VRCMT** es una aplicación de escritorio para **llevar tu propia biblioteca** de lo que ves en **VRChat**: películas, series, anime, streams e imágenes enlazadas desde mundos (reproductores tipo AVPro, ProTV, etc.). Lee los **registros (logs) que VRChat genera en tu PC**, extrae la URL que se está reproduciendo e intenta **identificar el título** (por ejemplo con TMDB) para guardar cartel, datos y tu progreso. **No sustituye a VRChat** ni reproduce por sí sola lo que ocurre dentro del juego: organiza y muestra **tu historial** en una interfaz propia.

En este repositorio público solo hay **instrucciones**, el archivo **`version.txt`** (para comprobar actualizaciones) y los **ejecutables** en [Releases](https://github.com/doctorlatex/VRCMT/releases/latest). **No se publica código fuente aquí.**

---

## Ventana principal: zonas y controles

### Barra lateral (izquierda)

- **Logo “VRCMT”** (visible con la barra expandida).
- **Botones de sección** (cada uno cambia la vista central):
  - **Todo** — Catálogo completo.
  - **Películas**, **Series**, **Anime** — Filtros por tipo de contenido.
  - **Streams/Imágenes** — Contenido tipo stream o capturas.
  - **Estadísticas** — Resumen de tu perfil y consumo.
  - **Configuración** — Ajustes, respaldos, Discord, Premium, YouTube en VRChat.
  - **Acerca de** — Versión, enlaces y acceso a **instrucciones detalladas**.
- **Flecha ◀ / ▶** al pie de la barra — **Colapsar o expandir** la barra (modo compacto solo con iconos y tooltips).

### Área del catálogo (vista principal)

- **Campo de búsqueda** arriba — Filtra por texto dentro de tu catálogo (no es la búsqueda en internet). Atajo: **Ctrl+F**.
- **“Ordenar por”** — Lista desplegable: por fecha de agregado, año, nombre, etc.
- **Botón cuadrícula / lista** (icono **☰** o **⊞**) — Alterna entre **tarjetas en cuadrícula** y **vista lista**.
- **Filtro “Género”** — Aparece en vistas de catálogo donde aplica; reduce entradas por género.
- **Filtro “Mundo / World”** — En **Streams/Imágenes**, filtra por mundo de VRChat.
- **Paginación** abajo — Botones **primera / anterior / siguiente / última** página, texto **“Página X/Y”**, campo **“Ir a…”** y botón **Ir** para saltar a una página (el catálogo se pagina en bloques de tarjetas).

### Atajos útiles (catálogo)

- **Ctrl+1 … Ctrl+5** — Saltan a los filtros **Todo**, **Películas**, **Series**, **Anime**, **Streams/Imágenes** respectivamente.
- **Escape** — Cierra modales o devuelve el foco según el contexto.

### Bandeja del sistema (Windows)

- Si está activada la opción correspondiente en **Configuración**, al **cerrar la ventana** la app puede **minimizar a la bandeja** en lugar de salir del todo.
- Desde el **icono de la bandeja** puedes **volver a abrir** la ventana o **salir** por completo (según el menú contextual).

---

## Ficha de un título (modal al pulsar una tarjeta)

Al hacer clic en una **tarjeta** del catálogo se abre una **ventana grande** con la ficha del contenido.

### Panel izquierdo de la ficha

- **Cartel** (póster) del título cuando hay datos o imagen disponible.
- **Ver tráiler oficial** — Abre el tráiler en el navegador si aplica.
- **Marcar visto / Ya la ví** — Alterna el estado visto.
- **Favorito** — Marca o quita favorito (según diseño actual del botón).
- **Marcar anime / Quitar anime** — Solo en tipos donde aplica (no en todos los streams públicos).
- **Eliminar del catálogo** — Quita esa entrada de tu biblioteca local.
- **Cerrar ✕** — Cierra la ficha.

### Pestañas de la ficha

1. **Detalles / Info** — Sinopsis, año, géneros, director, reparto, enlaces de colección cuando existan, notas propias, campo **ID IMDb**, **etiquetas**, botón **Guardar** de etiquetas, ajustes de **temporada/episodio** para series con botón **Act.**, paginador de episodios (**◀ ▶**, ir a página, **Ir**), y el botón **Fix** para corregir o asignar metadatos (abre el buscador o aplica por IMDb).
2. **Reproducción / Watch** — Zona del **reproductor interno** (según plan y disponibilidad): reproduce la URL guardada, muestra progreso y controles propios del reproductor.

### Calificación por estrellas

- Con **sesión de Discord** activa suele mostrarse la **barra de estrellas (0–10)** para tu nota y la **media de la comunidad VRCMT** cuando hay datos.

### Botón **Fix**

- Sirve para **corregir** un título mal detectado o **vincularlo** a TMDB: abre el **buscador** con resultados en fila (título, año, tipo) o aplica cambios si rellenas **IMDb** según lo que indique la propia app en instrucciones.

---

## Buscador TMDB (cuadro de búsqueda al usar Fix u otras acciones)

- Campo para **escribir** el nombre de película o serie.
- Lista de **resultados** en filas (miniatura, título, año, nota TMDB).
- Al **pulsar una fila** se selecciona ese resultado para **asociarlo** a tu entrada.

---

## Reproductor manual (Premium)

Desde **Configuración → Herramientas Premium**, si tu cuenta tiene **Premium**, puede habilitarse **“Abrir reproductor manual”**.

Dentro del reproductor (ventana o panel según versión):

- **Cargar** — Tras pegar una URL en el campo superior, carga el vídeo.
- **Play / Pausa**, **Retroceder 10 s**, **Avanzar 10 s**.
- **Volumen** (icono altavoz / silencio).
- **Pantalla completa**.
- **Velocidad** (p. ej. 0.75× … 2×) — Ciclos al pulsar.
- **Copiar URL** — Copia el enlace actual al portapapeles.
- **Descargar** — En plataformas soportadas (p. ej. YouTube, Twitch, Vimeo públicos), inicia descarga con progreso en el botón.

---

## Estadísticas

- Título tipo **“Mi perfil cinéfilo”**.
- **Tarjetas numéricas**: títulos guardados, vistos, horas acumuladas, nota media (según tus calificaciones).
- Sección **Géneros que más consumes** — Lista o barras según consumo por género.

---

## Configuración (vista completa en scroll)

### Configuración general

- **Idioma** — Desplegable con el idioma alternativo disponible y **Aplicar** (la interfaz se reconstruye al cambiar).
- **Tema visual** — Selección de tema y **Aplicar**.
- **Clave personal TMDb (opcional)** — Para cuotas propias si la predeterminada se agota.
- **Directorio de logs de VRChat** — Ruta donde VRChat escribe los logs; botón **📁** para elegir carpeta.
- **Guardar configuración** — Graba los cambios de esta sección.

### Exportar catálogo

- **Exportar CSV** y **Exportar JSON** — Descargan listados de tu biblioteca en esos formatos.

### YouTube en VRChat

- Texto explicativo y botón **¿Qué hace esto?**
- Casilla para **activar la mejora** del `yt-dlp` que usa VRChat.
- **Instalar / Actualizar desde internet** — Descarga/actualiza el componente según el manifiesto oficial del proyecto.
- **Instalar desde archivo…** — Si tienes un paquete local.
- **Quitar mejora** — Restaura el estado anterior de VRChat.
- **Opciones avanzadas** (plegable) — Cookies de YouTube (archivo), URL de manifiesto opcional, ruta del ejecutable de VRChat, token de GitHub para repos privados, **Guardar configuración avanzada** y botón de **actualizar estado**.

### Respaldo universal

- **Exportar todo mi historial (local)** — Genera un ZIP con base de datos, ajustes, imágenes de mundos y export para Letterboxd.
- **Importar respaldo (local)** — Restaura desde un ZIP que hayas exportado antes.

### Nube VRCMT (Premium)

- **Subir a la nube** / **Descargar de la nube** — Con Premium activo, respaldo cifrado en la nube del servicio vinculado a tu cuenta; barras de progreso durante la operación.

### Herramientas Premium

- Tras verificar tu estado con Discord/Firebase: **Abrir reproductor manual** cuando esté disponible.

### Integración Discord

- Texto de ayuda.
- **Iniciar sesión con Discord** / **Cerrar sesión de Discord** — Enlaza tu cuenta para Premium, calificaciones comunitarias y nube.

---

## Acerca de

- Muestra **nombre de la app**, **versión**, autor, enlace a **Discord** y a **GitHub Releases**.
- Comprobación / descarga de **actualizaciones** cuando la app lo ofrece.
- **Instrucciones de uso** — Abre un modal con texto largo: qué es VRCMT, cómo lee los logs, regla del **90 %** para marcar visto, **Free vs Premium** (enlaces privados con candado), **Fix**, reproductor, YouTube en VRChat, limitaciones de detección y nota legal.

---

## Comportamiento general que conviene conocer

- **Detección automática** depende de que la **URL o nombre de archivo** en el log sea reconocible; enlaces crípticos pueden guardarse solo como título genérico.
- **Marcado como visto** puede ser **automático** (aprox. al 90 % de duración conocida, con reglas alternativas si no hay duración) o **manual** con el botón en la ficha.
- **Plan Free**: en contenido privado de mundos, el enlace completo puede no guardarse; en públicos (YouTube, Twitch, Kick, etc.) suele guardarse. **Premium** amplía el guardado de enlaces en nuevas capturas según las reglas de la app.

---

## Dónde descargar la app

La última versión publicada está en **[Releases](https://github.com/doctorlatex/VRCMT/releases/latest)**. Descarga **`VRCMT.exe`** (o el archivo con nombre de versión si se ofrece como duplicado del mismo binario).

Si necesitas ayuda o comunidad, usa el enlace de **Discord** que aparece dentro de la propia aplicación en **Acerca de**.
