"""
KriteriusMX — versión remota (HTTP) del conector de fuentes jurídicas mexicanas
e interamericanas. Paridad de tools con la versión local en Node (kriterius-mx.mcpb).

Fuentes:
  SJF / SCJN   sjf2.scjn.gob.mx        API JSON (ingeniería inversa)
  TFJA         tfja.gob.mx/cesmdfa     scraping con sesión CSRF
  DOF          dof.gob.mx + sidof      scraping, con espejo de SEGOB como respaldo
  Corte IDH    bjdh.org.mx             scraping del Buscador Jurídico de DH

API del SJF (no documentado oficialmente):
  POST /services/sjftesismicroservice/api/public/tesis?page=N&size=M
  GET  /services/sjftesismicroservice/api/public/tesis/{registro}
"""

import html
import re

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

BASE = "https://sjf2.scjn.gob.mx/services/sjftesismicroservice/api/public"
HOST = "https://sjf2.scjn.gob.mx"

# IDs internos del clasificador idEpoca (verificados contra el API)
EPOCAS = {
    "5a": "1", "6a": "2", "7a": "3", "8a": "4",
    "9a": "5", "10a": "100", "11a": "200", "12a": "210",
}

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-MX,es;q=0.9",
    "Origin": HOST,
    "Referer": f"{HOST}/busqueda-principal-tesis",
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
}

# ---- Cuidado de las fuentes: caché, freno de concurrencia y reintentos ----
#
# El servidor es uno solo y atiende a todos los usuarios, así que sin estos topes
# cada consulta viaja directo a los portales de gobierno desde la misma IP. Tres
# mecanismos evitan que eso se convierta en un bloqueo:
#
#   Caché       lo que ya se preguntó no se vuelve a pedir. El material jurídico
#               casi no cambia, así que los tiempos de vida son generosos.
#   Semáforo    tope de peticiones simultáneas por fuente, para no abrir veinte
#               conexiones de golpe contra el mismo sitio.
#   Reintento   ante un fallo pasajero espera 1s, 2s y 4s antes de rendirse.
#
# No hay límite por usuario porque Claude se conecta desde la infraestructura de
# Anthropic: todas las peticiones llegan con el mismo origen y no hay forma de
# distinguir a quién preguntó. El tope global cumple el objetivo real, que es
# cuidar a los portales.

import asyncio as _asyncio
import hashlib as _hashlib
import json as _json
import time as _time
from collections import OrderedDict as _OrderedDict, deque as _deque

CACHE_MAX_ENTRADAS = 400
LIMITE_PETICIONES_MINUTO = 60
ESPERA_MAX_CUPO_S = 3.0

# Tiempos de vida en segundos, según qué tanto cambia cada cosa
TTL = {
    "sjf_detalle": 30 * 24 * 3600,   # una tesis publicada ya no cambia
    "sjf_busqueda": 6 * 3600,
    "tfja": 24 * 3600,
    "bjdh": 7 * 24 * 3600,           # sentencias de la Corte IDH, prácticamente fijas
    "dof_hoy": 3600,                 # la edición del día todavía puede crecer
    "dof_pasado": 30 * 24 * 3600,    # un DOF de ayer ya quedó cerrado
    "dof_indicadores": 12 * 3600,
}

CONCURRENCIA = {"sjf": 4, "tfja": 3, "dof": 6, "bjdh": 3}


class _Cache:
    """Caché en memoria con vencimiento y tope de entradas. Descarta lo más viejo
    cuando se llena, que en 512 MB de RAM es lo que evita un disgusto."""

    def __init__(self, max_entradas: int = CACHE_MAX_ENTRADAS):
        self._datos: _OrderedDict = _OrderedDict()
        self._max = max_entradas
        self.acierto = 0
        self.fallo = 0

    def obtener(self, clave: str):
        item = self._datos.get(clave)
        if item is None:
            self.fallo += 1
            return None
        valor, vence = item
        if _time.time() > vence:
            del self._datos[clave]
            self.fallo += 1
            return None
        self._datos.move_to_end(clave)
        self.acierto += 1
        return valor

    def guardar(self, clave: str, valor, ttl: float) -> None:
        if valor is None:
            return
        self._datos[clave] = (valor, _time.time() + ttl)
        self._datos.move_to_end(clave)
        while len(self._datos) > self._max:
            self._datos.popitem(last=False)

    def resumen(self) -> str:
        total = self.acierto + self.fallo
        pct = (self.acierto / total * 100) if total else 0
        return f"{len(self._datos)}/{self._max} entradas · {self.acierto} aciertos de {total} ({pct:.0f}%)"


_CACHE = _Cache()
_SEMAFOROS: dict = {}
_PETICIONES = _deque()


def _semaforo(fuente: str):
    """Los semáforos se crean cuando ya hay bucle de eventos corriendo."""
    if fuente not in _SEMAFOROS:
        _SEMAFOROS[fuente] = _asyncio.Semaphore(CONCURRENCIA.get(fuente, 4))
    return _SEMAFOROS[fuente]


def _clave(*partes) -> str:
    crudo = "|".join(_json.dumps(p, sort_keys=True, default=str) for p in partes)
    return _hashlib.sha256(crudo.encode()).hexdigest()[:32]


async def _esperar_cupo() -> None:
    """Tope global de peticiones salientes por minuto, con espera breve."""
    limite = _time.monotonic() + ESPERA_MAX_CUPO_S
    while True:
        ahora = _time.monotonic()
        while _PETICIONES and ahora - _PETICIONES[0] > 60:
            _PETICIONES.popleft()
        if len(_PETICIONES) < LIMITE_PETICIONES_MINUTO:
            _PETICIONES.append(ahora)
            return
        if _time.monotonic() >= limite:
            raise RuntimeError(
                f"el conector alcanzó su tope de {LIMITE_PETICIONES_MINUTO} consultas por minuto "
                f"a las fuentes oficiales. Espera unos segundos y vuelve a intentar."
            )
        await _asyncio.sleep(0.25)


def _es_pasajero(e: Exception) -> bool:
    if isinstance(e, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)):
        return True
    m = str(e).lower()
    return any(s in m for s in ("429", "502", "503", "504", "imperva", "timeout", "temporal"))


async def _traer(fuente: str, hacer, clave: str | None = None, ttl: float | None = None):
    """Punto único de salida a la red: consulta la caché, respeta el tope global,
    limita la concurrencia por fuente y reintenta los fallos pasajeros."""
    if clave:
        guardado = _CACHE.obtener(clave)
        if guardado is not None:
            return guardado

    await _esperar_cupo()

    async with _semaforo(fuente):
        ultimo: Exception | None = None
        for intento in range(3):
            try:
                resultado = await hacer()
                if clave and ttl:
                    _CACHE.guardar(clave, resultado, ttl)
                return resultado
            except Exception as e:
                ultimo = e
                if intento == 2 or not _es_pasajero(e):
                    raise
                await _asyncio.sleep(2 ** intento)
        raise ultimo  # pragma: no cover


# El SDK trae protección contra DNS rebinding y la aplica al transporte HTTP:
# si el header Host de la petición no está en esta lista, responde 421 y el
# cliente no logra ni el handshake. Sin declarar el dominio, Claude reporta un
# fallo de "sign-in service" que despista, porque el 421 ocurre antes de todo.
SEGURIDAD_TRANSPORTE = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[
        "mcp.kriterius.mx", "mcp.kriterius.mx:*",
        "kriterius.mx", "kriterius.mx:*",
        "kriterius-mx-a8xhr.ondigitalocean.app",
        "localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*",
    ],
    allowed_origins=[
        "https://claude.ai", "https://claude.com",
        "https://mcp.kriterius.mx", "https://kriterius.mx",
        "http://localhost:*", "http://127.0.0.1:*",
    ],
)

mcp = FastMCP(
    "KriteriusMX",
    transport_security=SEGURIDAD_TRANSPORTE,
    website_url="https://kriterius.mx",
    instructions=(
        "Consulta fuentes oficiales del derecho mexicano e interamericano: Semanario "
        "Judicial de la Federación (SCJN), Tribunal Federal de Justicia Administrativa, "
        "Diario Oficial de la Federación y Corte Interamericana de Derechos Humanos. "
        "Al citar cualquier criterio incluye SIEMPRE la cita completa y su link oficial. "
        "Los resultados no sustituyen la consulta directa a la fuente."
    ),
)

# Base mutable del API del SJF: permite auto-descubrir el endpoint si cambia
_SJF_BASE = BASE


async def _sjf_descubrir_base() -> str | None:
    """Busca en el HTML y bundles JS del sitio del SJF rutas /services/.../api/public
    para re-descubrir el endpoint si la SCJN lo mueve o renombra."""
    global _SJF_BASE
    from urllib.parse import urljoin
    patron = r"/services/[A-Za-z0-9_-]+/api/public"
    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        r = await client.get(f"{HOST}/busqueda-principal-tesis")
        rutas = re.findall(patron, r.text)
        if not rutas:
            scripts = re.findall(r"src=[\"']([^\"']*(?:main|runtime|chunk)[^\"']*\.js)[\"']",
                                 r.text, flags=re.I)[:4]
            for s in scripts:
                try:
                    js = (await client.get(urljoin(HOST + "/", s))).text
                    rutas += re.findall(patron, js)
                except Exception:
                    continue
    ruta = next((x for x in rutas if "tesis" in x.lower()), rutas[0] if rutas else None)
    if not ruta:
        return None
    _SJF_BASE = f"{HOST}{ruta}"
    return _SJF_BASE


async def _sjf_fetch(path: str, method: str = "GET", json_body: dict | None = None):
    """Llama al API del SJF; si falla, auto-descubre el endpoint y reintenta una vez.

    Pasa por caché y por el freno de concurrencia: el detalle de una tesis se guarda
    un mes porque no cambia, y las búsquedas unas horas."""
    ttl = TTL["sjf_detalle"] if method == "GET" else TTL["sjf_busqueda"]
    clave = _clave("sjf", method, path, json_body)

    async def pedir():
        async with httpx.AsyncClient(headers=HEADERS, timeout=30) as client:
            r = await client.request(method, f"{_SJF_BASE}{path}", json=json_body)
            if r.status_code != 200:
                raise RuntimeError(f"respondió {r.status_code}")
            if not r.text.strip():
                return None
            try:
                return r.json()
            except Exception:
                extracto = re.sub(r"\s+", " ", _strip_html(r.text))[:120]
                raise RuntimeError(f"respondió contenido no-JSON (posible cambio de API): '{extracto}'")

    async def intento():
        return await _traer("sjf", pedir, clave=clave, ttl=ttl)

    try:
        return await intento()
    except Exception as e1:
        nueva = None
        try:
            nueva = await _sjf_descubrir_base()
        except Exception:
            pass
        if not nueva:
            raise RuntimeError(
                f"El API del SJF falló ({e1}) y el auto-descubrimiento no encontró un endpoint "
                f"alternativo. Puede ser falla temporal o un cambio mayor del API. Ejecuta "
                f"diagnosticar_conector; si persiste, el conector necesita re-mapearse.")
        try:
            return await intento()
        except Exception as e2:
            raise RuntimeError(
                f"El API del SJF falló ({e1}). Se auto-descubrió {nueva} pero también falló "
                f"({e2}). La estructura del API probablemente cambió; el conector necesita "
                f"re-mapearse (pide a Claude en Cowork re-mapearlo con el navegador).")


def _strip_html(texto: str | None) -> str:
    if not texto:
        return ""
    texto = re.sub(r"<style[\s\S]*?</style>|<script[\s\S]*?</script>", " ", texto, flags=re.I)
    texto = re.sub(r"<br\s*/?>|</p>", "\n", texto, flags=re.I)
    texto = re.sub(r"<[^>]+>", "", texto)
    return html.unescape(texto).strip()


