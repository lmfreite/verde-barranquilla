"""Carga de zonas de análisis: barrios, microcuencas, entornos de colegios..."""

import geopandas as gpd
import requests
import shapely
from shapely.geometry import MultiPolygon, Polygon

from .config import EPSG_TRABAJO

COLUMNAS_NOMBRE = (
    "nombre",
    "nom_barrio",
    "nombre_barrio",
    "nombre_bar",
    "barrio",
    "name",
    "etiqueta",
)


def detectar_columna_nombre(gdf):
    columnas = {c.lower(): c for c in gdf.columns if c != gdf.geometry.name}
    for candidata in COLUMNAS_NOMBRE:
        if candidata in columnas:
            return columnas[candidata]
    return None


def descargar_capa_arcgis(url, session=None, por_pagina=1000):
    """Descarga todas las entidades de una capa FeatureServer/MapServer."""
    session = session or requests.Session()
    url = url.rstrip("/")
    if not url.endswith("/query"):
        url += "/query"
    entidades = []
    desplazamiento = 0
    while True:
        params = {
            "where": "1=1",
            "outFields": "*",
            "outSR": 4326,
            "f": "geojson",
            "resultOffset": desplazamiento,
            "resultRecordCount": por_pagina,
        }
        r = session.get(url, params=params, timeout=120)
        r.raise_for_status()
        datos = r.json()
        if "error" in datos:
            raise RuntimeError(f"{url}: {datos['error'].get('message')}")
        lote = datos.get("features", [])
        entidades.extend(lote)
        hay_mas = datos.get("exceededTransferLimit") or (
            datos.get("properties") or {}
        ).get("exceededTransferLimit")
        if not lote or not hay_mas:
            break
        desplazamiento += len(lote)
    return gpd.GeoDataFrame.from_features(entidades, crs=4326)


def _solo_poligonos(geometria):
    geometria = shapely.make_valid(geometria)
    if isinstance(geometria, (Polygon, MultiPolygon)):
        return geometria
    partes = [g for g in getattr(geometria, "geoms", []) if isinstance(g, (Polygon, MultiPolygon))]
    return shapely.union_all(partes) if partes else None


def cargar_zonas(fuente, capa=None, donde=None, buffer_m=None, columna_nombre=None,
                 epsg=EPSG_TRABAJO, session=None):
    """Carga zonas desde un archivo (GPKG, SHP, GeoJSON...) o una capa ArcGIS.

    - `donde`: filtro simple "COLUMNA=valor" (sin distinguir mayúsculas).
    - `buffer_m`: obligatorio para capas de puntos (p. ej. colegios): cada
      punto se convierte en un círculo de ese radio.
    Devuelve un GeoDataFrame en `epsg` con columnas `zona_id` y `nombre`.
    """
    if str(fuente).startswith(("http://", "https://")):
        gdf = descargar_capa_arcgis(str(fuente), session)
    else:
        gdf = gpd.read_file(fuente, layer=capa)
    if gdf.crs is None:
        raise ValueError(f"{fuente}: la capa no tiene sistema de coordenadas")
    if gdf.geometry.name != "geometry":
        gdf = gdf.rename_geometry("geometry")

    if donde:
        columna, _, valor = donde.partition("=")
        columna = columna.strip()
        if columna not in gdf.columns:
            raise ValueError(f"--donde: la columna {columna!r} no existe")
        gdf = gdf[gdf[columna].astype(str).str.strip().str.upper() == valor.strip().upper()]

    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].to_crs(epsg)
    if len(gdf) == 0:
        raise ValueError(f"{fuente}: no quedó ninguna zona")

    if set(gdf.geom_type) <= {"Point", "MultiPoint"}:
        if not buffer_m:
            raise ValueError("La capa es de puntos: indica --buffer en metros")
        gdf = gdf.set_geometry(gdf.geometry.buffer(buffer_m))

    poligonos = gpd.GeoSeries(
        [_solo_poligonos(g) for g in gdf.geometry], index=gdf.index, crs=gdf.crs, name="geometry"
    )
    gdf = gdf.set_geometry(poligonos)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].reset_index(drop=True)

    columna = columna_nombre or detectar_columna_nombre(gdf)
    if columna_nombre and columna_nombre not in gdf.columns:
        raise ValueError(f"--columna-nombre: la columna {columna_nombre!r} no existe")
    gdf["zona_id"] = range(1, len(gdf) + 1)
    if columna:
        gdf["nombre"] = gdf[columna].astype(str)
    else:
        gdf["nombre"] = [f"zona {i}" for i in gdf["zona_id"]]
    return gdf
