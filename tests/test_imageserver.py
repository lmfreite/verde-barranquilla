import json

import pytest
from shapely.geometry import LinearRing, MultiPolygon, Polygon, box

from baqgeo import imageserver
from baqgeo.config import BANDAS_LEGION
from baqgeo.demo import Respuesta, esri_a_poligono
from baqgeo.imageserver import ImageServer, ImageServerError


def test_poligono_a_esri_orientacion():
    con_hueco = Polygon(box(0, 0, 10, 10).exterior.coords, [box(2, 2, 4, 4).exterior.coords])
    esri = imageserver.poligono_a_esri(con_hueco, 9377)
    exterior, hueco = (LinearRing(a) for a in esri["rings"])
    assert not exterior.is_ccw  # horario
    assert hueco.is_ccw
    assert esri["spatialReference"] == {"wkid": 9377}
    assert esri_a_poligono(esri).equals(con_hueco)


def test_poligono_a_esri_multipoligono():
    multi = MultiPolygon([box(0, 0, 1, 1), box(5, 5, 6, 6)])
    esri = imageserver.poligono_a_esri(multi, 9377)
    assert len(esri["rings"]) == 2
    assert esri_a_poligono(esri).equals(multi)


def test_mapa_de_bandas_por_nombre():
    info = {"bandCount": 8, "bandNames": ["Coastal", "Blue", "Green", "Yellow", "Red",
                                          "Red Edge 1", "Red Edge 2", "NIR"]}
    mapa, auto = imageserver.mapa_de_bandas(info)
    assert auto
    assert mapa == BANDAS_LEGION


def test_mapa_de_bandas_orden_distinto():
    info = {"bandCount": 4, "bandNames": ["NIR", "Red", "Green", "Blue"]}
    mapa, auto = imageserver.mapa_de_bandas(info)
    assert auto
    assert mapa == {"nir": 1, "red": 2, "green": 3, "blue": 4}


def test_mapa_de_bandas_nombres_genericos():
    mapa, auto = imageserver.mapa_de_bandas({"bandCount": 8, "bandNames": [f"Band_{i}" for i in range(1, 9)]})
    assert not auto
    assert mapa == BANDAS_LEGION
    with pytest.raises(ImageServerError):
        imageserver.mapa_de_bandas({"bandCount": 3, "bandNames": ["a", "b", "c"]})


def test_leer_mapa_bandas():
    assert imageserver.leer_mapa_bandas("blue=2, green=3,red=5,nir=8") == {
        "blue": 2, "green": 3, "red": 5, "nir": 8}
    with pytest.raises(ValueError):
        imageserver.leer_mapa_bandas("red=5,nir=8")


def test_regla_ndvi_indices():
    ba = imageserver.regla_ndvi(BANDAS_LEGION)
    assert ba["rasterFunctionArguments"]["BandIndexes"] == "8 5"
    clasica = imageserver.regla_ndvi(BANDAS_LEGION, "ndvi")
    assert clasica["rasterFunctionArguments"]["InfraredBandID"] == 7
    assert clasica["rasterFunctionArguments"]["VisibleBandID"] == 4


class SesionGuionada:
    """Devuelve respuestas en orden y registra las peticiones."""

    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.peticiones = []

    def _siguiente(self, url, params):
        self.peticiones.append((url, params))
        return self.respuestas.pop(0)

    def get(self, url, params=None, timeout=None):
        return self._siguiente(url, params)

    def post(self, url, data=None, timeout=None):
        return self._siguiente(url, data)


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    monkeypatch.setattr(imageserver.time, "sleep", lambda s: None)


def test_reintenta_errores_5xx():
    sesion = SesionGuionada(Respuesta({}, estado=503), Respuesta({"bandCount": 8}))
    servidor = ImageServer("https://x.invalid/ImageServer", session=sesion)
    assert servidor.info()["bandCount"] == 8
    assert len(sesion.peticiones) == 2


def test_no_reintenta_errores_4xx():
    sesion = SesionGuionada(Respuesta({"error": {"message": "prohibido"}}, estado=403))
    servidor = ImageServer("https://x.invalid/ImageServer", session=sesion)
    with pytest.raises(ImageServerError, match="403"):
        servidor.info()
    assert len(sesion.peticiones) == 1


def test_error_arcgis_en_json():
    sesion = SesionGuionada(Respuesta({"error": {"code": 400, "message": "Invalid rendering rule"}}))
    servidor = ImageServer("https://x.invalid/ImageServer", session=sesion)
    with pytest.raises(ImageServerError, match="Invalid rendering rule"):
        servidor.info()


def test_exportar_rechaza_json_sin_imagen():
    sesion = SesionGuionada(Respuesta({"bandCount": 8}), Respuesta({"href": "x"}))
    servidor = ImageServer("https://x.invalid/ImageServer", session=sesion)
    with pytest.raises(ImageServerError, match="imagen"):
        servidor.exportar((0, 0, 1, 1), 10, 10, [1, 2])


def test_parametros_de_histogramas():
    sesion = SesionGuionada(
        Respuesta({"spatialReference": {"wkid": 9377}}),
        Respuesta({"statistics": [], "histograms": []}),
    )
    servidor = ImageServer("https://x.invalid/ImageServer", session=sesion)
    servidor.histogramas(box(0, 0, 10, 10), imageserver.regla_ndvi(BANDAS_LEGION), pixel=1.2)
    url, params = sesion.peticiones[-1]
    assert url.endswith("/computeStatisticsHistograms")
    assert params["pixelSize"] == "1.2,1.2"
    assert json.loads(params["renderingRule"])["rasterFunction"] == "BandArithmetic"
    assert json.loads(params["geometry"])["spatialReference"] == {"wkid": 9377}


def test_muestras_por_location_id():
    sesion = SesionGuionada(
        Respuesta({"spatialReference": {"wkid": 9377}}),
        Respuesta({"samples": [{"locationId": 1, "value": "10 20 30"},
                               {"locationId": 0, "value": "NoData"}]}),
    )
    servidor = ImageServer("https://x.invalid/ImageServer", session=sesion)
    assert servidor.muestras([(0, 0), (1, 1), (2, 2)]) == [None, [10.0, 20.0, 30.0], None]
