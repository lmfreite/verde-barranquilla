"""Cliente mínimo del ImageServer de ArcGIS (REST)."""

import json
import time

import requests
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.polygon import orient

from .config import BANDAS_LEGION, BANDAS_NECESARIAS, EPSG_TRABAJO, SERVICIO_URL


class ImageServerError(RuntimeError):
    pass


def _json(obj):
    return json.dumps(obj, separators=(",", ":"))


def poligono_a_esri(geometria, wkid):
    """Convierte un (Multi)Polygon de shapely en un polígono JSON de Esri.

    Esri espera los anillos exteriores en sentido horario y los huecos en
    sentido antihorario.
    """
    if isinstance(geometria, Polygon):
        poligonos = [geometria]
    elif isinstance(geometria, MultiPolygon):
        poligonos = list(geometria.geoms)
    else:
        raise TypeError(f"Se esperaba un polígono, llegó {geometria.geom_type}")
    anillos = []
    for poligono in poligonos:
        poligono = orient(poligono, sign=-1.0)
        anillos.append([list(c) for c in poligono.exterior.coords])
        anillos.extend([list(c) for c in hueco.coords] for hueco in poligono.interiors)
    return {"rings": anillos, "spatialReference": {"wkid": wkid}}


def puntos_a_esri(puntos, wkid):
    return {"points": [[x, y] for x, y in puntos], "spatialReference": {"wkid": wkid}}


def regla_ndvi(bandas, funcion="bandarithmetic"):
    """Regla de renderizado que calcula el NDVI en el servidor.

    `bandas` usa índices base 1. "bandarithmetic" usa BandArithmetic (base 1);
    "ndvi" usa la función NDVI clásica (base 0). `baqgeo validar` dice cuál
    coincide con el cálculo local.
    """
    nir, red = bandas["nir"], bandas["red"]
    if funcion == "bandarithmetic":
        return {
            "rasterFunction": "BandArithmetic",
            "rasterFunctionArguments": {"Method": 1, "BandIndexes": f"{nir} {red}"},
            "outputPixelType": "F32",
        }
    if funcion == "ndvi":
        return {
            "rasterFunction": "NDVI",
            "rasterFunctionArguments": {
                "VisibleBandID": red - 1,
                "InfraredBandID": nir - 1,
                "ScientificOutput": True,
            },
            "outputPixelType": "F32",
        }
    raise ValueError(f"Función NDVI desconocida: {funcion}")


def _clave_banda(nombre, ya_vistas):
    n = nombre.lower().replace("_", " ").replace("-", " ")
    if "coastal" in n or "costera" in n:
        return "coastal"
    if "edge" in n or "borde" in n:
        return "rededge2" if "rededge1" in ya_vistas else "rededge1"
    if "nir" in n or "near" in n or "infra" in n:
        return "nir"
    if "yellow" in n or "amarill" in n:
        return "yellow"
    if "blue" in n or "azul" in n:
        return "blue"
    if "green" in n or "verde" in n:
        return "green"
    if "red" in n or "rojo" in n:
        return "red"
    return None


def mapa_de_bandas(info):
    """Deduce qué índice (base 1) tiene cada banda.

    Devuelve (mapa, autodetectado). Si los nombres no son reconocibles y el
    servicio tiene 8 bandas, asume el orden estándar de Legion.
    """
    mapa = {}
    for i, nombre in enumerate(info.get("bandNames") or [], start=1):
        clave = _clave_banda(str(nombre), mapa)
        if clave and clave not in mapa:
            mapa[clave] = i
    if all(k in mapa for k in BANDAS_NECESARIAS):
        return mapa, True
    if info.get("bandCount") == 8:
        return dict(BANDAS_LEGION), False
    raise ImageServerError(
        "No pude deducir el orden de las bandas; indícalo con --bandas "
        '"blue=2,green=3,red=5,nir=8"'
    )


def leer_mapa_bandas(texto):
    """Lee "blue=2,green=3,red=5,nir=8" (índices base 1)."""
    mapa = {}
    for parte in texto.split(","):
        clave, _, valor = parte.partition("=")
        mapa[clave.strip().lower()] = int(valor)
    faltan = [k for k in BANDAS_NECESARIAS if k not in mapa]
    if faltan:
        raise ValueError(f"Faltan bandas en --bandas: {', '.join(faltan)}")
    return mapa


