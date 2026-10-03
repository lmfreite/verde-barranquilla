"""Une los indicadores a las zonas y los guarda para QGIS y para un visor web."""

import json
from pathlib import Path

import numpy as np


def armar_resultado(zonas, indicadores, columna_poblacion=None):
    columnas = ["zona_id", "geometry"]
    if columna_poblacion:
        if columna_poblacion not in zonas.columns:
            raise ValueError(f"--poblacion: la columna {columna_poblacion!r} no existe")
        columnas.insert(1, columna_poblacion)
    gdf = zonas[columnas].merge(indicadores, on="zona_id")
    gdf = gdf[["zona_id", "nombre", *[c for c in gdf.columns if c not in ("zona_id", "nombre")]]]

    gdf["m2_vegetacion"] = gdf["area_valida_m2"] * gdf["pct_vegetacion"] / 100
    gdf["m2_impermeable"] = gdf["area_valida_m2"] * gdf["pct_impermeable"] / 100
    if columna_poblacion:
        poblacion = gdf[columna_poblacion].astype("float64").replace(0, np.nan)
        gdf["m2_vegetacion_por_habitante"] = gdf["m2_vegetacion"] / poblacion
    # 1 = la zona con menos vegetación / más superficie impermeable.
    gdf["rank_menos_vegetacion"] = gdf["pct_vegetacion"].rank(method="min").astype("Int64")
    gdf["rank_mas_impermeable"] = (
        gdf["pct_impermeable"].rank(method="min", ascending=False).astype("Int64")
    )
    return gdf


def ruta_metadatos(geojson):
    """Archivo de metadatos que acompaña a un GeoJSON de indicadores."""
    geojson = Path(geojson)
    return geojson.with_name(f"{geojson.stem}.meta.json")


def guardar(gdf, carpeta, prefijo="indicadores", metadatos=None):
    """Escribe GPKG (EPSG del análisis), GeoJSON (WGS84, para MapLibre) y CSV.

    Solo salen estadísticas por zona: ningún píxel de la imagen. `metadatos`
    (modo, umbrales, fecha...) se guarda junto al GeoJSON para el visor.
    """
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    rutas = {
        "gpkg": carpeta / f"{prefijo}.gpkg",
        "geojson": carpeta / f"{prefijo}.geojson",
        "csv": carpeta / f"{prefijo}_ranking.csv",
    }
    if metadatos is not None:
        rutas["meta"] = ruta_metadatos(rutas["geojson"])
    for ruta in rutas.values():
        ruta.unlink(missing_ok=True)

    gdf.to_file(rutas["gpkg"], layer="indicadores_zonas", driver="GPKG")

    publico = gdf.copy()
    decimales = {c: (3 if c == "ndvi_medio" else 2) for c in publico.columns
                 if c not in ("zona_id", "nombre", "geometry") and publico[c].dtype.kind == "f"}
    publico = publico.round(decimales)
    publico = publico.set_geometry(publico.geometry.simplify(1.0, preserve_topology=True))
    publico.to_crs(4326).to_file(rutas["geojson"], driver="GeoJSON", COORDINATE_PRECISION=6)

    (
        gdf.drop(columns="geometry")
        .sort_values("pct_vegetacion", na_position="last")
        .round(decimales)
        .to_csv(rutas["csv"], index=False)
    )
    if metadatos is not None:
        rutas["meta"].write_text(json.dumps(metadatos, indent=2, ensure_ascii=False))
    return rutas
