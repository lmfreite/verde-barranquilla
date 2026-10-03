"""Escena sintética y un ImageServer falso para probar todo sin red.

El servidor falso imita las operaciones REST que usa el proyecto (info,
query, exportImage, computeStatisticsHistograms, getSamples) sobre una imagen
de 8 bandas generada aquí. Sirve para la demo y para las pruebas; no
reemplaza `baqgeo validar` contra el servicio real.
"""

import json
from datetime import datetime, timezone
from urllib.parse import urlparse

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.warp import reproject
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

PIXEL_DEMO = 0.4
ORIGEN_DEMO = (4803000.0, 2772000.0)  # ~Barranquilla en EPSG:9377
EPSG_DEMO = 9377
NOMBRES_BANDAS = ["Coastal", "Blue", "Green", "Yellow", "Red", "RedEdge1", "RedEdge2", "NIR"]

# Firmas en niveles digitales (coastal, blue, green, yellow, red, re1, re2, nir).
FIRMAS = {
    "vegetacion": (350, 300, 600, 500, 350, 1200, 2600, 3500),
    "concreto": (1700, 1800, 1900, 1950, 2000, 2050, 2100, 2200),
    "asfalto": (850, 900, 950, 980, 1000, 1020, 1050, 1100),
    "agua": (900, 800, 900, 700, 600, 450, 350, 300),
    "sombra": (280, 250, 260, 250, 240, 245, 250, 260),
}

ALTO, ANCHO = 600, 900  # 240 m x 360 m

# (fila0, fila1, col0, col1, clase). Bordes múltiplos de 3 píxeles, así el
# remuestreo a 1,2 m (3x3 píxeles) no mezcla clases.
BLOQUES = [
    # Villa Verde (columnas 0-450)
    (0, 360, 0, 450, "vegetacion"),
    (360, 420, 0, 225, "agua"),
    (360, 420, 225, 450, "sombra"),
    (420, 600, 0, 450, "concreto"),
    # Centro Gris (columnas 450-900)
    (0, 60, 450, 900, "vegetacion"),
    (60, 120, 450, 900, "sombra"),
    (120, 360, 450, 900, "asfalto"),
    (360, 600, 450, 900, "concreto"),
]

# Porcentajes esperados. En modo servidor la sombra cae en "impermeable".
VERDAD = {
    "Villa Verde": {"vegetacion": 60, "impermeable": 30, "agua": 5, "sombra": 5, "cobertura": 100},
    "Centro Gris": {"vegetacion": 10, "impermeable": 80, "agua": 0, "sombra": 10, "cobertura": 100},
    "Ribera (parcial)": {"vegetacion": 10, "impermeable": 80, "agua": 0, "sombra": 10, "cobertura": 50},
}


def _caja_pixeles(f0, f1, c0, c1):
    x0, y0 = ORIGEN_DEMO
    return box(x0 + c0 * PIXEL_DEMO, y0 - f1 * PIXEL_DEMO, x0 + c1 * PIXEL_DEMO, y0 - f0 * PIXEL_DEMO)


def escena_sintetica(semilla=7):
    """Devuelve (arreglo uint16 de 8 x ALTO x ANCHO, transform)."""
    rng = np.random.default_rng(semilla)
    datos = np.zeros((8, ALTO, ANCHO), dtype="float64")
    for f0, f1, c0, c1, clase in BLOQUES:
        datos[:, f0:f1, c0:c1] = np.asarray(FIRMAS[clase], dtype="float64")[:, None, None]
    datos *= rng.normal(1.0, 0.02, size=datos.shape)
    transform = from_origin(*ORIGEN_DEMO, PIXEL_DEMO, PIXEL_DEMO)
    return np.clip(datos, 1, 65535).astype("uint16"), transform


def zonas_sinteticas():
    """Tres barrios; "Ribera (parcial)" se sale de la imagen y se solapa con Centro Gris."""
    return gpd.GeoDataFrame(
        {
            "NOMBRE": ["Villa Verde", "Centro Gris", "Ribera (parcial)"],
            "POBLACION": [3000, 5000, 1000],
        },
        geometry=[
            _caja_pixeles(0, ALTO, 0, 450),
            _caja_pixeles(0, ALTO, 450, ANCHO),
            _caja_pixeles(0, ALTO, 750, 1050),
        ],
        crs=EPSG_DEMO,
    )


