import json

import pytest

from baqgeo.cli import main
from baqgeo.publicar import armar_visor, leer_capa


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("demo")
    assert main(["demo", "--salida", str(carpeta)]) == 0
    return carpeta


def test_leer_capa():
    assert leer_capa("Barrios=r/indicadores.geojson")[0] == "Barrios"
    titulo, ruta = leer_capa("r/colegios.geojson")
    assert titulo == "colegios" and ruta.name == "colegios.geojson"


def test_demo_arma_visor(demo):
    visor = demo / "visor"
    for archivo in ("index.html", "visor.js", "visor.css"):
        assert (visor / archivo).exists()
    catalogo = json.loads((visor / "datos" / "capas.json").read_text())
    assert [c["id"] for c in catalogo["capas"]] == [
        "barrios-modo-servidor", "barrios-modo-local", "entornos-de-colegios"]
    primera = catalogo["capas"][0]
    assert primera["meta"]["demo"] is True
    assert primera["meta"]["modo"] == "servidor"
    assert (visor / "datos" / primera["archivo"]).exists()


def test_visor_cli_ids_unicos_y_titulo(demo, tmp_path):
    geojson = demo / "barrios_servidor.geojson"
    salida = tmp_path / "visor"
    codigo = main(["visor", "--capa", f"Barrios={geojson}", "--capa", f"Barrios={geojson}",
                   "--titulo", "Verde en Barranquilla", "--salida", str(salida)])
    assert codigo == 0
    catalogo = json.loads((salida / "datos" / "capas.json").read_text())
    assert catalogo["titulo"] == "Verde en Barranquilla"
    assert [c["id"] for c in catalogo["capas"]] == ["barrios", "barrios-2"]


def test_visor_rechaza_archivos_ajenos(tmp_path):
    ajeno = tmp_path / "otro.geojson"
    ajeno.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"x": 1}, "geometry": None}]}))
    with pytest.raises(ValueError, match="zona_id"):
        armar_visor([("Otro", ajeno)], tmp_path / "visor")
    assert main(["visor", "--capa", str(tmp_path / "no_existe.geojson"),
                 "--salida", str(tmp_path / "v")]) == 1
