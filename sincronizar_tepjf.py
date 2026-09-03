"""
Sincronizador del IUS Electoral del TEPJF: baja el corpus completo de jurisprudencia
y tesis electorales y lo deja en `kriterius_datos/tepjf.jsonl`, una línea por criterio.

Este script NO corre dentro del servidor. Lo ejecuta la Action semanal (o una
persona desde su máquina) y el resultado —el snapshot— es lo que viaja al
contenedor. El servidor nunca le pega al TEPJF en tiempo de consulta: por eso la
fuente responde en milisegundos y no depende de que el portal esté vivo.

Por qué un snapshot y no consulta en vivo: `GetJurisprudenciasTesis` ignora sus
parámetros `texto` y `sala` y devuelve SIEMPRE el corpus entero (11.7 MB entre las
dos colecciones). "Buscar en vivo" sería bajar eso en cada consulta.

Lo que hay que saber del API antes de tocar este archivo:

  1. `elecciones2021.te.gob.mx` está detrás de Radware Bot Manager. Un cliente HTTP
     pelón recibe un 302 a validate.perfdrive.com, o un JSON `{"_event_transid": …}`
     que parece una respuesta pero no lo es; `_bloqueado()` distingue las tres
     formas. Hacen falta dos cosas para que no bloquee: cabeceras de navegador
     completas (User-Agent, Origin, Referer y las Sec-Fetch-*) y, sobre todo,
     RITMO. Lo que lo enciende es la cadencia, no el volumen: ver `Fuente`.

  2. La vigencia no es un campo: viene como sufijo de la clave, y hay TRES motivos
     —Sentencia, Acuerdo General y Reiteración—, no dos. El motivo y el link al
     documento que derogó o reiteró el criterio están dentro de HTML en
     `urL_tesisjurRel`.

  3. `DetalleSentenciaHTML` exige DOS cosas a la vez: la `Clave` cruda (con su
     sufijo de vigencia; con la limpia responde 500) y un `url` que sea solo la ruta
     `tesisjur.aspx?…`. Si se le manda el `urL_tesisjurRel` completo de un no
     vigente —que trae la nota en HTML— el WAF contesta `{"_event_transid": …}`.
     Ver `_url_detalle`. Su respuesta pesa ~180 KB de los cuales 168 KB son un QR
     en base64 que aquí se tira.

  4. Las tesis se numeran con romanos (XVI/2026) y las jurisprudencias con arábigos
     (10/2026). El orden del archivo tiene que entender ambos o el diff semanal se
     llena de líneas que solo se movieron de lugar.

Uso:
    python sincronizar_tepjf.py --salida kriterius_datos/tepjf.jsonl --resumen /tmp/resumen.md
    python sincronizar_tepjf.py --sin-detalle          # solo los dos volcados
    python sincronizar_tepjf.py --cache .cache_tepjf   # reanudable: guarda detalles
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

API = "https://elecciones2021.te.gob.mx/APIIUS/api/"

# Cabeceras de navegador completas. No es cosmética: sin Origin, Referer y las
# Sec-Fetch-*, el bot manager contesta 302 a TODO. Con ellas, la mayoría pasa.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-MX,es;q=0.9",
    "Origin": "https://www.te.gob.mx",
    "Referer": "https://www.te.gob.mx/ius2021/",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Dest": "empty",
}

# Tope de seguridad: si el volcado trae menos de este porcentaje de lo que ya había
# en el snapshot anterior, no se escribe nada. Un JSON truncado con HTTP 200 no
# puede convertirse en un PR que borra el corpus.
UMBRAL_MERMA = 0.95

_RE_SUFIJO = re.compile(r"--\s*No Vigente por\s*(.+?)\s*$", re.I)
_RE_NOTA = re.compile(r"^#&(.*?)(?:<a\s+href=[\"']([^\"']*)[\"'])?[^<]*(?:</a>)?&", re.S | re.I)
_RE_EPOCA = re.compile(r"(\d+)\s*a\s+Época", re.I)
_RE_LOCALIZACION = re.compile(r"Localización:\s*(.+)$", re.S | re.I)
_RE_CLAVE_ANTERIOR = re.compile(r"Clave anterior:\s*([^,]*),", re.I)

_ROMANOS = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


# ---- Normalización de texto ----

def _strip_html(texto: str | None) -> str:
    """Igual que la del core, a propósito: el JSONL debe verse como el resto del
    conector. Se copia en vez de importarse para que este script no arrastre
    `kriterius_mx` (y con él `mcp` y `httpx` del servidor) a la Action."""
    if not texto:
        return ""
    texto = re.sub(r"<style[\s\S]*?</style>|<script[\s\S]*?</script>", " ", texto, flags=re.I)
    texto = re.sub(r"<br\s*/?>|</p>", "\n", texto, flags=re.I)
    texto = re.sub(r"<[^>]+>", "", texto)
    return html.unescape(texto).strip()


def _limpiar(texto: str | None) -> str:
    """Texto de párrafo: sin HTML, sin \\r, sin espacios repetidos y sin más de un
    renglón en blanco seguido."""
    t = _strip_html(texto).replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _una_linea(texto: str | None) -> str:
    return re.sub(r"\s+", " ", _strip_html(texto)).strip()


# ---- Claves y vigencia ----

def _clave_limpia(clave_cruda: str) -> str:
    """Quita el sufijo de vigencia y normaliza el año de dos dígitos.

    "47/2016--No Vigente por Sentencia" → "47/2016"
    "1/97"                              → "1/1997"
    """
    clave = _RE_SUFIJO.sub("", clave_cruda or "").strip()
    if "/" not in clave:
        return clave
    num, _, anio = clave.rpartition("/")
    anio = anio.strip()
    if len(anio) == 2 and anio.isdigit():
        anio = ("19" if int(anio) >= 90 else "20") + anio
    return f"{num.strip()}/{anio}"


def _vigencia(clave_cruda: str, url_rel: str | None) -> dict:
    """La vigencia va escondida en el sufijo de la clave; el motivo y el link al
    documento que derogó o reiteró el criterio, dentro de HTML en `urL_tesisjurRel`.

    Tres motivos observados: Sentencia, Acuerdo General y Reiteración. La regex
    acepta cualquiera para no perder uno nuevo: lo que no se puede perder es el
    hecho de que el criterio dejó de estar vigente.
    """
    m = _RE_SUFIJO.search(clave_cruda or "")
    if not m:
        return {"vigente": True, "motivo_no_vigente": None,
                "nota_vigencia": None, "url_vigencia": None}
    n = _RE_NOTA.search(url_rel or "")
    return {
        "vigente": False,
        "motivo_no_vigente": m.group(1).strip(),
        "nota_vigencia": _una_linea(n.group(1)) or None if n else None,
        "url_vigencia": (n.group(2) or "").strip() or None if n else None,
    }


def _numero(clave: str) -> int:
    """Número de la clave como entero, entienda romanos (tesis) o arábigos
    (jurisprudencias). Solo sirve para ordenar el archivo; si no se puede leer,
    devuelve 0 y el desempate lo hace la clave como texto."""
    crudo = (clave.split("/")[0] or "").strip().upper()
    if crudo.isdigit():
        return int(crudo)
    if not crudo or any(c not in _ROMANOS for c in crudo):
        return 0
    total = 0
    for i, c in enumerate(crudo):
        v = _ROMANOS[c]
        siguiente = _ROMANOS.get(crudo[i + 1]) if i + 1 < len(crudo) else None
        total += -v if siguiente and siguiente > v else v
    return total


def _orden(r: dict) -> tuple:
    """Orden estable del archivo: jurisprudencias antes que tesis, año descendente,
    número descendente. Determinista y sin depender del orden en que conteste el
    API, que es lo que hace que el `git diff` semanal sea legible."""
    return (0 if r["tipo"] == "J" else 1, -r["anio"], -_numero(r["clave"]), r["clave"])


# ---- Cliente HTTP con detección del bot manager ----

def _bloqueado(r: httpx.Response) -> bool:
    """Las caras del bot manager de Radware: un 302 a validate.perfdrive.com, un
    303 de su limitador de ritmo, el HTML con su reto de JavaScript, o un JSON que
    solo trae `_event_transid`. Ninguna es un error de red, así que sin esto el
    script las tomaría por respuestas buenas y escribiría un snapshot vacío."""
    if r.status_code in (301, 302, 303, 307):
        return True
    cuerpo = r.text.lstrip()
    if not cuerpo.startswith(("{", "[")):
        return True
    if cuerpo.startswith("{"):
        try:
            if list(json.loads(cuerpo).keys()) == ["_event_transid"]:
                return True
        except Exception:
            return True
    return False


class Fuente:
    """Un solo proceso hablándole al TEPJF una vez por semana. No hace falta el
    semáforo del servidor: hace falta ritmo.

    Lo que dispara al bot manager no es el volumen, es la cadencia. Medido: tres
    peticiones simultáneas sin pausa lo encienden en pocos minutos y a partir de
    ahí bloquea casi todo (una corrida completa acumuló 9 223 bloqueos y solo
    enriqueció 472 de 2 078 criterios); las mismas peticiones espaciadas pasan sin
    una sola falla. De ahí las dos piezas de esta clase:

      - una puerta que garantiza un intervalo mínimo ENTRE peticiones, compartida
        por todas las tareas;
      - un enfriamiento global cuando aparece un bloqueo: no reintenta solo quien
        se topó con él, se frena todo el proceso. Sin eso, las demás tareas siguen
        golpeando mientras una espera, y el bloqueo nunca se suelta.
    """

    def __init__(self, client: httpx.AsyncClient, intentos: int = 6,
                 pausa: float = 1.0, intervalo: float = 2.5):
        self.client = client
        self.intentos = intentos
        self.pausa = pausa
        self.intervalo = intervalo
        self.bloqueos = 0
        self._puerta = asyncio.Lock()
        self._proximo = 0.0

    async def _turno(self) -> None:
        """Espera a que toque. La espera se hace con el candado tomado a propósito:
        si no, todas las tareas calcularían el mismo instante y saldrían juntas."""
        async with self._puerta:
            ahora = asyncio.get_running_loop().time()
            if self._proximo > ahora:
                await asyncio.sleep(self._proximo - ahora)
            self._proximo = asyncio.get_running_loop().time() + self.intervalo

    def _enfriar(self, segundos: float) -> None:
        """Frena a TODO el proceso, no solo a quien se topó con el bloqueo."""
        self._proximo = max(self._proximo,
                            asyncio.get_running_loop().time() + segundos)

    async def traer(self, ruta: str, params: dict) -> object | None:
        """Devuelve el JSON, o None si se agotaron los intentos. Nunca lanza: un
        criterio que no se pudo enriquecer no debe tumbar la sincronización."""
        espera = self.pausa
        for intento in range(self.intentos):
            await self._turno()
            try:
                r = await self.client.get(API + ruta, params=params)
                if r.status_code == 500:
                    return None          # el API dice "no existe", no "no puedo"
                if not _bloqueado(r) and r.status_code == 200:
                    return r.json()
                self.bloqueos += 1
                self._enfriar(espera)
            except Exception:
                self._enfriar(espera)
            espera = min(espera * 2, 60)
        return None


# ---- Los tres pasos de la descarga ----

async def volcado(fuente: Fuente, tipo: str) -> list[dict]:
    """El corpus entero de una colección. tipo=1 jurisprudencias, tipo=2 tesis."""
    d = await fuente.traer("JurisprudenciaTesis/GetJurisprudenciasTesis",
                           {"texto": "", "tipo": tipo, "sala": ""})
    if not isinstance(d, list):
        raise RuntimeError(
            f"el volcado tipo={tipo} no devolvió una lista. El bot manager bloqueó "
            f"todos los intentos o el API cambió de forma.")
    return d


def normalizar(crudo: dict) -> dict:
    """Un registro del volcado → una línea del JSONL, sin los campos de detalle."""
    clave_cruda = (crudo.get("claveNueva") or "").strip()
    ayuda = crudo.get("textoAyuda") or ""
    epoca = _RE_EPOCA.search(ayuda)
    localizacion = _RE_LOCALIZACION.search(ayuda)
    anterior = _RE_CLAVE_ANTERIOR.search(ayuda)
    anio = re.sub(r"\D", "", str(crudo.get("anio") or ""))
    registro = {
        "clave": _clave_limpia(clave_cruda),
        "clave_cruda": clave_cruda,
        "clave_anterior": (anterior.group(1).strip() if anterior else "") or None,
        "id_tesis": str(crudo.get("idTesis") or "").strip(),
        "tipo": (crudo.get("clasificacion") or "").strip().upper()[:1] or "T",
        "sala": _una_linea(crudo.get("sala")),
        "anio": int(anio) if anio else 0,
        "epoca": f"{epoca.group(1)}a Época" if epoca else None,
        "rubro": _una_linea(crudo.get("rubro")),
        "texto": _limpiar(crudo.get("texto")),
        "localizacion": _una_linea(localizacion.group(1)) if localizacion else None,
        "url_relacionada": (crudo.get("urL_tesisjurRel") or "").strip() or None,
        # Los que llena el detalle. Se declaran aquí para que todas las líneas del
        # JSONL tengan las mismas claves, con detalle o sin él.
        "precedentes": [],
        "sentencias": [],
        "aprobacion": None,
        "publicacion": None,
        "notas": None,
        "articulos": [],
        "temas": [],
        "detalle_ok": False,
    }
    registro.update(_vigencia(clave_cruda, registro["url_relacionada"]))
    return registro


def _url_detalle(registro: dict) -> str:
    """El parámetro `url` que pide `DetalleSentenciaHTML`: la ruta a tesisjur.aspx y
    nada más.

    En los criterios no vigentes, `urL_tesisjurRel` trae ANTES de esa ruta la nota de
    vigencia en HTML, con su `<a href="…">`. Mandarla tal cual hace que el WAF
    conteste `{"_event_transid": …}` —el query string parece un intento de XSS— y el
    criterio se queda sin detalle para siempre, sin que nada diga por qué: la
    respuesta no es un error, es un JSON. Cortarla convierte esos fallos en 200.

    La `Clave`, en cambio, sí va cruda, con su sufijo `--No Vigente por …`: con la
    clave limpia el API responde 500. Las dos cosas juntas, y solo juntas, funcionan.
    """
    url = registro.get("url_relacionada") or ""
    i = url.find("tesisjur.aspx")
    if i >= 0:
        return url[i:]
    return f"tesisjur.aspx?idtesis={registro['clave']}&tpoBusqueda=S"


async def enriquecer(fuente: Fuente, registro: dict, cache: Path | None,
                     solo_cache: bool = False) -> None:
    """Detalle de un criterio: precedentes, aprobación, publicación, artículos.

    El volcado es obligatorio; esto es enriquecimiento. Si falla, el criterio entra
    igual con `detalle_ok: false` y el resumen del PR lo reporta: es preferible un
    corpus completo con algunos criterios sin precedentes que un corpus incompleto.

    Con `solo_cache`, lo que no esté ya bajado se deja sin detalle en vez de pedirlo:
    así se rearma el archivo tras una corrida interrumpida sin volver a cargarle la
    mano al portal.
    """
    archivo = cache / f"{registro['id_tesis'] or registro['clave'].replace('/', '-')}.json" if cache else None
    d = None
    if archivo is not None and archivo.exists():
        try:
            d = json.loads(archivo.read_text(encoding="utf-8"))
        except Exception:
            d = None
    if d is None and solo_cache:
        return
    if d is None:
        d = await fuente.traer(
            "JurisprudenciaTesis/DetalleSentenciaHTML",
            {"Clave": registro["clave_cruda"], "Idioma": "1",
             "url": _url_detalle(registro)})
        if isinstance(d, dict) and isinstance(d.get("sentencia"), dict):
            # El QR pesa 168 KB de los 180 KB de la respuesta y no se usa nunca.
            d["sentencia"].pop("qrCode", None)
            if archivo is not None:
                archivo.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    if not isinstance(d, dict) or not isinstance(d.get("sentencia"), dict):
        return
    s = d["sentencia"]
    registro["precedentes"] = [
        _limpiar(s.get(f"precedente{i}")) for i in range(1, 6)
        if _limpiar(s.get(f"precedente{i}"))
    ]
    # Los expedientes de los precedentes vienen con su link a la sentencia completa
    # en el sitio del TEPJF. Es el único link por documento que ofrece esta fuente,
    # así que se guarda: vale más que la cita a secas.
    vistos, sentencias = set(), []
    for x in s.get("toReplaceInPrecedents") or []:
        if not isinstance(x, dict):
            continue
        exp = _una_linea(x.get("word"))
        liga = re.search(r"href=['\"]([^'\"]+)['\"]", x.get("replace") or "")
        if exp and exp not in vistos:
            vistos.add(exp)
            sentencias.append({"expediente": exp, "url": liga.group(1) if liga else None})
    registro["sentencias"] = sentencias
    registro["aprobacion"] = _limpiar(s.get("obliga")) or None
    registro["publicacion"] = _limpiar(s.get("publica")) or None
    registro["notas"] = _limpiar(s.get("notas")) or None
    if not registro["epoca"] and _una_linea(s.get("desepoca")):
        registro["epoca"] = _una_linea(s.get("desepoca"))
    articulos, vistos = [], set()
    for a in d.get("descripcionArticulos") or []:
        if not isinstance(a, dict):
            continue
        par = (_una_linea(a.get("desLey")), _una_linea(a.get("desHipervinculo")))
        if any(par) and par not in vistos:
            vistos.add(par)
            articulos.append({"ley": par[0] or None, "articulo": par[1] or None})
    registro["articulos"] = articulos
    registro["detalle_ok"] = True


async def temas(fuente: Fuente) -> dict[str, list[str]]:
    """Los 15 temas del carrusel, invertidos: clave del criterio → temas.

    Es el punto de entrada para quien no sabe qué palabras buscar, y son 16
    peticiones en total: barato."""
    carrusel = await fuente.traer("JurisprudenciaTesis/GetCarrusel", {})
    por_clave: dict[str, list[str]] = {}
    if not isinstance(carrusel, list):
        return por_clave
    for tema in carrusel:
        nombre = _una_linea(tema.get("nombre"))
        d = await fuente.traer("JurisprudenciaTesis/GetSentenciasxTema",
                               {"tema": str(tema.get("id") or "")})
        for c in d or []:
            if not isinstance(c, dict):
                continue
            clave = _clave_limpia((c.get("claveNueva") or "").strip())
            tipo = (c.get("clasificacion") or "T").strip().upper()[:1]
            por_clave.setdefault(f"{tipo}:{clave}", [])
            if nombre not in por_clave[f"{tipo}:{clave}"]:
                por_clave[f"{tipo}:{clave}"].append(nombre)
    return por_clave


# ---- Escritura y resumen ----

def escribir(registros: list[dict], ruta: Path) -> None:
    """Salida determinista: mismo corpus, mismo archivo byte a byte. Sin fecha de
    generación adentro (esa va en el commit), para que no haya diff sin cambios."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8") as f:
        for r in registros:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def leer(ruta: Path) -> list[dict]:
    if not ruta.exists():
        return []
    salida = []
    with ruta.open(encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if linea:
                salida.append(json.loads(linea))
    return salida


def resumen(anteriores: list[dict], nuevos: list[dict]) -> str:
    """El cuerpo del PR semanal. Lo que más importa leer son los cambios de
    vigencia: un criterio que dejó de estar vivo y su nota."""
    def indice(rs):
        return {f"{r['tipo']}:{r['clave']}": r for r in rs}

    antes, ahora = indice(anteriores), indice(nuevos)
    altas = [ahora[k] for k in ahora.keys() - antes.keys()]
    bajas = [antes[k] for k in antes.keys() - ahora.keys()]
    cambios_vigencia = [
        (antes[k], ahora[k]) for k in antes.keys() & ahora.keys()
        if antes[k]["vigente"] != ahora[k]["vigente"]
    ]
    modificados = [
        k for k in antes.keys() & ahora.keys()
        if (antes[k]["rubro"], antes[k]["texto"]) != (ahora[k]["rubro"], ahora[k]["texto"])
    ]
    sin_detalle = [r for r in nuevos if not r["detalle_ok"]]
    no_vigentes = [r for r in nuevos if not r["vigente"]]

    def linea(r):
        return f"`{r['tipo']} {r['clave']}` — {r['rubro'][:110]}"

    L = [f"# TEPJF — snapshot del {date.today().isoformat()}", ""]
    L.append(f"- **{len(nuevos)}** criterios "
             f"({sum(1 for r in nuevos if r['tipo'] == 'J')} jurisprudencias, "
             f"{sum(1 for r in nuevos if r['tipo'] == 'T')} tesis)")
    L.append(f"- **{len(no_vigentes)}** no vigentes")
    L.append(f"- **{len(sin_detalle)}** sin detalle (`detalle_ok: false`)")
    con_tema = sum(1 for r in nuevos if r.get("temas"))
    L.append(f"- **{con_tema}** con tema del carrusel"
             + ("  ⚠️ **ninguno: el carrusel no respondió**" if not con_tema else ""))
    L.append(f"- Snapshot anterior: {len(anteriores)} criterios")
    L.append("")
    if cambios_vigencia:
        L.append(f"## ⚠️ Cambios de vigencia ({len(cambios_vigencia)}) — revisar uno por uno")
        for viejo, nuevo in sorted(cambios_vigencia, key=lambda p: p[1]["clave"]):
            estado = "VIGENTE → NO VIGENTE" if viejo["vigente"] else "NO VIGENTE → VIGENTE"
            L.append(f"- **{estado}** {linea(nuevo)}")
            if nuevo["nota_vigencia"]:
                L.append(f"  - {nuevo['nota_vigencia']}")
            if nuevo["url_vigencia"]:
                L.append(f"  - {nuevo['url_vigencia']}")
        L.append("")
    if altas:
        L.append(f"## Criterios nuevos ({len(altas)})")
        L += [f"- {linea(r)}" for r in sorted(altas, key=_orden)]
        L.append("")
    if bajas:
        L.append(f"## Criterios que desaparecieron del IUS ({len(bajas)})")
        L += [f"- {linea(r)}" for r in sorted(bajas, key=_orden)]
        L.append("")
    if modificados:
        L.append(f"## Rubro o texto modificado ({len(modificados)})")
        L += [f"- {linea(ahora[k])}" for k in sorted(modificados)]
        L.append("")
    if sin_detalle:
        L.append(f"<details><summary>Sin detalle ({len(sin_detalle)})</summary>")
        L.append("")
        L += [f"- {linea(r)}" for r in sorted(sin_detalle, key=_orden)[:60]]
        L.append("")
        L.append("</details>")
    return "\n".join(L) + "\n"


# ---- Orquestación ----

async def sincronizar(args) -> int:
    salida = Path(args.salida)
    anteriores = leer(salida)
    cache = Path(args.cache) if args.cache else None
    if cache:
        cache.mkdir(parents=True, exist_ok=True)

    limites = httpx.Limits(max_connections=args.concurrencia)
    async with httpx.AsyncClient(headers=HEADERS, timeout=180, limits=limites) as client:
        fuente = Fuente(client, intentos=args.intentos, intervalo=args.intervalo)

        crudos = []
        for tipo in ("1", "2"):
            lote = await volcado(fuente, tipo)
            print(f"volcado tipo={tipo}: {len(lote)} registros", file=sys.stderr)
            crudos += lote

        registros = [normalizar(c) for c in crudos]
        # Duplicados: el API ha llegado a repetir un criterio entre colecciones.
        # Se queda el primero; la clave+tipo es la identidad en todo el conector.
        unicos, vistos = [], set()
        for r in registros:
            llave = f"{r['tipo']}:{r['clave']}"
            if llave not in vistos:
                vistos.add(llave)
                unicos.append(r)
        registros = unicos

        if anteriores and len(registros) < len(anteriores) * UMBRAL_MERMA:
            print(f"ABORTA: el volcado trae {len(registros)} criterios contra los "
                  f"{len(anteriores)} del snapshot anterior (menos del "
                  f"{int(UMBRAL_MERMA * 100)} %). No se escribe nada.", file=sys.stderr)
            return 2

        if not args.sin_detalle:
            pendientes = registros[:args.limite] if args.limite else registros
            semaforo = asyncio.Semaphore(args.concurrencia)
            hechos = 0

            async def uno(r):
                nonlocal hechos
                async with semaforo:
                    await enriquecer(fuente, r, cache, args.solo_cache)
                hechos += 1
                if hechos % 100 == 0:
                    print(f"detalle {hechos}/{len(pendientes)} "
                          f"(bloqueos reintentados: {fuente.bloqueos})", file=sys.stderr)

            await asyncio.gather(*(uno(r) for r in pendientes))

        # Los temas se rearman en cada corrida, pero si el carrusel no respondió no
        # se pueden dar por vacíos: eso borraría los temas de los 2 078 criterios por
        # una sola petición bloqueada. (Pasó: una corrida entera se fue con
        # `temas: []` en todo el archivo.) Sin carrusel, se conservan los del
        # snapshot anterior.
        mapa_temas = await temas(fuente)
        if mapa_temas:
            for r in registros:
                r["temas"] = mapa_temas.get(f"{r['tipo']}:{r['clave']}", [])
        else:
            print("AVISO: el carrusel de temas no respondió; se conservan los del "
                  "snapshot anterior", file=sys.stderr)
            previos = {f"{r['tipo']}:{r['clave']}": r.get("temas") or []
                       for r in anteriores}
            for r in registros:
                r["temas"] = previos.get(f"{r['tipo']}:{r['clave']}", [])

    registros.sort(key=_orden)
    escribir(registros, salida)

    meta = {
        "fecha_snapshot": date.today().isoformat(),
        "generado": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "criterios": len(registros),
        "jurisprudencias": sum(1 for r in registros if r["tipo"] == "J"),
        "tesis": sum(1 for r in registros if r["tipo"] == "T"),
        "no_vigentes": sum(1 for r in registros if not r["vigente"]),
        "sin_detalle": sum(1 for r in registros if not r["detalle_ok"]),
        "fuente": API,
    }
    Path(args.meta).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")

    texto = resumen(anteriores, registros)
    if args.resumen:
        Path(args.resumen).write_text(texto, encoding="utf-8")
    print(texto, file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Sincroniza el snapshot del IUS Electoral (TEPJF)")
    p.add_argument("--salida", default="kriterius_datos/tepjf.jsonl")
    p.add_argument("--meta", default="kriterius_datos/tepjf.meta.json")
    p.add_argument("--resumen", default="")
    p.add_argument("--cache", default="",
                   help="carpeta donde guardar los detalles ya bajados, para reanudar")
    p.add_argument("--solo-cache", action="store_true",
                   help="arma el snapshot con los detalles YA bajados a --cache, sin "
                        "pedirle uno solo más al TEPJF. Para rehacer el archivo tras "
                        "una corrida interrumpida sin volver a molestar al portal.")
    p.add_argument("--sin-detalle", action="store_true",
                   help="solo los volcados y los temas: 17 peticiones en vez de ~2 100")
    p.add_argument("--limite", type=int, default=0, help="enriquecer solo los primeros N (pruebas)")
    p.add_argument("--concurrencia", type=int, default=2)
    p.add_argument("--intervalo", type=float, default=2.5,
                   help="segundos mínimos entre peticiones. Medido: a 1.2 s el bot "
                        "manager bloquea ~40 %% de los detalles; a 2.5-3 s pasa todo. "
                        "Bajarlo hace la corrida más lenta, no más rápida.")
    p.add_argument("--intentos", type=int, default=8)
    args = p.parse_args()
    return asyncio.run(sincronizar(args))


if __name__ == "__main__":
    raise SystemExit(main())