def colegios_sinteticos():
    return gpd.GeoDataFrame(
        {"nombre": ["IED Villa Verde", "IED Centro"]},
        geometry=[
            Point(ORIGEN_DEMO[0] + 90, ORIGEN_DEMO[1] - 72),
            Point(ORIGEN_DEMO[0] + 270, ORIGEN_DEMO[1] - 180),
        ],
        crs=EPSG_DEMO,
    )


# ----------------------------------------------------------- servidor falso


def esri_a_poligono(esri):
    """Inverso de `poligono_a_esri`: anillo horario = exterior, antihorario = hueco."""
    poligonos = []
    for anillo in esri["rings"]:
        horario = sum((x1 - x0) * (y1 + y0) for (x0, y0), (x1, y1) in zip(anillo, anillo[1:])) > 0
        if horario or not poligonos:
            poligonos.append([anillo, []])
        else:
            poligonos[-1][1].append(anillo)
    return unary_union([Polygon(exterior, huecos) for exterior, huecos in poligonos])


class Respuesta:
    def __init__(self, cuerpo, tipo="application/json", estado=200):
        self.status_code = estado
        self.headers = {"Content-Type": tipo}
        self.content = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode()
        self.text = "" if tipo.startswith("image") else self.content.decode()

    def json(self):
        return json.loads(self.content)


