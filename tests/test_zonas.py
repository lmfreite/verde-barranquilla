import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from baqgeo.zonas import cargar_zonas


@pytest.fixture
def barrios(tmp_path):
    ruta = tmp_path / "barrios.gpkg"
    gpd.GeoDataFrame(
        {"NOM_BARRIO": ["El Prado", "Rebolo"]},
        geometry=[box(-74.80, 10.99, -74.79, 11.00), box(-74.78, 10.96, -74.77, 10.97)],
        crs=4326,
    ).to_file(ruta, layer="U_BARRIO", driver="GPKG")
    return ruta


def test_reproyecta_y_detecta_nombre(barrios):
    zonas = cargar_zonas(barrios, capa="U_BARRIO")
    assert zonas.crs.to_epsg() == 9377
    assert list(zonas["nombre"]) == ["El Prado", "Rebolo"]
    assert list(zonas["zona_id"]) == [1, 2]
    assert zonas.geometry.name == "geometry"
    # ~1,1 km x 1,1 km
    assert 1.1e6 < zonas.geometry.area.iloc[0] < 1.3e6


def test_filtro_donde(barrios):
    zonas = cargar_zonas(barrios, donde="NOM_BARRIO=el prado")
    assert list(zonas["nombre"]) == ["El Prado"]
    with pytest.raises(ValueError):
        cargar_zonas(barrios, donde="NO_EXISTE=x")


def test_puntos_necesitan_buffer(tmp_path):
    ruta = tmp_path / "colegios.geojson"
    gpd.GeoDataFrame({"nombre": ["IED"]}, geometry=[Point(-74.8, 10.98)], crs=4326).to_file(ruta)
    with pytest.raises(ValueError, match="--buffer"):
        cargar_zonas(ruta)
    zonas = cargar_zonas(ruta, buffer_m=300)
    assert zonas.geom_type.iloc[0] == "Polygon"
    assert abs(zonas.geometry.area.iloc[0] - 3.1416 * 300**2) / (3.1416 * 300**2) < 0.01


def test_sin_columna_de_nombre(tmp_path):
    ruta = tmp_path / "cuencas.geojson"
    gpd.GeoDataFrame({"codigo": [7]}, geometry=[box(0, 0, 10, 10)], crs=9377).to_file(ruta)
    assert cargar_zonas(ruta)["nombre"].iloc[0] == "zona 1"
    assert cargar_zonas(ruta, columna_nombre="codigo")["nombre"].iloc[0] == "7"
