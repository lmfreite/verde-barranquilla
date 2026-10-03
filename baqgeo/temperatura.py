"""Temperatura de la superficie por zona con Landsat 8/9 (Colección 2, nivel 2).

Usa la banda térmica ST_B10 (`lwir11`) del catálogo STAC abierto de Microsoft
Planetary Computer, que no pide API key. Para cada píxel de 30 m se toma la
mediana de todas las pasadas sin nubes del periodo y luego se promedia por zona.

Landsat pasa sobre Barranquilla hacia las 10:30 de la mañana: es la temperatura
de la superficie (techos, calles, suelo, copas de árboles) a media mañana, no la
del aire. Sirve para comparar zonas entre sí, no como lectura de termómetro.
Los datos de Landsat son de dominio público (USGS).
"""

import math
import warnings
from datetime import date

import numpy as np
import requests
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
TOKEN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{coleccion}"
COLECCION = "landsat-c2-l2"
# ST_B10 en niveles digitales -> kelvin (Colección 2, nivel 2).
ESCALA, DESPLAZAMIENTO = 0.00341802, 149.0
# QA_PIXEL: relleno, nube dilatada, cirro, nube y sombra de nube.
BITS_DESCARTE = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4)
PIXEL_M = 30
FUENTE = "Landsat 8/9 Colección 2 nivel 2, banda ST_B10 (USGS), vía Microsoft Planetary Computer"


def buscar_escenas(bbox, desde, hasta, nubes_max=40, session=None):
    """Escenas Landsat 8/9 que tocan `bbox` (lon/lat) con menos de `nubes_max` % de nubes."""
    session = session or requests.Session()
    url = f"{STAC_URL}/search"
    cuerpo = {
        "collections": [COLECCION],
        "bbox": [float(v) for v in bbox],
        "datetime": f"{desde}/{hasta}",
        "query": {
            "eo:cloud_cover": {"lt": nubes_max},
            "platform": {"in": ["landsat-8", "landsat-9"]},
        },
        "limit": 100,
    }
    metodo = "POST"
    escenas = []
    while url:
        if metodo == "POST":
            r = session.post(url, json=cuerpo, timeout=120)
        else:
            r = session.get(url, timeout=120)
        r.raise_for_status()
        datos = r.json()
        escenas.extend(datos.get("features", []))
        siguiente = next((l for l in datos.get("links", []) if l.get("rel") == "next"), None)
        if not siguiente:
            break
        url = siguiente["href"]
        metodo = siguiente.get("method", "GET").upper()
        cuerpo = siguiente.get("body", cuerpo)
    return escenas


class Firmador:
    """Agrega el token SAS anónimo de Planetary Computer a las URL de los archivos."""

    def __init__(self, session=None):
        self.session = session or requests.Session()
        self._token = None

    def __call__(self, href):
        if not href.startswith("http"):
            return href
        if self._token is None:
            r = self.session.get(TOKEN_URL.format(coleccion=COLECCION), timeout=60)
            r.raise_for_status()
            self._token = r.json()["token"]
        return f"{href}{'&' if '?' in href else '?'}{self._token}"


def _ventana(transform, limites, ancho, alto):
    xmin, ymin, xmax, ymax = limites
    c0 = math.floor((xmin - transform.c) / transform.a)
    c1 = math.ceil((xmax - transform.c) / transform.a)
    f0 = math.floor((ymax - transform.f) / transform.e)
    f1 = math.ceil((ymin - transform.f) / transform.e)
    c0, f0 = max(0, c0), max(0, f0)
    c1, f1 = min(ancho, c1), min(alto, f1)
    if c1 <= c0 or f1 <= f0:
        return None
    return Window(c0, f0, c1 - c0, f1 - f0)


def _leer_escena(escena, limites, crs_destino, firmar):
    """Temperatura en °C (NaN donde hay nubes o relleno) recortada a `limites`."""
    activos = escena["assets"]
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                      CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".TIF,.tif"):
        with rasterio.open(firmar(activos["lwir11"]["href"])) as st, \
                rasterio.open(firmar(activos["qa_pixel"]["href"])) as qa:
            limites_escena = transform_bounds(crs_destino, st.crs, *limites, densify_pts=21)
            ventana = _ventana(st.transform, limites_escena, st.width, st.height)
            if ventana is None:
                return None
            dn = st.read(1, window=ventana).astype("float64")
            calidad = qa.read(1, window=ventana)
            return {
                "celsius": np.where(
                    (dn > 0) & ((calidad & BITS_DESCARTE) == 0),
                    dn * ESCALA + DESPLAZAMIENTO - 273.15,
                    np.nan,
                ).astype("float32"),
                "transform": st.window_transform(ventana),
                "crs": st.crs,
            }


