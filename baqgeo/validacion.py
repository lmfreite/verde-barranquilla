"""Comprueba que el NDVI calculado por el servidor coincide con el calculado aquí."""

import numpy as np
from shapely.geometry import Point

from .imageserver import ImageServerError, regla_ndvi

TOLERANCIA = 0.02


def puntos_aleatorios(geometria, n, semilla=0):
    rng = np.random.default_rng(semilla)
    xmin, ymin, xmax, ymax = geometria.bounds
    puntos = []
    intentos = 0
    while len(puntos) < n and intentos < n * 200:
        x, y = rng.uniform(xmin, xmax), rng.uniform(ymin, ymax)
        if geometria.contains(Point(x, y)):
            puntos.append((x, y))
        intentos += 1
    return puntos


def validar(servidor, bandas, geometria, n=40, funciones=("bandarithmetic", "ndvi")):
    """Muestrea `n` puntos y compara el NDVI del servidor con el local.

    Devuelve un dict con los rangos de cada banda, percentiles del NDVI local
    (útiles para calibrar umbrales) y, por cada función NDVI, si coincide.
    """
    puntos = puntos_aleatorios(geometria, n)
    crudos = servidor.muestras(puntos) if puntos else []
    n_bandas = max(bandas.values())
    buenos = [
        (p, v) for p, v in zip(puntos, crudos)
        if v and len(v) >= n_bandas and all(x > 0 for x in v[:n_bandas])
    ]
    resultado = {
        "n_puntos": len(buenos),
        "rangos_bandas": {},
        "ndvi_local_percentiles": {},
        "funciones": {},
        "recomendada": None,
        "advertencias": [],
    }
    if not buenos:
        return resultado

    valores = np.array([v[:n_bandas] for _, v in buenos], dtype="float64")
    for nombre, indice in bandas.items():
        columna = valores[:, indice - 1]
        resultado["rangos_bandas"][nombre] = (float(columna.min()), float(columna.max()))
    nir = valores[:, bandas["nir"] - 1]
    red = valores[:, bandas["red"] - 1]
    ndvi_local = (nir - red) / (nir + red)
    resultado["ndvi_local_percentiles"] = {
        f"p{q}": float(np.percentile(ndvi_local, q)) for q in (10, 25, 50, 75, 90)
    }
    # En una ciudad con árboles el NDVI alto debe aparecer en la muestra. Si
    # casi todo sale negativo, lo más probable es que rojo y NIR estén
    # invertidos; servidor y cálculo local coincidirían igual porque los dos
    # usan el mismo mapa de bandas.
    if resultado["ndvi_local_percentiles"]["p90"] < 0.1:
        resultado["advertencias"].append(
            "El NDVI sale casi todo negativo o muy bajo: revisa que red y nir no estén "
            "invertidos en el mapa de bandas, o que el área tenga vegetación."
        )

    puntos_buenos = [p for p, _ in buenos]
    for funcion in funciones:
        try:
            servidor_ndvi = servidor.muestras(puntos_buenos, regla_ndvi(bandas, funcion))
        except ImageServerError as e:
            resultado["funciones"][funcion] = {"estado": "error", "detalle": str(e)}
            continue
        pares = np.array(
            [(s[0], l) for s, l in zip(servidor_ndvi, ndvi_local) if s], dtype="float64"
        )
        if len(pares) == 0:
            resultado["funciones"][funcion] = {"estado": "sin_datos"}
            continue
        directa = float(np.median(np.abs(pares[:, 0] - pares[:, 1])))
        escalada = float(np.median(np.abs(pares[:, 0] / 100 - 1 - pares[:, 1])))
        if directa < TOLERANCIA:
            estado = "coincide"
        elif escalada < TOLERANCIA:
            estado = "coincide_escala_0_200"
        else:
            estado = "no_coincide"
        resultado["funciones"][funcion] = {
            "estado": estado,
            "diferencia_mediana": min(directa, escalada),
            "n": len(pares),
        }
        if estado != "no_coincide" and resultado["recomendada"] is None:
            resultado["recomendada"] = funcion
    return resultado