class ImageServer:
    def __init__(self, url=SERVICIO_URL, session=None, timeout=180, reintentos=4, pausa=0.0):
        self.url = url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.reintentos = reintentos
        self.pausa = pausa
        self._info = None

    def _pedir(self, operacion, params, metodo="get", binario=False):
        url = f"{self.url}/{operacion}" if operacion else self.url
        espera = 2
        for intento in range(self.reintentos + 1):
            try:
                if metodo == "post":
                    r = self.session.post(url, data=params, timeout=self.timeout)
                else:
                    r = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                error = e
            else:
                if r.status_code < 500 and r.status_code != 429:
                    break
                error = ImageServerError(f"HTTP {r.status_code}")
            if intento == self.reintentos:
                raise ImageServerError(f"{operacion or 'info'}: {error}") from error
            time.sleep(espera)
            espera *= 2
        if self.pausa:
            time.sleep(self.pausa)
        if r.status_code >= 400:
            raise ImageServerError(f"{operacion or 'info'}: HTTP {r.status_code} {r.text[:200]}")

        tipo = r.headers.get("Content-Type", "")
        if binario and "json" not in tipo and "text" not in tipo:
            return r.content
        try:
            datos = r.json()
        except ValueError:
            raise ImageServerError(f"{operacion or 'info'}: respuesta no es JSON: {r.text[:200]}")
        if "error" in datos:
            err = datos["error"]
            raise ImageServerError(
                f"{operacion or 'info'}: {err.get('message')} {err.get('details') or ''}".strip()
            )
        if binario:
            raise ImageServerError(f"{operacion}: se esperaba una imagen y llegó JSON")
        return datos

    def info(self):
        if self._info is None:
            self._info = self._pedir("", {"f": "json"})
        return self._info

    @property
    def wkid(self):
        sr = self.info().get("spatialReference") or {}
        return sr.get("latestWkid") or sr.get("wkid") or EPSG_TRABAJO

    def catalogo(self, max_registros=50):
        """Atributos de las imágenes del mosaico (fechas, ángulos, nubes).

        Devuelve [] si el servicio no expone catálogo.
        """
        params = {
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "false",
            "resultRecordCount": max_registros,
            "f": "json",
        }
        try:
            datos = self._pedir("query", params)
        except ImageServerError:
            return []
        return [f.get("attributes", {}) for f in datos.get("features", [])]

    def histogramas(self, geometria, regla=None, pixel=None):
        """Estadísticas e histograma calculados en el servidor para un polígono."""
        params = {
            "geometry": _json(poligono_a_esri(geometria, self.wkid)),
            "geometryType": "esriGeometryPolygon",
            "f": "json",
        }
        if regla:
            params["renderingRule"] = _json(regla)
        if pixel:
            params["pixelSize"] = f"{pixel},{pixel}"
        return self._pedir("computeStatisticsHistograms", params, metodo="post")

    def muestras(self, puntos, regla=None):
        """Valores de píxel en cada punto. Devuelve una lista (None si no hay dato)."""
        params = {
            "geometry": _json(puntos_a_esri(puntos, self.wkid)),
            "geometryType": "esriGeometryMultipoint",
            "returnFirstValueOnly": "true",
            "interpolation": "RSP_NearestNeighbor",
            "f": "json",
        }
        if regla:
            params["renderingRule"] = _json(regla)
        datos = self._pedir("getSamples", params, metodo="post")
        valores = [None] * len(puntos)
        for muestra in datos.get("samples", []):
            i = muestra.get("locationId")
            if i is None or not 0 <= i < len(puntos):
                continue
            try:
                valores[i] = [float(v) for v in str(muestra.get("value", "")).split()]
            except ValueError:
                valores[i] = None
        return valores

    def exportar(self, limites, ancho, alto, band_ids=None, interpolacion="RSP_NearestNeighbor"):
        """GeoTIFF de una ventana. `band_ids` usa índices base 0."""
        params = {
            "bbox": ",".join(f"{v:.4f}" for v in limites),
            "bboxSR": self.wkid,
            "imageSR": self.wkid,
            "size": f"{ancho},{alto}",
            "format": "tiff",
            "compression": "LZ77",
            "noData": 0,
            "interpolation": interpolacion,
            "f": "image",
        }
        if band_ids is not None:
            params["bandIds"] = ",".join(str(b) for b in band_ids)
        return self._pedir("exportImage", params, binario=True)
