import json
import math

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.transform import from_origin

from baqgeo import demo
from baqgeo.cli import main
from baqgeo.config import Umbrales
from baqgeo.estadisticas import fila_desde_histograma
from baqgeo.temperatura import DESPLAZAMIENTO, ESCALA, Firmador, temperatura_superficie

# --------------------------------------------------------------- verde denso


def test_verde_denso_y_ralo_desde_histograma():
    zona = demo.zonas_sinteticas().assign(zona_id=1, nombre="x").iloc[0]
    # 30 % concreto (NDVI 0,05), 40 % pasto (0,45) y 30 % árboles (0,75).
    conteos = [0] * 256
    conteos[0], conteos[136], conteos[255] = 30, 40, 30
    respuesta = {
        "statistics": [{"min": 0.05, "max": 0.75, "mean": 0.42}],
        "histograms": [{"min": 0.05, "max": 0.75, "counts": conteos}],
    }
    fila = fila_desde_histograma(zona, respuesta, Umbrales(), 1.2)
    assert fila["pct_vegetacion"] == pytest.approx(70)
    assert fila["pct_vegetacion_densa"] == pytest.approx(30)
    assert fila["pct_vegetacion_rala"] == pytest.approx(40)


# --------------------------------------------------------------- temperatura

A_UTM = Transformer.from_crs(9377, 32618, always_xy=True)


def _dn(celsius):
    return np.round((np.asarray(celsius) + 273.15 - DESPLAZAMIENTO) / ESCALA).astype("uint16")


def _escena(carpeta, nombre, fecha, frio=30.0, caliente=40.0, qa_valor=1 << 6, dn_fijo=None):
    """Escena en UTM 18N: oeste de la frontera Villa Verde/Centro Gris a `frio`, este a `caliente`."""
    x0, y0 = demo.ORIGEN_DEMO
    frontera, _ = A_UTM.transform(x0 + 180, y0 - 120)
    xc, yc = A_UTM.transform(x0 + 210, y0 - 120)
    origen = (math.floor((xc - 900) / 30) * 30, math.ceil((yc + 900) / 30) * 30)
    lado = 60
    xs = origen[0] + (np.arange(lado) + 0.5) * 30
    celsius = np.repeat(np.where(xs < frontera, frio, caliente)[None, :], lado, axis=0)
    dn = _dn(celsius) if dn_fijo is None else np.full((lado, lado), dn_fijo, dtype="uint16")
    perfil = {"driver": "GTiff", "width": lado, "height": lado, "count": 1, "dtype": "uint16",
              "crs": CRS.from_epsg(32618), "transform": from_origin(*origen, 30, 30)}
    rutas = {}
    for activo, datos in (("lwir11", dn), ("qa_pixel", np.full((lado, lado), qa_valor, "uint16"))):
        ruta = carpeta / f"{nombre}_{activo}.tif"
        with rasterio.open(ruta, "w", **perfil) as dst:
            dst.write(datos, 1)
        rutas[activo] = {"href": str(ruta)}
    return {"id": nombre, "properties": {"datetime": f"{fecha}T15:30:00Z"}, "assets": rutas}


class _Resp:
    def __init__(self, datos):
        self.datos = datos

    def json(self):
        return self.datos

    def raise_for_status(self):
        pass


class StacFalso:
    """Busca en dos páginas, como el STAC de Planetary Computer (paginación por POST)."""

    def __init__(self, escenas):
        self.paginas = [escenas[:2], escenas[2:]]
        self.cuerpos = []

    def post(self, url, json=None, timeout=None):
        self.cuerpos.append(json)
        if "token" not in json:
            siguiente = {"rel": "next", "href": url, "method": "POST", "body": {**json, "token": "p2"}}
            return _Resp({"features": self.paginas[0], "links": [siguiente]})
        return _Resp({"features": self.paginas[1], "links": []})


