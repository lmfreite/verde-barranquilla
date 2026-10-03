# verde-barranquilla — NDVI e impermeabilidad por barrio

Calcula, para cada barrio (o cualquier zona: microcuencas, entornos de colegios),
el **NDVI medio**, el **% de vegetación**, el **% de superficie impermeable** y el
**% de agua** a partir del ImageServer WorldView Legion 2026 de la Alcaldía:

```
https://miciudad.barranquilla.gov.co/image/rest/services/orto/orto35_worldview_08001barranquilla_2026/ImageServer
```

El resultado son estadísticas por zona en GPKG, GeoJSON (listo para MapLibre) y un
CSV con el ranking de "barrios sin sombra". **Ningún píxel de la imagen sale en los
resultados.**

Proyecto independiente: no es un producto oficial de la Alcaldía de Barranquilla.

## Instalación

Requiere Python 3.10 o superior.

```bash
git clone https://github.com/lmfreite/verde-barranquilla.git
cd verde-barranquilla
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Prueba que todo funciona sin conexión, con una escena sintética de 8 bandas y un
ImageServer simulado:

```bash
baqgeo demo --salida resultados/demo
pytest
```

## Uso con el servicio real

### 1. Revisar el servicio

```bash
baqgeo inspeccionar --json datos/servicio.json
```

Muestra bandas, tipo de píxel, CRS, extensión, funciones raster permitidas y,
si el servicio tiene catálogo, la fecha, el ángulo y la nubosidad de cada imagen.
Si aparecen varias imágenes, la radiometría puede cambiar entre pasadas.

### 2. Conseguir los barrios

Sirve cualquier capa de polígonos con CRS: GPKG, SHP, GeoJSON o la URL de una
capa ArcGIS (FeatureServer/MapServer), que se descarga paginada.

- Cartografía catastral (incluye la capa `U_BARRIO`):
  [datos.gov.co](https://www.datos.gov.co/Ordenamiento-Territorial/Cartograf-a-Catastral-en-formato-Shapefile-y-GeoPa/3i4i-apsf).
  Revisa la vigencia: la Gerencia de Catastro del Distrito puede tener una más reciente.
- Capa de barrios del geoportal: `--zonas https://.../MapServer/<id>`.

### 3. Validar el NDVI del servidor

```bash
baqgeo validar --zonas barrios.gpkg --capa U_BARRIO
```

Toma 40 puntos y compara el NDVI calculado por el servidor con el calculado aquí
a partir de las bandas crudas. Prueba dos funciones de ArcGIS (`BandArithmetic`
y `NDVI`) y te dice cuál usar. También muestra:

- el rango de cada banda, para saber si son niveles digitales o reflectancia;
- percentiles del NDVI, para calibrar el umbral de vegetación;
- un aviso si el NDVI sale casi todo negativo, señal de que rojo y NIR están
  invertidos.

Si los nombres de banda del servicio no se reconocen, se asume el orden estándar
de Legion (coastal, blue, green, yellow, red, red edge 1, red edge 2, NIR).
Para otro orden: `--bandas "blue=2,green=3,red=5,nir=8"` (índices base 1).

### 4. Calcular los indicadores

**Modo servidor (recomendado).** Hace una petición `computeStatisticsHistograms`
por barrio: el servidor calcula el NDVI y devuelve un histograma. Así no se
descarga la imagen.

```bash
baqgeo indicadores --zonas barrios.gpkg --capa U_BARRIO --salida resultados/barrios
```

**Modo local.** Descarga teselas (`exportImage`) y clasifica cada píxel con NDVI,
NDWI y brillo. Separa además la sombra, que en modo servidor queda dentro de
"impermeable".

```bash
baqgeo indicadores --zonas barrios.gpkg --capa U_BARRIO --modo local \
  --salida resultados/barrios_local --guardar-clases
```

Antes de descargar estima el tamaño y se detiene si pasa de `--max-gb` (2 GB por
defecto). Barranquilla completa son unos 0,9 GB a 1,2 m con 4 bandas. Las teselas
quedan en `datos/teselas/` y se reutilizan. `--guardar-clases` escribe el raster
de clases para revisarlo en QGIS (uso interno, no publicar).

