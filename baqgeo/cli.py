"""Línea de comandos: baqgeo inspeccionar | validar | indicadores | demo."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import box

from .config import BANDAS_NECESARIAS, PIXEL_ANALISIS_M, SERVICIO_URL, Umbrales
from .descarga import descargar_teselas, estimar_mb, filtrar_teselas, planificar_teselas
from .estadisticas import estadisticas_local, estadisticas_servidor
from .imageserver import ImageServer, ImageServerError, leer_mapa_bandas, mapa_de_bandas, regla_ndvi
from .salidas import armar_resultado, guardar
from .validacion import validar
from .zonas import cargar_zonas

CAMPOS_CATALOGO = ("name", "date", "fecha", "sensor", "nadir", "sun", "cloud", "nube", "elev", "azim")


def _argumentos_zonas(parser, requerido):
    parser.add_argument("--zonas", required=requerido,
                        help="archivo (GPKG, SHP, GeoJSON) o URL de una capa ArcGIS")
    parser.add_argument("--capa", help="capa dentro del archivo (p. ej. U_BARRIO)")
    parser.add_argument("--donde", help='filtro simple "COLUMNA=valor"')
    parser.add_argument("--columna-nombre", help="columna con el nombre de cada zona")
    parser.add_argument("--buffer", type=float,
                        help="radio en metros para capas de puntos (p. ej. colegios)")
    parser.add_argument("--bandas", help='índices base 1, p. ej. "blue=2,green=3,red=5,nir=8"')


def construir_parser():
    p = argparse.ArgumentParser(
        prog="baqgeo",
        description="NDVI e impermeabilidad por zona desde el ImageServer WorldView de Barranquilla.",
    )
    p.add_argument("--servicio", default=SERVICIO_URL, help="URL del ImageServer")
    p.add_argument("--pausa", type=float, default=0.2,
                   help="segundos de espera entre peticiones (cortesía con el servidor)")
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("inspeccionar", help="metadatos, bandas, funciones y fechas de captura")
    s.add_argument("--json", help="guarda la respuesta completa en este archivo")

    s = sub.add_parser("validar", help="compara el NDVI del servidor con el calculado localmente")
    _argumentos_zonas(s, requerido=False)
    s.add_argument("--n", type=int, default=40, help="número de puntos de muestra")

    s = sub.add_parser("indicadores", help="NDVI, %% vegetación e %% impermeable por zona")
    _argumentos_zonas(s, requerido=True)
    s.add_argument("--modo", choices=("servidor", "local"), default="servidor")
    s.add_argument("--pixel", type=float, default=PIXEL_ANALISIS_M,
                   help="tamaño de píxel del análisis en metros (por defecto %(default)s)")
    s.add_argument("--salida", default="resultados", help="carpeta de resultados")
    s.add_argument("--prefijo", default="indicadores")
    s.add_argument("--poblacion", help="columna de población para m² verdes por habitante")
    s.add_argument("--cache", default="datos", help="carpeta para teselas y respuestas")
    s.add_argument("--funcion-ndvi", choices=("bandarithmetic", "ndvi"), default="bandarithmetic",
                   help="función de ArcGIS para el NDVI (usa la que recomiende `validar`)")
    s.add_argument("--regla-ndvi", help="regla de renderizado JSON propia (reemplaza --funcion-ndvi)")
    s.add_argument("--umbral-vegetacion", type=float, default=Umbrales.vegetacion_ndvi)
    s.add_argument("--umbral-agua-ndvi", type=float, default=Umbrales.agua_ndvi)
    s.add_argument("--umbral-agua-ndwi", type=float, default=Umbrales.agua_ndwi)
    s.add_argument("--umbral-sombra", type=float, default=Umbrales.sombra_brillo)
    s.add_argument("--tesela-px", type=int, default=2048, help="lado de cada tesela (modo local)")
    s.add_argument("--max-gb", type=float, default=2.0,
                   help="no descarga si el estimado supera este tamaño (modo local)")
    s.add_argument("--guardar-clases", action="store_true",
                   help="guarda el raster de clases por tesela para revisarlo en QGIS (modo local)")

    s = sub.add_parser("demo", help="corre todo sobre una escena sintética, sin red")
    s.add_argument("--salida", default="resultados/demo")
    return p


def _bandas(servidor, args):
    if getattr(args, "bandas", None):
        return leer_mapa_bandas(args.bandas)
    mapa, autodetectado = mapa_de_bandas(servidor.info())
    if not autodetectado:
        print("Aviso: los nombres de banda no son reconocibles; se asume el orden estándar de "
              "Legion. Confírmalo con `baqgeo validar`.", file=sys.stderr)
    return mapa


def _extension(info):
    e = info["extent"]
    return box(e["xmin"], e["ymin"], e["xmax"], e["ymax"])


def _fecha(valor):
    if isinstance(valor, (int, float)) and valor > 1e11:
        return datetime.fromtimestamp(valor / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return valor


def inspeccionar(servidor, args):
    info = servidor.info()
    e = info.get("extent", {})
    print(f"Servicio:        {info.get('name')}")
    print(f"Descripción:     {(info.get('description') or '')[:300]}")
    print(f"Derechos:        {info.get('copyrightText')}")
    print(f"Bandas:          {info.get('bandCount')} {info.get('bandNames')}")
    print(f"Tipo de píxel:   {info.get('pixelType')}")
    print(f"Tamaño de píxel: {info.get('pixelSizeX')} x {info.get('pixelSizeY')}")
    print(f"CRS (wkid):      {servidor.wkid}")
    if e:
        area = (e["xmax"] - e["xmin"]) * (e["ymax"] - e["ymin"]) / 1e6
        print(f"Extensión:       {e['xmin']:.1f}, {e['ymin']:.1f}, {e['xmax']:.1f}, {e['ymax']:.1f}"
              f" (~{area:.1f} km²)")
    print(f"Máx. por imagen: {info.get('maxImageWidth')} x {info.get('maxImageHeight')} px")
    print(f"Capacidades:     {info.get('capabilities')}")
    print(f"Funciones raster: permitidas={info.get('allowRasterFunction')} "
          f"{[f.get('name') for f in info.get('rasterFunctionInfos') or []]}")
    try:
        mapa, autodetectado = mapa_de_bandas(info)
        origen = "nombres del servicio" if autodetectado else "orden estándar de Legion (supuesto)"
        print(f"Mapa de bandas:  {mapa} ({origen})")
    except ImageServerError as error:
        print(f"Mapa de bandas:  {error}")

    catalogo = servidor.catalogo()
    print(f"\nCatálogo: {len(catalogo)} imágenes")
    for atributos in catalogo[:20]:
        relevantes = {k: _fecha(v) for k, v in atributos.items()
                      if any(c in k.lower() for c in CAMPOS_CATALOGO)}
        print(f"  {relevantes}")
    if len(catalogo) > 1:
        print("  Varias imágenes: la radiometría puede cambiar entre pasadas; "
              "revisa las uniones antes de comparar zonas.")

    if getattr(args, "json", None):
        Path(args.json).write_text(
            json.dumps({"info": info, "catalogo": catalogo}, indent=2, ensure_ascii=False)
        )
        print(f"\nGuardado en {args.json}")


def _area_de_muestra(servidor, args):
    extension = _extension(servidor.info())
    if args.zonas:
        zonas = cargar_zonas(args.zonas, args.capa, args.donde, args.buffer, args.columna_nombre,
                             epsg=servidor.wkid)
        return zonas.union_all().intersection(extension)
    # Sin zonas: un cuadro de 2 km en el centro del servicio.
    centro = extension.centroid
    return centro.buffer(1000, cap_style="square").intersection(extension)


def ejecutar_validacion(servidor, args):
    bandas = _bandas(servidor, args)
    resultado = validar(servidor, bandas, _area_de_muestra(servidor, args), n=args.n)
    print(f"Puntos con dato: {resultado['n_puntos']}")
    if not resultado["n_puntos"]:
        print("Ningún punto devolvió valores: revisa la URL o el área.")
        return 1
    print("Rango de cada banda en la muestra:")
    for nombre, (minimo, maximo) in resultado["rangos_bandas"].items():
        print(f"  {nombre:9s} {minimo:10.1f} - {maximo:10.1f}")
    print("NDVI local (percentiles):",
          ", ".join(f"{k}={v:.3f}" for k, v in resultado["ndvi_local_percentiles"].items()))
    print("NDVI del servidor:")
    for funcion, r in resultado["funciones"].items():
        detalle = r.get("detalle") or (
            f"diferencia mediana {r['diferencia_mediana']:.4f}" if "diferencia_mediana" in r else ""
        )
        print(f"  {funcion:15s} {r['estado']:22s} {detalle}")
    for advertencia in resultado["advertencias"]:
        print(f"\nAviso: {advertencia}")
    if resultado["advertencias"]:
        return 1
    if resultado["recomendada"]:
        print(f"\nUsa: baqgeo indicadores ... --funcion-ndvi {resultado['recomendada']}")
        return 0
    print("\nNinguna función coincide: revisa --bandas o usa --modo local.")
    return 1


def _descargar_para(servidor, zonas, bandas, args):
    info = servidor.info()
    extension = _extension(info)
    area = zonas.union_all().intersection(extension)
    if area.is_empty:
        raise SystemExit("Las zonas no se cruzan con la imagen.")
    lado = min(args.tesela_px, info.get("maxImageWidth") or 4100, info.get("maxImageHeight") or 4100)
    teselas = filtrar_teselas(
        planificar_teselas(area.bounds, args.pixel, (extension.bounds[0], extension.bounds[3]), lado),
        area,
    )
    mb = estimar_mb(teselas, len(BANDAS_NECESARIAS))
    print(f"Descarga: {len(teselas)} teselas, ~{mb:,.0f} MB sin comprimir a {args.pixel} m")
    if mb / 1000 > args.max_gb:
        raise SystemExit(f"Supera --max-gb={args.max_gb}. Sube el límite, reduce el área o usa "
                         "--modo servidor.")
    seleccion = {n: bandas[n] for n in BANDAS_NECESARIAS}
    nativo = info.get("pixelSizeX") or args.pixel
    interpolacion = "RSP_NearestNeighbor" if args.pixel <= nativo * 1.01 else "RSP_BilinearInterpolation"
    carpeta = (Path(args.cache) / "teselas" /
               f"{args.pixel:g}m_px{lado}_b{'-'.join(str(seleccion[n]) for n in BANDAS_NECESARIAS)}")
    return descargar_teselas(servidor, teselas, carpeta, seleccion, interpolacion)


def ejecutar_indicadores(servidor, args):
    bandas = _bandas(servidor, args)
    zonas = cargar_zonas(args.zonas, args.capa, args.donde, args.buffer, args.columna_nombre,
                         epsg=servidor.wkid)
    umbrales = Umbrales(args.umbral_vegetacion, args.umbral_agua_ndvi, args.umbral_agua_ndwi,
                        args.umbral_sombra)
    print(f"{len(zonas)} zonas, modo {args.modo}, píxel {args.pixel} m")
    if args.modo == "servidor":
        regla = json.loads(args.regla_ndvi) if args.regla_ndvi else regla_ndvi(bandas, args.funcion_ndvi)
        indicadores = estadisticas_servidor(servidor, zonas, bandas, umbrales, args.pixel, regla,
                                            cache=Path(args.cache) / "histogramas")
    else:
        rutas = _descargar_para(servidor, zonas, bandas, args)
        clases = Path(args.salida) / "clases" if args.guardar_clases else None
        indicadores = estadisticas_local(rutas, zonas, umbrales, carpeta_clases=clases)

    resultado = armar_resultado(zonas, indicadores, args.poblacion)
    rutas = guardar(resultado, args.salida, args.prefijo)
    _imprimir_resumen(resultado)
    print("\nArchivos:")
    for ruta in rutas.values():
        print(f"  {ruta}")
    return resultado


def _imprimir_resumen(resultado, n=10):
    columnas = ["nombre", "pct_vegetacion", "pct_impermeable", "pct_agua", "pct_sombra",
                "ndvi_medio", "cobertura_pct"]
    tabla = resultado.sort_values("pct_vegetacion", na_position="last")[columnas].head(n)
    print(f"\nZonas con menos vegetación (primeras {min(n, len(resultado))}):")
    print(tabla.round(2).to_string(index=False))
    incompletas = resultado[resultado["cobertura_pct"] < 90]
    if len(incompletas):
        print(f"\nAviso: {len(incompletas)} zonas con menos del 90 % cubierto por la imagen "
              "(columna cobertura_pct).")


def ejecutar_demo(args):
    from . import demo

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    servidor = ImageServer("https://demo.invalid/arcgis/rest/services/demo/ImageServer",
                           session=demo.ServidorFalso())
    barrios = salida / "barrios_demo.gpkg"
    colegios = salida / "colegios_demo.gpkg"
    demo.zonas_sinteticas().to_file(barrios, driver="GPKG")
    demo.colegios_sinteticos().to_file(colegios, driver="GPKG")
    demo.guardar_escena(salida / "escena_sintetica.tif")
    parser = construir_parser()

    print("== inspeccionar ==")
    inspeccionar(servidor, parser.parse_args(["inspeccionar"]))
    print("\n== validar ==")
    ejecutar_validacion(servidor, parser.parse_args(["validar", "--zonas", str(barrios)]))

    resultados = {}
    for modo in ("servidor", "local"):
        print(f"\n== indicadores ({modo}) ==")
        resultados[modo] = ejecutar_indicadores(servidor, parser.parse_args([
            "indicadores", "--zonas", str(barrios), "--modo", modo, "--poblacion", "POBLACION",
            "--salida", str(salida), "--prefijo", f"barrios_{modo}", "--cache", str(salida / "cache"),
        ]))
    print("\n== indicadores de colegios (radio 50 m) ==")
    ejecutar_indicadores(servidor, parser.parse_args([
        "indicadores", "--zonas", str(colegios), "--buffer", "50", "--salida", str(salida),
        "--prefijo", "colegios_servidor", "--cache", str(salida / "cache"),
    ]))

    print("\n== comparación con la verdad sintética (% vegetación / impermeable) ==")
    for nombre, verdad in demo.VERDAD.items():
        local = resultados["local"].set_index("nombre").loc[nombre]
        servidor_ = resultados["servidor"].set_index("nombre").loc[nombre]
        print(f"  {nombre:17s} verdad {verdad['vegetacion']:5.1f} / {verdad['impermeable']:5.1f}"
              f" | local {local.pct_vegetacion:5.1f} / {local.pct_impermeable:5.1f}"
              f" | servidor {servidor_.pct_vegetacion:5.1f} / {servidor_.pct_impermeable:5.1f}"
              " (incluye sombra)")
    return 0


def main(argv=None, session=None):
    args = construir_parser().parse_args(argv)
    if args.comando == "demo":
        return ejecutar_demo(args)
    servidor = ImageServer(args.servicio, session=session, pausa=args.pausa)
    try:
        if args.comando == "inspeccionar":
            inspeccionar(servidor, args)
            return 0
        if args.comando == "validar":
            return ejecutar_validacion(servidor, args)
        ejecutar_indicadores(servidor, args)
        return 0
    except (ImageServerError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
