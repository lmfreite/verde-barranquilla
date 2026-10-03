import math

import numpy as np

from baqgeo import indices
from baqgeo.config import Umbrales
from baqgeo.demo import FIRMAS


def _bandas_de(*clases):
    """Un píxel por clase, con las firmas de la demo."""
    orden = {"blue": 1, "green": 2, "red": 4, "nir": 7}
    return {n: np.array([[FIRMAS[c][i] for c in clases]], dtype="uint16") for n, i in orden.items()}


def test_ndvi_y_division_por_cero():
    v = indices.ndvi(np.array([3, 0]), np.array([1, 0]))
    assert v[0] == np.float32(0.5)
    assert math.isnan(v[1])


def test_clasificar_firmas():
    bandas = _bandas_de("vegetacion", "concreto", "asfalto", "agua", "sombra")
    clases, ndvi = indices.clasificar(bandas, Umbrales(), brillo_ref=1000)
    assert clases.tolist() == [[indices.VEGETACION, indices.IMPERMEABLE, indices.IMPERMEABLE,
                                indices.AGUA, indices.SOMBRA]]
    assert ndvi[0, 0] > 0.8


def test_clasificar_sin_dato():
    bandas = _bandas_de("concreto", "concreto")
    bandas["nir"][0, 1] = 0
    clases, ndvi = indices.clasificar(bandas)
    assert clases[0, 1] == indices.SIN_DATO
    assert math.isnan(ndvi[0, 1])


def test_arbol_en_sombra_sigue_siendo_vegetacion():
    bandas = {n: np.array([[v]], dtype="uint16")
              for n, v in {"blue": 60, "green": 120, "red": 70, "nir": 700}.items()}
    clases, _ = indices.clasificar(bandas, brillo_ref=1000)
    assert clases[0, 0] == indices.VEGETACION


def test_fraccion_en_rango_intervalos_completos_y_parciales():
    hist = {"min": 0.0, "max": 1.0, "counts": [10, 10, 10, 10]}  # bordes 0, .25, .5, .75, 1
    assert indices.fraccion_en_rango(hist, desde=0.5) == 0.5
    assert indices.fraccion_en_rango(hist, hasta=0.125) == 0.125
    assert indices.fraccion_en_rango(hist) == 1.0


def test_fraccion_en_rango_vacio_y_valor_unico():
    assert math.isnan(indices.fraccion_en_rango({"min": 0, "max": 1, "counts": [0, 0]}))
    unico = {"min": 0.4, "max": 0.4, "counts": [5]}
    assert indices.fraccion_en_rango(unico, desde=0.3) == 1.0
    assert indices.fraccion_en_rango(unico, desde=0.5) == 0.0
