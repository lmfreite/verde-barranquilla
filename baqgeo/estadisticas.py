"""NDVI, % vegetación e % impermeable por zona.

Dos modos con la misma salida:

- servidor: el ImageServer calcula el NDVI y devuelve un histograma por zona
  (`computeStatisticsHistograms`). No se descarga ni se guarda ningún píxel.
  Clases: vegetación (NDVI alto), agua (NDVI negativo) e impermeable (resto).
- local: se descargan teselas con exportImage y se clasifica cada píxel con
  NDVI, NDWI y brillo. Separa además la sombra, que en modo servidor queda
  dentro de "impermeable".
"""

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask
from affine import Affine
from rasterio.windows import Window
from shapely import STRtree
from shapely.geometry import box

from . import indices
from .config import PIXEL_ANALISIS_M, Umbrales
from .imageserver import ImageServerError, regla_ndvi

COLUMNAS = [
    "zona_id",
    "nombre",
    "area_m2",
    "area_valida_m2",
    "cobertura_pct",
    "ndvi_medio",
    "pct_vegetacion",
    "pct_vegetacion_densa",
    "pct_vegetacion_rala",
    "pct_impermeable",
    "pct_agua",
    "pct_sombra",
]


def _fila(zona, area_pixel, validos, vegetacion, impermeable, agua, sombra, ndvi_medio, densa):
    """Conteos de píxeles -> porcentajes. `densa` es la parte de `vegetacion`
    con NDVI alto (verde denso); el resto es pasto o verde ralo."""
    area = zona.geometry.area
    if not validos:
        return {
            "zona_id": zona.zona_id,
            "nombre": zona.nombre,
            "area_m2": area,
            "area_valida_m2": 0.0,
            "cobertura_pct": 0.0,
            **{c: math.nan for c in COLUMNAS[5:]},
        }
    return {
        "zona_id": zona.zona_id,
        "nombre": zona.nombre,
        "area_m2": area,
        "area_valida_m2": validos * area_pixel,
        "cobertura_pct": min(100.0, 100 * validos * area_pixel / area) if area else math.nan,
        "ndvi_medio": ndvi_medio,
        "pct_vegetacion": 100 * vegetacion / validos,
        "pct_vegetacion_densa": 100 * densa / validos,
        "pct_vegetacion_rala": 100 * max(0.0, vegetacion - densa) / validos,
        "pct_impermeable": 100 * impermeable / validos,
        "pct_agua": 100 * agua / validos,
        "pct_sombra": math.nan if sombra is None else 100 * sombra / validos,
    }


# ---------------------------------------------------------------- servidor


def escala_ndvi(estadisticas, histograma):
    """(a, b) tales que NDVI = a * valor + b.

    La función NDVI de ArcGIS sin salida científica entrega NDVI*100+100
    (rango 0-200); se detecta y se corrige.
    """
    maximo = max(histograma.get("max", 0), estadisticas.get("max", 0))
    if maximo > 1.5:
        return 0.01, -1.0
    return 1.0, 0.0


def fila_desde_histograma(zona, respuesta, umbrales, pixel):
    estadisticas = (respuesta.get("statistics") or [{}])[0]
    histograma = (respuesta.get("histograms") or [None])[0]
    total = sum(histograma["counts"]) if histograma else 0
    if not total:
        return _fila(zona, pixel * pixel, 0, 0, 0, 0, None, math.nan, 0)
    a, b = escala_ndvi(estadisticas, histograma)
    hist_ndvi = {
        "min": a * histograma["min"] + b,
        "max": a * histograma["max"] + b,
        "counts": histograma["counts"],
    }
    f_vegetacion = indices.fraccion_en_rango(hist_ndvi, desde=umbrales.vegetacion_ndvi)
    f_densa = indices.fraccion_en_rango(
        hist_ndvi, desde=max(umbrales.vegetacion_densa_ndvi, umbrales.vegetacion_ndvi)
    )
    f_agua = indices.fraccion_en_rango(hist_ndvi, hasta=umbrales.agua_ndvi)
    f_impermeable = max(0.0, 1.0 - f_vegetacion - f_agua)
    media = estadisticas.get("mean")
    return _fila(
        zona,
        pixel * pixel,
        total,
        f_vegetacion * total,
        f_impermeable * total,
        f_agua * total,
        None,
        a * media + b if media is not None else math.nan,
        f_densa * total,
    )


def _consulta_cacheada(cache, geometria, regla, pixel, consultar):
    if cache is None:
        return consultar()
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    clave = hashlib.sha1(
        geometria.wkb + json.dumps(regla, sort_keys=True).encode() + str(pixel).encode()
    ).hexdigest()
    ruta = cache / f"{clave}.json"
    if ruta.exists():
        return json.loads(ruta.read_text())
    respuesta = consultar()
    ruta.write_text(json.dumps(respuesta))
    return respuesta


def estadisticas_servidor(servidor, zonas, bandas, umbrales=Umbrales(), pixel=PIXEL_ANALISIS_M,
                          regla=None, cache=None, progreso=print):
    """Una petición por zona; las respuestas se guardan en `cache` para no repetirlas.

    Si una zona falla, queda sin dato y se sigue con las demás; solo se aborta
    si fallan todas.
    """
    regla = regla or regla_ndvi(bandas)
    filas = []
    fallidas = 0
    for k, zona in enumerate(zonas.itertuples(), start=1):
        # Simplificar a medio píxel no cambia el resultado y acorta la petición.
        geometria = zona.geometry.simplify(pixel / 2, preserve_topology=True)
        try:
            respuesta = _consulta_cacheada(
                cache, geometria, regla, pixel,
                lambda: servidor.histogramas(geometria, regla, pixel),
            )
        except ImageServerError as error:
            fallidas += 1
            progreso(f"  zona {k}/{len(zonas)} {zona.nombre}: FALLÓ ({error})")
            respuesta = {}
        else:
            progreso(f"  zona {k}/{len(zonas)} {zona.nombre}")
        filas.append(fila_desde_histograma(zona, respuesta, umbrales, pixel))
    if fallidas == len(zonas):
        raise ImageServerError("Fallaron todas las zonas; revisa el servicio con `baqgeo validar`.")
    if fallidas:
        progreso(f"Aviso: {fallidas} de {len(zonas)} zonas quedaron sin dato por errores del servicio.")
    return pd.DataFrame(filas, columns=COLUMNAS)