# ---- Jerarquía de criterios del SJF ----
#
# Prelación definida por el usuario:
#   1. Órgano: Pleno > 1a Sala > 2a Sala > Salas históricas > Plenos Regionales
#              > Plenos de Circuito > TCC
#   2. Jurisprudencia antes que tesis aislada
#   3. Época más reciente primero
#   4. Coincidencia en el rubro antes que solo en el texto

# NOTA: idéntico a rangoOrgano()/numeroEpoca() de la versión Node. Si cambia uno,
# cambiar el otro: test_corteidh.py y test-corteidh.js verifican ambos.

INSTANCIAS = {
    "pleno": {"id": "6", "etiqueta": "Pleno SCJN", "rango": 0},
    "primera": {"id": "1", "etiqueta": "Primera Sala", "rango": 1},
    "segunda": {"id": "2", "etiqueta": "Segunda Sala", "rango": 2},
    "plenos_regionales": {"id": "60", "etiqueta": "Plenos Regionales", "rango": 4},
    "plenos_circuito": {"id": "50", "etiqueta": "Plenos de Circuito", "rango": 5},
    "tcc": {"id": "7", "etiqueta": "Tribunales Colegiados", "rango": 6},
}

NOMBRE_ORGANO = [
    "Pleno SCJN", "Primera Sala", "Segunda Sala", "Salas históricas (3a/4a/Aux.)",
    "Plenos Regionales", "Plenos de Circuito", "Tribunales Colegiados de Circuito",
    "Tribunal de Disciplina Judicial", "Otros órganos",
]


def _rango_organo(localizacion: str) -> int:
    l = _normalizar_txt(localizacion or "")
    if re.search(r"plenos? regional", l):
        return 4
    if re.search(r"plenos? de circuito", l):
        return 5
    if re.search(r"\bpleno\b", l):
        return 0
    if re.search(r"1a\.? sala|primera sala", l):
        return 1
    if re.search(r"2a\.? sala|segunda sala", l):
        return 2
    if re.search(r"3a\.? sala|4a\.? sala|sala aux", l):
        return 3
    if re.search(r"t\.?c\.?c|tribunales colegiados", l):
        return 6
    if re.search(r"tdj|disciplina", l):
        return 7
    return 8


def _numero_epoca(localizacion: str) -> int:
    """Número de época para ordenar (mayor = más reciente).

    El SJF publica la época en formato numérico ("12a. Época"), no con el ordinal
    escrito. Se aceptan ambos por si cambia, cuidando que "undécima" no se lea como
    "décima" (por eso el orden y los límites de palabra)."""
    loc = _normalizar_txt(localizacion or "")
    m = re.search(r"(\d+)a\.?\s*epoca", loc)
    if m:
        return int(m.group(1))
    ordinales = [("duodecima", 12), ("undecima", 11), ("decima", 10), ("novena", 9),
                 ("octava", 8), ("septima", 7), ("sexta", 6), ("quinta", 5)]
    for nombre, n in ordinales:
        if re.search(rf"\b{nombre}\b", loc):
            return n
    return 0


def _normalizar_txt(s: str) -> str:
    import unicodedata as _u
    return "".join(c for c in _u.normalize("NFD", (s or "").lower())
                   if _u.category(c) != "Mn")


def _ordenar_por_jerarquia(docs: list[dict], consulta: str) -> list[dict]:
    """Ordena por obligatoriedad: órgano, luego jurisprudencia, luego época y
    finalmente si la coincidencia está en el rubro (más pertinente) o solo en el texto."""
    terminos = [t for t in re.split(r"\W+", _normalizar_txt(consulta)) if len(t) > 3]

    def clave(par):
        i, d = par
        loc = _strip_html(d.get("localizacion") or "")
        es_j = "[J]" in loc or d.get("ta_tj") == 1
        rubro = _normalizar_txt(_strip_html(d.get("rubro") or ""))
        en_rubro = any(t in rubro for t in terminos) if terminos else False
        return (_rango_organo(loc), 0 if es_j else 1, -_numero_epoca(loc),
                0 if en_rubro else 1, i)

    return [d for _, d in sorted(enumerate(docs), key=clave)]


def _linea_resultado(d: dict) -> list[str]:
    """Formato acordado: [RUBRO], [Órgano], [Tipo], [Integración], [Época], [Registro]
    seguido del link directo, que es obligatorio al citar."""
    loc = _strip_html(d.get("localizacion") or "")
    partes = [p.strip() for p in loc.split(";")]
    epoca = next((p for p in partes if "Época" in p), "")
    organo = partes[2] if len(partes) >= 3 else ""
    es_j = "[J]" in loc or d.get("ta_tj") == 1
    tipo_int = _strip_html(str(d.get("tipoJurisprudencia") or "")) or "—"
    return [
        f"[{_strip_html(d.get('rubro'))}], [{organo}], "
        f"[{'Jurisprudencia' if es_j else 'Tesis Aislada'}], "
        f"[{tipo_int}], [{epoca}], [{d.get('ius')}]",
        f"{HOST}/detalle/tesis/{d.get('ius')}",
        "",
    ]


def _search_body(consulta: str, epocas: list[str] | None, incluir_precedentes: bool) -> dict:
    fields = ["localizacionBusqueda", "rubro", "texto"]
    if incluir_precedentes:
        fields.append("precedentes")
    classifiers = []
    if epocas:
        ids = [EPOCAS[e.lower().strip()] for e in epocas if e.lower().strip() in EPOCAS]
        if ids:
            classifiers.append({
                "name": "idEpoca", "value": ids,
                "allSelected": False, "visible": False, "isMatrix": False,
            })
    return {
        "classifiers": classifiers,
        "searchTerms": [{
            "expression": consulta,
            "fields": fields,
            "fieldsUser": "", "fieldsText": "",
            "operator": 0, "operatorUser": "Y", "operatorText": "Y",
            "lsFields": [], "esInicial": True, "esNRD": False,
        }],
        "bFacet": False,
        "ius": [],
        "idApp": "SJFAPP2020",
        "lbSearch": [],
        "filterExpression": "",
    }


@mcp.tool()
async def buscar_tesis(
    consulta: str,
    epocas: list[str] | None = None,
    tipo: str | None = None,
    incluir_precedentes: bool = False,
    pagina: int = 1,
    por_pagina: int = 20,
    orden: str = "jerarquia",
) -> str:
    """Busca tesis y jurisprudencia en el Semanario Judicial de la Federación (SCJN).

    Args:
        consulta: Expresión de búsqueda. Usa comillas dobles para frases exactas,
            p. ej. '"interés legítimo"'. Busca en localización, rubro y texto.
        epocas: Filtro opcional de épocas: lista con valores de "5a" a "12a"
            (p. ej. ["11a", "12a"]). Sin filtro busca en todas (9a-12a son las vigentes).
        tipo: Filtro opcional: "jurisprudencia" (obligatoria, [J]) o "aislada" ([TA]).
            Se aplica sobre los resultados de la página, no sobre el total.
        incluir_precedentes: Si True, también busca la expresión en los precedentes.
        pagina: Página de resultados (desde 1).
        por_pagina: Resultados por página (máx. recomendado 50).
        orden: "jerarquia" (default) ordena por obligatoriedad — órgano
            (Pleno > Salas > Plenos Regionales > Plenos de Circuito > TCC),
            jurisprudencia antes que aislada, época más reciente y coincidencia
            en el rubro. "fecha" respeta el orden nativo del SJF.

    Returns:
        Lista de resultados con registro digital, tipo, rubro, localización y link
        directo (https://sjf2.scjn.gob.mx/detalle/tesis/{registro}). Al citar un
        criterio al usuario incluye SIEMPRE su link, para ir directo al texto oficial.
        Usa ver_tesis(registro_digital) para leer el texto íntegro.
    """
    body = _search_body(consulta, epocas, incluir_precedentes)
    page, size = max(pagina, 1) - 1, min(max(por_pagina, 1), 50)
    data = await _sjf_fetch(f"/tesis?page={page}&size={size}", method="POST", json_body=body)

    if data and "documents" not in data and "total" not in data:
        claves = ", ".join(list(data.keys())[:8])
        return (f"El API del SJF respondió con una estructura distinta a la esperada "
                f"(claves recibidas: {claves}). El API probablemente cambió; ejecuta "
                f"diagnosticar_conector y, si persiste, el conector necesita re-mapearse.")
    data = data or {}
    docs = data.get("documents") or []
    total = data.get("total", 0)
    if tipo:
        es_j = tipo.lower().startswith("j")
        marca = "[J]" if es_j else "[TA]"
        docs = [d for d in docs
                if marca in (d.get("localizacion") or "")
                or d.get("ta_tj") == (1 if es_j else 0)]

    if not docs:
        return f"Sin resultados para '{consulta}' (total en el sistema: {total})."

    if orden == "jerarquia":
        docs = _ordenar_por_jerarquia(docs, consulta)

    lineas = [f"Total: {total} resultados. Página {pagina} ({len(docs)} mostrados"
              + (f", filtrados por tipo={tipo}" if tipo else "") + ").",
              "Formato: [RUBRO], [Órgano], [Jurisprudencia/Tesis Aislada], "
              "[Tipo de integración], [Época], [No. de Registro] + link directo "
              "al criterio (inclúyelo SIEMPRE al citar).",
              ("Orden: jerarquía de obligatoriedad (órgano → jurisprudencia → "
               "época reciente → coincidencia en rubro)."
               if orden == "jerarquia" else "Orden: nativo del SJF por publicación."),
              ""]
    for d in docs:
        lineas.extend(_linea_resultado(d))
    lineas.append("Para el texto completo de una tesis: ver_tesis(registro_digital).")
    return "\n".join(lineas)


@mcp.tool()
async def investigar_criterio(
    tema: str,
    epocas: list[str] | None = None,
    limite: int = 12,
    incluir_texto: bool = False,
) -> str:
    """Investigación jerárquica en el SJF: consulta el corpus POR ETAPAS siguiendo el
    orden de obligatoriedad y se detiene al reunir suficiente criterio vinculante.

    ÚSALA cuando el usuario quiera saber "qué dice la Corte" sobre un tema, buscar el
    criterio más vinculante o preparar un escrito: da menos ruido que buscar_tesis
    porque prioriza lo que realmente obliga.

    Args:
        tema: Tema o expresión a investigar, p. ej. "interés legítimo ambiental".
        epocas: Filtro opcional de épocas ("5a" a "12a"). Sin filtro, todas.
        limite: Máximo de criterios a devolver.
        incluir_texto: Si True, agrega un extracto del texto de cada criterio.

    Returns:
        Criterios ordenados por obligatoriedad, cada uno con su link directo.
    """
    etapas = [
        ("Jurisprudencia del Pleno de la SCJN", 0, True),
        ("Jurisprudencia de la Primera Sala", 1, True),
        ("Jurisprudencia de la Segunda Sala", 2, True),
        ("Jurisprudencia de Plenos Regionales", 6, True),
        ("Jurisprudencia de Tribunales Colegiados", 8, True),
        ("Tesis aisladas (cualquier órgano)", None, False),
    ]

    body = _search_body(tema, epocas, False)
    data = await _sjf_fetch("/tesis?page=0&size=50", method="POST", json_body=body) or {}
    docs = data.get("documents") or []
    total = data.get("total", 0)
    if not docs:
        return (f"Sin criterios del SJF sobre '{tema}' (total en el sistema: {total}). "
                f"Prueba una expresión más general.")

    recolectados: list[dict] = []
    vistos: set = set()
    reporte: list[str] = []

    for nombre, rango, solo_j in etapas:
        if len(recolectados) >= limite:
            reporte.append(f"  (etapa '{nombre}' no fue necesaria: ya había criterio suficiente)")
            continue
        lote = []
        for d in docs:
            if d.get("ius") in vistos:
                continue
            loc = _strip_html(d.get("localizacion") or "")
            es_j = "[J]" in loc or d.get("ta_tj") == 1
            if solo_j and not es_j:
                continue
            if rango is not None and _rango_organo(loc) != rango:
                continue
            lote.append(d)
        for d in lote:
            vistos.add(d.get("ius"))
        recolectados.extend(lote)
        reporte.append(f"  {nombre}: {len(lote)} criterio(s)")

    seleccion = _ordenar_por_jerarquia(recolectados, tema)[:limite]
    lineas = [
        f"INVESTIGACIÓN — SJF: '{tema}'",
        f"{total} resultados en el sistema · {len(docs)} revisados · "
        f"se presentan los {len(seleccion)} más vinculantes.",
        "Etapas recorridas (de mayor a menor obligatoriedad):",
        *reporte,
        "",
    ]
    for d in seleccion:
        lineas.extend(_linea_resultado(d))
        if incluir_texto:
            txt = _strip_html(d.get("texto") or "")
            if txt:
                lineas.insert(len(lineas) - 1, txt[:700] + ("…" if len(txt) > 700 else ""))
    lineas.append("Al citar cualquiera de estos criterios incluye SIEMPRE su link.")
    return "\n".join(lineas)


