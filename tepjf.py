"""
TEPJF — jurisprudencia y tesis del Tribunal Electoral del Poder Judicial de la
Federación (IUS Electoral), servidas desde un snapshot local.

Este módulo NO toca la red. Lee `kriterius_datos/tepjf.jsonl` —el corpus completo, una línea
por criterio, versionado en el repo y actualizado cada semana por
`sincronizar_tepjf.py`— y lo indexa en SQLite FTS5 en memoria al arrancar.

Por qué así, y no consultando al TEPJF en cada búsqueda:

  - El API del IUS ignora su parámetro `texto` y devuelve siempre el corpus entero
    (11.7 MB). "Buscar en vivo" sería bajar eso en cada consulta.
  - El host está detrás de Radware Bot Manager. Un servidor que le pegara en cada
    consulta se ganaría el bloqueo por IP en un día de uso normal.
  - Con el snapshot en el repo, la fuente responde en milisegundos, arranca aunque
    el TEPJF esté caído, se prueba sin internet y cada cambio de vigencia queda en
    el `git diff` del PR semanal.

FTS5 hace el trabajo que si no habría que programar a mano: tokeniza, ignora
acentos (`remove_diacritics 2`, así "genero" encuentra "GÉNERO"), entiende frases
entre comillas y AND/OR/NOT, y ordena por BM25 pesando el rubro diez veces más que
el texto, que es justo la regla de prelación del proyecto.

Si el archivo no existe o está corrupto, `cargar()` no lanza: deja la fuente
apagada y las tools responden que no está disponible. Un JSONL roto no puede
tumbar al SJF, al DOF ni a la Corte IDH.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

RUTA_DATOS = Path(__file__).parent / "kriterius_datos" / "tepjf.jsonl"
RUTA_META = Path(__file__).parent / "kriterius_datos" / "tepjf.meta.json"

IUS_URL = "https://www.te.gob.mx/ius2021/#/"
TRIBUNAL = "Tribunal Electoral del Poder Judicial de la Federación"

# Cuántos resultados se toman por relevancia BM25 antes de aplicar la prelación.
# Aplicar las reglas fijas sobre TODO el conjunto relevante enterraría una tesis
# con la palabra exacta en el rubro debajo de veinte jurisprudencias que la
# mencionan de paso; aplicarlas sobre los más relevantes, no.
TOPE_RELEVANCIA = 60

NO_DISPONIBLE = (
    "La fuente TEPJF no está disponible en este despliegue: falta el snapshot "
    "kriterius_datos/tepjf.jsonl o no se pudo leer. Las demás fuentes (SJF, TFJA, DOF, "
    "Corte IDH) funcionan con normalidad. Ejecuta diagnosticar_conector para ver "
    "el detalle."
)

_db: sqlite3.Connection | None = None
_por_clave: dict[str, dict] = {}
_meta: dict = {}
_candado = threading.Lock()
_candado_carga = threading.Lock()
_INTENTADO = False


# ---- Carga e índice ----

def cargar(ruta: Path | str = RUTA_DATOS, ruta_meta: Path | str = RUTA_META) -> int:
    """Carga el snapshot en una base FTS5 en memoria. Idempotente: llamarla dos
    veces reconstruye el índice desde cero. Devuelve cuántos criterios cargó, o 0
    si el archivo no está: nunca lanza."""
    global _db, _por_clave, _meta
    ruta = Path(ruta)
    try:
        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.execute("""
            CREATE VIRTUAL TABLE criterios USING fts5(
                rubro, texto, precedentes,
                clave UNINDEXED, tipo UNINDEXED, vigente UNINDEXED, anio UNINDEXED,
                tokenize = 'unicode61 remove_diacritics 2'
            )""")
        por_clave: dict[str, dict] = {}
        filas = []
        with ruta.open(encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                r = json.loads(linea)
                por_clave[llave(r["clave"], r["tipo"])] = r
                filas.append((
                    r.get("rubro") or "", r.get("texto") or "",
                    " ".join(r.get("precedentes") or []),
                    r["clave"], r["tipo"], int(bool(r.get("vigente"))),
                    int(r.get("anio") or 0),
                ))
        db.executemany("INSERT INTO criterios VALUES (?,?,?,?,?,?,?)", filas)
        db.commit()
    except Exception:
        with _candado:
            _db, _por_clave, _meta = None, {}, {}
        return 0

    meta = {}
    try:
        meta = json.loads(Path(ruta_meta).read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    with _candado:
        _db, _por_clave, _meta = db, por_clave, meta
    return len(por_clave)


def asegurar() -> int:
    """Carga el snapshot la primera vez que alguien lo necesita y no más.

    El servidor lo carga al arrancar (server_http.py), pero el core también se usa
    suelto —la app web lo importa como paquete, y `python kriterius_mx.py` lo corre
    en local—, así que las tools no pueden dar por hecho que alguien ya llamó a
    `cargar`."""
    global _INTENTADO
    if _db is None and not _INTENTADO:
        with _candado_carga:
            if _db is None and not _INTENTADO:
                _INTENTADO = True
                cargar()
    return len(_por_clave)


def disponible() -> bool:
    return _db is not None and bool(_por_clave)


def llave(clave: str, tipo: str) -> str:
    return f"{(tipo or 'J').strip().upper()[:1]}:{(clave or '').strip()}"


def meta() -> dict:
    """Lo que se sabe del snapshot cargado: fecha, conteos y fuente."""
    d = dict(_meta)
    d.setdefault("criterios", len(_por_clave))
    d.setdefault("no_vigentes", sum(1 for r in _por_clave.values() if not r["vigente"]))
    return d


# ---- Búsqueda ----

def _entrecomillar(palabra: str) -> str:
    """Cada palabra del usuario va entre comillas. Sin esto, un guion, un asterisco
    o una palabra reservada de FTS5 rompen la sintaxis o —peor— cambian en silencio
    el significado de la consulta."""
    return '"' + palabra.replace('"', '""') + '"'


def _consulta_fts(texto: str, frase: str = "", excluir: str = "") -> str:
    partes = [_entrecomillar(p) for p in (texto or "").split()]
    if (frase or "").strip():
        partes.append(_entrecomillar(frase.strip()))
    for p in (excluir or "").split():
        partes.append("NOT " + _entrecomillar(p))
    # Si solo hubiera exclusiones no habría nada que buscar: FTS5 no acepta una
    # consulta que empiece con NOT.
    if not partes or partes[0].startswith("NOT "):
        return ""
    return " ".join(partes)


def buscar(texto: str, *, frase: str = "", excluir: str = "",
           solo_jurisprudencia: bool = False, incluir_no_vigentes: bool = True,
           anio_desde: int | None = None, anio_hasta: int | None = None,
           tema: str = "", tope: int = TOPE_RELEVANCIA) -> list[dict]:
    """Los criterios más relevantes, ya ordenados por la prelación del proyecto:
    vigentes antes que no vigentes, jurisprudencia antes que tesis, coincidencia en
    el rubro antes que solo en el texto, año más reciente primero y, al final,
    BM25."""
    if not disponible():
        return []
    consulta = _consulta_fts(texto, frase, excluir)
    if not consulta:
        return []

    sql = ("SELECT clave, tipo, bm25(criterios, 10.0, 1.0, 2.0) AS rank "
           "FROM criterios WHERE criterios MATCH ?")
    args: list = [consulta]
    if solo_jurisprudencia:
        sql += " AND tipo = 'J'"
    if not incluir_no_vigentes:
        sql += " AND vigente = 1"
    if anio_desde:
        sql += " AND anio >= ?"
        args.append(int(anio_desde))
    if anio_hasta:
        sql += " AND anio <= ?"
        args.append(int(anio_hasta))
    sql += " ORDER BY rank LIMIT ?"
    args.append(int(tope))

    solo_rubro = _consulta_fts(texto, frase)
    with _candado:
        try:
            filas = _db.execute(sql, args).fetchall()
            # Segunda consulta, restringida a la columna rubro, para saber cuáles
            # coinciden ahí y no solo en el cuerpo del criterio.
            en_rubro = {
                (c, t) for c, t in _db.execute(
                    "SELECT clave, tipo FROM criterios WHERE criterios MATCH ?",
                    [f"rubro : ({solo_rubro})"])
            } if solo_rubro else set()
        except sqlite3.OperationalError:
            # Consulta que FTS5 no supo leer pese al entrecomillado: sin resultados
            # es mejor respuesta que una traza.
            return []

    def prelacion(fila):
        clave, tipo, rank = fila
        r = _por_clave[llave(clave, tipo)]
        return (not r["vigente"], tipo != "J", (clave, tipo) not in en_rubro,
                -int(r.get("anio") or 0), rank)

    resultados = [_por_clave[llave(c, t)] for c, t, _ in sorted(filas, key=prelacion)]
    if tema.strip():
        objetivo = _sin_acentos(tema)
        resultados = [r for r in resultados
                      if any(objetivo in _sin_acentos(t) for t in r.get("temas") or [])]
    return resultados


def obtener(clave: str, tipo: str = "J") -> dict | None:
    """Un criterio por su clave. Si no está con el tipo pedido, se busca con el
    otro: nadie se sabe de memoria si 47/2016 es jurisprudencia o tesis."""
    clave = (clave or "").strip()
    r = _por_clave.get(llave(clave, tipo))
    if r is None:
        otro = "T" if (tipo or "J").upper().startswith("J") else "J"
        r = _por_clave.get(llave(clave, otro))
    return r


def lista_temas() -> list[tuple[str, int]]:
    """Los temas del IUS con su conteo, de mayor a menor."""
    cuenta: dict[str, int] = {}
    for r in _por_clave.values():
        for t in r.get("temas") or []:
            cuenta[t] = cuenta.get(t, 0) + 1
    return sorted(cuenta.items(), key=lambda p: (-p[1], p[0]))


def por_tema(tema: str) -> list[dict]:
    """Los criterios de un tema, con la misma prelación que la búsqueda."""
    objetivo = _sin_acentos(tema)
    hallados = [r for r in _por_clave.values()
                if any(objetivo in _sin_acentos(t) for t in r.get("temas") or [])]
    return sorted(hallados, key=lambda r: (not r["vigente"], r["tipo"] != "J",
                                           -int(r.get("anio") or 0), r["clave"]))


def _sin_acentos(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn")


# ---- Presentación ----

def _tipo_largo(r: dict) -> str:
    return "Jurisprudencia" if r["tipo"] == "J" else "Tesis"


def _sala(r: dict) -> str:
    """El IUS guarda la sala en mayúsculas ("SALA SUPERIOR"). En una cita se lee
    mejor con mayúsculas iniciales, que es como la escriben los tribunales."""
    s = (r.get("sala") or "Sala Superior").strip()
    return s.title() if s.isupper() else s


def liga(r: dict) -> str:
    """El IUS Electoral no publica un permalink por criterio en su documentación,
    pero sus propias notas de vigencia enlazan así (`…/ius2021/#/7-2026`), que es
    lo más cercano a un link verificable. Si la aplicación abriera en su pantalla
    inicial, la clave que va junto al link es lo que hay que buscar ahí."""
    return IUS_URL + r["clave"].replace("/", "-")


def estado(r: dict) -> str:
    if r["vigente"]:
        return "VIGENTE"
    motivo = r.get("motivo_no_vigente")
    return f"NO VIGENTE por {motivo}" if motivo else "NO VIGENTE"


def cita(r: dict) -> str:
    """La cita completa, con los datos de identificación y el link, como pide la
    regla del proyecto."""
    partes = [r["rubro"], TRIBUNAL, _sala(r),
              f"{_tipo_largo(r)} {r['clave']}"]
    if r.get("epoca"):
        partes.append(r["epoca"])
    if r.get("localizacion"):
        partes.append(r["localizacion"])
    partes.append(estado(r))
    return ", ".join(p for p in partes if p) + f"\nFuente: {liga(r)} — clave {r['clave']}"


def linea(r: dict, n: int, extracto: str = "") -> list[str]:
    """Un resultado de búsqueda: rubro, identificación, localización y un extracto."""
    L = [f"{n}. {r['rubro']}",
         f"   TEPJF, {_sala(r)} · {_tipo_largo(r)} {r['clave']}"
         + (f" · {r['epoca']}" if r.get("epoca") else "")
         + f" · {estado(r)}"]
    if r.get("localizacion"):
        L.append(f"   {r['localizacion']}")
    if not r["vigente"] and r.get("nota_vigencia"):
        L.append(f"   {r['nota_vigencia']}")
    if not r["vigente"] and r.get("url_vigencia"):
        L.append(f"   Documento: {r['url_vigencia']}")
    cuerpo = extracto or (r.get("texto") or "").replace("\n", " ")
    if cuerpo:
        L.append(f"   «{cuerpo[:280].strip()}…»")
    L.append(f"   {liga(r)}")
    return L


def formatear_busqueda(resultados: list[dict], encabezado: str,
                       pagina: int = 1, por_pagina: int = 10) -> str:
    """Los resultados de una página, con los no vigentes en un bloque aparte al
    final: mezclarlos con los vigentes es cómo se acaba citando derecho muerto."""
    if not resultados:
        return (f"{encabezado}\nSin resultados. Prueba con menos palabras, con "
                f"sinónimos, o usa temas_tepjf para ver por dónde entrar.")
    total = len(resultados)
    paginas = max(1, (total + por_pagina - 1) // por_pagina)
    pagina = max(1, min(pagina, paginas))
    trozo = resultados[(pagina - 1) * por_pagina: pagina * por_pagina]
    vigentes = [r for r in trozo if r["vigente"]]
    caidos = [r for r in trozo if not r["vigente"]]

    L = [f"{encabezado} — {total} resultado(s), página {pagina} de {paginas}", ""]
    # La numeración sigue de página en página: en la 2 el primero es el 11, no el 1.
    # Así "el número 15" quiere decir lo mismo en toda la conversación.
    n = (pagina - 1) * por_pagina
    for r in vigentes:
        n += 1
        L += linea(r, n) + [""]
    if caidos:
        L.append(f"NO VIGENTES ({len(caidos)} en esta página) — se muestran para "
                 f"contexto histórico; no citar como derecho vigente")
        for r in caidos:
            n += 1
            L += linea(r, n) + [""]
    if paginas > 1 and pagina < paginas:
        L.append(f"Hay más: pide la página {pagina + 1}.")
    L.append("Fuente: IUS Electoral del TEPJF. Los resultados no sustituyen la "
             "consulta directa a la fuente.")
    return "\n".join(L)


def formatear_criterio(r: dict) -> str:
    """El criterio íntegro: texto completo, precedentes, aprobación, publicación,
    artículos citados y, si no está vigente, por qué dejó de estarlo."""
    L = [r["rubro"], ""]
    L.append(f"TEPJF, {_sala(r)} · {_tipo_largo(r)} {r['clave']}"
             + (f" · {r['epoca']}" if r.get("epoca") else ""))
    L.append(estado(r))
    if not r["vigente"]:
        if r.get("nota_vigencia"):
            L.append(r["nota_vigencia"])
        if r.get("url_vigencia"):
            L.append(f"Documento: {r['url_vigencia']}")
        # La clave cruda se muestra siempre en los no vigentes: es el dato con el
        # que el IUS los identifica y con el que se pueden verificar en la fuente.
        L.append(f"Clave en el IUS: {r['clave_cruda']}")
    if r.get("clave_anterior"):
        L.append(f"Clave anterior: {r['clave_anterior']}")
    if r.get("localizacion"):
        L.append(f"Localización: {r['localizacion']}")
    if r.get("temas"):
        L.append("Temas: " + ", ".join(r["temas"]))
    L += ["", "TEXTO", r.get("texto") or "(sin texto en la fuente)"]
    if r.get("precedentes"):
        L += ["", "PRECEDENTES"]
        L += [f"- {p}" for p in r["precedentes"]]
    if r.get("sentencias"):
        L += ["", "SENTENCIAS"]
        L += [f"- {s['expediente']}" + (f" — {s['url']}" if s.get("url") else "")
              for s in r["sentencias"]]
    if r.get("aprobacion"):
        L += ["", "APROBACIÓN", r["aprobacion"]]
    if r.get("publicacion"):
        L += ["", "PUBLICACIÓN", r["publicacion"]]
    if r.get("notas"):
        L += ["", "NOTAS", r["notas"]]
    if r.get("articulos"):
        L += ["", "ARTÍCULOS CITADOS"]
        L += [f"- {a.get('articulo') or ''}"
              + (f" ({a['ley']})" if a.get("ley") else "") for a in r["articulos"]]
    if not r.get("detalle_ok"):
        L += ["", "(El detalle de este criterio —precedentes, aprobación, publicación "
              "y artículos— no se pudo bajar en la última sincronización. El texto y "
              "la vigencia sí están completos.)"]
    L += ["", "CITA", cita(r)]
    return "\n".join(L)
