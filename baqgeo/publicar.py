"""Arma la carpeta estática del visor (MapLibre) con las capas de indicadores."""

import json
import re
import shutil
import unicodedata
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .salidas import ruta_metadatos

WEB = Path(__file__).parent / "web"


def _slug(texto):
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-") or "capa"


def leer_capa(argumento):
    """"Barrios=resultados/barrios/indicadores.geojson" -> ("Barrios", Path(...))."""
    titulo, separador, ruta = argumento.partition("=")
    if not separador:
        ruta = argumento
        titulo = Path(argumento).stem
    return titulo.strip(), Path(ruta.strip())


def _validar_geojson(ruta):
    try:
        datos = json.loads(Path(ruta).read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"{ruta}: no es un GeoJSON legible ({error})")
    if datos.get("type") != "FeatureCollection" or not datos.get("features"):
        raise ValueError(f"{ruta}: se esperaba un FeatureCollection con zonas")
    propiedades = datos["features"][0].get("properties") or {}
    faltan = [c for c in ("zona_id", "nombre") if c not in propiedades]
    if faltan:
        raise ValueError(
            f"{ruta}: faltan {faltan}; usa el .geojson que genera `baqgeo indicadores`"
        )


def armar_visor(capas, salida, titulo=None):
    """Copia el visor a `salida` y agrega las capas en `salida/datos/`.

    `capas` es una lista de (título, ruta al GeoJSON de `baqgeo indicadores`).
    Si junto al GeoJSON está su `.meta.json`, el visor muestra el método.
    """
    salida = Path(salida)
    for _, ruta in capas:
        _validar_geojson(ruta)
    salida.mkdir(parents=True, exist_ok=True)
    for archivo in WEB.iterdir():
        if archivo.is_file():
            shutil.copy2(archivo, salida / archivo.name)

    datos = salida / "datos"
    if datos.exists():
        shutil.rmtree(datos)
    datos.mkdir()
    catalogo = {"titulo": titulo, "capas": []}
    usados = set()
    for titulo_capa, ruta in capas:
        base = _slug(titulo_capa)
        identificador, n = base, 2
        while identificador in usados:
            identificador, n = f"{base}-{n}", n + 1
        usados.add(identificador)
        shutil.copy2(ruta, datos / f"{identificador}.geojson")
        meta = ruta_metadatos(ruta)
        catalogo["capas"].append({
            "id": identificador,
            "titulo": titulo_capa,
            "archivo": f"{identificador}.geojson",
            "meta": json.loads(meta.read_text()) if meta.exists() else {},
        })
    (datos / "capas.json").write_text(json.dumps(catalogo, indent=2, ensure_ascii=False))
    return salida


def servir(carpeta, puerto=8000):
    """Servidor local para probar el visor (no usar en producción)."""
    manejador = partial(SimpleHTTPRequestHandler, directory=str(carpeta))
    with ThreadingHTTPServer(("127.0.0.1", puerto), manejador) as servidor:
        print(f"Visor en http://127.0.0.1:{puerto}/  (Ctrl+C para salir)")
        try:
            servidor.serve_forever()
        except KeyboardInterrupt:
            pass