@mcp.tool()
async def ver_tesis(registro_digital: int) -> str:
    """Obtiene el texto íntegro de una tesis del SJF por su número de registro digital.

    Args:
        registro_digital: Número de registro digital (IUS), p. ej. 2032415.

    Returns:
        Rubro, localización, clave, materias, texto completo y precedentes.
    """
    # El API exige isSemanal=true para tesis del Semanario en curso y isSemanal=false
    # para las históricas; el valor equivocado devuelve 404. Se prueban ambos.
    d, fallos = None, []
    for semanal in ("true", "false"):
        try:
            d = await _sjf_fetch(f"/tesis/{registro_digital}?isSemanal={semanal}&hostName={HOST}")
            if d and d.get("rubro"):
                break
            d = None
        except Exception as e:
            fallos.append(f"isSemanal={semanal}: {e}")
    if not d:
        detalle = (" Detalle → " + " | ".join(fallos) + ".") if fallos else ""
        return (f"No se encontró la tesis con registro digital {registro_digital} "
                f"(se intentó como tesis del Semanario en curso y como histórica).{detalle} "
                f"Verifica el registro o consúltala en {HOST}/detalle/tesis/{registro_digital}")

    materias = d.get("materias")
    if isinstance(materias, list):
        materias = ", ".join(str(m) for m in materias)

    partes = [
        f"Registro digital: {d.get('ius')}",
        f"Rubro: {_strip_html(d.get('rubro'))}",
        f"Localización: {_strip_html(d.get('localizacion'))}",
        f"Instancia: {d.get('instancia') or ''} | Época: {d.get('epoca') or ''}",
        f"Tipo: {d.get('tipoTesis') or ''}" + (f" | Clave: {d.get('claveTesis')}" if d.get("claveTesis") else ""),
    ]
    if materias:
        partes.append(f"Materia(s): {_strip_html(str(materias))}")
    if d.get("fechaPublicacion"):
        partes.append(f"Publicación: {d['fechaPublicacion']}")
    partes += ["", "TEXTO:", _strip_html(d.get("texto"))]
    if d.get("precedentes"):
        partes += ["", "PRECEDENTES:", _strip_html(d.get("precedentes"))]
    if d.get("notasGenericas"):
        partes += ["", "NOTAS:", _strip_html(str(d.get("notasGenericas")))]
    partes += ["", f"Fuente: {HOST}/detalle/tesis/{d.get('ius')}"]
    return "\n".join(partes)


# ---- TFJA: Sistema General de Consulta de Tesis y Jurisprudencias ----

TFJA = "https://www.tfja.gob.mx"
TFJA_BUSQUEDA = f"{TFJA}/cesmdfa/sctj/sctj-busqueda/"
TFJA_RESULTADOS = f"{TFJA}/cesmdfa/sctj/busqueda-resultados/"
TFJA_DETALLE = f"{TFJA}/cesmdfa/sctj/detalle-tesis/"

# Épocas del TFJA: 1 (1937-1978) … 9 (2022 a la fecha)
TFJA_EPOCAS_ROMANOS = {"I": "1a", "II": "2a", "III": "3a", "IV": "4a", "V": "5a",
                       "VI": "6a", "VII": "7a", "VIII": "8a", "IX": "9a"}

TFJA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-MX,es;q=0.9",
    "Origin": TFJA,
    "Referer": TFJA_BUSQUEDA,
    "User-Agent": HEADERS["User-Agent"],
}


