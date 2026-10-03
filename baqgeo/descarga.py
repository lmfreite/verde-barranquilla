"""Descarga por teselas con exportImage, alineada a la grilla del servicio."""

import math
from dataclasses import dataclass
from pathlib import Path

import rasterio
from rasterio.crs import CRS
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.geometry import box

from .imageserver import ImageServerError


@dataclass(frozen=True)
class Tesela:
    fila: int
    columna: int
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    ancho: int
    alto: int
    pixel: float

    @property
    def nombre(self):
        return f"f{self.fila:03d}_c{self.columna:03d}"

    @property
    def limites(self):
        return (self.xmin, self.ymin, self.xmax, self.ymax)

    @property
    def transform(self):
        return from_origin(self.xmin, self.ymax, self.pixel, self.pixel)


def planificar_teselas(limites, pixel, origen, max_px=2048):
    """Parte `limites` en teselas de hasta `max_px` píxeles por lado.

    Los bordes se ajustan a la grilla que arranca en `origen` (esquina
    superior izquierda del servicio) para que el servidor no remuestree de más.
    """
    xmin, ymin, xmax, ymax = limites
    x0, y0 = origen
    c0 = math.floor((xmin - x0) / pixel + 1e-9)
    c1 = math.ceil((xmax - x0) / pixel - 1e-9)
    f0 = math.floor((y0 - ymax) / pixel + 1e-9)
    f1 = math.ceil((y0 - ymin) / pixel - 1e-9)
    teselas = []
    for i, fila in enumerate(range(f0, f1, max_px)):
        for j, col in enumerate(range(c0, c1, max_px)):
            ancho = min(max_px, c1 - col)
            alto = min(max_px, f1 - fila)
            x = x0 + col * pixel
            y = y0 - fila * pixel
            teselas.append(Tesela(i, j, x, y - alto * pixel, x + ancho * pixel, y, ancho, alto, pixel))
    return teselas


def filtrar_teselas(teselas, geometria):
    return [t for t in teselas if box(*t.limites).intersects(geometria)]


def estimar_mb(teselas, n_bandas, bytes_por_valor=2):
    return sum(t.ancho * t.alto for t in teselas) * n_bandas * bytes_por_valor / 1e6


def descargar_teselas(servidor, teselas, carpeta, bandas, interpolacion="RSP_NearestNeighbor",
                      progreso=print):
    """Descarga las teselas que falten. `bandas` es {nombre: índice base 1}.

    Cada GeoTIFF guarda el nombre de sus bandas, así se puede leer por nombre.
    Las teselas ya descargadas se reutilizan.
    """
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    nombres = list(bandas)
    band_ids = [bandas[n] - 1 for n in nombres]
    crs = CRS.from_epsg(servidor.wkid)
    rutas = []
    for k, t in enumerate(teselas, start=1):
        ruta = carpeta / f"{t.nombre}.tif"
        if not ruta.exists():
            contenido = servidor.exportar(t.limites, t.ancho, t.alto, band_ids, interpolacion)
            with MemoryFile(contenido) as mem, mem.open() as src:
                datos = src.read()
            if datos.shape != (len(nombres), t.alto, t.ancho):
                raise ImageServerError(
                    f"Tesela {t.nombre}: forma {datos.shape}, se esperaba "
                    f"{(len(nombres), t.alto, t.ancho)}"
                )
            temporal = ruta.with_suffix(".parcial")
            perfil = {
                "driver": "GTiff",
                "width": t.ancho,
                "height": t.alto,
                "count": len(nombres),
                "dtype": datos.dtype,
                "crs": crs,
                "transform": t.transform,
                "nodata": 0,
                "compress": "deflate",
                "tiled": True,
            }
            with rasterio.open(temporal, "w", **perfil) as dst:
                dst.write(datos)
                for i, nombre in enumerate(nombres, start=1):
                    dst.set_band_description(i, nombre)
            temporal.replace(ruta)
        rutas.append(ruta)
        progreso(f"  tesela {k}/{len(teselas)} {t.nombre}")
    return rutas