# ------------------------------------------------------------------- local


def leer_bandas(src, ventana=None, forma=None):
    """Lee por nombre las bandas de una tesela creada por `descargar_teselas`."""
    nombres = list(src.descriptions)
    faltan = [n for n in ("blue", "green", "red", "nir") if n not in nombres]
    if faltan:
        raise ValueError(f"{src.name}: faltan bandas {faltan} (descripciones: {nombres})")
    return {
        n: src.read(nombres.index(n) + 1, window=ventana, out_shape=forma)
        for n in ("blue", "green", "red", "nir")
    }


def brillo_de_referencia(rutas, max_lado=512):
    """Brillo mediano de toda el área, leyendo cada tesela reducida."""
    muestras = []
    for ruta in rutas:
        with rasterio.open(ruta) as src:
            escala = max(1, math.ceil(max(src.width, src.height) / max_lado))
            forma = (math.ceil(src.height / escala), math.ceil(src.width / escala))
            bandas = leer_bandas(src, forma=forma)
        validos = indices.pixeles_validos(bandas)
        muestras.append(indices.brillo(bandas)[validos])
    todas = np.concatenate(muestras) if muestras else np.array([])
    return float(np.median(todas)) if todas.size else 0.0


def _ventana_de(geometria, transform, alto, ancho):
    """Ventana de la tesela (norte arriba) que contiene la geometría."""
    xmin, ymin, xmax, ymax = geometria.bounds
    c0, c1 = (xmin - transform.c) / transform.a, (xmax - transform.c) / transform.a
    f0, f1 = (ymax - transform.f) / transform.e, (ymin - transform.f) / transform.e
    c0, f0 = max(0, math.floor(c0)), max(0, math.floor(f0))
    c1, f1 = min(ancho, math.ceil(c1)), min(alto, math.ceil(f1))
    if c1 <= c0 or f1 <= f0:
        return None
    return Window(c0, f0, c1 - c0, f1 - f0)


def estadisticas_local(rutas, zonas, umbrales=Umbrales(), carpeta_clases=None, progreso=print):
    """Clasifica cada tesela y acumula conteos por zona (las zonas pueden solaparse)."""
    referencia = brillo_de_referencia(rutas)
    geometrias = list(zonas.geometry)
    arbol = STRtree(geometrias)
    # validos, vegetacion, impermeable, agua, sombra, suma_ndvi, vegetacion densa
    acumulado = np.zeros((len(zonas), 7), dtype="float64")
    umbral_denso = max(umbrales.vegetacion_densa_ndvi, umbrales.vegetacion_ndvi)
    area_pixel = None

    for k, ruta in enumerate(rutas, start=1):
        with rasterio.open(ruta) as src:
            bandas = leer_bandas(src)
            transform, alto, ancho = src.transform, src.height, src.width
            limites, perfil = src.bounds, src.profile
        area_pixel = area_pixel or abs(transform.a * transform.e)
        clases, ndvi = indices.clasificar(bandas, umbrales, brillo_ref=referencia)

        if carpeta_clases:
            carpeta = Path(carpeta_clases)
            carpeta.mkdir(parents=True, exist_ok=True)
            perfil.update(count=1, dtype="uint8", nodata=0)
            with rasterio.open(carpeta / Path(ruta).name, "w", **perfil) as dst:
                dst.write(clases, 1)

        for i in arbol.query(box(*limites), predicate="intersects"):
            geometria = geometrias[i]
            ventana = _ventana_de(geometria, transform, alto, ancho)
            if ventana is None:
                continue
            filas = slice(ventana.row_off, ventana.row_off + ventana.height)
            cols = slice(ventana.col_off, ventana.col_off + ventana.width)
            dentro = geometry_mask(
                [geometria],
                out_shape=(ventana.height, ventana.width),
                transform=Affine(transform.a, 0.0, transform.c + ventana.col_off * transform.a,
                                 0.0, transform.e, transform.f + ventana.row_off * transform.e),
                invert=True,
            )
            c = clases[filas, cols][dentro]
            v = ndvi[filas, cols][dentro]
            acumulado[i] += [
                np.count_nonzero(c != indices.SIN_DATO),
                np.count_nonzero(c == indices.VEGETACION),
                np.count_nonzero(c == indices.IMPERMEABLE),
                np.count_nonzero(c == indices.AGUA),
                np.count_nonzero(c == indices.SOMBRA),
                np.nansum(v),
                np.count_nonzero((c == indices.VEGETACION) & (v >= umbral_denso)),
            ]
        progreso(f"  clasificada {k}/{len(rutas)} {Path(ruta).name}")

    filas_df = []
    for zona, (validos, veg, imp, agua, sombra, suma, densa) in zip(zonas.itertuples(), acumulado):
        filas_df.append(_fila(
            zona, area_pixel or 0.0, int(validos), veg, imp, agua, sombra,
            suma / validos if validos else math.nan, densa,
        ))
    return pd.DataFrame(filas_df, columns=COLUMNAS)
