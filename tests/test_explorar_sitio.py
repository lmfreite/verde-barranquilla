import json
import math

import geopandas as gpd
import pytest
import requests

from baqgeo import demo
from baqgeo.cli import main
from baqgeo.estadisticas import estadisticas_servidor
from baqgeo.explorar import buscar_capas
from baqgeo.imageserver import BANDAS_LEGION, ImageServer
from baqgeo.publicar import agregar_a_sitio


class _Resp:
    def __init__(self, datos, estado=200):
        self.datos, self.status_code = datos, estado

    def json(self):
        return self.datos

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class GeoportalFalso:
    """Directorio ArcGIS REST mínimo: una carpeta con un servicio de barrios."""

    BASE = "https://geo.invalid/server/rest/services"

    def get(self, url, params=None, timeout=None):
        rutas = {
            self.BASE: {"folders": ["Catastro"], "services": [{"name": "Base", "type": "MapServer"}]},
            f"{self.BASE}/Catastro": {"services": [
                {"name": "Catastro/Division", "type": "FeatureServer"},
                {"name": "Catastro/Orto", "type": "ImageServer"},
            ]},
            f"{self.BASE}/Base/MapServer": {"layers": [{"id": 0, "name": "Vias"}]},
            f"{self.BASE}/Catastro/Division/FeatureServer": {"layers": [
                {"id": 0, "name": "Barrios"}, {"id": 1, "name": "Manzanas"},
                {"id": 2, "name": "Instituciones Educativas"},
            ]},
            f"{self.BASE}/Catastro/Division/FeatureServer/0": {
                "geometryType": "esriGeometryPolygon",
                "fields": [{"name": "OBJECTID"}, {"name": "NOMBRE"}],
            },
            f"{self.BASE}/Catastro/Division/FeatureServer/0/query": {"count": 188},
            f"{self.BASE}/Catastro/Division/FeatureServer/2": {"geometryType": "esriGeometryPoint"},
            f"{self.BASE}/Catastro/Division/FeatureServer/2/query": {"count": 154},
        }
        if url in rutas:
            return _Resp(rutas[url])
        return _Resp({}, estado=404)


def test_buscar_capas_recorre_carpetas_y_filtra():
    encontrados = buscar_capas("https://geo.invalid", ["barrio", "educa"],
                               session=GeoportalFalso(), progreso=lambda m: None)
    assert [c["capa"] for c in encontrados] == ["Barrios", "Instituciones Educativas"]
    barrios = encontrados[0]
    assert barrios["url"].endswith("/Catastro/Division/FeatureServer/0")
    assert barrios["entidades"] == 188
    assert barrios["campos"] == ["OBJECTID", "NOMBRE"]
    assert encontrados[1]["geometria"] == "esriGeometryPoint"


@pytest.fixture(scope="module")
def corrida_demo(tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("demo")
    assert main(["demo", "--salida", str(carpeta)]) == 0
    return carpeta


def test_agregar_a_sitio_crea_y_reemplaza(corrida_demo, tmp_path):
    sitio = tmp_path / "sitio"
    geojson = corrida_demo / "barrios_servidor.geojson"
    agregar_a_sitio(sitio, "Barrios", "barrios", geojson)
    agregar_a_sitio(sitio, "Colegios", "colegios", corrida_demo / "colegios_servidor.geojson")
    agregar_a_sitio(sitio, "Barrios 2026", "barrios", geojson)
    manifiesto = json.loads((sitio / "visor.json").read_text())
    assert manifiesto["titulo"]
    assert [(c["titulo"], c["archivo"]) for c in manifiesto["capas"]] == [
        ("Colegios", "colegios.geojson"), ("Barrios 2026", "barrios.geojson")]
    assert (sitio / "barrios.meta.json").exists()


def test_indicadores_funcion_auto(tmp_path):
    barrios = tmp_path / "barrios.gpkg"
    demo.zonas_sinteticas().to_file(barrios, driver="GPKG")
    assert main(["--servicio", "https://demo.invalid/ImageServer", "--pausa", "0",
                 "indicadores", "--zonas", str(barrios), "--funcion-ndvi", "auto",
                 "--salida", str(tmp_path / "res"), "--cache", str(tmp_path / "cache")],
                session=demo.ServidorFalso()) == 0
    meta = json.loads((tmp_path / "res" / "indicadores.meta.json").read_text())
    assert meta["funcion_ndvi"] == "bandarithmetic"


class ServidorQueFallaUnaZona(demo.ServidorFalso):
    def _histogramas(self, params):
        geometria = demo.esri_a_poligono(json.loads(params["geometry"]))
        if geometria.bounds[0] > demo.ORIGEN_DEMO[0] + 250:  # "Ribera (parcial)"
            return demo.Respuesta({"error": {"code": 500, "message": "Timeout"}})
        return super()._histogramas(params)


def test_una_zona_fallida_no_detiene_el_calculo():
    servidor = ImageServer("https://demo.invalid/ImageServer", session=ServidorQueFallaUnaZona())
    zonas = demo.zonas_sinteticas()
    zonas = gpd.GeoDataFrame(zonas.assign(zona_id=range(1, 4), nombre=zonas["NOMBRE"]))
    mensajes = []
    df = estadisticas_servidor(servidor, zonas, BANDAS_LEGION, progreso=mensajes.append)
    ribera = df.set_index("nombre").loc["Ribera (parcial)"]
    assert math.isnan(ribera.pct_vegetacion)
    assert df.set_index("nombre").loc["Villa Verde"].pct_vegetacion == pytest.approx(60, abs=0.5)
    assert any("1 de 3 zonas" in m for m in mensajes)