@pytest.fixture
def escenas(tmp_path):
    return [
        _escena(tmp_path, "LC09_a", "2024-01-10"),
        _escena(tmp_path, "LC08_b", "2024-02-03", frio=31.0, caliente=41.0),
        _escena(tmp_path, "LC09_c", "2025-01-15", frio=29.0, caliente=39.0),
        # Nublada (bit 3): no debe contar aunque marque 10 °C.
        _escena(tmp_path, "LC08_nube", "2025-02-01", frio=10.0, caliente=10.0, qa_valor=1 << 3),
        # Solo relleno: tampoco cuenta.
        _escena(tmp_path, "LC09_vacia", "2025-03-01", dn_fijo=0),
    ]


def test_temperatura_por_zona_con_mediana_y_nubes(escenas):
    sesion = StacFalso(escenas)
    temperaturas, meta = temperatura_superficie(demo.zonas_sinteticas(), session=sesion,
                                                progreso=lambda m: None)
    verde, gris, ribera = temperaturas
    assert verde == pytest.approx(30, abs=2)
    assert gris == pytest.approx(40, abs=2)
    assert ribera == pytest.approx(40, abs=1)
    assert meta["escenas"] == 3
    assert (meta["desde"], meta["hasta"]) == ("2024-01-10", "2025-01-15")
    primera = sesion.cuerpos[0]
    assert primera["collections"] == ["landsat-c2-l2"]
    assert primera["query"]["platform"]["in"] == ["landsat-8", "landsat-9"]
    assert len(sesion.cuerpos) == 2  # siguió la paginación


def test_firmador_pide_el_token_una_vez():
    class SesionToken:
        llamadas = 0

        def get(self, url, timeout=None):
            SesionToken.llamadas += 1
            assert url.endswith("/token/landsat-c2-l2")
            return _Resp({"token": "st=1&sig=abc"})

    firmar = Firmador(SesionToken())
    assert firmar("https://x.blob/a.TIF") == "https://x.blob/a.TIF?st=1&sig=abc"
    assert firmar("https://x.blob/b.TIF?v=1") == "https://x.blob/b.TIF?v=1&st=1&sig=abc"
    assert firmar("/datos/local.tif") == "/datos/local.tif"
    assert SesionToken.llamadas == 1


def _correr_indicadores(tmp_path, *extra):
    barrios = tmp_path / "barrios.gpkg"
    demo.zonas_sinteticas().to_file(barrios, driver="GPKG")
    codigo = main(["--servicio", "https://demo.invalid/ImageServer", "--pausa", "0",
                   "indicadores", "--zonas", str(barrios), "--salida", str(tmp_path / "res"),
                   "--cache", str(tmp_path / "cache"), *extra], session=demo.ServidorFalso())
    assert codigo == 0
    datos = json.loads((tmp_path / "res" / "indicadores.geojson").read_text())
    meta = json.loads((tmp_path / "res" / "indicadores.meta.json").read_text())
    return datos["features"], meta


def test_indicadores_con_temperatura(tmp_path, monkeypatch):
    monkeypatch.setattr("baqgeo.cli.temperatura_superficie",
                        lambda zonas, **kw: ([30.5, 39.5, 40.0], {"escenas": 7, "fuente": "Landsat"}))
    entidades, meta = _correr_indicadores(tmp_path, "--temperatura")
    assert [f["properties"]["temp_superficie_c"] for f in entidades] == [30.5, 39.5, 40.0]
    assert meta["temperatura"]["escenas"] == 7
    verde = entidades[0]["properties"]
    assert verde["pct_vegetacion_densa"] == pytest.approx(verde["pct_vegetacion"], abs=0.5)


def test_si_falla_landsat_se_publica_igual(tmp_path, monkeypatch, capsys):
    def falla(zonas, **kw):
        raise RuntimeError("sin red")

    monkeypatch.setattr("baqgeo.cli.temperatura_superficie", falla)
    entidades, meta = _correr_indicadores(tmp_path, "--temperatura")
    assert "temp_superficie_c" not in entidades[0]["properties"]
    assert "temperatura" not in meta
    assert "no se pudo calcular la temperatura" in capsys.readouterr().err
