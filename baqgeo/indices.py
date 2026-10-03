"""Índices espectrales y clasificación por píxel."""

import numpy as np

from .config import Umbrales

SIN_DATO, VEGETACION, IMPERMEABLE, AGUA, SOMBRA = 0, 1, 2, 3, 4
NOMBRES_CLASES = {
    SIN_DATO: "sin_dato",
    VEGETACION: "vegetacion",
    IMPERMEABLE: "impermeable",
    AGUA: "agua",
    SOMBRA: "sombra",
}


def indice_normalizado(a, b):
    """(a - b) / (a + b), con NaN donde la suma es cero."""
    a = np.asarray(a, dtype="float32")
    b = np.asarray(b, dtype="float32")
    suma = a + b
    with np.errstate(divide="ignore", invalid="ignore"):
        resultado = np.where(suma != 0, (a - b) / suma, np.nan)
    return resultado.astype("float32")


def ndvi(nir, red):
    return indice_normalizado(nir, red)


def ndwi(green, nir):
    """NDWI de McFeeters: resalta agua abierta."""
    return indice_normalizado(green, nir)


def brillo(bandas):
    return (
        bandas["blue"].astype("float32")
        + bandas["green"].astype("float32")
        + bandas["red"].astype("float32")
    ) / 3


def pixeles_validos(bandas, nodata=0):
    validos = None
    for nombre in ("blue", "green", "red", "nir"):
        banda = bandas[nombre] != nodata
        validos = banda if validos is None else validos & banda
    return validos


def clasificar(bandas, umbrales=Umbrales(), brillo_ref=None, nodata=0):
    """Clasifica cada píxel en vegetación, impermeable, agua o sombra.

    `bandas` es un dict con arrays "blue", "green", "red" y "nir".
    `brillo_ref` es el brillo mediano del área de estudio; si no se da, se usa
    la mediana del propio arreglo. Devuelve (clases uint8, ndvi float32).

    Sin SWIR no se separa bien el suelo desnudo del concreto, así que el suelo
    desnudo queda dentro de "impermeable".
    """
    validos = pixeles_validos(bandas, nodata)
    v = ndvi(bandas["nir"], bandas["red"])
    w = ndwi(bandas["green"], bandas["nir"])
    b = brillo(bandas)
    if brillo_ref is None:
        brillo_ref = float(np.median(b[validos])) if validos.any() else 0.0

    clases = np.full(validos.shape, SIN_DATO, dtype="uint8")
    clases[validos] = IMPERMEABLE
    clases[validos & (b < umbrales.sombra_brillo * brillo_ref)] = SOMBRA
    # El NDVI es un cociente y aguanta mejor la sombra que el brillo:
    # un árbol en sombra sigue contando como vegetación.
    clases[validos & (v >= umbrales.vegetacion_ndvi)] = VEGETACION
    clases[validos & (w > umbrales.agua_ndwi) & (v < umbrales.agua_ndvi)] = AGUA
    v[~validos] = np.nan
    return clases, v


def fraccion_en_rango(histograma, desde=-np.inf, hasta=np.inf):
    """Fracción de los conteos de un histograma con valores en [desde, hasta).

    `histograma` tiene la forma que devuelve ArcGIS: {"min", "max", "counts"}.
    Dentro de cada intervalo se asume distribución uniforme.
    """
    conteos = np.asarray(histograma["counts"], dtype="float64")
    total = conteos.sum()
    if total == 0:
        return float("nan")
    bordes = np.linspace(histograma["min"], histograma["max"], len(conteos) + 1)
    izq, der = bordes[:-1], bordes[1:]
    ancho = der - izq
    dentro = np.clip(np.minimum(hasta, der) - np.maximum(desde, izq), 0, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        fraccion = np.where(
            ancho > 0,
            dentro / ancho,
            ((izq >= desde) & (izq < hasta)).astype("float64"),
        )
    return float((conteos * fraccion).sum() / total)
