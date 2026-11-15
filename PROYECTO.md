# Vigilancia de incendios en España con satélites de la NASA

Este documento explica qué es el proyecto, qué problema resuelve y cómo funciona por dentro, sin
entrar en código. Para los detalles técnicos están los README de `backend/`, `backend/etls/` y
`backend/databases/`.

---

## 1. La idea en una frase

Coger los avisos de "aquí hay fuego" que envían varios satélites de la NASA, limpiarlos,
juntarlos y convertirlos en **incendios** con nombre propio que se pueden ver y estudiar en un
**mapa de España**, tanto los de hoy como los de los últimos 25 años.

---

## 2. El problema de partida

La NASA publica gratis, a través de su servicio **FIRMS** (*Fire Information for Resource
Management System*), las anomalías térmicas que detectan sus satélites en todo el mundo. Cada vez
que un satélite pasa por encima de una zona y nota un punto mucho más caliente de lo normal,
registra una **detección**: un "píxel caliente" con su posición, su hora y la energía que emite.

Esos datos tienen varios problemas para usarlos tal cual:

- **Vienen de dos instrumentos distintos**, MODIS y VIIRS, que miden de forma diferente, tienen
  resoluciones distintas y expresan la confianza de manera incompatible (uno con un número de 0 a
  100, el otro con las palabras "baja", "nominal" y "alta").
- **Una detección no es un incendio.** Un incendio grande puede generar cientos o miles de
  detecciones a lo largo de varios días y de varias pasadas de satélite. Nadie las agrupa.
- **No todo lo caliente es un incendio forestal**: hay industrias, volcanes (como el de La Palma
  en 2021), plataformas en el mar o simples señales muy débiles que son ruido.
- **Los datos llegan por dos vías** que no encajan entre sí: un archivo histórico anual y un flujo
  "casi en tiempo real" que solo guarda los últimos dos meses.

El nombre del repositorio, *Harmonization MODIS and VIIRS*, resume el objetivo: **armonizar** las
dos fuentes para que hablen el mismo idioma y, a partir de ahí, construir algo útil.

---

## 3. Los satélites y lo que miden

| Instrumento | Satélites | Desde | Tamaño del píxel | Qué aporta |
|---|---|---|---|---|
| **MODIS** | Aqua y Terra | 2000 | ~1 km | La serie histórica más larga |
| **VIIRS** | Suomi NPP, NOAA-20 y NOAA-21 | 2012 / 2018 | ~375 m | Mucha más resolución: ve fuegos más pequeños |

De cada detección nos interesan sobre todo:

- **Dónde y cuándo**: coordenadas y hora exacta (en UTC).
- **El tamaño del píxel**: el satélite no ve un punto, ve un rectángulo que puede medir desde
  unos cientos de metros hasta varios kilómetros según lo inclinado que mire.
- **La FRP** (*Fire Radiative Power*, potencia radiativa del fuego): cuánta energía emite el
  fuego en ese instante, en megavatios. Es la mejor pista de la **intensidad** del incendio.
- **La confianza**: cuánto se fía el propio satélite de que aquello sea fuego.
- **Día o noche**: de noche el suelo está frío y un fuego pequeño destaca más, así que los
  criterios cambian.

---

## 4. Las piezas del proyecto

El proyecto tiene cuatro piezas que trabajan en cadena:

```
  NASA FIRMS  ──►  1. Pipeline de datos  ──►  2. Base de datos  ──►  3. API  ──►  4. Mapa web
 (MODIS+VIIRS)     (limpia, fusiona,           (PostGIS)            (sirve        (lo que ve
  + SIGPAC          agrupa en incendios)                             los datos)    el usuario)
```

1. **El pipeline de datos** (`backend/etls`): descarga, limpia, une y agrupa. Es el cerebro.
2. **La base de datos** (`backend/databases`): guarda el resultado con su geografía, de forma que
   se pueda preguntar "qué incendios hubo en esta zona entre estas fechas" muy rápido.
3. **La API** (`backend/api`): un pequeño servidor que responde a esas preguntas.
4. **El mapa web** (`frontend`): la aplicación que se abre en el navegador.

