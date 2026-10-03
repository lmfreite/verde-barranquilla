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


def _copiar_capa(demo, destino, nombre, demo_flag):
    destino.mkdir(exist_ok=True)
    (destino / f"{nombre}.geojson").write_text((demo / "barrios_servidor.geojson").read_text())
    meta = json.loads((demo / "barrios_servidor.meta.json").read_text())
    meta["demo"] = demo_flag
    (destino / f"{nombre}.meta.json").write_text(json.dumps(meta))


def test_manifiesto_como_en_el_workflow(demo, tmp_path):
    sitio = tmp_path / "sitio"
    _copiar_capa(demo, sitio, "barrios", demo_flag=False)
    (sitio / "visor.json").write_text(json.dumps({
        "titulo": "Verde en Barranquilla",
        "capas": [{"titulo": "Barrios", "archivo": "barrios.geojson"}],
    }))
    salida = tmp_path / "_site"
    assert main(["visor", "--manifiesto", str(sitio / "visor.json"), "--solo-datos-reales",
                 "--salida", str(salida)]) == 0
    catalogo = json.loads((salida / "datos" / "capas.json").read_text())
    assert catalogo["titulo"] == "Verde en Barranquilla"
    assert catalogo["capas"][0]["meta"]["demo"] is False
    assert (salida / "index.html").exists()


def test_solo_datos_reales_rechaza_demo_y_capas_sin_meta(demo, tmp_path, capsys):
    con_demo = tmp_path / "con_demo"
    _copiar_capa(demo, con_demo, "barrios", demo_flag=True)
    assert main(["visor", "--capa", str(con_demo / "barrios.geojson"), "--solo-datos-reales",
                 "--salida", str(tmp_path / "v1")]) == 1
    assert "demostración" in capsys.readouterr().err

    sin_meta = tmp_path / "sin_meta"
    sin_meta.mkdir()
    (sin_meta / "b.geojson").write_text((demo / "barrios_servidor.geojson").read_text())
    assert main(["visor", "--capa", str(sin_meta / "b.geojson"), "--solo-datos-reales",
                 "--salida", str(tmp_path / "v2")]) == 1
    assert "meta.json" in capsys.readouterr().err
    # Sin la opción, la demo sí se puede armar para probar en local.
    assert main(["visor", "--capa", str(con_demo / "barrios.geojson"),
                 "--salida", str(tmp_path / "v3")]) == 0


def test_manifiesto_de_ejemplo_es_valido():
    from pathlib import Path

    from baqgeo.publicar import leer_manifiesto

    ejemplo = Path(__file__).parents[1] / "sitio" / "visor.ejemplo.json"
    titulo, capas = leer_manifiesto(ejemplo)
    assert titulo and [t for t, _ in capas] == ["Barrios", "Entornos de colegios (300 m)"]


def test_visor_rechaza_archivos_ajenos(tmp_path):
    ajeno = tmp_path / "otro.geojson"
    ajeno.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"x": 1}, "geometry": None}]}))
    with pytest.raises(ValueError, match="zona_id"):
        armar_visor([("Otro", ajeno)], tmp_path / "visor")
    assert main(["visor", "--capa", str(tmp_path / "no_existe.geojson"),
                 "--salida", str(tmp_path / "v")]) == 1
