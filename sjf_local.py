"""
SJF local — tesis y jurisprudencias del Semanario Judicial de la Federación
servidas desde la Gaceta oficial, como respaldo cuando el API del SJF no responde.

Este módulo NO toca la red. Lee `kriterius_datos/sjf_gaceta.jsonl.gz` —34 041 tesis
extraídas de los 448 PDF de la Gaceta que publica la propia Corte en
scjn.gob.mx/coordinacion/gaceta— y las sirve indexadas en SQLite FTS5.

Por qué existe: desde el 3 de septiembre de 2026 `sjf2.scjn.gob.mx` está detrás de
Imperva Incapsula y rechaza a cualquier cliente que no pase su reto de JavaScript
(302/403). Las dos tools centrales del producto, buscar_tesis y ver_tesis, se
quedaron sin fuente desde el servidor remoto. La decisión tomada y que no se
reabre es que NO se elude el cortafuegos: ni navegador headless ni resolución de
retos. El respaldo es un acervo propio, construido de documentos públicos
oficiales, que se usa SOLO cuando el API falla. Con el API vivo, el dato vivo gana
siempre.

Lo que este acervo NO tiene, y por eso cada respuesta lo dice:

  - **El registro digital.** La Gaceta no lo imprime junto a sus tesis, solo la
    clave. Por eso no se puede construir el link canónico
    `sjf2.scjn.gob.mx/detalle/tesis/{registro}`. Se cita con clave + libro, tomo y
    página + la URL del PDF oficial. `resolver_registros.py` llenará el campo
    cuando el API vuelva.
  - **Los cambios de vigencia posteriores** a la publicación de cada libro. Una
    tesis interrumpida o sustituida después sigue impresa igual en su Gaceta.
  - **Lo publicado después del último libro** del acervo (agosto de 2026).

El índice FTS5 se construye una vez en disco, junto al JSONL, y se reutiliza en
los arranques siguientes; si el directorio no es escribible cae a memoria. En
memoria son ~400 MB de RSS, que en un droplet chico no siempre caben, y en disco
son milisegundos de arranque.

Si el archivo no existe o está corrupto, `cargar()` no lanza: deja la fuente
apagada y las tools lo dicen. Un JSONL roto no puede tumbar al TEPJF, al DOF ni a
la Corte IDH.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sqlite3
import threading
import unicodedata
from pathlib import Path

_DIR = Path(__file__).parent / "kriterius_datos"
RUTA_DATOS = _DIR / "sjf_gaceta.jsonl.gz"
RUTA_META = _DIR / "sjf_gaceta.meta.json"
# Mapa id -> registro digital que produce resolver_registros.py cuando el API del
# SJF vuelve a estar accesible. Va aparte del JSONL a propósito: así el JSONL
# sigue siendo la salida exacta y determinista del parser, y el enriquecimiento se
# puede rehacer o tirar sin tocarlo.
RUTA_REGISTROS = _DIR / "sjf_gaceta.registros.json"


def _dir_indice() -> Path:
    """Dónde vive el índice FTS. NO va junto al JSONL: el repo se sincroniza (en la
    Mac de Gonzalo, por iCloud) y el índice pesa cientos de megas y se reconstruye
    solo. Además, sobre algunos montajes de red SQLite ni siquiera puede escribir
    ("disk I/O error"), y ahí la caída a memoria es lo correcto.
    Se puede fijar con KRITERIUS_CACHE_DIR (el Dockerfile lo apunta al volumen)."""
    base = os.environ.get("KRITERIUS_CACHE_DIR")
    if not base:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "kriteriusmx"


RUTA_INDICE = _dir_indice() / "sjf_gaceta.fts.sqlite"

SJF_HOST = "https://sjf2.scjn.gob.mx"
GACETA_URL = "https://www.scjn.gob.mx/coordinacion/gaceta"

# Cuántos resultados se toman por relevancia BM25 antes de aplicar la prelación.
# Aplicar las reglas fijas sobre TODO el conjunto relevante enterraría una tesis
# con la palabra exacta en el rubro debajo de cien tesis de Pleno que la mencionan
# de paso; aplicarlas sobre los más relevantes, no.
TOPE_RELEVANCIA = 120

NO_DISPONIBLE = (
    "El acervo local de la Gaceta no está disponible en este despliegue: falta "
    "kriterius_datos/sjf_gaceta.jsonl.gz o no se pudo leer. Ejecuta "
    "diagnosticar_conector para ver el detalle."
)

_db: sqlite3.Connection | None = None
_por_clave: dict[str, list[int]] = {}
_registros: dict[str, int] = {}
_meta: dict = {}
_n: int = 0
_candado = threading.Lock()
_candado_carga = threading.Lock()
_INTENTADO = False


# ---- Prelación del proyecto ----
#
# Los rangos son los mismos que `_rango_organo` de kriterius_mx.py, para que el
# acervo local ordene igual que el API: 0 Pleno, 1 Primera Sala, 2 Segunda Sala,
# 3 Salas históricas, 4 Plenos Regionales, 5 Plenos de Circuito, 6 TCC.

def rango_organo(clave: str, nivel: str) -> int:
    c = (clave or "").strip()
    if c.startswith("P."):
        return 0
    if c.startswith("1a."):
        return 1
    if c.startswith("2a."):
        return 2
    if c.startswith(("3a.", "4a.")):
        return 3
    if nivel == "PLENO_REGIONAL":
        return 4
    if nivel == "PLENO_CIRCUITO":
        return 5
    return 6


NOMBRE_RANGO = ["Pleno SCJN", "Primera Sala", "Segunda Sala",
                "Salas históricas", "Plenos Regionales", "Plenos de Circuito",
                "Tribunales Colegiados de Circuito"]


def _epoca_n(epoca: str) -> int:
    m = re.match(r"(\d{1,2})", (epoca or "").strip())
    return int(m.group(1)) if m else 0


# ---- Carga e índice ----

_ESQUEMA = """
CREATE VIRTUAL TABLE criterios USING fts5(
    rubro, texto, precedentes,
    clave UNINDEXED, tipo UNINDEXED, rango UNINDEXED,
    epoca_n UNINDEXED, anio UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE docs (rowid INTEGER PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE info (k TEXT PRIMARY KEY, v TEXT);
"""


def _abrir_lineas(ruta: Path):
    if str(ruta).endswith(".gz"):
        return gzip.open(ruta, "rt", encoding="utf-8")
    return ruta.open(encoding="utf-8")


def _construir(db: sqlite3.Connection, ruta: Path) -> int:
    db.executescript(_ESQUEMA)
    n = 0
    lote_f, lote_d = [], []
    with _abrir_lineas(ruta) as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            r = json.loads(linea)
            n += 1
            lote_f.append((
                n, r.get("rubro") or "", r.get("texto") or "",
                " ".join(r.get("precedentes") or []),
                r.get("clave") or "", r.get("tipo") or "TA",
                rango_organo(r.get("clave") or "", r.get("nivel") or ""),
                _epoca_n(r.get("epoca") or ""),
                int((r.get("gaceta") or {}).get("anio") or 0),
            ))
            lote_d.append((n, linea))
            if len(lote_f) >= 2000:
                db.executemany(
                    "INSERT INTO criterios(rowid,rubro,texto,precedentes,clave,tipo,"
                    "rango,epoca_n,anio) VALUES (?,?,?,?,?,?,?,?,?)", lote_f)
                db.executemany("INSERT INTO docs(rowid,json) VALUES (?,?)", lote_d)
                lote_f, lote_d = [], []
    if lote_f:
        db.executemany(
            "INSERT INTO criterios(rowid,rubro,texto,precedentes,clave,tipo,"
            "rango,epoca_n,anio) VALUES (?,?,?,?,?,?,?,?,?)", lote_f)
        db.executemany("INSERT INTO docs(rowid,json) VALUES (?,?)", lote_d)
    db.execute("INSERT INTO info(k,v) VALUES('tesis',?)", (str(n),))
    db.execute("INSERT INTO info(k,v) VALUES('origen',?)", (_huella(ruta),))
    db.commit()
    return n


def _huella(ruta: Path) -> str:
    """Nombre, tamaño y fecha del JSONL de origen. Los tres, no solo la fecha: al
    copiar un repo todos los archivos quedan con la misma marca de tiempo, y un
    índice construido de otro archivo pasaría por bueno."""
    st = ruta.stat()
    return f"{ruta.name}|{st.st_size}|{int(st.st_mtime)}"


def _indice_sirve(ruta_indice: Path, ruta_datos: Path) -> bool:
    """El índice en disco vale si existe, abre y se construyó de ESTE JSONL. Un
    índice viejo sirviendo tesis que ya no están es peor que no tener índice."""
    try:
        if not ruta_indice.exists():
            return False
        db = sqlite3.connect(f"file:{ruta_indice}?mode=ro", uri=True)
        try:
            fila = db.execute("SELECT v FROM info WHERE k='origen'").fetchone()
            n = db.execute("SELECT v FROM info WHERE k='tesis'").fetchone()
            return bool(fila and n and fila[0] == _huella(ruta_datos) and int(n[0]) > 0)
        finally:
            db.close()
    except Exception:
        return False


def cargar(ruta: Path | str = RUTA_DATOS, ruta_meta: Path | str = RUTA_META,
           ruta_indice: Path | str | None = RUTA_INDICE) -> int:
    """Deja el acervo listo para consultar. Devuelve cuántas tesis hay, o 0 si no
    se pudo: nunca lanza.

    Reutiliza el índice en disco si corresponde a este JSONL; si no, lo construye.
    Si el directorio no es escribible (imagen de solo lectura, permisos), cae a un
    índice en memoria, que funciona igual pero cuesta RAM y unos segundos de
    arranque."""
    global _db, _por_clave, _meta, _n, _registros
    ruta = Path(ruta)
    db = None
    try:
        if ruta_indice is not None and _indice_sirve(Path(ruta_indice), ruta):
            db = sqlite3.connect(f"file:{Path(ruta_indice)}?mode=ro", uri=True,
                                 check_same_thread=False)
            n = int(db.execute("SELECT v FROM info WHERE k='tesis'").fetchone()[0])
        else:
            if not ruta.exists():
                raise FileNotFoundError(ruta)
            n = 0
            if ruta_indice is not None:
                tmp = Path(ruta_indice).with_suffix(".sqlite.tmp")
                try:
                    Path(ruta_indice).parent.mkdir(parents=True, exist_ok=True)
                    tmp.unlink(missing_ok=True)
                    constructor = sqlite3.connect(tmp)
                    n = _construir(constructor, ruta)
                    constructor.close()
                    # Publicación atómica: nadie ve un índice a medio construir.
                    tmp.replace(Path(ruta_indice))
                    db = sqlite3.connect(f"file:{Path(ruta_indice)}?mode=ro", uri=True,
                                         check_same_thread=False)
                except Exception:
                    try:
                        tmp.unlink(missing_ok=True)
                    except Exception:
                        pass
                    db, n = None, 0
            if db is None:
                db = sqlite3.connect(":memory:", check_same_thread=False)
                n = _construir(db, ruta)
    except Exception:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass
        with _candado:
            _db, _por_clave, _meta, _n, _registros = None, {}, {}, 0, {}
        return 0

    # Índice de claves en Python, no en SQL: SQLite no normaliza acentos y las
    # claves de los tribunales auxiliares los llevan ("(IV Región)1o. J/1 A").
    por_clave: dict[str, list[int]] = {}
    try:
        for rid, clave in db.execute("SELECT rowid, clave FROM criterios"):
            por_clave.setdefault(_plegar(clave), []).append(rid)
    except Exception:
        por_clave = {}

    meta = {}
    try:
        meta = json.loads(Path(ruta_meta).read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    registros = {}
    try:
        registros = {k: int(v) for k, v in
                     json.loads(RUTA_REGISTROS.read_text(encoding="utf-8")).items()}
    except Exception:
        registros = {}
    with _candado:
        _db, _por_clave, _meta, _n, _registros = db, por_clave, meta, n, registros
    return n


def asegurar() -> int:
    """Carga el acervo la primera vez que alguien lo necesita y no más. Igual que
    en tepjf.py: el core también se usa suelto, así que las tools no pueden dar
    por hecho que alguien ya llamó a `cargar`."""
    global _INTENTADO
    if _db is None and not _INTENTADO:
        with _candado_carga:
            if _db is None and not _INTENTADO:
                _INTENTADO = True
                cargar()
    return _n


def disponible() -> bool:
    return _db is not None and _n > 0


def meta() -> dict:
    d = dict(_meta)
    d.setdefault("tesis", _n)
    return d


def cobertura() -> str:
    """Una línea con hasta dónde llega el acervo, para los avisos."""
    m = meta()
    return (f"{m.get('tesis', 0)} tesis, de {m.get('desde', 'la Novena Época')} "
            f"a {m.get('hasta', 'agosto de 2026')}")


# ---- Búsqueda ----

def _entrecomillar(palabra: str) -> str:
    """Cada palabra del usuario va entre comillas. Sin esto, un guion, un asterisco
    o una palabra reservada de FTS5 rompen la sintaxis o —peor— cambian en silencio
    el significado de la consulta."""
    return '"' + palabra.replace('"', '""') + '"'


def _consulta_fts(texto: str) -> str:
    """Traduce la consulta del usuario a sintaxis FTS5 respetando las frases entre
    comillas dobles, que es como se busca una expresión exacta en buscar_tesis."""
    texto = (texto or "").strip()
    if not texto:
        return ""
    partes = []
    for trozo in re.findall(r'"[^"]*"|\S+', texto):
        t = trozo.strip('"').strip()
        if t:
            partes.append(_entrecomillar(t))
    return " ".join(partes)


def buscar(consulta: str, *, epocas: list[str] | None = None,
           tipo: str | None = None, incluir_precedentes: bool = False,
           tope: int = TOPE_RELEVANCIA) -> list[dict]:
    """Las tesis más relevantes, ya ordenadas por la prelación del proyecto:
    órgano (Pleno > Salas > Plenos Regionales > Plenos de Circuito > TCC),
    jurisprudencia antes que aislada, época más reciente, coincidencia en el rubro
    y, al final, BM25."""
    if not disponible():
        return []
    q = _consulta_fts(consulta)
    if not q:
        return []

    columnas = "{rubro texto precedentes}" if incluir_precedentes else "{rubro texto}"
    sql = ("SELECT rowid, tipo, rango, epoca_n, anio, "
           "bm25(criterios, 10.0, 1.0, 2.0) AS rank "
           "FROM criterios WHERE criterios MATCH ?")
    args: list = [f"{columnas} : ({q})"]
    if tipo:
        args.append("J" if tipo.lower().startswith("j") else "TA")
        sql += " AND tipo = ?"
    nums = [_epoca_n(e) for e in (epocas or []) if _epoca_n(e)]
    if nums:
        sql += " AND epoca_n IN (%s)" % ",".join("?" * len(nums))
        args += nums
    sql += " ORDER BY rank LIMIT ?"
    args.append(int(tope))

    with _candado:
        try:
            filas = _db.execute(sql, args).fetchall()
            en_rubro = {f[0] for f in _db.execute(
                "SELECT rowid FROM criterios WHERE criterios MATCH ?",
                [f"rubro : ({q})"])} if filas else set()
        except sqlite3.OperationalError:
            # Consulta que FTS5 no supo leer pese al entrecomillado: sin resultados
            # es mejor respuesta que una traza.
            return []
        orden = sorted(filas, key=lambda f: (
            f[2],                       # rango de órgano
            f[1] != "J",                # jurisprudencia antes que aislada
            -f[3],                      # época más reciente
            f[0] not in en_rubro,       # coincidencia en el rubro
            -f[4],                      # año más reciente
            f[5],                       # BM25
        ))
        ids = [f[0] for f in orden]
        if not ids:
            return []
        crudos = dict(_db.execute(
            "SELECT rowid, json FROM docs WHERE rowid IN (%s)" % ",".join("?" * len(ids)),
            ids).fetchall())
    return [json.loads(crudos[i]) for i in ids if i in crudos]


def registro(r: dict) -> int | None:
    """El registro digital de una tesis, si resolver_registros.py ya lo averiguó.
    Mientras no exista, la cita lo declara pendiente en vez de inventarlo."""
    return r.get("registro_digital") or _registros.get(r.get("id") or "")


def _plegar(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").upper())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Z0-9]", "", s)


def obtener(clave: str) -> list[dict]:
    """Las tesis con esa clave. Devuelve lista porque una clave puede aparecer en
    dos libros (reimpresión o corrección de rubro) y ocultarlo sería mentir.

    La comparación ignora acentos, espacios y puntuación: nadie teclea
    "(IV Región)1o. J/1 A (12a.)" exactamente igual dos veces. Si la clave viene
    sin el sufijo de época se aceptan las que empiezan igual."""
    if not disponible():
        return []
    objetivo = _plegar(clave)
    if not objetivo:
        return []
    ids = list(_por_clave.get(objetivo) or [])
    if not ids:
        ids = [i for k, v in _por_clave.items() if k.startswith(objetivo) for i in v]
    if not ids:
        return []
    ids = ids[:20]
    with _candado:
        filas = _db.execute(
            "SELECT rowid, json FROM docs WHERE rowid IN (%s)" % ",".join("?" * len(ids)),
            ids).fetchall()
    por_id = {r: j for r, j in filas}
    return [json.loads(por_id[i]) for i in ids if i in por_id]


# ---- Presentación ----

AVISO = (
    "⚠ ACERVO LOCAL — el API del SJF no respondió: {motivo}. Estos resultados salen "
    "de la Gaceta oficial del Semanario que publica la propia Corte ({cobertura}). "
    "NO incluyen tesis publicadas después ni cambios de vigencia posteriores, y no "
    "traen registro digital porque la Gaceta no lo imprime. Verifícalos en {host} "
    "cuando el API vuelva."
)


def aviso(motivo: str = "bloqueo del cortafuegos de la SCJN") -> str:
    return AVISO.format(motivo=motivo, cobertura=cobertura(), host=SJF_HOST)


def _publicacion(r: dict) -> str:
    """El nombre de la publicación cambió con la Décima Época: hasta noviembre de
    2013 era el *Semanario Judicial de la Federación y su Gaceta*; desde diciembre
    de 2013 es la *Gaceta del Semanario Judicial de la Federación*. Citarlas al
    revés es un error de cita, no un detalle."""
    g = r.get("gaceta") or {}
    if g.get("serie") == "SJFyG" or (r.get("epoca") or "").startswith("9"):
        return "Semanario Judicial de la Federación y su Gaceta"
    return "Gaceta del Semanario Judicial de la Federación"


def _localizacion(r: dict) -> str:
    g = r.get("gaceta") or {}
    unidad = "Tomo" if (r.get("epoca") or "").startswith("9") else "Libro"
    partes = [_publicacion(r)]
    if g.get("libro_cita") or g.get("libro"):
        partes.append(f"{unidad} {g.get('libro_cita') or g.get('libro')}")
    if g.get("tomo"):
        partes.append(f"Tomo {g['tomo']}")
    if g.get("parte"):
        partes.append(f"parte {g['parte']}")
    if g.get("mes") and g.get("anio"):
        partes.append(f"{g['mes']} de {g['anio']}")
    elif g.get("anio"):
        partes.append(str(g["anio"]))
    if g.get("pagina"):
        partes.append(f"página {g['pagina']}")
    return ", ".join(partes)


_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_larga(iso: str) -> str:
    """2026-08-14 -> 14 de agosto de 2026. Una cita no lleva fechas en ISO."""
    try:
        a, m, d = (iso or "").split("-")
        return f"{int(d)} de {_MESES[int(m) - 1]} de {a}"
    except Exception:
        return iso or ""


def _epoca_larga(e: str) -> str:
    return {"9a": "Novena Época", "10a": "Décima Época",
            "11a": "Undécima Época", "12a": "Duodécima Época"}.get(e or "", e or "")


def _tipo_largo(r: dict) -> str:
    return "Jurisprudencia" if r.get("tipo") == "J" else "Tesis aislada"


def cita(r: dict) -> str:
    """La cita completa con datos de identificación y fuente verificable, como pide
    la regla del proyecto. El registro digital va explícitamente como pendiente:
    la Gaceta no lo imprime y inventarlo sería peor que no darlo."""
    partes = [(r.get("rubro") or "").rstrip("."), (r.get("organo") or "").rstrip("."),
              f"{_tipo_largo(r)} {r.get('clave')}",
              _epoca_larga(r.get("epoca")), _localizacion(r)]
    L = [", ".join(p for p in partes if p) + "."]
    pdf = r.get("fuente_pdf") or GACETA_URL
    pag = (r.get("gaceta") or {}).get("pagina")
    linea = f"Fuente: {pdf}" + (f" (p. {pag})" if pag else "")
    if r.get("publicacion_sjf"):
        linea += f" — publicada en el SJF el {_fecha_larga(r['publicacion_sjf'])}"
    L.append(linea)
    ius = registro(r)
    if ius:
        L.append(f"Registro digital: {ius} — {SJF_HOST}/detalle/tesis/{ius}")
    else:
        L.append(f"Registro digital: pendiente (la Gaceta impresa no lo publica); "
                 f"verificar en {SJF_HOST}")
    return "\n".join(L)


def linea(r: dict, n: int) -> list[str]:
    g = r.get("gaceta") or {}
    L = [f"{n}. {r.get('rubro')}",
         f"   {r.get('organo')}",
         f"   {_tipo_largo(r)} {r.get('clave')} · {_epoca_larga(r.get('epoca'))} · "
         f"{_localizacion(r)}"]
    if r.get("obligatoria_desde"):
        L.append(f"   Obligatoria desde el {_fecha_larga(r['obligatoria_desde'])}")
    cuerpo = (r.get("texto") or "").replace("\n", " ")
    if cuerpo:
        L.append(f"   «{cuerpo[:280].strip()}…»")
    ius = registro(r)
    if ius:
        L.append(f"   {SJF_HOST}/detalle/tesis/{ius}")
    if r.get("fuente_pdf"):
        L.append(f"   PDF oficial: {r['fuente_pdf']}"
                 + (f" (p. {g['pagina']})" if g.get("pagina") else ""))
    return L


def formatear_busqueda(resultados: list[dict], consulta: str, motivo: str,
                       pagina: int = 1, por_pagina: int = 20) -> str:
    encabezado = aviso(motivo)
    if not resultados:
        return (f"{encabezado}\n\nSin resultados en el acervo local para "
                f"'{consulta}'. Prueba con menos palabras o con sinónimos. "
                f"Recuerda que el acervo llega hasta agosto de 2026 y no trae "
                f"tesis anteriores a octubre de 2011.")
    total = len(resultados)
    paginas = max(1, (total + por_pagina - 1) // por_pagina)
    pagina = max(1, min(pagina, paginas))
    trozo = resultados[(pagina - 1) * por_pagina: pagina * por_pagina]

    L = [encabezado, "",
         f"{total} resultado(s) en el acervo local, página {pagina} de {paginas}.",
         "Orden: jerarquía de obligatoriedad (órgano → jurisprudencia → época "
         "reciente → coincidencia en rubro).", ""]
    n = (pagina - 1) * por_pagina
    for r in trozo:
        n += 1
        L += linea(r, n) + [""]
    if pagina < paginas:
        L.append(f"Hay más: pide la página {pagina + 1}.")
    L.append("Para el texto íntegro: ver_tesis(clave) con la clave de la tesis, "
             "p. ej. ver_tesis(\"1a./J. 45/2026 (12a.)\").")
    return "\n".join(L)


def formatear_tesis(r: dict, motivo: str) -> str:
    g = r.get("gaceta") or {}
    L = [aviso(motivo), "", r.get("rubro") or "", "",
         r.get("organo") or "",
         f"{_tipo_largo(r)} {r.get('clave')} · {_epoca_larga(r.get('epoca'))}",
         _localizacion(r)]
    if r.get("publicacion_sjf"):
        L.append(f"Publicación en el SJF: {_fecha_larga(r['publicacion_sjf'])}"
                 + (f" · obligatoria desde el {_fecha_larga(r['obligatoria_desde'])}"
                    if r.get("obligatoria_desde") else ""))
    L += ["", "TEXTO", r.get("texto") or "(sin texto)"]
    if r.get("precedentes"):
        L += ["", "PRECEDENTES"] + [f"- {p}" for p in r["precedentes"]]
    if r.get("aprobacion"):
        L += ["", "APROBACIÓN", r["aprobacion"]]
    if r.get("notas"):
        L += ["", "NOTAS"] + [f"- {x}" for x in r["notas"]]
    if r.get("duplicado_de"):
        L += ["", f"(Esta clave también aparece en {r['duplicado_de']}. La Corte "
                  f"reimprime o corrige rubros; compara ambas en el PDF oficial.)"]
    marcas = r.get("revisar") or []
    if marcas:
        expl = {
            "organo_inferido": "el órgano se dedujo de la clave (Pleno y Salas no "
                               "imprimen esa línea en la Gaceta)",
            "texto_recortado": "el texto se cortó donde empieza la ejecutoria o los "
                               "votos que la Gaceta imprime a continuación",
            "texto_largo": "el texto es inusualmente largo",
            "rubro_dudoso": "el rubro pudo quedar incompleto o arrastrar datos de la "
                            "ejecutoria; contrástalo con el PDF antes de citarlo",
        }
        L += ["", "AVISOS DE EXTRACCIÓN: "
              + "; ".join(expl.get(m, m) for m in marcas)
              + ". Contrasta con el PDF oficial."]
    L += ["", "CITA", cita(r)]
    return "\n".join(L)
