// Visor de indicadores por zona (MapLibre GL JS 5, sin build).
// Lee datos/capas.json, generado por `baqgeo visor`.

const CONFIG = {
  servicio:
    "https://miciudad.barranquilla.gov.co/image/rest/services/orto/" +
    "orto35_worldview_08001barranquilla_2026/ImageServer",
  // Índices base 1 (orden estándar de WorldView Legion). Se reemplazan por
  // los del archivo de metadatos si vienen ahí.
  bandas: { blue: 2, green: 3, red: 5, nir: 8 },
  capas: "datos/capas.json",
  centro: [-74.81, 10.98],
  zoom: 11.5,
};

// Rampas de 5 pasos ordenadas de menor a mayor valor, validadas como ordinales
// (lightness monótona, pasos >= 0,06, extremo claro >= 2:1 sobre la superficie).
// En oscuro el ancla se invierte: los valores bajos se funden con el fondo.
const RAMPAS = {
  verde: {
    claro: ["#6fc373", "#54a859", "#398e40", "#1a7426", "#025915"],
    oscuro: ["#036819", "#2b8134", "#479c4d", "#62b667", "#7dd281"],
  },
  naranja: {
    claro: ["#f78c51", "#da7134", "#bd5711", "#9a4303", "#763200"],
    oscuro: ["#893b01", "#ad4d04", "#cc6526", "#e97f44", "#ff9f6d"],
  },
};
const SIN_DATO = { claro: "#e1e0d9", oscuro: "#383835" };
const SUPERFICIE = { claro: "#fcfcfb", oscuro: "#1a1a19" };
const PLANO = { claro: "#f9f9f7", oscuro: "#0d0d0d" };
const TINTA = { claro: "#0b0b0b", oscuro: "#ffffff" };

