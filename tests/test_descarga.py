import rasterio
from shapely.geometry import box

from baqgeo import demo
from baqgeo.descarga import descargar_teselas, estimar_mb, filtrar_teselas, planificar_teselas
from baqgeo.imageserver import ImageServer


def test_planificar_teselas_alineadas_y_cubren_el_area():
    origen = (1000.0, 2000.0)
    limites = (1003.7, 1500.2, 1450.1, 1990.9)
    teselas = planificar_teselas(limites, 1.2, origen, max_px=100)
    for t in teselas:
        assert t.ancho <= 100 and t.alto <= 100
        # Bordes sobre la grilla del servicio.
        assert abs(((t.xmin - origen[0]) / 1.2) - round((t.xmin - origen[0]) / 1.2)) < 1e-6
        assert abs(((origen[1] - t.ymax) / 1.2) - round((origen[1] - t.ymax) / 1.2)) < 1e-6
    cubierto = box(*teselas[0].limites)
    for t in teselas[1:]:
        cubierto = cubierto.union(box(*t.limites))
    assert cubierto.contains(box(*limites))
    # Sin solapes: el área total es la suma de las teselas.
    assert abs(cubierto.area - sum(box(*t.limites).area for t in teselas)) < 1e-6


def test_filtrar_y_estimar():
    teselas = planificar_teselas((0, 0, 100, 100), 1.0, (0, 100), max_px=50)
    assert len(teselas) == 4
    solo_una = filtrar_teselas(teselas, box(10, 60, 20, 70))
    assert [t.nombre for t in solo_una] == ["f000_c000"]
    assert estimar_mb(teselas, 4) == 100 * 100 * 4 * 2 / 1e6


def test_descargar_teselas_georreferenciadas_y_reutilizables(tmp_path):
    falso = demo.ServidorFalso()
    servidor = ImageServer("https://demo.invalid/ImageServer", session=falso)
    x0, y0 = demo.ORIGEN_DEMO
    teselas = planificar_teselas((x0, y0 - 240, x0 + 360, y0), 1.2, (x0, y0), max_px=128)
    bandas = {"blue": 2, "green": 3, "red": 5, "nir": 8}
    rutas = descargar_teselas(servidor, teselas, tmp_path, bandas, progreso=lambda m: None)
    assert len(rutas) == len(teselas) == 6
    with rasterio.open(rutas[0]) as src:
        assert src.descriptions == ("blue", "green", "red", "nir")
        assert src.crs.to_epsg() == 9377
        assert src.transform.a == 1.2
        assert (src.bounds.left, src.bounds.top) == (x0, y0)
        # Esquina superior izquierda = vegetación: NIR alto, rojo bajo.
        assert src.read(4)[0, 0] > 3000 and src.read(3)[0, 0] < 500

    exportaciones = falso.llamadas.count("exportImage")
    descargar_teselas(servidor, teselas, tmp_path, bandas, progreso=lambda m: None)
    assert falso.llamadas.count("exportImage") == exportaciones
