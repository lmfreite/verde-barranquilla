# sitio/ — lo que se publica en GitHub Pages

El workflow `.github/workflows/visor-pages.yml` arma el visor con lo que haya en
esta carpeta y lo publica en GitHub Pages cada vez que se sube un cambio a `master`.
Mientras no exista `visor.json`, el workflow no publica nada.

## Primera publicación

1. Calcula los indicadores con el servicio real (desde un equipo con acceso a
   `miciudad.barranquilla.gov.co`):

   ```bash
   baqgeo indicadores --zonas barrios.gpkg --capa U_BARRIO --salida resultados/barrios
   baqgeo indicadores --zonas colegios.geojson --buffer 300 --salida resultados/barrios --prefijo colegios
   ```

2. Copia aquí cada GeoJSON **con su `.meta.json`** (el visor muestra el método a
   partir de ese archivo):

   ```bash
   cp resultados/barrios/indicadores.geojson      sitio/barrios.geojson
   cp resultados/barrios/indicadores.meta.json    sitio/barrios.meta.json
   cp resultados/barrios/colegios.geojson         sitio/colegios.geojson
   cp resultados/barrios/colegios.meta.json       sitio/colegios.meta.json
   ```

3. Crea el manifiesto a partir del ejemplo y ajusta título y capas:

   ```bash
   cp sitio/visor.ejemplo.json sitio/visor.json
   ```

4. Prueba en local exactamente lo que va a publicar el workflow:

   ```bash
   baqgeo visor --manifiesto sitio/visor.json --solo-datos-reales --servir
   ```

5. Haz commit de `sitio/` y súbelo a `master`.

En GitHub, una sola vez: **Settings → Pages → Build and deployment → Source:
GitHub Actions**.

## Qué se puede subir aquí

Solo estadísticas por zona (`.geojson`, `.meta.json`). Nunca teselas, rasters ni
recortes de la imagen: su licencia es de la Alcaldía. El workflow falla si una
capa viene de `baqgeo demo` o no tiene `.meta.json`.