async def _tfja_post_directo(client: httpx.AsyncClient, url: str, data: list[tuple[str, str]]) -> str:
    from urllib.parse import urlencode
    r = await client.post(url, content=urlencode(data).encode(),
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    r.raise_for_status()
    return r.text


async def _tfja_post(client: httpx.AsyncClient, url: str, data: list[tuple[str, str]]) -> str:
    """Con caché y freno. El token CSRF cambia en cada sesión, así que se excluye
    de la clave: lo que identifica a la consulta son los campos del formulario."""
    sin_token = [(k, v) for k, v in data if k != "csrfmiddlewaretoken"]
    return await _traer(
        "tfja",
        lambda: _tfja_post_directo(client, url, data),
        clave=_clave("tfja", url, sorted(sin_token)),
        ttl=TTL["tfja"],
    )


async def _tfja_token(client: httpx.AsyncClient) -> str:
    """GET a la búsqueda; el cookie jar del cliente conserva cookies de todos los saltos."""
    r = await client.get(TFJA_BUSQUEDA)
    r.raise_for_status()
    m = re.search(r"name=[\"']csrfmiddlewaretoken[\"'][^>]*value=[\"']([^\"']+)[\"']", r.text)
    if not m:
        raise RuntimeError("No se encontró el token CSRF del TFJA")
    return m.group(1)


def _tfja_celdas(row_html: str) -> list[str]:
    return [re.sub(r"\s+", " ", _strip_html(c)).strip()
            for c in re.findall(r"<td[^>]*>([\s\S]*?)</td>", row_html, flags=re.I)]


@mcp.tool()
async def buscar_tesis_tfja(
    rubro: str = "",
    texto: str = "",
    materia: str = "",
    precedente: str = "",
    clave: str = "",
    referencia: str = "",
    epocas: list[str] | None = None,
    solo_jurisprudencia: bool = False,
    modo: str = "palabras",
    pagina: int = 1,
) -> str:
    """Busca tesis, precedentes y jurisprudencia del Tribunal Federal de Justicia
    Administrativa (TFJA). Materia fiscal y administrativa.

    Args:
        rubro: Palabras o frase a buscar en el rubro.
        texto: Palabras o frase a buscar en el texto del criterio.
        materia: Materia u ordenamiento, p. ej. 'LEY DEL IMPUESTO SOBRE LA RENTA'.
        precedente: Búsqueda dentro del precedente.
        clave: Clave de tesis, p. ej. 'IX-P-SS-522'.
        referencia: Búsqueda en la referencia de publicación (R.T.F.J.A.).
        epocas: Épocas del TFJA, "1a" a "9a" (9a = 2022 a la fecha). Sin filtro, todas.
        solo_jurisprudencia: Si True, solo jurisprudencias.
        modo: "palabras" o "frase". Default: palabras.
        pagina: Página de resultados (10 por página), desde 1.

    Returns:
        [RUBRO], [Órgano], [Jurisprudencia/Precedente], [Clave], [Época], [Identificador].
        Usa ver_tesis_tfja(identificador) para el texto íntegro.
    """
    if not any([rubro, texto, materia, precedente, clave, referencia]):
        return ("Indica al menos un criterio de búsqueda "
                "(rubro, texto, materia, precedente, clave o referencia).")

    eps = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
    if epocas:
        eps = [e.lower().rstrip("a").strip() for e in epocas]
        eps = [e for e in eps if re.fullmatch(r"[1-9]", e)] or ["9"]

    por_palabras = modo != "frase"
    data: list[tuple[str, str]] = []
    for e in eps:
        data.append(("checkSelectEg", e))
    if solo_jurisprudencia:
        data.append(("jurisprudencias", "jurisprudencias"))
    # Peculiaridad del TFJA: modo "palabras" en un campo VACÍO anula los resultados.
    data += [
        ("rubro", rubro),
        ("rubroRadioEg", "rubroPalabras" if (rubro and por_palabras) else "rubroFrase"),
        ("texto", texto),
        ("textoRadioEg", "textoPalabras" if (texto and por_palabras) else "textoFrase"),
        ("materia", materia), ("precedente", precedente), ("cve_tesis", clave),
        ("sala_pleno", ""), ("referencia", referencia),
    ]
    if pagina > 1:
        data += [("cbp", "current"), ("current", str(pagina)),
                 ("input-pagina-siguiente", str(pagina + 1)),
                 ("input-pagina-ultimo", "9999")]

    async with httpx.AsyncClient(headers=TFJA_HEADERS, timeout=30, follow_redirects=True) as client:
        token = await _tfja_token(client)
        html_resp = await _tfja_post(client, TFJA_RESULTADOS, [("csrfmiddlewaretoken", token)] + data)

    pag = re.search(r"(\d+)\s+de\s+(\d+)", _strip_html(html_resp))
    total_paginas = pag.group(2) if pag else "1"
    # El HTML del TFJA no incluye <tbody>; tomar la tabla completa como respaldo
    m = re.search(r"<tbody[^>]*>([\s\S]*?)</tbody>", html_resp, flags=re.I) \
        or re.search(r"<table[^>]*>([\s\S]*?)</table>", html_resp, flags=re.I)
    filas = []
    if m:
        for row in re.split(r"<tr[^>]*>", m.group(1), flags=re.I)[1:]:
            c = _tfja_celdas(row)
            if len(c) >= 6:
                filas.append(c)

    if not filas:
        return "Sin resultados en el TFJA con esos criterios."

    lineas = [
        f"TFJA — Página {pagina} de {total_paginas} "
        f"({len(filas)} resultados en esta página, 10 por página).",
        "Formato: [RUBRO], [Órgano], [Jurisprudencia/Precedente], [Clave], "
        "[Época], [No. de Registro (Identificador)]", "",
    ]
    for c in filas:
        # Columnas: Materia | Identificador | Clave | Rubro | Referencia | Sala/Pleno
        _, ident_raw, cve, rub, _, sala = c[:6]
        ident = (re.search(r"\d+", ident_raw) or [ident_raw]).group(0) \
            if re.search(r"\d+", ident_raw) else ident_raw
        if len(ident) % 2 == 0 and ident[: len(ident) // 2] == ident[len(ident) // 2:]:
            ident = ident[: len(ident) // 2]
        es_j = bool(re.search(r"-J-", cve, flags=re.I))
        rom = (re.match(r"^([IVX]+)-", cve) or [None, None])[1]
        epoca = f"{TFJA_EPOCAS_ROMANOS[rom]} Época TFJA" if rom in TFJA_EPOCAS_ROMANOS else ""
        lineas.append(f"[{rub}], [{sala}], [{'Jurisprudencia' if es_j else 'Precedente'}], "
                      f"[{cve}], [{epoca}], [{ident}]")
        lineas.append(f"Consulta oficial: {TFJA_BUSQUEDA} (buscar por clave {cve})")
        lineas.append("")
    lineas.append("Para el texto completo: ver_tesis_tfja(identificador). Al citar, incluye la "
                  "liga de consulta oficial (el TFJA no tiene URL directa por tesis; "
                  "se localiza con la clave).")
    return "\n".join(lineas)


@mcp.tool()
async def ver_tesis_tfja(identificador: int) -> str:
    """Obtiene el texto íntegro de una tesis o jurisprudencia del TFJA por su
    identificador (el número devuelto por buscar_tesis_tfja).

    Args:
        identificador: Identificador del TFJA, p. ej. 48235.

    Returns:
        Rubro, texto, precedente y referencia de publicación (R.T.F.J.A.).
    """
    data: list[tuple[str, str]] = []
    # El TFJA exige valores repetidos, no lista separada por comas
    for e in "123456789":
        data.append(("detalle_checkSelectEg", e))
    for k in ["detalle_materia", "detalle_cve_tesis", "detalle_rubro", "detalle_precedente",
              "detalle_referencia", "detalle_sala_pleno", "detalle_texto"]:
        data.append((k, ""))
    data += [("detalle_jurisprudencias", "None"),
             ("detalle_identificador", str(identificador)),
             ("rubroRadioEg", "rubroPalabras"), ("textoRadioEg", "textoFrase")]

    async with httpx.AsyncClient(headers=TFJA_HEADERS, timeout=30, follow_redirects=True) as client:
        token = await _tfja_token(client)
        html_resp = await _tfja_post(client, TFJA_DETALLE, [("csrfmiddlewaretoken", token)] + data)

    texto = re.sub(r"[ \t]+", " ", _strip_html(html_resp))
    texto = texto.replace("Se ha copiado correctamente el texto", "")
    texto = re.sub(r"\bCerrar\b", "", texto)
    ini = texto.find("TESIS SELECCIONADA, NIVEL DE DETALLE")
    if ini >= 0:
        texto = texto[ini + len("TESIS SELECCIONADA, NIVEL DE DETALLE"):]
    for marca in ["Centro de Estudios Superiores", "TFJA. 20", "Director General"]:
        fin = texto.find(marca)
        if fin > 0:
            texto = texto[:fin]
    # Recortar controles de interfaz al final del detalle
    for marca in ["Ver sentencia relacionada", "Ver acuerdo relacionado",
                  "Regresar al listado anterior", "Regresar al menú principal",
                  "Imprimir", "Copiar texto"]:
        fin = texto.find(marca)
        if fin > 200:
            texto = texto[:fin]
    texto = re.sub(r"\n{3,}", "\n\n", texto).strip()
    if not texto:
        return f"No se encontró la tesis del TFJA con identificador {identificador}."
    return (f"Registro (Identificador TFJA): {identificador}\n\n{texto}\n\n"
            f"Fuente: {TFJA_BUSQUEDA} (consulta por identificador {identificador})")


# ---- DOF: Diario Oficial de la Federación ----

import asyncio
import unicodedata
from datetime import date, datetime, timedelta

DOF = "https://www.dof.gob.mx"
SIDOF = "https://sidof.segob.gob.mx"  # espejo oficial SEGOB (TLS válido)
DOF_HEADERS = {
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-MX,es;q=0.9",
    "User-Agent": HEADERS["User-Agent"],
}
DOF_MAX_DIAS = 20
DOF_CONCURRENCIA = 6
DOF_PRESUPUESTO_S = 32


def _normalizar(s: str) -> str:
    t = _strip_html(str(s or ""))
    t = unicodedata.normalize("NFD", t)
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower()


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _parse_fecha_mx(s: str) -> date | None:
    m = re.search(r"(\d{2})[/\-](\d{2})[/\-](\d{4})", str(s))
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


async def _dof_indice_dia(client: httpx.AsyncClient, fecha: str, edicion: str = "MAT") -> list[dict]:
    """Índice de un día, con caché. La edición de hoy todavía puede crecer, así que
    se guarda una hora; la de días pasados ya quedó cerrada y dura un mes."""
    d0 = _parse_fecha_mx(fecha)
    ttl = TTL["dof_hoy"] if (d0 and d0 >= date.today()) else TTL["dof_pasado"]
    return await _traer(
        "dof",
        lambda: _dof_indice_dia_directo(client, fecha, edicion),
        clave=_clave("dof_indice", fecha, edicion),
        ttl=ttl,
    )


async def _dof_indice_dia_directo(client: httpx.AsyncClient, fecha: str, edicion: str = "MAT") -> list[dict]:
    """Cada nota aparece varias veces (título + iconos PDF/DOC); se conserva el
    texto más largo por código, que es el título real."""
    d = _parse_fecha_mx(fecha)
    if not d:
        raise RuntimeError(f"Fecha inválida '{fecha}': usa DD/MM/AAAA")
    edic = f"&edicion={edicion}" if edicion in ("VES", "EXT") else ""
    qs = f"index_113.php?year={d.year}&month={d.month:02d}&day={d.day:02d}{edic}"
    fecha_guiones = f"{d.day:02d}-{d.month:02d}-{d.year}"
    # El espejo de SEGOB va primero: dof.gob.mx tiene la cadena de certificados
    # incompleta y los clientes estrictos la rechazan.
    html_txt, via = "", ""
    errores = []
    for cand in (f"{SIDOF}/welcome/{fecha_guiones}", f"{DOF}/{qs}", f"https://dof.gob.mx/{qs}"):
        try:
            r = await client.get(cand)
            r.raise_for_status()
            if len(r.text) > 200:
                html_txt, via = r.text, cand
                break
        except Exception as e:
            errores.append(f"{cand.split('/')[2]}: {type(e).__name__}")
    if not html_txt:
        raise RuntimeError("no se pudo alcanzar el DOF por ninguna vía → " + "; ".join(errores))

    por_codigo: dict[str, dict] = {}

    def guardar(codigo: str, titulo_raw: str, fecha_nota: str) -> None:
        t = re.sub(r"^\d+\.-\s*", "", re.sub(r"\s+", " ", _strip_html(titulo_raw)).strip())
        prev = por_codigo.get(codigo)
        if prev is None or len(t) > len(prev["titulo"]):
            por_codigo[codigo] = {"codigo": codigo, "fecha": fecha_nota, "titulo": t}

    if "sidof" in via:
        # Las notas del día llevan class="sumario-nota"; los otros enlaces /notas/
        # son bloques fijos del portal (lo más consultado, etc.).
        pats = [
            r"""<a[^>]*class=["'][^"']*sumario-nota[^"']*["'][^>]*href=["'][^"']*/notas/(\d{6,8})[^"']*["'][^>]*>([\s\S]{0,500}?)</a>""",
            r"""<a[^>]*href=["'][^"']*/notas/(\d{6,8})[^"']*["'][^>]*class=["'][^"']*sumario-nota[^"']*["'][^>]*>([\s\S]{0,500}?)</a>""",
        ]
        for pat in pats:
            for m in re.finditer(pat, html_txt, flags=re.I):
                guardar(m.group(1), m.group(2), _ddmmyyyy(d))
    else:
        for m in re.finditer(
            r"""<a[^>]*href=["'][^"']*nota_detalle[^"']*codigo=(\d+)[^"']*["'][^>]*>([\s\S]{0,600}?)</a>""",
            html_txt, flags=re.I,
        ):
            fm = re.search(r"fecha=([\d/]+)", m.group(0))
            guardar(m.group(1), m.group(2), fm.group(1) if fm else _ddmmyyyy(d))
    return [n for n in por_codigo.values() if len(n["titulo"]) >= 8]


def _dof_dias_habiles(desde: str, hasta: str) -> list[str]:
    d_ini, d_fin = _parse_fecha_mx(desde), _parse_fecha_mx(hasta)
    if not d_ini or not d_fin:
        raise RuntimeError("Fechas inválidas: usa DD/MM/AAAA")
    if d_fin < d_ini:
        raise RuntimeError("El rango de fechas está invertido")
    dias, d = [], d_fin
    while d >= d_ini:
        if d.weekday() < 5:
            dias.append(_ddmmyyyy(d))
        d -= timedelta(days=1)
    return dias


async def _dof_descargar_rango(desde: str, hasta: str, edicion: str = "MAT") -> dict:
    """Descarga los índices del rango UNA sola vez, en lotes paralelos y con presupuesto."""
    dias = _dof_dias_habiles(desde, hasta)
    aviso = ""
    if len(dias) > DOF_MAX_DIAS:
        aviso = (f"El rango tenía {len(dias)} días hábiles; se consultaron los {DOF_MAX_DIAS} más "
                 f"recientes (tope para no sobrecargar el DOF ni exceder el tiempo de espera).")
        dias = dias[:DOF_MAX_DIAS]

    t0 = datetime.now()
    por_dia: list[dict] = []
    async with httpx.AsyncClient(headers=DOF_HEADERS, timeout=15, follow_redirects=True) as client:
        for i in range(0, len(dias), DOF_CONCURRENCIA):
            if (datetime.now() - t0).total_seconds() > DOF_PRESUPUESTO_S:
                aviso = (aviso + " " if aviso else "") + (
                    f"Se alcanzó el presupuesto de tiempo tras {len(por_dia)} día(s); "
                    f"acota el rango para una revisión completa.")
                break
            lote = dias[i:i + DOF_CONCURRENCIA]

            async def uno(f: str) -> dict:
                try:
                    return {"fecha": f, "notas": await _dof_indice_dia(client, f, edicion)}
                except Exception:
                    return {"fecha": f, "notas": []}

            por_dia.extend(await asyncio.gather(*[uno(f) for f in lote]))
    return {"por_dia": por_dia, "dias_consultados": len(por_dia), "aviso": aviso}


def _dof_casa_termino(texto_norm: str, termino: str) -> bool:
    """Coincidencia por inicio de palabra: 'convenio' casa con 'convenios',
    pero 'nada' NO casa dentro de 'denominadas'."""
    return re.search(rf"(?:^|[^a-z0-9áéíóúñ]){re.escape(termino)}", texto_norm, flags=re.I) is not None


def _dof_coincide(titulo: str, terminos: list[str], modo: str = "todas") -> bool:
    if not terminos:
        return True
    t = _normalizar(titulo)
    f = any if modo == "alguna" else all
    return f(_dof_casa_termino(t, x) for x in terminos)


def _dof_formatear(resultados: list[dict], encabezado: str, extra: str = "") -> str:
    if not resultados:
        return encabezado + "\nSin coincidencias." + (("\n" + extra) if extra else "")
    lineas = [encabezado,
              "Formato: [TÍTULO], [Fecha de publicación], [Código] + link directo "
              "(inclúyelo SIEMPRE al citar).", ""]
    for n in resultados:
        lineas.append(f"[{n['titulo']}], [{n['fecha']}], [{n['codigo']}]")
        lineas.append(f"{DOF}/nota_detalle.php?codigo={n['codigo']}&fecha={n['fecha']}")
        lineas.append("")
    if extra:
        lineas.append(extra)
    lineas.append("Para el texto íntegro: ver_nota_dof(codigo, fecha).")
    return "\n".join(lineas)


@mcp.tool()
async def buscar_dof(
    texto: str = "",
    desde: str | None = None,
    hasta: str | None = None,
    dias: int = 7,
    modo: str = "todas",
    edicion: str = "MAT",
    limite: int = 40,
) -> str:
    """Busca publicaciones del Diario Oficial de la Federación (DOF) por palabras del TÍTULO
    en un rango de fechas. Recorre el índice oficial (el DOF publica en días hábiles).

    Args:
        texto: Palabras a buscar en el título. Vacío = todas las publicaciones del rango.
        desde: Fecha inicial DD/MM/AAAA. Si se omite se usa 'dias'.
        hasta: Fecha final DD/MM/AAAA. Si se omite se usa 'dias'.
        dias: Días hacia atrás desde hoy si no das rango. Default 7.
        modo: "todas" (todas las palabras) o "alguna". Default todas.
        edicion: "MAT" (matutina, default), "VES" o "EXT".
        limite: Máximo de resultados. Default 40.

    Returns:
        [TÍTULO], [Fecha], [Código] + link directo. Al citar incluye SIEMPRE el link.
        Para vigilancia recurrente usa monitorear_dof.
    """
    if desde and hasta:
        d_ini, d_fin = desde, hasta
    else:
        hoy = date.today()
        d_fin = _ddmmyyyy(hoy)
        d_ini = _ddmmyyyy(hoy - timedelta(days=max(dias, 1) - 1))

    datos = await _dof_descargar_rango(d_ini, d_fin, edicion)
    terminos = [_normalizar(p) for p in texto.split()] if texto.strip() else []
    resultados, truncado = [], False
    for dia in datos["por_dia"]:
        for n in dia["notas"]:
            if _dof_coincide(n["titulo"], terminos, modo):
                resultados.append(n)
            if len(resultados) >= limite:
                truncado = True
                break
        if truncado:
            break

    total_notas = sum(len(d["notas"]) for d in datos["por_dia"])
    enc = (f"DOF — {len(resultados)} coincidencia(s)"
           + (f' para "{texto}" (modo: {"alguna palabra" if modo == "alguna" else "todas las palabras"})' if texto else "")
           + f" en {datos['dias_consultados']} día(s) hábiles del {d_ini} al {d_fin}, edición {edicion}"
           + f" ({total_notas} publicaciones revisadas)."
           + (f" Se alcanzó el límite de {limite}; sube 'limite' para ver más." if truncado else "")
           + ((" " + datos["aviso"]) if datos["aviso"] else ""))
    nota = ""
    if not resultados and total_notas:
        nota = ("Nota: la búsqueda es sobre el TÍTULO de la publicación. Los títulos del DOF no incluyen "
                "el nombre de la dependencia (p. ej. los avisos de Banxico se titulan 'Tipo de cambio para "
                "solventar obligaciones…', 'Tasas de interés interbancarias…'), así que conviene buscar por "
                "materia y no por emisor, o usar buscar_dof sin texto para ver el índice completo del día.")
    return _dof_formatear(resultados, enc, nota)


@mcp.tool()
async def ver_nota_dof(codigo: str, fecha: str) -> str:
    """Texto íntegro de una publicación del DOF por su código y fecha.

    Args:
        codigo: Código de la nota, p. ej. '5794664'.
        fecha: Fecha de publicación DD/MM/AAAA, p. ej. '24/07/2026'.

    Returns:
        Dependencia emisora, texto completo y link a la fuente oficial.
    """
    url = f"{DOF}/nota_detalle.php?codigo={codigo}&fecha={fecha}"
    html_txt = ""
    async with httpx.AsyncClient(headers=DOF_HEADERS, timeout=25, follow_redirects=True) as client:
        for cand in (f"{SIDOF}/notas/{codigo}", url):
            try:
                r = await client.get(cand)
                r.raise_for_status()
                if len(r.text) > 200:
                    html_txt = r.text
                    break
            except Exception:
                continue
    if not html_txt:
        return (f"No se pudo recuperar la nota {codigo}; consúltala directamente: {url}")

    texto = re.sub(r"[ \t]+", " ", _strip_html(html_txt))
    m = re.search(r"DOF:\s*\d{2}/\d{2}/\d{4}", texto)
    if m:
        texto = texto[m.start():]
    for marca in ["AVISO LEGAL", "Aviso legal", "Río Amazonas No. 62",
                  "SECRETARÍA DE GOBERNACIÓN", "Portal de Obligaciones"]:
        i = texto.find(marca)
        if i > 300:
            texto = texto[:i]
    texto = re.sub(r"\n{3,}", "\n\n", texto).strip()
    if len(texto) < 40:
        return (f"No se pudo recuperar el texto de la nota {codigo} del {fecha}. Verifica el código y la "
                f"fecha, o consúltala directamente: {url}")

    dependencia = ""
    sello = re.search(r"Al margen[\s\S]{0,200}?dice:\s*([\s\S]{0,160})", texto)
    if sello:
        dep = re.sub(r"Estados Unidos Mexicanos\.?-?\s*", "", sello.group(1), flags=re.I)
        dep = re.split(r"\s(?=ACUERDO|DECRETO|OFICIO|AVISO|RESOLUCI|CIRCULAR|REGLAS|NORMA|LINEAMIENTOS|CONVENIO)",
                       dep)[0]
        dependencia = re.sub(r"\s+", " ", dep.split("\n")[0]).strip()[:120]

    partes = [f"Nota del DOF — código {codigo}, publicada el {fecha}"]
    if dependencia:
        partes.append(f"Dependencia: {dependencia}")
    partes += ["", "TEXTO:", texto, "", f"Fuente oficial: {url}"]
    return "\n".join(partes)


@mcp.tool()
async def indicadores_dof() -> str:
    """Indicadores económicos oficiales del DOF: tipo de cambio (dólar), UDIS,
    TIIE (28, 91, 182 días y de fondeo), CCP y CPP. Útiles para actualizaciones,
    recargos y cálculos fiscales."""
    plano = ""
    async with httpx.AsyncClient(headers=DOF_HEADERS, timeout=20, follow_redirects=True) as client:
        for cand in (f"{SIDOF}/", f"{DOF}/"):
            try:
                r = await client.get(cand)
                r.raise_for_status()
                plano = re.sub(r"[ \t]+", " ", _strip_html(r.text))
                if plano:
                    break
            except Exception:
                continue
    if not plano:
        return f"No se pudieron obtener los indicadores; consúltalos en {SIDOF}/ o {DOF}/indicadores.php"

    f = re.search(r"Tipo de [Cc]ambio y [Tt]asas(?: de inter\u00e9s interbancarias)? al\s*([^,\n]{1,30}?)(?:\s*ver|\s*D\u00d3LAR|\s*DOLAR|$)",
                  plano, flags=re.I)
    fecha = f.group(1).strip() if f else ""
    nombres = ["DOLAR", "UDIS", "TIIE 28 DIAS", "TIIE 91 DIAS", "TIIE 182 DIAS",
               "TIIE DE FONDEO", "CCP", "CCP-UDIS", "CCP-DOLARES", "CPP"]
    import unicodedata as _ud
    sin_acentos = "".join(c for c in _ud.normalize("NFD", plano) if _ud.category(c) != "Mn")
    vals = []
    for n in nombres:
        pat = re.sub(r"[-\s]", r"[-\\s]", n)
        m = re.search(pat + r"\s*\|?\s*([\d,]+\.?\d*%?)", sin_acentos, flags=re.I)
        if m:
            vals.append(f"{n}: {m.group(1)}")
    if not vals:
        return f"No se pudieron extraer los indicadores; consúltalos directamente: {DOF}/indicadores.php"
    return "\n".join([f"Indicadores del DOF{' al ' + fecha if fecha else ''}:", "", *vals, "",
                      f"Fuente oficial: {DOF}/indicadores.php"])


@mcp.tool()
async def monitorear_dof(temas: list[str], dias: int = 1, edicion: str = "MAT", limite: int = 50) -> str:
    """Vigilancia del DOF: revisa las publicaciones recientes y reporta las que coinciden
    con una lista de temas. Diseñada para ejecutarse como TAREA PROGRAMADA diaria.

    Args:
        temas: Temas a vigilar, p. ej. ["protección de datos", "impuesto sobre la renta"].
        dias: Días hacia atrás a revisar. Default 1 (solo hoy).
        edicion: "MAT" (default), "VES" o "EXT".
        limite: Máximo de notas por tema. Default 50.
    """
    if not temas:
        return 'Indica al menos un tema, p. ej. temas: ["protección de datos", "outsourcing"].'

    hoy = date.today()
    d_fin = _ddmmyyyy(hoy)
    d_ini = _ddmmyyyy(hoy - timedelta(days=max(dias, 1) - 1))
    datos = await _dof_descargar_rango(d_ini, d_fin, edicion)

    por_tema = []
    for tema in temas:
        terminos = [_normalizar(p) for p in str(tema).split()]
        notas = []
        for dia in datos["por_dia"]:
            for n in dia["notas"]:
                if _dof_coincide(n["titulo"], terminos, "todas"):
                    notas.append(n)
                if len(notas) >= limite:
                    break
            if len(notas) >= limite:
                break
        por_tema.append({"tema": tema, "notas": notas})

    con_hallazgos = [t for t in por_tema if t["notas"]]
    cab = (f"MONITOREO DEL DOF — {d_ini} al {d_fin} "
           f"({datos['dias_consultados']} día(s) hábiles, edición {edicion})")
    if datos["aviso"]:
        cab += "\n" + datos["aviso"]
    if not con_hallazgos:
        return f"{cab}\n\nSin publicaciones relevantes para: {'; '.join(temas)}."

    lineas = [cab, "", f"Hallazgos en {len(con_hallazgos)} de {len(temas)} tema(s):", ""]
    for t in con_hallazgos:
        lineas.append(f"── TEMA: {t['tema']} ({len(t['notas'])})")
        for n in t["notas"]:
            lineas.append(f"[{n['titulo']}], [{n['fecha']}], [{n['codigo']}]")
            lineas.append(f"{DOF}/nota_detalle.php?codigo={n['codigo']}&fecha={n['fecha']}")
        lineas.append("")
    lineas.append("Al reportar al usuario, incluye SIEMPRE el link de cada nota.")
    return "\n".join(lineas)


# ---- Corte IDH: Buscador Jurídico de Derechos Humanos (BJDH, SCJN + Corte IDH) ----
#
# No hay API JSON: todo pasa por un único endpoint que devuelve un fragmento HTML.
# La unidad de resultado es el PÁRRAFO de sentencia, no la tesis.
#
#   POST /interamericano/busqueda  (application/x-www-form-urlencoded)
#     q, type, coleccionBJDH, s, page, navTaxonomia, fromTaxonomia, tipoLista
#
# Dos trampas del sitio: sirve ISO-8859-1 (no UTF-8) y está detrás de Imperva,
# así que hace falta un GET previo para levantar cookies antes del POST.

BJDH = "https://www.bjdh.org.mx"
BJDH_BUSQUEDA = f"{BJDH}/interamericano/busqueda"

BJDH_COLECCIONES = {"todos": "todos", "casos": "Caso CoIDH", "opiniones": "Opinion Consultiva"}

BJDH_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9",
    "Origin": BJDH,
    "Referer": BJDH_BUSQUEDA,
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": HEADERS["User-Agent"],
}

BJDH_NOMBRE_RANGO = ["Opinión Consultiva", "Fondo/Reparaciones", "Excepciones Preliminares",
                     "Otra resolución", "Supervisión de cumplimiento"]


async def _bjdh_post(client: httpx.AsyncClient, campos: dict) -> str:
    """POST al BJDH, con caché de una semana y freno de concurrencia. Este es el
    sitio más delicado del conjunto: está detrás de Imperva y abrir muchas
    conexiones a la vez desde una sola IP es lo que dispara un bloqueo."""
    return await _traer(
        "bjdh",
        lambda: _bjdh_post_directo(client, campos),
        clave=_clave("bjdh", campos),
        ttl=TTL["bjdh"],
    )


async def _bjdh_post_directo(client: httpx.AsyncClient, campos: dict) -> str:
    data = {
        "q": campos.get("q", ""), "or": "", "type": campos.get("type", ""),
        "page": str(campos.get("page", 1)),
        "fromTaxonomia": campos.get("fromTaxonomia", ""),
        "navTaxonomia": campos.get("navTaxonomia", ""),
        "tipoLista": campos.get("tipoLista", ""),
        "s": campos.get("s", "INITIAL_PAGINATED"),
        "coleccionBJDH": campos.get("coleccionBJDH", "todos"),
        "sortTaxo": "", "temasRelevantesInfo": campos.get("temasRelevantesInfo", ""),
    }
    r = await client.post(BJDH_BUSQUEDA, data=data)
    if r.status_code != 200:
        raise RuntimeError(f"el BJDH respondió {r.status_code}")
    # ISO-8859-1: decodificar como UTF-8 rompe todos los acentos de las citas
    charset = "iso-8859-1"
    m = re.search(r"charset=([\w-]+)", r.headers.get("content-type", ""), re.I)
    if m:
        charset = m.group(1)
    try:
        texto = r.content.decode(charset, errors="replace")
    except LookupError:
        texto = r.content.decode("iso-8859-1", errors="replace")
    if re.search(r"Incapsula|Request unsuccessful", texto, re.I) and "listaResultados" not in texto:
        raise RuntimeError("el WAF del BJDH (Imperva) bloqueó la petición; reintenta en unos segundos")
    return texto


def _bjdh_client() -> httpx.AsyncClient:
    """Cliente con cookie jar: el GET previo levanta las cookies de Imperva."""
    return httpx.AsyncClient(headers=BJDH_HEADERS, timeout=30, follow_redirects=True)


async def _bjdh_sesion(client: httpx.AsyncClient) -> list[str]:
    r = await client.get(BJDH_BUSQUEDA)
    if r.status_code != 200:
        raise RuntimeError(f"el BJDH respondió {r.status_code} al iniciar sesión")
    return list(client.cookies.keys())


def _bjdh_rango_resolucion(tipo: str) -> int:
    t = _normalizar_txt(tipo or "")
    if "opinion consultiva" in t:
        return 0
    if "fondo" in t or "reparacion" in t:
        return 1
    if re.search(r"excepcion(es)? preliminar", t):
        return 2
    if "interpretacion" in t:
        return 3
    if "supervision" in t or "medidas provisionales" in t:
        return 4
    return 3


def _bjdh_normalizar_cita(cita: str) -> str:
    """"Serie C No. 470., Párrafo 104" -> "Serie C No. 470, párr. 104." """
    cita = re.sub(r"\s*\.\s*,", ",", cita)
    cita = re.sub(r"\s+,", ",", cita)
    cita = re.sub(r",?\s*P[áa]rrafos?\s+([\d\s,y-]+?)\s*\.?\s*$",
                  lambda m: f", párr. {m.group(1).strip()}.", cita, flags=re.I)
    return re.sub(r"\s{2,}", " ", cita).strip()


def _bjdh_desarmar_cita(cita: str) -> dict:
    out: dict = {"caso": "", "estado": "", "tipo": "", "serie": "", "parrafo": "", "anio": 0}
    oc = re.search(r"\bOC-(\d+)/(\d{2})", cita, re.I)
    if oc:
        out["caso"] = f"Opinión Consultiva OC-{oc.group(1)}/{oc.group(2)}"
        out["tipo"] = "Opinión Consultiva"
        yy = int(oc.group(2))
        out["anio"] = 1900 + yy if yy >= 80 else 2000 + yy
    else:
        c = re.search(r"Caso\s+([\s\S]*?)\s+Vs\.\s+([^.]+)\.", cita, re.I)
        if c:
            out["caso"] = f"Caso {c.group(1).strip()}"
            out["estado"] = c.group(2).strip()
        t = re.search(r"Vs\.\s+[^.]+\.\s*([\s\S]*?)\.\s*(?:Sentencia|Resoluci[óo]n)", cita, re.I)
        if t:
            out["tipo"] = t.group(1).strip()
    f = re.search(r"de\s+(\d{1,2}\s+de\s+[a-záéíóú]+\s+de\s+(\d{4}))", cita, re.I)
    if f:
        out["anio"] = int(f.group(2))
    if not out["anio"]:
        a = re.search(r"\b(?:19|20)\d{2}\b", cita)
        if a:
            out["anio"] = int(a.group(0))
    s = re.search(r"Serie\s+([A-C])\s+No\.\s*(\d+)", cita, re.I)
    if s:
        out["serie"] = f"Serie {s.group(1)} No. {s.group(2)}"
    p = re.search(r"P[áa]rrafo\s+([\d\s,y-]+)", cita, re.I)
    if p:
        out["parrafo"] = p.group(1).strip().rstrip(".,")
    return out


def _bjdh_texto_parrafo(bloque: str, cita: str, numero: str) -> str:
    """La mayoría de las tarjetas traen el texto en un <p>, pero muchas lo dejan
    vacío y lo cuelgan de divs anidados, que una regex <div>…</div> no recorta.
    Para esos casos se cae a texto plano y se arranca en el número de párrafo."""
    sin_notas = re.sub(r"<font[^>]*>[\s\S]*?</font>", " ", bloque, flags=re.I)  # notas al pie
    texto = ""
    for m in re.finditer(r"<p[^>]*>([\s\S]*?)</p>", sin_notas, re.I):
        t = re.sub(r"[ \t]+", " ", _strip_html(m.group(1))).strip()
        if len(t) > len(texto):
            texto = t
    if len(texto) > 40:
        return re.sub(r"\s{2,}", " ", texto).strip()

    plano = re.sub(r"<script[\s\S]*?</script>", " ", sin_notas, flags=re.I)
    plano = re.sub(r"[ \t]+", " ", _strip_html(plano)).strip()
    if cita:
        plano = plano.replace(cita, " ")
    for marca in ("Mostrar párrafo", "Ocultar párrafo", "Ver documento",
                  "Ficha técnica", "Resumen oficial"):
        i = plano.find(marca)
        if i > 0:
            plano = plano[:i]
    plano = re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]", " ", plano)).strip()
    patron = (rf"(?:^|\s){re.escape(numero)}\.\s" if numero
              else r"(?:^|\s)\d{1,4}\.\s+(?=[A-ZÁÉÍÓÚ¿\"«(])")
    m = re.search(patron, plano)
    if m:
        plano = plano[m.start():].strip()
    return re.sub(r"\s{2,}", " ", plano).strip()


def _bjdh_parsear(html_frag: str) -> list[dict]:
    """Extrae los resultados anclando en div.title, el elemento estable de cada tarjeta."""
    items: list[dict] = []
    partes = re.split(r"<div[^>]*class=[\"'][^\"']*\btitle\b[^\"']*[\"'][^>]*>", html_frag, flags=re.I)
    for bloque in partes[1:]:
        m = re.match(r"([\s\S]*?)</div>", bloque, re.I)
        cita = re.sub(r"\s+", " ", _strip_html(m.group(1) if m else "")).strip()
        # Tres formatos: "Corte IDH. Caso…", "Caso …" sin prefijo, y "OC-29/22 …"
        if not cita or not (re.search(r"Corte IDH", cita, re.I)
                            or re.search(r"\bOC-\d+/\d+", cita, re.I)
                            or re.match(r"Caso\s", cita, re.I)):
            continue

        m_pais = re.search(r"doBusqueda\(\s*[\"']([^\"']+)[\"']\s*,\s*[\"']pais[\"']", bloque, re.I)
        pais = m_pais.group(1) if m_pais else ""
        anio_link = re.search(r"doBusqueda\(\s*[\"'](\d{4})[\"']\s*,\s*[\"']anio[\"']", bloque, re.I)
        # Hay nombres de archivo CON espacios: leer hasta la comilla, no hasta el espacio
        d = re.search(r"doc\?doc=([^\"']+)", bloque, re.I)
        fch = re.search(r"doc\?ficha=([^\"']+)", bloque, re.I)
        doc = d.group(1).split("#")[0].strip() if d else ""
        ficha = fch.group(1).split("#")[0].strip() if fch else ""

        meta = _bjdh_desarmar_cita(cita)
        if not meta["serie"] and doc:
            s = re.search(r"serie([abc])_(\d+)", doc, re.I)
            if s:
                meta["serie"] = f"Serie {s.group(1).upper()} No. {int(s.group(2))}"

        texto = _bjdh_texto_parrafo(bloque, cita, meta["parrafo"])
        if not meta["parrafo"] and texto:
            p = re.match(r"(\d{1,4})\s*\.", texto)
            if p:
                meta["parrafo"] = p.group(1)

        cita_final = cita
        if not re.match(r"Corte IDH", cita_final, re.I):
            cita_final = f"Corte IDH. {cita_final}"
        if meta["serie"] and not re.search(r"Serie\s+[A-C]", cita_final, re.I):
            cita_final += f". {meta['serie']}"
        if meta["parrafo"] and not re.search(r"P[áa]rrafo", cita_final, re.I):
            cita_final += f", Párrafo {meta['parrafo']}"
        cita_final = _bjdh_normalizar_cita(cita_final)

        items.append({
            "cita": cita_final, "texto": texto,
            "pais": meta["estado"] or pais,
            "anio": meta["anio"] or (int(anio_link.group(1)) if anio_link else 0),
            "tipo": meta["tipo"], "caso": meta["caso"],
            "serie": meta["serie"], "parrafo": meta["parrafo"],
            "url_doc": f"{BJDH}/interamericano/doc?doc={doc}" if doc else "",
            "url_ficha": f"{BJDH}/interamericano/doc?ficha={ficha}" if ficha else "",
        })
    return items


def _bjdh_totales(html_frag: str) -> dict:
    plano = re.sub(r"\s+", " ", _strip_html(html_frag))
    total = re.search(r"Total de resultados:\s*([\d,]+)", plano, re.I)
    pag = re.search(r"(\d+)\s+de\s+(\d+)", plano)
    return {"total": total.group(1) if total else None,
            "pagina": pag.group(1) if pag else "1",
            "total_paginas": pag.group(2) if pag else "1"}


def _bjdh_es_mexico(r: dict) -> bool:
    return bool(re.search(r"m[ée]xico", r.get("pais") or "", re.I)
                or re.search(r"Vs\.\s*M[ée]xico", r.get("cita") or "", re.I))


def _bjdh_ordenar(items: list[dict]) -> list[dict]:
    """Prelación: casos contra México (vinculantes directos vía art. 1º
    constitucional y expediente Varios 912/2010) → tipo de resolución → año."""
    def clave(par):
        i, r = par
        return (0 if _bjdh_es_mexico(r) else 1,
                _bjdh_rango_resolucion(r.get("tipo", "")),
                -(r.get("anio") or 0),
                r.get("serie") or "",   # mantiene juntos los párrafos de una misma resolución
                i)
    return [r for _, r in sorted(enumerate(items), key=clave)]


def _bjdh_formatear(items: list[dict], encabezado: str, incluir_texto: bool = True) -> str:
    """Formato de cita judicial: cita oficial terminada en "párr. N.", el texto
    íntegro entre comillas y los links al cierre. Los párrafos de una misma
    resolución se agrupan y el link aparece una sola vez."""
    lineas = [encabezado, ""]
    grupos: list[dict] = []
    for r in items:
        clave = f"{r.get('serie') or ''}|{r.get('caso') or r['cita'][:60]}"
        if grupos and grupos[-1]["clave"] == clave:
            grupos[-1]["items"].append(r)
        else:
            grupos.append({"clave": clave, "items": [r]})

    for g in grupos:
        primero = g["items"][0]
        cita = primero["cita"]
        lineas.append(cita if cita.endswith(".") else cita + ".")
        if incluir_texto and primero["texto"]:
            lineas.append(f'"{primero["texto"]}"')
        for r in g["items"][1:]:
            lineas.append(f"Párr. {r.get('parrafo') or '?'} (mismo caso).")
            if incluir_texto and r["texto"]:
                lineas.append(f'"{r["texto"]}"')
        if primero["url_doc"]:
            lineas.append(f"Sentencia: {primero['url_doc']}")
        if primero["url_ficha"]:
            lineas.append(f"Ficha técnica: {primero['url_ficha']}")
        etiquetas = []
        if _bjdh_es_mexico(primero):
            etiquetas.append("CASO CONTRA MÉXICO — vinculante directo")
        if primero.get("tipo"):
            etiquetas.append(BJDH_NOMBRE_RANGO[_bjdh_rango_resolucion(primero["tipo"])])
        if etiquetas:
            lineas.append(f"[{' · '.join(etiquetas)}]")
        lineas.append("")
    return "\n".join(lineas)


@mcp.tool()
async def buscar_corteidh(
    consulta: str,
    coleccion: str = "todos",
    pagina: int = 1,
    orden: str = "jerarquia",
) -> str:
    """Busca jurisprudencia de la Corte Interamericana de Derechos Humanos en el
    Buscador Jurídico de Derechos Humanos (BJDH, SCJN + Corte IDH).

    La unidad de resultado es el PÁRRAFO de sentencia u opinión consultiva, con su
    cita oficial completa y el link al documento. Al citar un criterio interamericano
    incluye SIEMPRE la cita completa y su link.

    Args:
        consulta: Expresión a buscar, p. ej. "desaparición forzada ius cogens".
        coleccion: "todos" (default), "casos" o "opiniones".
        pagina: Página de resultados, desde 1 (10 por página).
        orden: "jerarquia" (default) pone los casos contra México primero, luego
            Opinión Consultiva/Fondo, Excepciones Preliminares y año reciente.
            "cronologico" y "alfabetico" usan el orden nativo del buscador.

    Returns:
        Criterios en formato de cita judicial, con el texto del párrafo y su link.
    """
    if not consulta or not consulta.strip():
        return "Indica qué buscar en la jurisprudencia de la Corte IDH."
    s = {"alfabetico": "alfabetico", "cronologico": "anioDesc"}.get(orden, "INITIAL_PAGINATED")
    async with _bjdh_client() as client:
        await _bjdh_sesion(client)
        frag = await _bjdh_post(client, {
            "q": consulta, "page": pagina, "s": s,
            "coleccionBJDH": BJDH_COLECCIONES.get(coleccion, "todos"),
        })

    items = _bjdh_parsear(frag)
    if not items:
        return (f'Sin resultados en el BJDH (Corte IDH) para "{consulta}". Prueba términos '
                f'más generales, o explora los temas con explorar_corteidh(indice="temas_relevantes").')
    if orden == "jerarquia":
        items = _bjdh_ordenar(items)
    t = _bjdh_totales(frag)

    enc = "\n".join([
        f'Corte IDH (BJDH) — "{consulta}"' + (f" · solo {coleccion}" if coleccion != "todos" else ""),
        (f"Total de resultados: {t['total']} · página {pagina} de {t['total_paginas']} (10 por página)"
         if t["total"] else f"Página {pagina} · {len(items)} resultados"),
        ("Orden: casos contra México → Opinión Consultiva/Fondo → Excepciones Preliminares → año reciente."
         if orden == "jerarquia" else f"Orden nativo del buscador ({orden})."),
        "La unidad de resultado es el párrafo; la cita ya viene en formato oficial.",
    ])
    return _bjdh_formatear(items, enc) + (
        "\nUsa investigar_criterio_corteidh(tema) para el criterio más pertinente ya "
        "jerarquizado, o ver_caso_corteidh(caso) para la ficha completa de un caso.")


@mcp.tool()
async def explorar_corteidh(
    indice: str = "temas_relevantes",
    ruta_tema: str = "",
    nodo: str = "",
    pagina: int = 1,
) -> str:
    """Navega los índices temáticos del BJDH sin búsqueda de texto libre.

    ÚSALA para descubrir qué temas existen antes de buscar, o para traer todos los
    párrafos clasificados bajo un concepto doctrinal concreto (más preciso que la
    búsqueda libre).

    Args:
        indice: "temas_relevantes" (árbol de conceptos doctrinales), "articulo"
            (artículos de la Convención Americana), "caso", "pais" u "opinion_consultiva".
            Se ignora si se envía ruta_tema.
        ruta_tema: Ruta jerárquica exacta de un nodo, p. ej. "/Concepto Doctrinal 2.
            Desaparición forzada/Naturaleza y características de la desaparición
            forzada/Ius cogens". Devuelve los párrafos de ese concepto.
        nodo: Id del nodo que acompaña a ruta_tema (p. ej. "3lv144").
        pagina: Página de resultados, desde 1.

    Returns:
        El índice solicitado, o los párrafos clasificados bajo el nodo indicado.
    """
    async with _bjdh_client() as client:
        await _bjdh_sesion(client)

        if ruta_tema:
            frag = await _bjdh_post(client, {
                "q": ruta_tema, "type": "taxo_path", "page": pagina,
                "fromTaxonomia": "true", "navTaxonomia": nodo or "",
                "tipoLista": "IND", "temasRelevantesInfo": "temas_relevantes",
            })
            items = _bjdh_ordenar(_bjdh_parsear(frag))
            if not items:
                return (f'Sin párrafos bajo la ruta "{ruta_tema}". Verifica la ruta exacta '
                        f'con explorar_corteidh(indice="temas_relevantes").')
            t = _bjdh_totales(frag)
            return _bjdh_formatear(items, "\n".join([
                f"Corte IDH — Tema: {ruta_tema}",
                (f"Total de resultados: {t['total']} · página {pagina} de {t['total_paginas']}"
                 if t["total"] else f"Página {pagina}"),
                "Orden: casos contra México → Opinión Consultiva/Fondo → año reciente.",
            ]))

        frag = await _bjdh_post(client, {
            "q": "", "type": indice, "page": pagina,
            "temasRelevantesInfo": "temas_relevantes" if indice == "temas_relevantes" else "",
        })

    temas, vistos = [], set()
    for m in re.finditer(r"indice\(\s*'taxo_path'\s*,\s*'([^']+)'\s*,\s*\"[^\"]*\"\s*,\s*'([^']+)'", frag):
        if m.group(1) in vistos:
            continue
        vistos.add(m.group(1))
        temas.append({"ruta": m.group(1), "nodo": m.group(2)})

    if temas:
        lineas = [f"Corte IDH — Índice de Temas Relevantes ({len(temas)} nodos).",
                  "Usa explorar_corteidh(ruta_tema, nodo) para traer los párrafos de un concepto.", ""]
        grupos: dict = {}
        for t in temas:
            raiz = [p for p in t["ruta"].split("/") if p]
            raiz = raiz[0] if raiz else "(sin concepto)"
            grupos.setdefault(raiz, []).append(t)
        for raiz, hijos in grupos.items():
            lineas.append(f"## {raiz} ({len(hijos)} subtemas)")
            for h in hijos[:40]:
                sub = " › ".join([p for p in h["ruta"].split("/") if p][1:])
                lineas.append(f"  {sub}  [nodo {h['nodo']}]")
            if len(hijos) > 40:
                lineas.append(f"  … {len(hijos) - 40} subtemas más")
            lineas.append("")
        lineas.append("La ruta completa para explorar_corteidh es la del concepto doctrinal "
                      f"más el subtema, p. ej. '{temas[0]['ruta']}'.")
        return "\n".join(lineas)

    entradas, vistas = [], set()
    for m in re.finditer(r"doBusqueda\(\s*[\"']([^\"']+)[\"']\s*,\s*[\"']([a-z_]+)[\"']", frag, re.I):
        clave = f"{m.group(2)}::{m.group(1)}"
        if clave in vistas:
            continue
        vistas.add(clave)
        entradas.append(m.group(1))
    if not entradas:
        items = _bjdh_parsear(frag)
        if items:
            return _bjdh_formatear(_bjdh_ordenar(items), f"Corte IDH — Índice: {indice}")
        extracto = re.sub(r"\s+", " ", _strip_html(frag))[:200]
        return f'No se pudo leer el índice "{indice}" del BJDH. Extracto: "{extracto}"'
    return "\n".join([
        f"Corte IDH — Índice: {indice} ({len(entradas)} entradas).",
        "Usa buscar_corteidh(consulta) con el nombre de la entrada para ver sus párrafos.",
        "",
        *[f"- {e}" for e in entradas[:200]],
        *([f"… {len(entradas) - 200} entradas más"] if len(entradas) > 200 else []),
    ])


@mcp.tool()
async def ver_caso_corteidh(caso: str, limite: int = 5) -> str:
    """Ficha de un caso contencioso u opinión consultiva de la Corte IDH.

    Úsala cuando el usuario pregunte por un caso concreto ("Caso Radilla Pacheco",
    "Campo Algodonero", "OC-24/17") o necesite el documento completo tras una búsqueda.

    Args:
        caso: Nombre del caso u opinión consultiva.
        limite: Máximo de párrafos de muestra por resolución.

    Returns:
        Resoluciones localizadas con país, tipo, párrafos de muestra y links oficiales.
    """
    if not caso or not caso.strip():
        return "Indica el caso u opinión consultiva a consultar."
    async with _bjdh_client() as client:
        await _bjdh_sesion(client)
        frag = await _bjdh_post(client, {"q": caso, "page": 1, "s": "INITIAL_PAGINATED"})

    items = _bjdh_parsear(frag)
    if not items:
        return (f'No se localizó "{caso}" en el BJDH. Verifica el nombre; puedes listar los '
                f'casos con explorar_corteidh(indice="caso").')

    por_resolucion: dict = {}
    for r in items:
        clave = r.get("serie") or r.get("caso") or r["cita"][:80]
        por_resolucion.setdefault(clave, {"ref": r, "parrafos": []})["parrafos"].append(r)

    lineas = [f'Corte IDH — Resoluciones localizadas para "{caso}" ({len(por_resolucion)}).', ""]
    for _, grupo in por_resolucion.items():
        r = grupo["ref"]
        lineas.append(re.sub(r",?\s*p[áa]rr\.\s*[\d\s,y-]+\.?$", "", r["cita"], flags=re.I))
        if r["pais"]:
            marca = " — CASO CONTRA MÉXICO, vinculante directo" if _bjdh_es_mexico(r) else ""
            lineas.append(f"Estado demandado: {r['pais']}{marca}")
        if r["tipo"]:
            lineas.append(f"Tipo de resolución: {r['tipo']} "
                          f"({BJDH_NOMBRE_RANGO[_bjdh_rango_resolucion(r['tipo'])]})")
        lineas.append(f"Párrafos localizados en esta consulta: {len(grupo['parrafos'])}")
        lineas.append("")
        for p in grupo["parrafos"][:limite]:
            if not p["texto"]:
                continue
            lineas.append(f"Párr. {p.get('parrafo') or '?'}.")
            lineas.append(f'"{p["texto"]}"')
        if r["url_doc"]:
            lineas.append(f"Sentencia: {r['url_doc']}")
        if r["url_ficha"]:
            lineas.append(f"Ficha técnica: {r['url_ficha']}")
        lineas.append("")
    lineas.append("Para más párrafos usa buscar_corteidh con el nombre del caso y un tema concreto.")
    return "\n".join(lineas)


@mcp.tool()
async def investigar_criterio_corteidh(
    tema: str,
    limite: int = 8,
    incluir_texto: bool = True,
) -> str:
    """Investigación jerárquica del corpus interamericano: consulta el BJDH por etapas
    y devuelve el criterio más pertinente ya jerarquizado.

    Los casos contra México van primero porque vinculan de forma directa al Estado
    mexicano (art. 1º constitucional y expediente Varios 912/2010); después las
    opiniones consultivas y sentencias de fondo, y al final el resto.

    ÚSALA cuando el usuario quiera saber "qué ha dicho la Corte IDH" sobre un tema,
    fundar un control de convencionalidad o preparar un escrito.

    Args:
        tema: Tema a investigar, p. ej. "prisión preventiva oficiosa".
        limite: Máximo de criterios a devolver.
        incluir_texto: Si True (default), incluye el texto íntegro de cada párrafo.

    Returns:
        Criterios jerarquizados en formato de cita judicial, con su texto y link.
    """
    if not tema or not tema.strip():
        return "Indica el tema a investigar en el corpus interamericano."

    recolectados: list[dict] = []
    vistos: set = set()
    notas: list[str] = []
    etapas = [("todos", [1, 2, 3]), ("opiniones", [1])]

    async with _bjdh_client() as client:
        await _bjdh_sesion(client)
        for coleccion, paginas in etapas:
            for page in paginas:
                try:
                    frag = await _bjdh_post(client, {
                        "q": tema, "page": page, "s": "INITIAL_PAGINATED",
                        "coleccionBJDH": BJDH_COLECCIONES[coleccion],
                    })
                except Exception as e:
                    notas.append(f"(etapa {coleccion} p.{page}: {e})")
                    continue
                items = _bjdh_parsear(frag)
                if not items:
                    break
                for r in items:
                    clave = f"{r['serie']}|{r['parrafo']}|{r['cita'][:60]}"
                    if clave in vistos:
                        continue
                    vistos.add(clave)
                    recolectados.append(r)

    if not recolectados:
        return (f'Sin criterios de la Corte IDH sobre "{tema}". {" ".join(notas)}\n'
                f'Prueba explorar_corteidh(indice="temas_relevantes") para ubicar el '
                f'concepto doctrinal exacto.')

    ordenados = _bjdh_ordenar(recolectados)
    seleccion = ordenados[:limite]
    mexicanos = sum(1 for r in ordenados if _bjdh_es_mexico(r))

    enc = "\n".join([
        f'INVESTIGACIÓN — Corte IDH: "{tema}"',
        f"{len(recolectados)} párrafos revisados · {mexicanos} de casos contra México · "
        f"se presentan los {len(seleccion)} más pertinentes.",
        "Prelación aplicada: casos contra México (vinculantes directos vía art. 1º "
        "constitucional y expediente Varios 912/2010) → Opinión Consultiva/Fondo → "
        "Excepciones Preliminares → Supervisión de cumplimiento; a igualdad, año más reciente.",
    ])
    salida = _bjdh_formatear(seleccion, enc, incluir_texto)
    if notas:
        salida += f"\nIncidencias durante el barrido: {' '.join(notas)}"
    return salida + ("\nRecuerda: al citar un criterio interamericano incluye la cita "
                     "oficial completa y su link.")


# ---- Estado y auto-diagnóstico del conector ----

_ARRANQUE = datetime.now()


@mcp.tool()
async def estado_conector() -> str:
    """Comprobación instantánea de que el servidor del conector está vivo.

    NO hace peticiones de red, responde de inmediato. Úsala primero si otra tool se
    quedó colgada o dio timeout, para distinguir entre "el servidor está caído" y
    "el sitio de gobierno está lento".
    """
    s = int((datetime.now() - _ARRANQUE).total_seconds())
    ahora = _time.monotonic()
    recientes = sum(1 for t in _PETICIONES if ahora - t <= 60)
    return "\n".join([
        "KriteriusMX — servidor VIVO (respuesta sin red).",
        f"Versión 2.6.0 (remota) · encendido hace {s}s · 15 tools registradas.",
        f"Endpoint SJF en uso: {_SJF_BASE}",
        "",
        f"Caché: {_CACHE.resumen()}",
        f"Peticiones a fuentes en el último minuto: {recientes} de {LIMITE_PETICIONES_MINUTO}",
        f"Concurrencia máxima por fuente: " +
        ", ".join(f"{k} {v}" for k, v in CONCURRENCIA.items()),
        "",
        "Si una tool dio timeout pero esta responde, el problema es el sitio de origen "
        "(lento o caído), no el conector. Ejecuta diagnosticar_conector para saber "
        "qué fuente falla.",
    ])


@mcp.tool()
async def diagnosticar_conector() -> str:
    """Auto-diagnóstico del conector KriteriusMX: prueba las tools contra el SJF y el
    TFJA con casos de resultado conocido e intenta auto-descubrir endpoints si cambiaron.
    ÚSALA cuando las demás tools devuelvan errores o respuestas vacías inesperadas.

    Returns:
        Reporte por componente: qué funciona, qué se rompió y cómo repararlo.
    """
    lineas = ["DIAGNÓSTICO DEL CONECTOR KRITERIUSMX", ""]
    resultados = []

    async def check(nombre, fn):
        try:
            detalle = await fn()
            lineas.append(f"[OK]    {nombre}" + (f" — {detalle}" if detalle else ""))
            resultados.append(True)
        except Exception as e:
            lineas.append(f"[FALLA] {nombre} — {e}")
            resultados.append(False)

    async def _sjf_busqueda():
        body = _search_body("amparo", None, False)
        d = await _sjf_fetch("/tesis?page=0&size=1", method="POST", json_body=body)
        if not d or not isinstance(d.get("total"), int) or not (d.get("documents") or [{}])[0].get("ius"):
            claves = ",".join(list(d.keys())[:6]) if d else "vacío"
            raise RuntimeError(f"estructura inesperada (claves: {claves})")
        return f"{d['total']} resultados para 'amparo', endpoint {_SJF_BASE}"

    async def _sjf_detalle():
        d = await _sjf_fetch(f"/tesis/2012594?isSemanal=true&hostName={HOST}")
        if not d or not d.get("rubro") or "IGUALDAD" not in _strip_html(d["rubro"]).upper():
            raise RuntimeError("no regresó el rubro esperado")
        return "texto íntegro recuperado"

    async def _tfja_sesion():
        async with httpx.AsyncClient(headers=TFJA_HEADERS, timeout=30, follow_redirects=True) as client:
            await _tfja_token(client)
        return "token CSRF obtenido"

    async def _tfja_busqueda():
        r = await buscar_tesis_tfja(rubro="renta", epocas=["9a"])
        if not r.startswith("TFJA — Página"):
            raise RuntimeError(r[:200])
        return r.split("\n")[0]

    async def _tfja_detalle():
        r = await ver_tesis_tfja(48235)
        if "IX-P-SS-522" not in r:
            raise RuntimeError("no regresó la clave esperada: " + r[:150])
        return "texto íntegro recuperado"

    async def _dof_indice():
        async with httpx.AsyncClient(headers=DOF_HEADERS, timeout=15, follow_redirects=True) as client:
            for i in range(7):
                d = date.today() - timedelta(days=i)
                if d.weekday() >= 5:
                    continue
                notas = await _dof_indice_dia(client, _ddmmyyyy(d))
                if notas:
                    return f"{len(notas)} notas el {_ddmmyyyy(d)}"
        raise RuntimeError("ningún día con publicaciones en la última semana (revisa el parseo del índice)")

    async def _dof_ind():
        r = await indicadores_dof()
        if "DOLAR" not in r and "UDIS" not in r:
            raise RuntimeError("no se extrajeron indicadores")
        return " | ".join([l for l in r.split("\n") if ":" in l][:2])

    async def _bjdh_sesion_check():
        async with _bjdh_client() as client:
            cookies = await _bjdh_sesion(client)
        return f"cookies: {','.join(cookies) or 'ninguna'}"

    async def _bjdh_busqueda():
        async with _bjdh_client() as client:
            await _bjdh_sesion(client)
            frag = await _bjdh_post(client, {"q": "desaparicion forzada", "page": 1})
        items = _bjdh_parsear(frag)
        if not items:
            raise RuntimeError("el parser no extrajo resultados; cambió el HTML del BJDH")
        if "Corte IDH" not in items[0]["cita"]:
            raise RuntimeError("la cita no tiene el formato esperado")
        t = _bjdh_totales(frag)
        return f'{t["total"] or len(items)} resultados, primera cita: "{items[0]["cita"][:70]}…"'

    async def _bjdh_taxonomia():
        ruta = ("/Concepto Doctrinal 2. Desaparición forzada/Naturaleza y características "
                "de la desaparición forzada/Ius cogens")
        async with _bjdh_client() as client:
            await _bjdh_sesion(client)
            frag = await _bjdh_post(client, {
                "q": ruta, "type": "taxo_path", "page": 1, "fromTaxonomia": "true",
                "navTaxonomia": "3lv144", "tipoLista": "IND",
                "temasRelevantesInfo": "temas_relevantes",
            })
        items = _bjdh_parsear(frag)
        if not items:
            raise RuntimeError("sin párrafos bajo el nodo de prueba; cambió el árbol o el parser")
        return f"{len(items)} párrafos bajo 'Desaparición forzada › Ius cogens'"

    await check("SJF búsqueda (API)", _sjf_busqueda)
    await check("SJF detalle (tesis 2012594, P./J. 9/2016)", _sjf_detalle)
    await check("TFJA sesión y token CSRF", _tfja_sesion)
    await check("TFJA búsqueda (formulario)", _tfja_busqueda)
    await check("TFJA detalle (identificador 48235, IX-P-SS-522)", _tfja_detalle)
    await check("DOF índice por fecha", _dof_indice)
    await check("DOF indicadores (dólar, UDIS, TIIE)", _dof_ind)
    await check("Corte IDH sesión BJDH (cookies Imperva)", _bjdh_sesion_check)
    await check("Corte IDH búsqueda ('desaparición forzada')", _bjdh_busqueda)
    await check("Corte IDH navegación temática (taxo_path)", _bjdh_taxonomia)

    lineas.append("")
    if all(resultados):
        lineas.append("Veredicto: todas las tools operan con normalidad (SJF, TFJA, DOF y Corte IDH).")
    else:
        lineas.append("Veredicto: hay fallas. Guía de acción:")
        lineas.append("- Fallas de red o status 5xx: probablemente temporal; reintentar más tarde.")
        lineas.append("- SJF con estructura inesperada o auto-descubrimiento fallido: el API cambió; "
                      "el conector necesita re-mapearse con el navegador en una sesión de Cowork.")
        lineas.append("- TFJA sin resultados o sin token: el sitio cambió su formulario; misma "
                      "solución apuntando a tfja.gob.mx/cesmdfa/sctj/sctj-busqueda.")
        lineas.append("- DOF sin notas o sin indicadores: cambió el HTML de dof.gob.mx "
                      "(índice index_113.php y nota_detalle.php); mismo remedio.")
        lineas.append("- Corte IDH bloqueado por Imperva: suele ser temporal (reintentar); si el "
                      "parser no extrae resultados, cambió el HTML del BJDH: mismo remedio "
                      "apuntando a bjdh.org.mx/interamericano/busqueda.")
    return "\n".join(lineas)


if __name__ == "__main__":
    mcp.run()
