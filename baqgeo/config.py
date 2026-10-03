"""Constantes del servicio y umbrales de clasificación."""

from dataclasses import dataclass

SERVICIO_URL = (
    "https://miciudad.barranquilla.gov.co/image/rest/services/orto/"
    "orto35_worldview_08001barranquilla_2026/ImageServer"
)

# MAGNA-SIRGAS 2018 / Origen-Nacional. Se usa si el servicio no informa su CRS.
EPSG_TRABAJO = 9377

# Orden estándar de las 8 bandas de WorldView Legion (índices base 1).
# Solo se usa si el servicio no publica nombres de banda reconocibles:
# confírmalo siempre con `baqgeo inspeccionar` y `baqgeo validar`.
BANDAS_LEGION = {
    "coastal": 1,
    "blue": 2,
    "green": 3,
    "yellow": 4,
    "red": 5,
    "rededge1": 6,
    "rededge2": 7,
    "nir": 8,
}

# Bandas que hacen falta para NDVI, NDWI y brillo.
BANDAS_NECESARIAS = ("blue", "green", "red", "nir")

# La multiespectral nativa de Legion es ~1,16 m en nadir; los 34 cm del
# servicio salen de fusionarla con la pancromática (pansharpening).
# Los índices espectrales se calculan a la resolución nativa, no a 34 cm.
PIXEL_ANALISIS_M = 1.2


@dataclass(frozen=True)
class Umbrales:
    """Umbrales de clasificación. Calíbralos con `baqgeo validar` y QGIS."""

    # NDVI >= este valor -> vegetación.
    vegetacion_ndvi: float = 0.30
    # NDVI >= este valor -> verde denso (sobre todo copas de árboles).
    vegetacion_densa_ndvi: float = 0.60
    # NDVI < este valor -> agua (modo servidor, y condición extra en modo local).
    agua_ndvi: float = 0.0
    # NDWI > este valor (y NDVI < agua_ndvi) -> agua (modo local).
    agua_ndwi: float = 0.10
    # Brillo < esta fracción de la mediana del área -> sombra (modo local).
    sombra_brillo: float = 0.35