Todo se levanta con un solo comando gracias a Docker, que arranca la base de datos, la API y el
mapa a la vez.

---

## 5. El pipeline, paso a paso

Es la parte con más trabajo. Lo que hace, en orden:

### 5.1. Descargar los datos

Hay dos fuentes y se combinan:

- **Archivo histórico anual** (sin necesidad de cuenta): un fichero por año y por satélite, ya
  recortado a España, desde el año 2000 hasta el último año completo.
- **Datos casi en tiempo real** (requieren un token gratuito de la NASA, que nunca se guarda en el
  código): un fichero por día de toda Europa, que solo cubre los últimos ~60 días. Los ficheros
  de los dos últimos días se vuelven a descargar siempre, porque se van completando a lo largo
  del día.

> **Ojo: hay un hueco.** El histórico acaba el 31 de diciembre del último año completo y el tiempo
> real solo guarda dos meses. Lo que queda en medio (en noviembre de 2026, del 1 de enero al 14 de
> septiembre de 2026) no lo cubre ninguna de las dos fuentes. El programa lo calcula y lo avisa.
> Se cubrirá cuando la NASA publique el fichero anual de 2026 o usando su API.

### 5.2. Leer y poner todo en el mismo formato

Cada fichero se lee y se traduce a un formato común, da igual de qué satélite venga. El programa
reconoce el instrumento por las columnas que trae, no por el nombre del fichero. También unifica
detalles como que el histórico llama "Aqua" a un satélite y el tiempo real lo llama "A".

A cada detección se le asigna una **huella dactilar** única calculada a partir de sus datos. Así,
si un mismo fichero se procesa dos veces, o si una detección aparece en el histórico y en el
tiempo real, **no se duplica**.

Las filas con datos imposibles (coordenadas fuera de rango, tamaños absurdos…) se apartan y se
anota el motivo.

### 5.3. Quedarse solo con España

Se descarta todo lo que cae fuera de España: Península, Baleares, Canarias, Ceuta y Melilla. Los
límites son algo generosos para no perder nada español, así que entra alguna franja de Portugal,
Francia o Marruecos.

### 5.4. Separar el fuego real del ruido

Aquí está una de las claves del proyecto. Hay detecciones que **no son incendios de vegetación**
y que no se borran, sino que **se marcan como no válidas** con su motivo, para poder revisarlas:

- **Energía demasiado baja**: si la FRP no llega a un mínimo, se considera ruido. El mínimo
  depende del instrumento y de si es de día o de noche (VIIRS: 15 MW de día y 6 de noche; MODIS:
  30 y 12, porque su píxel es más grande).
- **Fuentes que no son incendios**: la NASA ya etiqueta algunas como industria, volcán o mar.
- **Zonas de exclusión**: casos que la NASA no separa bien. El ejemplo es la erupción de La Palma
  de 2021: casi el 99 % de sus detecciones figuraban como "incendio". Se ha configurado una zona y
  unas fechas para apartarlas, y se pueden añadir otras igual.

Las detecciones válidas se clasifican además por intensidad en **baja, media o alta**, con
umbrales propios para cada instrumento.

### 5.5. Unir lo que ven los dos satélites

Se dibuja el píxel de cada detección sobre el terreno (un rectángulo con un pequeño margen de
400 m) y se agrupan las que **se tocan dentro de la misma ventana de tiempo** (por defecto, el
mismo día). Cada grupo es una **observación**: "en este sitio, este día, había fuego".

Una observación puede estar formada solo por MODIS, solo por VIIRS o por ambos. Que **los dos
instrumentos coincidan** es una señal mucho más fiable que si solo lo ve uno.

### 5.6. Calcular una confianza común

Como cada instrumento expresa la confianza a su manera, el proyecto usa una **tabla propia** que
combina ambas y da un único nivel: baja, media, alta o muy alta.

La regla de fondo es sencilla: **si solo lo ve un instrumento, como mucho es "media"**; para llegar
a "alta" o "muy alta" hace falta que MODIS y VIIRS lo confirmen. Dos satélites del mismo tipo
(por ejemplo Aqua y Terra) no cuentan como confirmación, porque miden igual. Las observaciones
débiles y aisladas (un solo instrumento con confianza baja) no forman incendios.