**Entornos de colegios, paraderos, puestos de salud.** Con una capa de puntos,
cada punto se convierte en un círculo:

```bash
baqgeo indicadores --zonas colegios.geojson --buffer 300 --prefijo colegios
```

Otras opciones útiles:

| Opción | Para qué |
|---|---|
| `--donde "NOMBRE=EL PRADO"` | analizar una sola zona |
| `--poblacion COLUMNA` | agrega m² de vegetación por habitante |
| `--pixel 1.2` | tamaño de píxel del análisis (ver advertencias) |
| `--umbral-vegetacion 0.3` | NDVI mínimo para contar como vegetación |
| `--funcion-ndvi ndvi` | la función que recomiende `validar` |
| `--regla-ndvi '{...}'` | una regla de renderizado propia |
| `--pausa 0.2` | segundos entre peticiones, para no cargar el servidor |

## Resultados

En `--salida` quedan cuatro archivos:

- `indicadores.gpkg`: zonas en EPSG:9377 con todos los campos, para QGIS.
- `indicadores.geojson`: WGS84, geometría simplificada a 1 m, para el visor web.
- `indicadores.meta.json`: modo, umbrales, bandas y fecha del cálculo.
- `indicadores_ranking.csv`: ordenado de menos a más vegetación.

| Campo | Significado |
|---|---|
| `area_m2`, `area_valida_m2` | área de la zona y área con dato de imagen |
| `cobertura_pct` | % de la zona cubierto por la imagen (revisa las < 90 %) |
| `ndvi_medio` | NDVI promedio |
| `pct_vegetacion` | NDVI ≥ umbral de vegetación |
| `pct_impermeable` | resto no vegetado: techos, vías, concreto, suelo desnudo (y sombra en modo servidor) |
| `pct_agua` | NDVI < 0 (servidor) o NDWI alto con NDVI < 0 (local) |
| `pct_sombra` | solo en modo local |
| `m2_vegetacion`, `m2_impermeable` | áreas absolutas |
| `m2_vegetacion_por_habitante` | si se dio `--poblacion` |
| `rank_menos_vegetacion`, `rank_mas_impermeable` | 1 = peor zona |

## Visor web (MapLibre)

`baqgeo visor` arma una carpeta estática con el mapa coroplético, la leyenda, el
ranking buscable y la ficha de cada zona. Puede llevar varias capas (barrios,
entornos de colegios...):

```bash
baqgeo visor \
  --capa "Barrios=resultados/barrios/indicadores.geojson" \
  --capa "Entornos de colegios=resultados/barrios/colegios.geojson" \
  --titulo "¿Cuánto verde tiene tu barrio?" \
  --salida resultados/visor --servir
```

Abre `http://127.0.0.1:8000`. Para probarlo con la demo:
`baqgeo demo` y luego `python -m http.server 8000 --directory resultados/demo/visor`.

Qué hace el visor:

- Colorea cada zona por quintiles del indicador elegido: vegetación, impermeable,
  NDVI medio o verde por habitante (solo los que tengan datos).
- Al pasar el puntero muestra el valor; al hacer clic abre la ficha con todas las
  cifras, su puesto en el ranking y la mediana de las zonas.
- El ranking es la vista en tabla del mapa: se puede buscar por nombre e invertir
  el orden.
- El fondo puede ser el mapa base o la imagen 2026 consultada directamente al
  ImageServer: vista por defecto del servicio, falso color infrarrojo o NDVI
  coloreado por el servidor. Si el servicio no devuelve una vista (CORS o función
  no habilitada), el visor lo avisa y el resto sigue funcionando.
- La URL guarda capa, indicador, fondo y zona (`#capa=barrios&zona=12`), así que
  se puede compartir el enlace a un barrio.
- Modo claro y oscuro según el sistema; se ve bien en móvil.
- Muestra el método (fecha, modo, umbral) desde el `.meta.json` que escribe
  `indicadores`, y un aviso cuando los datos son de demostración.

### Publicar en GitHub Pages

El workflow `.github/workflows/visor-pages.yml` publica el visor cada vez que
llega un cambio a `main`:

