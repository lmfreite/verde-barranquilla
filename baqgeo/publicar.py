"""Arma la carpeta estática del visor (MapLibre) con las capas de indicadores."""

import hashlib
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


def leer_manifiesto(ruta):
    """Lee un manifiesto del visor y devuelve (título, [(título, ruta), ...]).

    Formato (rutas relativas al manifiesto):
        {"titulo": "...", "capas": [{"titulo": "Barrios", "archivo": "barrios.geojson"}]}
    """
    ruta = Path(ruta)
    try:
        manifiesto = json.loads(ruta.read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"{ruta}: no pude leer el manifiesto ({error})")
    capas = manifiesto.get("capas") or []
    if not capas:
        raise ValueError(f"{ruta}: el manifiesto no tiene capas")
    resultado = []
    for capa in capas:
        if "archivo" not in capa:
            raise ValueError(f"{ruta}: cada capa necesita 'archivo'")
        archivo = ruta.parent / capa["archivo"]
        resultado.append((capa.get("titulo") or archivo.stem, archivo))
    return manifiesto.get("titulo"), resultado


def armar_visor(capas, salida, titulo=None, rechazar_demo=False):
    """Copia el visor a `salida` y agrega las capas en `salida/datos/`.

    `capas` es una lista de (título, ruta al GeoJSON de `baqgeo indicadores`).
    Si junto al GeoJSON está su `.meta.json`, el visor muestra el método.
    Con `rechazar_demo` falla si alguna capa viene de `baqgeo demo`, para no
    publicar datos sintéticos como si fueran reales.
    """
    salida = Path(salida)
    for _, ruta in capas:
        if not Path(ruta).exists():
            raise ValueError(f"No existe {ruta}")
        _validar_geojson(ruta)
        meta = ruta_metadatos(ruta)
        if rechazar_demo:
            if not meta.exists():
                raise ValueError(f"{ruta}: falta {meta.name}; genera la capa con `baqgeo indicadores`")
            if json.loads(meta.read_text()).get("demo"):
                raise ValueError(f"{ruta}: son datos de demostración sintéticos, no se publican")
    salida.mkdir(parents=True, exist_ok=True)
    for archivo in WEB.iterdir():
        if archivo.is_file():
            shutil.copy2(archivo, salida / archivo.name)
    _versionar_recursos(salida)

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


def version_recursos(carpeta):
    """Huella corta del JS y el CSS del visor: cambia cuando cambia el código."""
    huella = hashlib.sha1()
    for nombre in ("visor.js", "visor.css"):
        huella.update((Path(carpeta) / nombre).read_bytes())
    return huella.hexdigest()[:10]


def _versionar_recursos(carpeta):
    """Agrega ?v=<huella> a visor.js y visor.css en index.html.

    Los hosting estáticos (GitHub Pages incluido) dejan los archivos en caché
    unos minutos; con la huella en la URL el navegador pide el código nuevo
    en cuanto cambia, sin tener que borrar la caché.
    """
    version = version_recursos(carpeta)
    index = Path(carpeta) / "index.html"
    html = index.read_text()
    for nombre, atributo in (("visor.js", "src"), ("visor.css", "href")):
        original = f'{atributo}="{nombre}"'
        if original not in html:
            raise ValueError(f"index.html no referencia {nombre} como {original}")
        html = html.replace(original, f'{atributo}="{nombre}?v={version}"')
    index.write_text(html)
    return version


TITULO_SITIO = "¿Cuánto verde tiene tu barrio?"


def agregar_a_sitio(carpeta_sitio, titulo, nombre, geojson):
    """Copia una capa de `indicadores` a `sitio/` y la registra en visor.json.

    Si ya había una capa con ese `nombre`, la reemplaza. Devuelve la ruta del
    manifiesto.
    """
    sitio = Path(carpeta_sitio)
    geojson = Path(geojson)
    _validar_geojson(geojson)
    meta = ruta_metadatos(geojson)
    if not meta.exists():
        raise ValueError(f"{geojson}: falta {meta.name}; genera la capa con `baqgeo indicadores`")
    nombre = _slug(nombre)
    sitio.mkdir(parents=True, exist_ok=True)
    shutil.copy2(geojson, sitio / f"{nombre}.geojson")
    shutil.copy2(meta, sitio / f"{nombre}.meta.json")

    manifiesto = sitio / "visor.json"
    datos = json.loads(manifiesto.read_text()) if manifiesto.exists() else {}
    datos.setdefault("titulo", TITULO_SITIO)
    # Si la capa ya existía se actualiza en su lugar, para no cambiar el orden del selector.
    capas = datos.get("capas", [])
    nueva = {"titulo": titulo, "archivo": f"{nombre}.geojson"}
    posicion = next((i for i, c in enumerate(capas) if c.get("archivo") == nueva["archivo"]), None)
    if posicion is None:
        capas.append(nueva)
    else:
        capas[posicion] = nueva
    datos["capas"] = capas
    manifiesto.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n")
    return manifiesto


def servir(carpeta, puerto=8000):
    """Servidor local para probar el visor (no usar en producción)."""
    manejador = partial(SimpleHTTPRequestHandler, directory=str(carpeta))
    with ThreadingHTTPServer(("127.0.0.1", puerto), manejador) as servidor:
        print(f"Visor en http://127.0.0.1:{puerto}/  (Ctrl+C para salir)")
        try:
            servidor.serve_forever()
        except KeyboardInterrupt:
            pass