def compuesto(escenas, limites, crs_destino, firmar, pixel=PIXEL_M, min_observaciones=3,
              progreso=print):
    """Mediana por píxel de todas las escenas, en una grilla de `pixel` m sobre `limites`.

    Devuelve (mediana, transform, fechas usadas, observaciones por píxel).
    """
    xmin, ymin, xmax, ymax = limites
    ancho = math.ceil((xmax - xmin) / pixel)
    alto = math.ceil((ymax - ymin) / pixel)
    transform = from_origin(xmin, ymax, pixel, pixel)
    capas, fechas = [], []
    for k, escena in enumerate(escenas, start=1):
        try:
            leida = _leer_escena(escena, limites, crs_destino, firmar)
        except Exception as error:  # noqa: BLE001 - una escena dañada no detiene el resto
            progreso(f"  {escena.get('id')}: no se pudo leer ({type(error).__name__}: {error})")
            continue
        if leida is None:
            continue
        destino = np.full((alto, ancho), np.nan, dtype="float32")
        reproject(
            leida["celsius"], destino,
            src_transform=leida["transform"], src_crs=leida["crs"],
            dst_transform=transform, dst_crs=crs_destino,
            src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.nearest,
        )
        validos = int(np.isfinite(destino).sum())
        if validos:
            capas.append(destino)
            fechas.append(escena["properties"]["datetime"][:10])
        progreso(f"  escena {k}/{len(escenas)} {escena.get('id')}: {validos} píxeles sin nubes")
    if not capas:
        raise RuntimeError("Ninguna escena Landsat tuvo píxeles sin nubes sobre las zonas.")
    pila = np.stack(capas)
    observaciones = np.isfinite(pila).sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)  # columnas todas NaN
        mediana = np.nanmedian(pila, axis=0)
    mediana[observaciones < min_observaciones] = np.nan
    return mediana, transform, sorted(fechas), observaciones


def temperatura_por_zona(zonas, mediana, transform):
    """Promedio de la mediana dentro de cada zona (con los píxeles tocados si es muy pequeña)."""
    forma = mediana.shape
    valida = np.isfinite(mediana)
    resultado = []
    for geometria in zonas.geometry:
        valores = np.array([])
        for todos_los_tocados in (False, True):
            dentro = geometry_mask([geometria], out_shape=forma, transform=transform,
                                   invert=True, all_touched=todos_los_tocados)
            valores = mediana[dentro & valida]
            if valores.size >= 3:
                break
        resultado.append(float(valores.mean()) if valores.size else math.nan)
    return resultado


def temperatura_superficie(zonas, desde="2023-01-01", hasta=None, nubes_max=40,
                           session=None, firmar=None, progreso=print):
    """Temperatura de superficie media por zona. Devuelve (lista de °C, metadatos)."""
    hasta = hasta or date.today().isoformat()
    bbox = zonas.to_crs(4326).total_bounds
    escenas = buscar_escenas(bbox, desde, hasta, nubes_max, session)
    progreso(f"Landsat: {len(escenas)} escenas entre {desde} y {hasta} con menos de "
             f"{nubes_max} % de nubes")
    if not escenas:
        raise RuntimeError("No se encontraron escenas Landsat para el periodo.")
    xmin, ymin, xmax, ymax = zonas.total_bounds
    margen = 2 * PIXEL_M
    limites = (xmin - margen, ymin - margen, xmax + margen, ymax + margen)
    mediana, transform, fechas, observaciones = compuesto(
        escenas, limites, zonas.crs, firmar or Firmador(session), progreso=progreso,
    )
    temperaturas = temperatura_por_zona(zonas, mediana, transform)
    metadatos = {
        "fuente": FUENTE,
        "escenas": len(fechas),
        "desde": fechas[0],
        "hasta": fechas[-1],
        "pixel_m": PIXEL_M,
        "nubes_max_pct": nubes_max,
        "observaciones_mediana": int(np.median(observaciones[observaciones > 0])),
        "hora_local_aprox": "10:30",
        "metodo": "mediana por píxel de las pasadas sin nubes; promedio por zona",
    }
    return temperaturas, metadatos