1. Pon en `sitio/` los GeoJSON de `indicadores` con su `.meta.json` y un
   `sitio/visor.json` (copia `sitio/visor.ejemplo.json`). Los pasos están en
   [`sitio/LEEME.md`](sitio/LEEME.md).
2. Una sola vez, en GitHub: **Settings → Pages → Source: GitHub Actions**.
3. Sube a `main`. El sitio queda en `https://lmfreite.github.io/verde-barranquilla/`.

El workflow instala el paquete, corre
`baqgeo visor --manifiesto sitio/visor.json --solo-datos-reales` y despliega el
resultado. Si no existe `sitio/visor.json` no publica nada, y falla si alguna
capa viene de la demo o no tiene `.meta.json`, para no publicar datos sintéticos.

La carpeta que arma `baqgeo visor` sirve igual en cualquier otro hosting estático,
por ejemplo `npx wrangler pages deploy resultados/visor --project-name verde-barranquilla`.

Notas:

- MapLibre 5.24 se carga desde unpkg (la rama 6 solo trae módulos ES y su worker
  no carga bien desde otro dominio). El mapa base es de CARTO/OpenStreetMap.
- Las vistas "infrarrojo" y "NDVI" usan funciones raster de ArcGIS (`Stretch`,
  `ExtractBand`, `NDVI`, `Colormap`) que dependen de lo que habilite el servicio.
- Los colores son rampas de un solo tono (verde para vegetación, naranja para
  impermeable), validadas para daltonismo y contraste en modo claro y oscuro.

## Advertencias de método

- **Pansharpening.** Los 34 cm salen de fusionar la multiespectral (1,16 m en
  nadir) con la pancromática. Por eso los índices se calculan a 1,2 m: a 34 cm no
  hay más información espectral, solo más ruido.
- **Sin SWIR.** Legion no tiene infrarrojo de onda corta. Sin esa banda el suelo
  desnudo no se distingue bien del concreto, así que "impermeable" significa
  realmente "no vegetado". En lotes de tierra el valor sobreestima la
  impermeabilidad real.
- **Niveles digitales.** Si la imagen no tiene corrección atmosférica, los
  umbrales de la literatura no aplican tal cual. Calíbralos con `validar` y
  algunos puntos conocidos en QGIS. La comparación entre barrios suele ser más
  estable que los valores absolutos.
- **Una sola fecha.** Es una foto puntual (según el servicio, 28 de junio de 2026;
  confírmalo con `inspeccionar`): los pastos y lotes enmontados pesan distinto en
  época seca.
- **Sombras.** En modo servidor la sombra de edificios suma como impermeable; un
  árbol en sombra sigue contando como vegetación porque el NDVI es un cociente.
- **Agua oscura y sombra** pueden confundirse en modo local.

## Licencia de la imagen y publicación

WorldView es imagen comercial: la licencia de la Alcaldía probablemente no
permite redistribuir la imagen ni teselas derivadas.

- Se publica: este código, el visor y las estadísticas por zona (`.geojson`, `.csv`).
- No se publica: `datos/` (teselas, caché) ni los rasters de clases. Están en
  `.gitignore`.
- El visor público debe consumir la imagen directamente del servicio de la
  Alcaldía.
- Subir derivados a OpenStreetMap requiere permiso explícito.

## Estructura

```
baqgeo/
  config.py        URL del servicio, orden de bandas de Legion, umbrales
  imageserver.py   cliente REST: info, query, exportImage, histogramas, getSamples
  zonas.py         carga de barrios/colegios desde archivo o capa ArcGIS
  indices.py       NDVI, NDWI, clasificación por píxel, histogramas
  estadisticas.py  modo servidor y modo local
  descarga.py      teselas alineadas a la grilla del servicio
  validacion.py    NDVI del servidor contra el cálculo local
  salidas.py       ranking y escritura de GPKG/GeoJSON/CSV (+ .meta.json)
  publicar.py      arma la carpeta del visor y la sirve en local
  web/             visor estático: index.html, visor.css, visor.js (MapLibre)
  demo.py          escena sintética e ImageServer simulado (demo y pruebas)
  cli.py           comandos
sitio/             capas y manifiesto que publica GitHub Pages (ver LEEME.md)
tests/             pytest, sin red (corren en cada push: .github/workflows/pruebas.yml)
```