const nf0 = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("es-CO", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("es-CO", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (v) => `${nf1.format(v)} %`;

// peorEsBajo: el ranking arranca por la zona en peor situación.
const INDICADORES = {
  pct_vegetacion: {
    titulo: "Vegetación", corto: "% vegetación", unidad: "% del área con vegetación",
    rampa: "verde", fmt: pct, peorEsBajo: true,
    peor: "con menos vegetación", mejor: "con más vegetación",
  },
  pct_impermeable: {
    titulo: "Impermeable", corto: "% impermeable", unidad: "% del área no vegetada",
    rampa: "naranja", fmt: pct, peorEsBajo: false,
    peor: "con más superficie impermeable", mejor: "con menos superficie impermeable",
  },
  ndvi_medio: {
    titulo: "NDVI medio", corto: "NDVI", unidad: "índice de vegetación, de −1 a 1",
    rampa: "verde", fmt: (v) => nf2.format(v), peorEsBajo: true,
    peor: "con menor NDVI medio", mejor: "con mayor NDVI medio",
  },
  m2_vegetacion_por_habitante: {
    titulo: "Verde por habitante", corto: "m²/hab", unidad: "m² de vegetación por habitante",
    rampa: "verde", fmt: (v) => `${nf1.format(v)} m²`, peorEsBajo: true,
    peor: "con menos verde por habitante", mejor: "con más verde por habitante",
  },
};

const FONDOS_IMAGEN = new Set(["imagen", "falso_color", "ndvi"]);

const estado = {
  catalogo: null,
  capa: null,
  datos: null,
  indicador: "pct_vegetacion",
  fondo: "mapa",
  seleccion: null,
  hover: null,
  orden: "peor",
  filtro: "",
  clases: null,
};

const $ = (id) => document.getElementById(id);
let mapa;

// ------------------------------------------------------------------ tema

const consultaOscuro = matchMedia("(prefers-color-scheme: dark)");
const pantallaAngosta = matchMedia("(max-width: 899px)");
function tema() {
  const forzado = document.documentElement.dataset.theme;
  if (forzado === "dark") return "oscuro";
  if (forzado === "light") return "claro";
  return consultaOscuro.matches ? "oscuro" : "claro";
}

function teselasBase(t) {
  const variante = t === "oscuro" ? "dark_all" : "light_all";
  return ["a", "b", "c", "d"].map(
    (s) => `https://${s}.basemaps.cartocdn.com/${variante}/{z}/{x}/{y}@2x.png`,
  );
}

// ------------------------------------------------------------ utilidades

function valor(feature, prop = estado.indicador) {
  const v = feature.properties[prop];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function sinAcentos(texto) {
  return texto.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

function limites(features) {
  let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
  const visitar = (c) => {
    if (typeof c[0] === "number") {
      x0 = Math.min(x0, c[0]); y0 = Math.min(y0, c[1]);
      x1 = Math.max(x1, c[0]); y1 = Math.max(y1, c[1]);
    } else c.forEach(visitar);
  };
  features.forEach((f) => f.geometry && visitar(f.geometry.coordinates));
  return Number.isFinite(x0) ? [[x0, y0], [x1, y1]] : null;
}

// Cortes por quintiles; se descartan los repetidos.
function cortes(valores, n = 5) {
  const v = valores.filter(Number.isFinite).sort((a, b) => a - b);
  const resultado = [];
  for (let i = 1; i < n; i++) {
    const q = v[Math.min(v.length - 1, Math.floor((i * v.length) / n))];
    if (q > v[0] && (resultado.length === 0 || q > resultado[resultado.length - 1])) {
      resultado.push(q);
    }
  }
  return resultado;
}

function calcularClases() {
  const ind = INDICADORES[estado.indicador];
  const valores = estado.datos.features.map((f) => valor(f)).filter((v) => v !== null);
  if (!valores.length) {
    estado.clases = { cortes: [], colores: [], cuentas: [], sinDato: estado.datos.features.length };
    return;
  }
  const c = cortes(valores);
  const k = c.length + 1;
  const rampa = RAMPAS[ind.rampa][tema()];
  const colores = k === 1 ? [rampa[2]] : Array.from({ length: k }, (_, i) => rampa[Math.round((i * 4) / (k - 1))]);
  const cuentas = new Array(k).fill(0);
  valores.forEach((v) => { cuentas[claseDe(v, c)] += 1; });
  estado.clases = {
    cortes: c, colores, cuentas,
    sinDato: estado.datos.features.length - valores.length,
    min: Math.min(...valores), max: Math.max(...valores),
  };
}

function claseDe(v, c) {
  let i = 0;
  while (i < c.length && v >= c[i]) i++;
  return i;
}

function colorDe(feature) {
  const v = valor(feature);
  if (v === null) return SIN_DATO[tema()];
  return estado.clases.colores[claseDe(v, estado.clases.cortes)];
}

function expresionColor() {
  const { cortes: c, colores } = estado.clases;
  if (!colores.length) return SIN_DATO[tema()];
  const prop = ["get", estado.indicador];
  const escalon = c.length ? ["step", prop, colores[0], ...c.flatMap((corte, i) => [corte, colores[i + 1]])] : colores[0];
  return ["case", ["==", ["typeof", prop], "number"], escalon, SIN_DATO[tema()]];
}

function ordenadas() {
  const ind = INDICADORES[estado.indicador];
  const asc = (estado.orden === "peor") === ind.peorEsBajo;
  return [...estado.datos.features]
    .filter((f) => valor(f) !== null)
    .sort((a, b) => (asc ? valor(a) - valor(b) : valor(b) - valor(a)));
}

function puestoPeor(feature) {
  const ind = INDICADORES[estado.indicador];
  const v = valor(feature);
  if (v === null) return null;
  const peores = estado.datos.features.filter((f) => {
    const w = valor(f);
    return w !== null && (ind.peorEsBajo ? w < v : w > v);
  });
  return peores.length + 1;
}

function mediana(valores) {
  const v = valores.filter(Number.isFinite).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

function porId(id) {
  return estado.datos.features.find((f) => f.properties.zona_id === id);
}

// ------------------------------------------------------------------ hash

function leerHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  return {
    capa: p.get("capa"), indicador: p.get("indicador"), fondo: p.get("fondo"),
    zona: p.has("zona") ? Number(p.get("zona")) : null,
  };
}

function escribirHash() {
  const p = new URLSearchParams();
  p.set("capa", estado.capa.id);
  p.set("indicador", estado.indicador);
  if (estado.fondo !== "mapa") p.set("fondo", estado.fondo);
  if (estado.seleccion !== null) p.set("zona", estado.seleccion);
  history.replaceState(null, "", `#${p}`);
}

// ------------------------------------------------------------- imagen

function urlImagen(tipo, bandas, servicio) {
  const regla = (o) => encodeURIComponent(JSON.stringify(o));
  const base =
    `${servicio}/exportImage?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857` +
    "&size=512,512&format=jpgpng&transparent=true&f=image";
  if (tipo === "imagen") return base;
  if (tipo === "falso_color") {
    return `${base}&renderingRule=${regla({
      rasterFunction: "Stretch",
      rasterFunctionArguments: {
        StretchType: 6, MinPercent: 0.5, MaxPercent: 0.5, DRA: false,
        Raster: {
          rasterFunction: "ExtractBand",
          rasterFunctionArguments: { BandIDs: [bandas.nir - 1, bandas.red - 1, bandas.green - 1] },
        },
      },
      outputPixelType: "U8",
    })}`;
  }
  return `${base}&renderingRule=${regla({
    rasterFunction: "Colormap",
    rasterFunctionArguments: {
      ColormapName: "NDVI",
      Raster: {
        rasterFunction: "NDVI",
        rasterFunctionArguments: { VisibleBandID: bandas.red - 1, InfraredBandID: bandas.nir - 1 },
      },
    },
  })}`;
}

function servicioDeCapa() {
  const meta = estado.capa?.meta || {};
  return meta.demo ? null : meta.servicio || CONFIG.servicio;
}

function aplicarFondo() {
  if (mapa.getLayer("imagen")) mapa.removeLayer("imagen");
  if (mapa.getSource("imagen")) mapa.removeSource("imagen");
  const servicio = servicioDeCapa();
  const conImagen = FONDOS_IMAGEN.has(estado.fondo) && servicio;
  if (conImagen) {
    const bandas = { ...CONFIG.bandas, ...(estado.capa.meta?.bandas || {}) };
    mapa.addSource("imagen", {
      type: "raster",
      tiles: [urlImagen(estado.fondo, bandas, servicio)],
      tileSize: 512,
      minzoom: 11,
      attribution: "Imagen WorldView Legion 2026 · geoportal Alcaldía de Barranquilla",
    });
    mapa.addLayer({ id: "imagen", type: "raster", source: "imagen" }, "zonas-relleno");
  }
  mapa.setPaintProperty("zonas-relleno", "fill-opacity", conImagen ? 0.25 : 0.82);
  mapa.setPaintProperty("zonas-borde", "line-color", conImagen ? "#ffffff" : SUPERFICIE[tema()]);
  mapa.setPaintProperty("zonas-borde", "line-width", conImagen ? 1.5 : 1);
  avisoMapa(
    FONDOS_IMAGEN.has(estado.fondo) && !servicio
      ? "La imagen no está disponible con datos de demostración."
      : null,
  );
}

let temporizadorAviso;
function avisoMapa(texto, segundos = 0) {
  const el = $("aviso-mapa");
  clearTimeout(temporizadorAviso);
  el.hidden = !texto;
  el.textContent = texto || "";
  if (texto && segundos) temporizadorAviso = setTimeout(() => (el.hidden = true), segundos * 1000);
}

// -------------------------------------------------------------- render

function renderIndicadores() {
  const cont = $("indicadores");
  cont.replaceChildren();
  const disponibles = Object.keys(INDICADORES).filter((k) =>
    estado.datos.features.some((f) => valor(f, k) !== null),
  );
  if (!disponibles.includes(estado.indicador)) estado.indicador = disponibles[0] ?? "pct_vegetacion";
  for (const k of disponibles) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "indicador";
    input.value = k;
    input.checked = k === estado.indicador;
    input.addEventListener("change", () => {
      estado.indicador = k;
      actualizar();
    });
    const span = document.createElement("span");
    span.textContent = INDICADORES[k].titulo;
    label.append(input, span);
    cont.append(label);
  }
}

function renderLeyenda() {
  const ind = INDICADORES[estado.indicador];
  const { cortes: c, colores, cuentas, sinDato, min, max } = estado.clases;
  const cont = $("leyenda");
  cont.replaceChildren();
  const h3 = document.createElement("h3");
  h3.textContent = ind.titulo;
  const sub = document.createElement("p");
  sub.className = "sub";
  sub.textContent = c.length ? `${ind.unidad} · quintiles` : ind.unidad;
  const ul = document.createElement("ul");
  const fila = (color, rango, cuenta) => {
    const li = document.createElement("li");
    const m = document.createElement("span");
    m.className = "muestra";
    m.style.background = color;
    const r = document.createElement("span");
    r.className = "rango";
    r.textContent = rango;
    const n = document.createElement("span");
    n.className = "cuenta";
    n.textContent = `(${nf0.format(cuenta)})`;
    li.append(m, r, n);
    ul.prepend(li); // el valor más alto arriba
  };
  colores.forEach((color, i) => {
    let rango;
    if (!c.length) rango = min === max ? ind.fmt(min) : `${ind.fmt(min)} – ${ind.fmt(max)}`;
    else if (i === 0) rango = `menos de ${ind.fmt(c[0])}`;
    else if (i === c.length) rango = `${ind.fmt(c[i - 1])} o más`;
    else rango = `${ind.fmt(c[i - 1])} – ${ind.fmt(c[i])}`;
    fila(color, rango, cuentas[i]);
  });
  if (sinDato) {
    fila(SIN_DATO[tema()], "sin dato", sinDato);
    ul.append(ul.firstChild); // "sin dato" al final
  }
  cont.append(h3, sub, ul);
}

function renderLista() {
  const ind = INDICADORES[estado.indicador];
  const lista = $("lista");
  lista.replaceChildren();
  $("ranking-titulo").textContent = `Ranking: ${ind.titulo.toLowerCase()}`;
  $("ranking-orden").textContent = `Primero las zonas ${estado.orden === "peor" ? ind.peor : ind.mejor}.`;
  $("orden").textContent = `Ver primero las zonas ${estado.orden === "peor" ? ind.mejor : ind.peor}`;
  const filtro = sinAcentos(estado.filtro.trim());
  const filas = ordenadas();
  let visibles = 0;
  filas.forEach((f, i) => {
    if (filtro && !sinAcentos(String(f.properties.nombre)).includes(filtro)) return;
    visibles++;
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.id = f.properties.zona_id;
    if (f.properties.zona_id === estado.seleccion) b.setAttribute("aria-current", "true");
    const pos = document.createElement("span");
    pos.className = "pos";
    pos.textContent = String(i + 1);
    const m = document.createElement("span");
    m.className = "muestra";
    m.style.background = colorDe(f);
    const nombre = document.createElement("span");
    nombre.className = "nombre";
    nombre.textContent = f.properties.nombre;
    const val = document.createElement("span");
    val.className = "valor";
    val.textContent = ind.fmt(valor(f));
    if ((f.properties.cobertura_pct ?? 100) < 90) {
      const parcial = document.createElement("span");
      parcial.className = "parcial";
      parcial.textContent = " ⚠";
      parcial.title = "La imagen no cubre toda la zona";
      nombre.append(parcial);
    }
    b.append(pos, m, nombre, val);
    b.addEventListener("click", () => seleccionar(f.properties.zona_id, true));
    li.append(b);
    lista.append(li);
  });
  $("lista-vacia").hidden = visibles > 0;
}

function cifra(dl, titulo, texto) {
  const div = document.createElement("div");
  const dt = document.createElement("dt");
  dt.textContent = titulo;
  const dd = document.createElement("dd");
  dd.textContent = texto;
  div.append(dt, dd);
  dl.append(div);
}

function renderDetalle() {
  const cont = $("detalle");
  const f = estado.seleccion !== null ? porId(estado.seleccion) : null;
  cont.hidden = !f;
  cont.replaceChildren();
  if (!f) return;
  const p = f.properties;
  const ind = INDICADORES[estado.indicador];

  const cab = document.createElement("div");
  cab.className = "detalle-cabecera";
  const h2 = document.createElement("h2");
  h2.textContent = p.nombre;
  const cerrar = document.createElement("button");
  cerrar.type = "button";
  cerrar.className = "boton-texto";
  cerrar.textContent = "Cerrar";
  cerrar.addEventListener("click", () => seleccionar(null));
  cab.append(h2, cerrar);

  const puesto = document.createElement("p");
  puesto.className = "puesto";
  const n = puestoPeor(f);
  const total = estado.datos.features.filter((g) => valor(g) !== null).length;
  const med = mediana(estado.datos.features.map((g) => valor(g)));
  if (n !== null) {
    puesto.textContent =
      `Puesto ${n} de ${total} entre las zonas ${ind.peor}. ` +
      `Mediana de las zonas: ${ind.fmt(med)}.`;
  } else {
    puesto.textContent = "Sin dato para este indicador.";
  }

  const dl = document.createElement("dl");
  dl.className = "cifras";
  const num = (k) => (typeof p[k] === "number" ? p[k] : null);
  if (num("pct_vegetacion") !== null) cifra(dl, "Vegetación", pct(p.pct_vegetacion));
  if (num("pct_impermeable") !== null) cifra(dl, "Impermeable", pct(p.pct_impermeable));
  if (num("ndvi_medio") !== null) cifra(dl, "NDVI medio", nf2.format(p.ndvi_medio));
  if (num("m2_vegetacion_por_habitante") !== null) {
    cifra(dl, "Verde por habitante", `${nf1.format(p.m2_vegetacion_por_habitante)} m²`);
  }
  if (num("pct_sombra") !== null) cifra(dl, "Sombra", pct(p.pct_sombra));
  if (num("pct_agua") !== null && p.pct_agua > 0) cifra(dl, "Agua", pct(p.pct_agua));
  if (num("area_m2") !== null) cifra(dl, "Área", `${nf1.format(p.area_m2 / 10000)} ha`);

  cont.append(cab, puesto, dl);
  if ((p.cobertura_pct ?? 100) < 90) {
    const adv = document.createElement("p");
    adv.className = "advertencia";
    adv.textContent = `La imagen cubre solo el ${nf0.format(p.cobertura_pct)} % de esta zona.`;
    cont.append(adv);
  }
}

function renderMetodo() {
  const meta = estado.capa.meta || {};
  const ul = $("metodo-lista");
  ul.replaceChildren();
  const item = (texto) => {
    const li = document.createElement("li");
    li.textContent = texto;
    ul.append(li);
  };
  if (meta.fecha_calculo) item(`Calculado el ${meta.fecha_calculo}.`);
  if (meta.modo === "servidor") {
    item("NDVI calculado por el ImageServer (histograma por zona), sin descargar la imagen.");
  } else if (meta.modo === "local") {
    item("Clasificación por píxel con NDVI, NDWI y brillo sobre teselas descargadas.");
  }
  if (meta.pixel_m) item(`Píxel de análisis: ${nf1.format(meta.pixel_m)} m (resolución nativa multiespectral).`);
  if (meta.umbrales?.vegetacion_ndvi !== undefined) {
    item(`Vegetación: NDVI ≥ ${nf2.format(meta.umbrales.vegetacion_ndvi)}.`);
  }
  if (meta.buffer_m) item(`Cada punto se analiza en un radio de ${nf0.format(meta.buffer_m)} m.`);
  $("aviso-demo").hidden = !meta.demo;
}

// ------------------------------------------------------------- acciones

function seleccionar(id, volar = false) {
  if (estado.seleccion !== null) {
    mapa.setFeatureState({ source: "zonas", id: estado.seleccion }, { seleccionada: false });
  }
  estado.seleccion = id;
  if (id !== null) {
    mapa.setFeatureState({ source: "zonas", id }, { seleccionada: true });
    const f = porId(id);
    const b = f && limites([f]);
    if (volar && b) mapa.fitBounds(b, { padding: 60, maxZoom: 16, duration: 600 });
  }
  renderDetalle();
  renderLista();
  escribirHash();
}

function actualizar() {
  calcularClases();
  if (mapa.getLayer("zonas-relleno")) mapa.setPaintProperty("zonas-relleno", "fill-color", expresionColor());
  renderLeyenda();
  renderLista();
  renderDetalle();
  escribirHash();
}

async function cargarCapa(capa, zonaInicial = null) {
  const r = await fetch(`datos/${capa.archivo}`);
  if (!r.ok) throw new Error(`No pude leer datos/${capa.archivo} (HTTP ${r.status})`);
  estado.capa = capa;
  estado.datos = await r.json();
  estado.seleccion = null;
  mapa.removeFeatureState({ source: "zonas" });
  mapa.getSource("zonas").setData(estado.datos);
  renderIndicadores();
  renderMetodo();
  aplicarFondo();
  actualizar();
  const b = limites(estado.datos.features);
  if (b) mapa.fitBounds(b, { padding: 40, duration: 0 });
  if (zonaInicial !== null && porId(zonaInicial)) seleccionar(zonaInicial, true);
}

function prepararMapa() {
  const t = tema();
  mapa = new maplibregl.Map({
    container: "mapa",
    center: CONFIG.centro,
    zoom: CONFIG.zoom,
    attributionControl: { compact: true },
    style: {
      version: 8,
      sources: {
        base: {
          type: "raster", tiles: teselasBase(t), tileSize: 256, maxzoom: 20,
          attribution: "© OpenStreetMap © CARTO",
        },
      },
      layers: [
        { id: "plano", type: "background", paint: { "background-color": PLANO[t] } },
        { id: "base", type: "raster", source: "base" },
      ],
    },
  });
  mapa.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  mapa.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");

  return new Promise((resolve) => {
    mapa.on("load", () => {
      mapa.addSource("zonas", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
        promoteId: "zona_id",
      });
      mapa.addLayer({
        id: "zonas-relleno", type: "fill", source: "zonas",
        paint: { "fill-color": SIN_DATO[t], "fill-opacity": 0.82 },
      });
      // Línea del color de la superficie: separa zonas vecinas sin dibujar bordes.
      mapa.addLayer({
        id: "zonas-borde", type: "line", source: "zonas",
        paint: { "line-color": SUPERFICIE[t], "line-width": 1 },
      });
      const sel = ["boolean", ["feature-state", "seleccionada"], false];
      const hov = ["boolean", ["feature-state", "hover"], false];
      mapa.addLayer({
        id: "zonas-resaltado", type: "line", source: "zonas",
        paint: {
          "line-color": TINTA[t],
          "line-width": ["case", sel, 3, 2],
          "line-opacity": ["case", sel, 1, hov, 0.7, 0],
        },
      });
      escucharMapa();
      resolve();
    });
  });
}

function escucharMapa() {
  const tooltip = $("tooltip");
  mapa.on("mousemove", "zonas-relleno", (e) => {
    const f = e.features[0];
    const id = f.properties.zona_id;
    if (estado.hover !== id) {
      if (estado.hover !== null) mapa.setFeatureState({ source: "zonas", id: estado.hover }, { hover: false });
      estado.hover = id;
      mapa.setFeatureState({ source: "zonas", id }, { hover: true });
    }
    mapa.getCanvas().style.cursor = "pointer";
    const ind = INDICADORES[estado.indicador];
    const v = valor(f);
    const fuerte = document.createElement("strong");
    fuerte.textContent = v === null ? "sin dato" : `${ind.fmt(v)}`;
    const nombre = document.createElement("span");
    nombre.textContent = `${f.properties.nombre} · ${ind.titulo.toLowerCase()}`;
    tooltip.replaceChildren(fuerte, nombre);
    tooltip.style.left = `${e.point.x}px`;
    tooltip.style.top = `${e.point.y}px`;
    tooltip.hidden = false;
  });
  mapa.on("mouseleave", "zonas-relleno", () => {
    if (estado.hover !== null) mapa.setFeatureState({ source: "zonas", id: estado.hover }, { hover: false });
    estado.hover = null;
    mapa.getCanvas().style.cursor = "";
    tooltip.hidden = true;
  });
  mapa.on("click", "zonas-relleno", (e) => {
    seleccionar(e.features[0].properties.zona_id);
    // En pantallas angostas el detalle queda debajo del mapa: lo traemos a la vista.
    if (pantallaAngosta.matches) $("detalle").scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  mapa.on("error", (e) => {
    if (e.sourceId === "imagen") {
      avisoMapa("El servicio de la Alcaldía no devolvió esta vista de la imagen.", 6);
    }
  });
}

function cambiarTema() {
  const t = tema();
  mapa.getSource("base").setTiles(teselasBase(t));
  mapa.setPaintProperty("plano", "background-color", PLANO[t]);
  mapa.setPaintProperty("zonas-resaltado", "line-color", TINTA[t]);
  aplicarFondo();
  actualizar();
}

function mostrarError(texto) {
  const p = document.createElement("p");
  p.className = "muted";
  p.textContent = texto;
  $("lista").replaceChildren(p);
  avisoMapa(texto);
}

// ---------------------------------------------------------------- inicio

async function iniciar() {
  if (!window.maplibregl) {
    mostrarError("No se pudo cargar MapLibre. Revisa la conexión.");
    return;
  }
  await prepararMapa();

  let catalogo;
  try {
    const r = await fetch(CONFIG.capas);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    catalogo = await r.json();
  } catch {
    mostrarError(
      "No hay datos. Genera el visor con: baqgeo visor --capa Barrios=resultados/barrios/indicadores.geojson",
    );
    return;
  }
  estado.catalogo = catalogo;
  if (catalogo.titulo) {
    $("titulo").textContent = catalogo.titulo;
  }

  const inicial = leerHash();
  if (inicial.indicador && INDICADORES[inicial.indicador]) estado.indicador = inicial.indicador;
  if (inicial.fondo && (inicial.fondo === "mapa" || FONDOS_IMAGEN.has(inicial.fondo))) {
    estado.fondo = inicial.fondo;
  }
  $("fondo").value = estado.fondo;

  const selectorCapa = $("capa");
  for (const capa of catalogo.capas) {
    const op = document.createElement("option");
    op.value = capa.id;
    op.textContent = capa.titulo;
    selectorCapa.append(op);
  }
  $("control-capa").hidden = catalogo.capas.length < 2;
  const capa = catalogo.capas.find((c) => c.id === inicial.capa) || catalogo.capas[0];
  selectorCapa.value = capa.id;

  selectorCapa.addEventListener("change", () => {
    const elegida = catalogo.capas.find((c) => c.id === selectorCapa.value);
    cargarCapa(elegida).catch((e) => mostrarError(e.message));
  });
  $("fondo").addEventListener("change", (e) => {
    estado.fondo = e.target.value;
    aplicarFondo();
    escribirHash();
  });
  $("orden").addEventListener("click", () => {
    estado.orden = estado.orden === "peor" ? "mejor" : "peor";
    renderLista();
  });
  $("buscar").addEventListener("input", (e) => {
    estado.filtro = e.target.value;
    renderLista();
  });
  consultaOscuro.addEventListener("change", cambiarTema);

  try {
    await cargarCapa(capa, inicial.zona);
  } catch (e) {
    mostrarError(e.message);
  }
}

iniciar();
