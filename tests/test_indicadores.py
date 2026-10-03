"""De punta a punta contra el servidor falso, comparando con la verdad sintética."""

import json

import geopandas as gpd
import pytest

from baqgeo import demo
from baqgeo.cli import main
from baqgeo.estadisticas import escala_ndvi, fila_desde_histograma
from baqgeo.config import Umbrales


@pytest.fixture
def barrios(tmp_path):
    ruta = tmp_path / "barrios.gpkg"
    demo.zonas_sinteticas().to_file(ruta, driver="GPKG")
    return ruta


def _correr(tmp_path, barrios, *extra, sesion=None):
    sesion = sesion or demo.ServidorFalso()
    codigo = main([
        "--servicio", "https://demo.invalid/ImageServer", "--pausa", "0",
        "indicadores", "--zonas", str(barrios), "--salida", str(tmp_path / "res"),
        "--cache", str(tmp_path / "cache"), "--poblacion", "POBLACION", *extra,
    ], session=sesion)
    assert codigo == 0
    gdf = gpd.read_file(tmp_path / "res" / "indicadores.gpkg").set_index("nombre")
    return gdf, sesion


@pytest.mark.parametrize("modo", ["servidor", "local"])
def test_indicadores_coinciden_con_la_verdad(tmp_path, barrios, modo):
    gdf, _ = _correr(tmp_path, barrios, "--modo", modo)
    for nombre, verdad in demo.VERDAD.items():
        fila = gdf.loc[nombre]
        impermeable = verdad["impermeable"] + (verdad["sombra"] if modo == "servidor" else 0)
        assert fila.pct_vegetacion == pytest.approx(verdad["vegetacion"], abs=0.5)
        assert fila.pct_impermeable == pytest.approx(impermeable, abs=0.5)
        assert fila.pct_agua == pytest.approx(verdad["agua"], abs=0.5)
        assert fila.cobertura_pct == pytest.approx(verdad["cobertura"], abs=0.5)
        if modo == "local":
            assert fila.pct_sombra == pytest.approx(verdad["sombra"], abs=0.5)
    verde = gdf.loc["Villa Verde"]
    assert verde.m2_vegetacion_por_habitante == pytest.approx(43200 * 0.6 / 3000, rel=0.01)
    assert gdf.loc["Centro Gris"].rank_menos_vegetacion == 1
    assert verde.rank_menos_vegetacion == 3


def test_servidor_no_descarga_imagen_y_usa_cache(tmp_path, barrios):
    _, sesion = _correr(tmp_path, barrios)
    assert "exportImage" not in sesion.llamadas
    assert sesion.llamadas.count("computeStatisticsHistograms") == 3
    _, sesion = _correr(tmp_path, barrios)
    assert sesion.llamadas.count("computeStatisticsHistograms") == 0


def test_geojson_publico_en_wgs84(tmp_path, barrios):
    _correr(tmp_path, barrios)
    datos = json.loads((tmp_path / "res" / "indicadores.geojson").read_text())
    x, y = datos["features"][0]["geometry"]["coordinates"][0][0]
    assert -75 < x < -74.5 and 10.8 < y < 11.1
    assert (tmp_path / "res" / "indicadores_ranking.csv").exists()


def test_limite_de_descarga(tmp_path, barrios):
    with pytest.raises(SystemExit, match="max-gb"):
        _correr(tmp_path, barrios, "--modo", "local", "--max-gb", "0")


def test_escala_ndvi_0_200():
    zona = demo.zonas_sinteticas().assign(zona_id=1, nombre="x").iloc[0]
    # NDVI*100+100: 70 % en 180 (=0,8) y 30 % en 105 (=0,05).
    respuesta = {
        "statistics": [{"min": 105, "max": 180, "mean": 157.5}],
        "histograms": [{"min": 105, "max": 180, "counts": [30] + [0] * 254 + [70]}],
    }
    assert escala_ndvi(respuesta["statistics"][0], respuesta["histograms"][0]) == (0.01, -1.0)
    fila = fila_desde_histograma(zona, respuesta, Umbrales(), 1.2)
    assert fila["pct_vegetacion"] == pytest.approx(70)
    assert fila["ndvi_medio"] == pytest.approx(0.575)


def test_validar_e_inspeccionar(tmp_path, barrios, capsys):
    url = "https://demo.invalid/ImageServer"
    assert main(["--servicio", url, "--pausa", "0", "validar", "--zonas", str(barrios)],
                session=demo.ServidorFalso()) == 0
    assert "--funcion-ndvi bandarithmetic" in capsys.readouterr().out
    salida = tmp_path / "info.json"
    assert main(["--servicio", url, "--pausa", "0", "inspeccionar", "--json", str(salida)],
                session=demo.ServidorFalso()) == 0
    texto = capsys.readouterr().out
    assert "2026-06-28" in texto and "nombres del servicio" in texto
    assert json.loads(salida.read_text())["info"]["bandCount"] == 8


def test_validar_avisa_si_rojo_y_nir_estan_invertidos(barrios, capsys):
    codigo = main(["--servicio", "https://demo.invalid/ImageServer", "--pausa", "0",
                   "validar", "--zonas", str(barrios), "--bandas", "blue=2,green=3,red=8,nir=5"],
                  session=demo.ServidorFalso(nombres_bandas=[f"Band_{i}" for i in range(1, 9)]))
    assert codigo == 1
    assert "invertidos" in capsys.readouterr().out


def test_demo_completa(tmp_path):
    assert main(["demo", "--salida", str(tmp_path)]) == 0
    for nombre in ("barrios_servidor", "barrios_local", "colegios_servidor"):
        assert (tmp_path / f"{nombre}.geojson").exists()