### 5.7. Encadenar los días: de observaciones a incendios

Un incendio que dura una semana produce una observación cada día. El pipeline **enlaza las
observaciones de días consecutivos que están a menos de un kilómetro** entre sí, y el conjunto
resultante es un **incendio** (en el código, un "evento").

De cada incendio se guarda:

- primera y última detección, y cuántas veces lo vio un satélite;
- si está **activo** o **cerrado** (pasa a cerrado tras 48 horas sin detecciones);
- su centro y su **envolvente** (el contorno que abarca todas sus detecciones);
- la intensidad máxima y su evolución en el tiempo;
- la confianza combinada y los sensores que lo vieron.

Un detalle importante: cada incendio tiene un **identificador estable**. Si se vuelve a ejecutar
el pipeline con datos nuevos, un incendio que ya existía conserva su identificador; y si dos
incendios que parecían distintos resultan ser el mismo (porque se han juntado), se queda el nombre
del más antiguo y se anota la fusión.

### 5.8. ¿Qué se estaba quemando? (SIGPAC)

Opcionalmente, cada detección se cruza con el **SIGPAC**, el catálogo oficial del Ministerio de
Agricultura que divide España en parcelas y dice el uso de cada una (cultivo, pasto, bosque,
urbano…). Así se sabe si el fuego estaba en **terreno agrícola o forestal**, algo muy útil para
distinguir, por ejemplo, quemas de rastrojos de incendios de monte.

El SIGPAC son unos 34 GB repartidos en 50 provincias; el proyecto tiene un cargador que las
descarga y las mete en la base de datos de una en una, sin llenar el disco.

### 5.9. Estimar cuánta vegetación se quemó

A partir de la energía que emite el fuego se puede estimar cuánta **biomasa** se ha consumido (hay
una relación conocida: unos 0,368 kg por cada megajulio). El cálculo agrupa las detecciones por
pasada de satélite, supone que cada pasada representa unas horas de fuego y suma la energía.

Es una **estimación para comparar incendios entre sí**, no una medida exacta: no ve los fuegos
pequeños, ni lo que tapan las nubes o el humo, ni lo que pasa entre pasadas. Como referencia, el
incendio de la **Sierra de la Culebra (Zamora, 2022)** sale en torno a **100.000 toneladas**.

### 5.10. Guardar los resultados

El pipeline deja tres cosas:

- **Las tablas completas**, con todo el detalle del algoritmo (útiles para auditar).
- **Unos datos "limpios"** listos para el mapa y para un futuro chatbot, con una **frase resumen
  en castellano** de cada incendio.
- **La carga en la base de datos**, que es lo que usa el mapa. Si la base de datos falla, el
  pipeline no se detiene: los ficheros sirven de respaldo.

Todos los parámetros (distancias, umbrales, ventanas de tiempo…) están en **un único fichero de
configuración** (`backend/etls/config/default.yaml`), no repartidos por el código. Cambiar un
criterio es cambiar un número en ese fichero.

---

## 6. El mapa: lo que ve el usuario

Se abre en el navegador (`http://localhost:5173`) y ocupa toda la pantalla. Permite:

- **Ver los incendios y las detecciones** sobre el mapa de España. Los incendios se distinguen en
  activos y cerrados; las detecciones, por su intensidad (baja, media, alta). Al alejarse se ven
  como puntos; al acercarse mucho, como el **píxel real** que vio el satélite.
- **Filtrar por fechas**, con atajos como "última semana", "último mes", "último año" o "todas las
  fechas", o un rango a medida.
- **Panel de actividad reciente**: una lista plegable con los últimos incendios. Al pulsar uno, el
  mapa va hasta él.
- **Seleccionar una zona**: se arrastra un recuadro sobre el mapa y aparece un menú con:
  - *Incendios en esta zona*: los que tienen al menos una detección dentro del recuadro y de las
    fechas elegidas.
  - *Incendios de los últimos 10 años*: los que hubo **en esa misma zona y en esas
    mismas fechas del año** en cada uno de los diez años anteriores (incluidos los años sin
    ninguno). Sirve para responder a "¿esto es normal aquí en esta época?".
  - *Acercar a la zona* y *Quitar selección*.
