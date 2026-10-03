"""Busca capas (barrios, colegios...) en los servicios ArcGIS REST de un geoportal."""

import unicodedata

import requests

# Rutas habituales de ArcGIS Server / Enterprise.
RAICES = (
    "/server/rest/services",
    "/arcgis/rest/services",
    "/hosting/rest/services",
    "/gis/rest/services",
    "/geoportal/rest/services",
    "/image/rest/services",
)


def _normalizar(texto):
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return t.lower()


def _json(session, url, timeout, **params):
    r = session.get(url, params={"f": "json", **params}, timeout=timeout)
    r.raise_for_status()
    datos = r.json()
    if "error" in datos:
        raise RuntimeError(datos["error"].get("message"))
    return datos


def buscar_capas(base, palabras, session=None, timeout=30, max_servicios=400, progreso=print):
    """Recorre los directorios REST de `base` y devuelve las capas cuyo nombre
    (o el de su servicio) contiene alguna de `palabras`.

    Cada resultado trae url, nombre, servicio, geometría, número de entidades
    y campos, que es lo que hace falta para usarla con `--zonas`.
    """
    session = session or requests.Session()
    palabras = [_normalizar(p) for p in palabras if p.strip()]
    encontrados = []
    revisados = 0
    for raiz in RAICES:
        url_raiz = base.rstrip("/") + raiz
        try:
            directorio = _json(session, url_raiz, timeout)
        except Exception as error:  # noqa: BLE001 - cualquier fallo = raíz no disponible
            progreso(f"  {raiz}: no disponible ({type(error).__name__})")
            continue
        carpetas = directorio.get("folders", [])
        servicios = list(directorio.get("services", []))
        for carpeta in carpetas:
            try:
                servicios += _json(session, f"{url_raiz}/{carpeta}", timeout).get("services", [])
            except Exception:  # noqa: BLE001
                progreso(f"  {raiz}/{carpeta}: no se pudo listar")
        progreso(f"  {raiz}: {len(carpetas)} carpetas, {len(servicios)} servicios")

        for servicio in servicios:
            if servicio.get("type") not in ("MapServer", "FeatureServer"):
                continue
            if revisados >= max_servicios:
                progreso(f"  límite de {max_servicios} servicios alcanzado")
                return encontrados
            revisados += 1
            url_servicio = f"{url_raiz}/{servicio['name']}/{servicio['type']}"
            try:
                capas = _json(session, url_servicio, timeout).get("layers", [])
            except Exception:  # noqa: BLE001
                continue
            nombre_servicio = _normalizar(servicio["name"])
            for capa in capas:
                texto = f"{nombre_servicio} {_normalizar(capa.get('name', ''))}"
                if not any(p in texto for p in palabras):
                    continue
                encontrados.append(_detallar(session, url_servicio, capa, servicio["name"], timeout))
    return encontrados


def _detallar(session, url_servicio, capa, nombre_servicio, timeout):
    url = f"{url_servicio}/{capa['id']}"
    resultado = {
        "url": url,
        "capa": capa.get("name"),
        "servicio": nombre_servicio,
        "geometria": capa.get("geometryType"),
        "entidades": None,
        "campos": [],
    }
    try:
        info = _json(session, url, timeout)
        resultado["geometria"] = info.get("geometryType") or resultado["geometria"]
        resultado["campos"] = [c.get("name") for c in info.get("fields") or []]
        conteo = _json(session, f"{url}/query", timeout, where="1=1", returnCountOnly="true")
        resultado["entidades"] = conteo.get("count")
    except Exception:  # noqa: BLE001
        pass
    return resultado