class ServidorFalso:
    """Sesión tipo `requests` que responde como un ImageServer de ArcGIS."""

    def __init__(self, nombres_bandas=None):
        self.datos, self.transform = escena_sintetica()
        self.nombres_bandas = nombres_bandas or NOMBRES_BANDAS
        self.llamadas = []

    # API de requests.Session
    def get(self, url, params=None, timeout=None):
        return self._despachar(url, dict(params or {}))

    def post(self, url, data=None, timeout=None):
        return self._despachar(url, dict(data or {}))

    def _despachar(self, url, params):
        operacion = urlparse(url).path.rstrip("/").split("/")[-1]
        self.llamadas.append(operacion)
        if operacion == "ImageServer":
            return Respuesta(self._info())
        manejador = {
            "query": self._query,
            "exportImage": self._export,
            "computeStatisticsHistograms": self._histogramas,
            "getSamples": self._muestras,
        }.get(operacion)
        if manejador is None:
            return Respuesta({"error": {"code": 400, "message": f"Operación {operacion}"}})
        return manejador(params)

    def _info(self):
        x0, y0 = ORIGEN_DEMO
        return {
            "name": "demo_sintetica",
            "description": "Escena sintética de 8 bandas",
            "copyrightText": "Datos sintéticos",
            "bandCount": 8,
            "bandNames": self.nombres_bandas,
            "pixelType": "U16",
            "pixelSizeX": PIXEL_DEMO,
            "pixelSizeY": PIXEL_DEMO,
            "extent": {
                "xmin": x0, "ymin": y0 - ALTO * PIXEL_DEMO,
                "xmax": x0 + ANCHO * PIXEL_DEMO, "ymax": y0,
                "spatialReference": {"wkid": EPSG_DEMO},
            },
            "spatialReference": {"wkid": EPSG_DEMO, "latestWkid": EPSG_DEMO},
            "maxImageWidth": 15000,
            "maxImageHeight": 4100,
            "capabilities": "Image,Metadata,Catalog",
            "allowRasterFunction": True,
            "rasterFunctionInfos": [{"name": "None"}, {"name": "NDVI"}],
        }

    def _query(self, params):
        fecha = datetime(2026, 6, 28, 15, 30, tzinfo=timezone.utc).timestamp() * 1000
        return Respuesta({"features": [{"attributes": {
            "Name": "LG01_20260628", "AcquisitionDate": fecha, "SensorName": "WorldView Legion",
            "OffNadir": 18.5, "SunElevation": 66.2, "CloudCover": 0.02,
        }}]})

    def _remuestrear(self, limites, ancho, alto):
        xmin, ymin, xmax, ymax = limites
        destino = np.zeros((8, alto, ancho), dtype="float64")
        t_destino = from_origin(xmin, ymax, (xmax - xmin) / ancho, (ymax - ymin) / alto)
        metodo = Resampling.average if t_destino.a > PIXEL_DEMO * 1.01 else Resampling.nearest
        reproject(
            self.datos.astype("float64"), destino,
            src_transform=self.transform, src_crs=CRS.from_epsg(EPSG_DEMO),
            dst_transform=t_destino, dst_crs=CRS.from_epsg(EPSG_DEMO),
            src_nodata=0, dst_nodata=0, resampling=metodo,
        )
        return destino, t_destino

    def _export(self, params):
        limites = [float(v) for v in params["bbox"].split(",")]
        ancho, alto = (int(v) for v in params["size"].split(","))
        datos, transform = self._remuestrear(limites, ancho, alto)
        if params.get("bandIds"):
            datos = datos[[int(b) for b in params["bandIds"].split(",")]]
        datos = np.round(datos).astype("uint16")
        with MemoryFile() as mem:
            with mem.open(driver="GTiff", width=ancho, height=alto, count=len(datos),
                          dtype="uint16", crs=CRS.from_epsg(EPSG_DEMO), transform=transform) as dst:
                dst.write(datos)
            return Respuesta(mem.read(), tipo="image/tiff")

    def _aplicar_regla(self, bandas, regla):
        """`bandas` en float, forma (8, ...). Soporta las dos reglas de `regla_ndvi`."""
        if not regla:
            return bandas[0]
        regla = json.loads(regla)
        argumentos = regla["rasterFunctionArguments"]
        if regla["rasterFunction"] == "BandArithmetic" and argumentos["Method"] == 1:
            nir, red = (int(v) - 1 for v in argumentos["BandIndexes"].split())
            cientifico = True
        elif regla["rasterFunction"] == "NDVI":
            nir, red = argumentos["InfraredBandID"], argumentos["VisibleBandID"]
            cientifico = argumentos.get("ScientificOutput", False)
        else:
            raise ValueError("Regla no soportada por el servidor falso")
        suma = bandas[nir] + bandas[red]
        with np.errstate(divide="ignore", invalid="ignore"):
            v = np.where(suma > 0, (bandas[nir] - bandas[red]) / suma, np.nan)
        return v if cientifico else v * 100 + 100

    def _histogramas(self, params):
        pixel = float(params.get("pixelSize", f"{PIXEL_DEMO}").split(",")[0])
        x0, y0 = ORIGEN_DEMO
        ancho = round(ANCHO * PIXEL_DEMO / pixel)
        alto = round(ALTO * PIXEL_DEMO / pixel)
        bandas, transform = self._remuestrear(
            (x0, y0 - alto * pixel, x0 + ancho * pixel, y0), ancho, alto
        )
        validos = np.all(bandas > 0, axis=0)
        valores = self._aplicar_regla(bandas, params.get("renderingRule"))
        geometria = esri_a_poligono(json.loads(params["geometry"]))
        dentro = geometry_mask([geometria], out_shape=(alto, ancho), transform=transform, invert=True)
        v = valores[dentro & validos & np.isfinite(valores)]
        if v.size == 0:
            return Respuesta({"statistics": [], "histograms": []})
        conteos, _ = np.histogram(v, bins=256, range=(v.min(), v.max()))
        return Respuesta({
            "statistics": [{"min": float(v.min()), "max": float(v.max()), "mean": float(v.mean()),
                            "standardDeviation": float(v.std()), "count": int(v.size)}],
            "histograms": [{"size": 256, "min": float(v.min()), "max": float(v.max()),
                            "counts": conteos.tolist()}],
        })

    def _muestras(self, params):
        puntos = json.loads(params["geometry"])["points"]
        t = self.transform
        muestras = []
        for i, (x, y) in enumerate(puntos):
            c, f = int(np.floor((x - t.c) / t.a)), int(np.floor((y - t.f) / t.e))
            if not (0 <= f < ALTO and 0 <= c < ANCHO):
                continue
            bandas = self.datos[:, f, c].astype("float64")
            if params.get("renderingRule"):
                valor = f"{float(self._aplicar_regla(bandas, params['renderingRule'])):.6f}"
            else:
                valor = " ".join(str(int(b)) for b in bandas)
            muestras.append({"location": {"x": x, "y": y}, "locationId": i, "value": valor})
        return Respuesta({"samples": muestras})


def guardar_escena(ruta):
    """Escribe la escena sintética como GeoTIFF (útil para mirarla en QGIS)."""
    datos, transform = escena_sintetica()
    with rasterio.open(ruta, "w", driver="GTiff", width=ANCHO, height=ALTO, count=8,
                       dtype="uint16", crs=CRS.from_epsg(EPSG_DEMO), transform=transform,
                       nodata=0) as dst:
        dst.write(datos)
        for i, nombre in enumerate(NOMBRES_BANDAS, start=1):
            dst.set_band_description(i, nombre)
