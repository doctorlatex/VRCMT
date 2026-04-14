# VRCMT — VRChat Media Tracker

**VRCMT** es una aplicación de escritorio para **llevar tu propia biblioteca** de lo que ves en **VRChat**: películas, series, anime, streams e imágenes enlazadas desde mundos (reproductores tipo AVPro, ProTV, etc.). Lee los **registros (logs) que VRChat genera en tu PC**, extrae la URL que se está reproduciendo e intenta **identificar el título** (por ejemplo con TMDB) para guardar cartel, datos y tu progreso. **No sustituye a VRChat** ni reproduce por sí sola lo que ocurre dentro del juego: organiza y muestra **tu historial** en una interfaz propia.

En este repositorio público solo hay **instrucciones**, el archivo **`version.txt`** (para comprobar actualizaciones) y los **ejecutables** en [Releases](https://github.com/doctorlatex/VRCMT/releases/latest). **No se publica código fuente aquí.**

---

## Descripción del producto

### Español — VRChat Media Tracker (VRCMT)

**VRChat Media Tracker** es una herramienta para **tener una pequeña biblioteca de información** del contenido multimedia en VR. Está **diseñada para rastrear, capturar y compartir en tiempo real** lo que estás viendo dentro de VRChat.

#### Funcionalidades principales

- **Extractor de enlaces multimedia:** captura automáticamente los enlaces (URLs) de los reproductores dentro de los mundos de VRChat, facilitando el acceso directo a vídeos de YouTube y streams en vivo **sin salir de la experiencia** ni tener que pedirlos manualmente.
- **Tarjeta “Jugando” en Discord (Rich Presence):** muestra dinámicamente en tu perfil de Discord no solo el mundo en el que te encuentras, sino también el **título y el contenido** que estás visualizando en el reproductor del mundo.
- **Asistente de cine e integración con IMDb:** aunque no es la finalidad principal, ofrece compatibilidad con mundos de cine para mostrar **información detallada** del filme en curso.

**Nota:** debido a que los nombres de los archivos en los mundos pueden variar, el sistema ofrece una **búsqueda manual** (por ejemplo vinculada a IMDb) para identificar correctamente la película y obtener sus metadatos cuando la detección automática no es posible. Así puedes mantener el seguimiento de tus películas y de los capítulos que ya hayas visto de tus series favoritas.

**Sincronización social:** pensado para compartir tus gustos musicales o visuales con tu comunidad, de modo que otros usuarios puedan ver **qué contenido estás consumiendo** en tiempo real (según la configuración de Discord y la privacidad que elijas).

#### Modelos de acceso: Free vs. Premium

Para asegurar el desarrollo continuo de **VRChat Media Tracker (VRCMT)** y añadir nuevas funciones basadas en el apoyo de la comunidad, existen **dos niveles de acceso**:

**1. Plan Free (gratuito)** — pensado para el usuario casual que disfruta de contenido popular.

- **Acceso limitado:** captura y muestra enlaces procedentes sobre todo de **YouTube** y **streams en vivo** (por ejemplo Twitch / YouTube Live), según las reglas de la aplicación.
- **Funciones básicas:** incluye el **Rich Presence** estándar de Discord y la visualización del **mundo actual**.

**2. Plan Premium** — la experiencia completa para quien quiere el **control total** de su biblioteca multimedia.

- **Acceso total:** desbloquea la captura de **todos los enlaces** compartidos en los mapas (incluidos servidores directos, archivos de vídeo y plataformas externas).
- **Integración con mundos aliados:** en mundos que colaboran con VRCMT, este plan puede ofrecer **acceso exclusivo** a los enlaces de sus reproductores: pensado como “llave VIP” para quien desea interactuar con el contenido especial de esos creadores.
- **Evolución constante:** los suscriptores Premium pueden recibir **funciones exclusivas** adicionales según el crecimiento y el apoyo al proyecto.

### English — VRChat Media Tracker (VRCMT)

**VRChat Media Tracker** is a tool designed to maintain a **personal information library** about VR multimedia content. It is built to **track, capture, and share in real time** what you are watching inside VRChat.

#### Core features

- **Multimedia link extractor:** automatically captures links (URLs) from players inside VRChat worlds, giving you direct access to YouTube videos and live streams **without leaving the experience** or having to request links manually.
- **Discord Rich Presence (“Now playing” card):** dynamically shows on your Discord profile not only the world you are in, but also the **exact title and content** you are watching on the world’s media player.
- **Cinema assistant & IMDb-related lookup:** while not its primary purpose, it can work alongside movie worlds to show **detailed information** about the film currently playing.

**Note:** because file names in worlds can vary, the app offers **manual search** (including IMDb-based workflows) to correctly identify a title and fetch metadata when automatic detection is not possible—so you can keep track of movies and episodes you have already watched.

**Social sync:** great for sharing musical or visual tastes with your community, so others can see **what you are consuming in real time** (depending on your Discord settings and privacy choices).

#### Access models: Free vs. Premium

To support ongoing development of **VRChat Media Tracker (VRCMT)** and ship new features based on community backing, there are **two access tiers**:

**1. Free plan** — for casual users who mostly enjoy mainstream content.

- **Limited access:** captures and surfaces links primarily from **YouTube** and **live streams** (e.g. Twitch / YouTube Live), according to the app’s rules.
- **Core features:** standard **Discord Rich Presence** and **current world** information.

**2. Premium plan** — the full experience for users who want **total control** over their media library.

- **Unlimited access:** unlocks capture of **all shared links** within maps (including direct servers, video files, and third-party platforms).
- **Partner world integration:** in worlds that collaborate with VRCMT, Premium can provide **exclusive access** to their player links—a “VIP key” for users who want deeper interaction with creators’ special content.
- **Continuous evolution:** Premium subscribers may receive **additional exclusive features** as the project grows and gains more support.

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