- **Una página de fichas** que se abre desde los resultados de una zona: una tarjeta por incendio
  con su propio minimapa (contorno y píxeles), fechas, número de detecciones, intensidad máxima,
  duración, superficie de la huella, sensores y confianza.

---

## 7. Cómo leer bien los datos (avisos importantes)

Para no sacar conclusiones equivocadas:

- **La "huella" no es la superficie quemada.** Es el área de los píxeles en los que el satélite
  vio calor, más un margen. Un píxel de MODIS mide como mínimo un kilómetro cuadrado aunque el
  fuego ocupe una parte pequeña.
- **La duración es un mínimo.** Es el tiempo entre la primera y la última vez que un satélite vio
  el fuego; empezó antes y probablemente terminó después. Si solo se vio una vez, se indica "visto
  en 1 pasada" en lugar de "0 horas".
- **La biomasa es una estimación** para comparar, no un dato oficial.
- **Los datos en tiempo real son provisionales**: la NASA los sustituye después por una versión
  definitiva.
- **Los satélites no lo ven todo**: nubes, humo denso o fuegos muy pequeños pueden pasar
  desapercibidos.
- **La confianza "muy alta" es una categoría propia** de este proyecto, no un dato oficial de la
  NASA.

---

## 8. Calidad y fiabilidad

- **Pruebas automáticas** para cada parte del pipeline (lectura, confianza, geometría, fusión,
  incendios, clasificación por intensidad, SIGPAC, carga en base de datos) y para la API.
- **Validación con un incendio real**: un script reprocesa la Sierra de la Culebra (junio de 2022)
  y genera un resumen y un mapa para comprobar que los resultados tienen sentido.
- **Repetible**: procesar los mismos datos dos veces da el mismo resultado, sin duplicados, y los
  identificadores de los incendios se mantienen.
- **Seguro con las credenciales**: el token de la NASA nunca se escribe en el código ni se sube al
  repositorio, y si es rechazado no se envía a ningún otro sitio.

---

## 9. Estado actual y próximos pasos

Es un **MVP** (primera versión funcional) centrado en España. Ya funciona de extremo a extremo:
descarga, limpieza, fusión, incendios, base de datos, API y mapa.

Pendiente o en el horizonte:

- **Cubrir el hueco de 2026** con la API de FIRMS o el fichero anual cuando se publique.
- **Un chatbot** que responda preguntas sobre los incendios; los datos limpios con resumen en
  castellano ya están pensados para él.
- **Ajustar el contorno de España** con la frontera exacta en lugar de rectángulos.
- **Mejorar la forma de los píxeles**, que hoy se dibujan como rectángulos alineados con los ejes
  sin tener en cuenta la inclinación de la órbita.
- **Revisar las condiciones de reutilización del SIGPAC** antes de redistribuir resultados.

---

## 10. Glosario rápido

| Término | Significado |
|---|---|
| **FIRMS** | Servicio de la NASA que publica las detecciones de fuego de sus satélites. |
| **MODIS / VIIRS** | Los dos instrumentos que detectan el fuego. VIIRS es más moderno y preciso. |
| **Detección** | Un píxel en el que un satélite vio fuego en una pasada. |
| **Observación** | Detecciones que se tocan en la misma ventana de tiempo (normalmente un día). |
| **Incendio (evento)** | Observaciones enlazadas en días consecutivos y cercanas entre sí. |
| **FRP** | Potencia radiativa del fuego, en megavatios: su intensidad en ese instante. |
| **Huella** | Área de los píxeles con calor. No es superficie quemada. |
| **Envolvente** | Contorno que abarca todas las detecciones de un incendio. |
| **NRT** | *Near Real Time*: datos casi en tiempo real, provisionales, de los últimos ~60 días. |
| **SIGPAC** | Catálogo oficial de parcelas de España con su uso (cultivo, bosque, etc.). |
| **Biomasa quemada** | Estimación de la vegetación consumida a partir de la energía del fuego. |
