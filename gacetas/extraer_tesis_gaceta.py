#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extraer_tesis_gaceta.py — Extrae tesis y jurisprudencias de los Markdown de la
Gaceta del Semanario Judicial de la Federación (KriteriusMX, respaldo local del SJF).

Determinista, sin red. Lee md/<epoca>/*.md y escribe:
    datos/sjf_gaceta.jsonl              tesis validadas
    datos/sjf_gaceta.rechazados.jsonl   bloques que no validaron, con motivo y contexto
    datos/sjf_gaceta.meta.json          cobertura por libro contra el índice del propio libro

Uso:
    python3 extraer_tesis_gaceta.py [--epoca 12a] [--libro 12] [--salida datos]
                                    [--max-segundos N] [--limite N] [--stdout-muestra CLAVE]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

# La raíz por defecto es la carpeta del script. GACETAS_RAIZ la mueve para que la
# sincronización semanal trabaje en un directorio temporal con un solo libro
# dentro, sin arrastrar los 1.5 GB de Markdown del acervo completo.
RAIZ = Path(os.environ.get("GACETAS_RAIZ") or Path(__file__).resolve().parent)
DIR_MD = RAIZ / "md"
MANIFEST = Path(os.environ.get("GACETAS_MANIFEST") or (RAIZ / "gacetas_manifest.tsv"))


def cargar_manifest() -> dict:
    """archivo .pdf -> URL oficial del PDF en scjn.gob.mx. La cabecera del Markdown
    solo guarda la portada de la Gaceta; la cita necesita el PDF del libro."""
    urls = {}
    try:
        with MANIFEST.open(encoding="utf-8") as f:
            cab = f.readline().rstrip("\n").split("\t")
            i_url, i_arch = cab.index("url"), cab.index("archivo")
            for linea in f:
                c = linea.rstrip("\n").split("\t")
                if len(c) > max(i_url, i_arch):
                    urls[c[i_arch]] = c[i_url]
    except Exception:
        pass
    return urls


URLS_PDF = cargar_manifest()

# ---------------------------------------------------------------- normalización

_GUION_SUAVE = "­"

def normalizar(texto: str) -> str:
    """Quita guiones suaves (>12k por libro), une la palabra partida por el salto
    de línea que los acompaña, y normaliza comillas y espacios raros."""
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    # guion suave + salto de línea = palabra partida por el PDF -> unir
    texto = re.sub(_GUION_SUAVE + r"[ \t]*\n[ \t]*", "", texto)
    texto = texto.replace(_GUION_SUAVE, "")
    texto = (texto.replace("‘", "'").replace("’", "'")
                  .replace("“", '"').replace("”", '"')
                  .replace(" ", " ").replace("–", "-").replace("—", "-"))
    return texto

def plegar(s: str) -> str:
    """Forma canónica para comparar rubros: sin acentos, sin puntuación, sin espacios."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Za-z0-9]", "", s).upper()

_RE_MINUS = re.compile(r"[a-záéíóúüñ]")
_RE_MAYUS = re.compile(r"[A-ZÁÉÍÓÚÜÑ]")

_RE_ORDINAL = re.compile(r"\(\d{1,2}a\.\)|\b\d{1,3}[oa]\.")

def es_mayuscula(linea: str) -> bool:
    """Línea de versalitas. Las claves y los ordinales que a veces se imprimen
    dentro del rubro ("… PR.P.T.CN. J/3 P (11a.)].") llevan minúsculas y no
    deben romper la racha."""
    l = linea.strip()
    if not l:
        return False
    if _RE_MINUS.search(_RE_ORDINAL.sub("", l)):
        return False
    return bool(_RE_MAYUS.search(l))

# ---------------------------------------------------------------- claves

_REGION = r"(?:\([IVXLCDM]+\s+Regi[oó]n\)\s*)?"
_EPOCA_SUF = r"(?:\s*\((?:9|10|11|12)a\.\))"
_PUNTO_FINAL = r"\.?"

# Familia 1: Pleno y Salas -> P./J. 1/2025 (12a.) | P. I/2025 (12a.) | 1a./J. 45/2026 (12a.) | 2a./J. 65/2000
RE_CLAVE_ALTA = re.compile(
    r"^(?P<sala>P|1a|2a|3a|4a)\.(?P<jur>/J\.)?\s*"
    r"(?P<num>\d+|[IVXLCDM]+)/(?P<anio>\d{4})" + _EPOCA_SUF + r"?" + _PUNTO_FINAL + r"$")

# Familia 2: Plenos Regionales, Plenos de Circuito y TCC
# PR.P.T.CN. J/1 L (12a.) | PC.I.C. J/82 K (10a.) | I.3o.C.36 K (10a.) | (IV Región)1o. J/1 A (12a.)
RE_CLAVE_ORG = re.compile(
    r"^" + _REGION +
    r"(?P<organo>[A-Z0-9][A-Za-z0-9º°]{0,9}"
    r"(?:\.(?:[A-Za-z0-9º°]{1,9}|\([IVXLCDM]+\s+Regi[oó]n\)))*\.?)"
    r"\s*(?P<jur>J/)?(?P<num>\d+)(?:\s+(?P<materia>CS|[ACKLP]))?" + _EPOCA_SUF + r"?" + _PUNTO_FINAL + r"$")

def parsear_clave(linea: str):
    l = linea.strip()
    if not (3 <= len(l) <= 60):
        return None
    m = RE_CLAVE_ALTA.match(l)
    if m:
        sala = m.group("sala")
        nivel = "PLENO" if sala == "P" else "SALA"
        tipo = "J" if m.group("jur") else "TA"
        return {"clave": (l[:-1] if l.endswith(").") else l), "nivel": nivel, "tipo": tipo}
    m = RE_CLAVE_ORG.match(l)
    if m:
        org = m.group("organo").upper()
        if "." not in org:
            return None
        # Los Plenos de Circuito de 2013-2014 no imprimian la letra de materia
        # ("PC.XIII. J/1 (10a.)"). Sin materia se exige el sufijo de epoca, que es
        # lo que impide colar lineas cualesquiera con un numero y una letra.
        if not m.group("materia") and not re.search(r"\(\d{1,2}a\.\)", l):
            return None
        if org.startswith("PR."):
            nivel = "PLENO_REGIONAL"
        elif org.startswith("PC."):
            nivel = "PLENO_CIRCUITO"
        else:
            nivel = "TCC"
        tipo = "J" if m.group("jur") else "TA"
        return {"clave": (l[:-1] if l.endswith(").") else l), "nivel": nivel, "tipo": tipo}
    return None

def epoca_de_clave(clave: str, por_defecto: str) -> str:
    m = re.search(r"\((\d{1,2})a\.\)\s*$", clave)
    return (m.group(1) + "a") if m else por_defecto

# ---------------------------------------------------------------- marcadores

RE_FIN_TESIS = re.compile(r"^Esta tesis se public[oó] el ", re.I)
# Frontera de bloque: una tesis empieza justo despues del cierre de la tesis o
# sentencia anterior. Es lo que impide confundir el rubro con una tesis CITADA
# dentro del texto (van en versalitas y entre comillas).
RE_FIN_BLOQUE = re.compile(r"^Esta (tesis|sentencia|ejecutoria) se public[oó] el ", re.I)

# Un organo real nombra al emisor; sirve para no tomar por organo la ultima
# linea en versalitas del texto (p. ej. el rubro entrecomillado de otra tesis).
RE_ORGANO = re.compile(r"\b(TRIBUNAL|PLENO|SALA|SUPREMA CORTE|CIRCUITO|CORTE)\b")

ORGANO_INFERIDO = {
    "P": "PLENO DE LA SUPREMA CORTE DE JUSTICIA DE LA NACIÓN.",
    "1a": "PRIMERA SALA DE LA SUPREMA CORTE DE JUSTICIA DE LA NACIÓN.",
    "2a": "SEGUNDA SALA DE LA SUPREMA CORTE DE JUSTICIA DE LA NACIÓN.",
    "3a": "TERCERA SALA DE LA SUPREMA CORTE DE JUSTICIA DE LA NACIÓN.",
    "4a": "CUARTA SALA DE LA SUPREMA CORTE DE JUSTICIA DE LA NACIÓN.",
}

def organo_por_clave(clave: str):
    m = re.match(r"^(P|1a|2a|3a|4a)\.", clave.strip())
    return ORGANO_INFERIDO.get(m.group(1)) if m else None

RE_APROBACION = re.compile(r"^Tesis de jurisprudencia\b", re.I)
RE_NOTA = re.compile(r"^Nota[s]?:", re.I)
RE_VOTO = re.compile(r"^(Voto|Votos)\b", re.I)

RE_PRECEDENTE = re.compile(
    r"^(Amparo\b|Contradicci[oó]n de (tesis|criterios|opiniones)\b|Controversia constitucional\b|"
    r"Acci[oó]n de inconstitucionalidad\b|Recurso de \b|Queja\b|Incidente\b|Solicitud\b|"
    r"Conflicto competencial\b|Competencia\b|Varios\b|Revisi[oó]n administrativa\b|Impedimento\b|"
    r"Reclamaci[oó]n\b|Inconformidad\b|Juicio ordinario\b|Consulta a tr[aá]mite\b|"
    r"Cumplimiento de ejecutoria\b|Denuncia de \b|Expediente\b|Facultad de atracci[oó]n\b)")

def a_romano(n: int) -> str:
    tabla = ((100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"),
             (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
    out = ""
    for v, sig in tabla:
        while n >= v:
            out += sig
            n -= v
    return out


# El encabezado de una ejecutoria va en versalitas y arranca con el tipo de
# asunto y su numero ("CONTRADICCIÓN DE TESIS 123/2010. ENTRE LAS SUSTENTADAS…").
# RE_PRECEDENTE distingue mayusculas, por eso este va aparte y con IGNORECASE.
RE_ENCABEZADO_EJECUTORIA = re.compile(RE_PRECEDENTE.pattern, re.I)

MESES = {m: i + 1 for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
     "agosto", "septiembre", "octubre", "noviembre", "diciembre"])}

RE_FECHA = re.compile(r"(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})", re.I)

def fechas_del_marcador(bloque: str):
    """('2026-08-14', '2026-08-17') a partir del párrafo de cierre."""
    fechas = []
    for m in RE_FECHA.finditer(bloque):
        mes = MESES.get(m.group(2).lower())
        if mes:
            fechas.append("%04d-%02d-%02d" % (int(m.group(3)), mes, int(m.group(1))))
    pub = fechas[0] if fechas else None
    obl = fechas[1] if len(fechas) > 1 and "obligatoria" in bloque.lower() else None
    return pub, obl

# Encabezados de sección que pueden pegarse al rubro
ENCABEZADOS = {
    "TESIS AISLADAS Y, EN SU CASO, SENTENCIAS", "SENTENCIAS Y TESIS QUE NO INTEGRAN JURISPRUDENCIA",
    "JURISPRUDENCIA", "TESIS AISLADAS", "TESIS DE JURISPRUDENCIA", "ÍNDICES", "INDICES",
    "PRIMERA PARTE", "SEGUNDA PARTE", "TERCERA PARTE", "CUARTA PARTE", "QUINTA PARTE",
}
RE_ENCABEZADO = re.compile(r"^(SUBSECCI[OÓ]N|SECCI[OÓ]N|APARTADO|CAP[IÍ]TULO|VOLUMEN|TOMO|LIBRO)\b")
# Los encabezados de seccion vienen partidos en varias lineas por el PDF
# ("SENTENCIAS Y TESIS" / "QUE NO INTEGRAN JURISPRUDENCIA"), asi que no basta
# comparar la linea completa: se descartan por prefijo, y solo si no llevan punto.
RE_ENCABEZADO_FRAG = re.compile(
    r"^(SENTENCIAS|TESIS|JURISPRUDENCIA|Y, EN SU CASO|QUE NO INTEGRAN|[ÍI]NDICE|"
    r"ACUERDOS|VOTOS|EJECUTORIAS|AP[EÉ]NDICE|NORMATIVA|SUBSECCI[OÓ]N|SECCI[OÓ]N|"
    r"PRIMERA PARTE|SEGUNDA PARTE|TERCERA PARTE|CUARTA PARTE|QUINTA PARTE)\b")

def es_encabezado(linea: str) -> bool:
    l = linea.strip()
    return "." not in l and len(l) < 70 and bool(RE_ENCABEZADO_FRAG.match(l))


# --- corte rubro/texto -------------------------------------------------------
# Hay dos maquetas. En la moderna (11a tardia y 12a) el rubro ocupa lineas
# completas en versalitas y el texto empieza en "Hechos:". En la antigua (10a y
# 11a temprana) el rubro y el texto van en el MISMO parrafo. El corte que sirve
# para las dos: el rubro es el prefijo en versalitas y termina en el ultimo
# punto antes de la primera minuscula real del bloque.
_RE_MASCARA = re.compile(r"\(\d{1,2}a\.\)|\b\d{1,3}[oa]\.|\b[a-z]\)|\bJ/")

def arranca_versalitas(linea: str) -> bool:
    cabeza = _RE_MASCARA.sub("", linea.strip())[:20]
    letras = [c for c in cabeza if c.isalpha()]
    return len(letras) >= 8 and not _RE_MINUS.search(cabeza)

# Cuando la tesis no lleva linea de organo (Pleno y Salas), entre su texto y su
# clave la Gaceta puede imprimir la ejecutoria completa y los votos. El texto de
# la tesis termina en el primero de estos marcadores. Solo se aplica a los
# registros anomalos (>8 000 caracteres): la mediana real ronda los 3 500.
RE_FIN_TEXTO = re.compile(
    r"\bVotos?\s+(?:particular|concurrente|minoritario|aclaratorio|paralelo)\b"
    r"|\bEJECUTORIA\b"
    r"|\bEl Tribunal Pleno,?\s+el\b"
    r"|\bPonente:|\bSecretari[oa]:"
    r"|\b(?:Contradicci[oó]n de tesis|Contradicci[oó]n de criterios|Amparo directo en revisi[oó]n|"
    r"Amparo en revisi[oó]n|Amparo directo|Acci[oó]n de inconstitucionalidad|"
    r"Controversia constitucional)\s+\d+/\d{4}\b")

LIMITE_TEXTO = 8000
MINIMO_TEXTO = 1000
TOPE_TEXTO = 25000


def recortar_texto(texto: str):
    if len(texto) <= LIMITE_TEXTO:
        return texto, False
    m = RE_FIN_TEXTO.search(texto, MINIMO_TEXTO)
    if m:
        corte = texto.rfind(".", MINIMO_TEXTO, m.start())
        corte = corte + 1 if corte != -1 else m.start()
        texto = texto[:corte].strip()
    if len(texto) <= TOPE_TEXTO:
        return texto, bool(m)
    # Tope duro: ninguna tesis real llega aqui. Se corta en el ultimo punto y se
    # marca; el registro conserva libro, tomo, pagina y el PDF oficial para ir
    # al texto completo.
    corte = texto.rfind(".", MINIMO_TEXTO, TOPE_TEXTO)
    return texto[:(corte + 1 if corte != -1 else TOPE_TEXTO)].strip(), True


# pdftotext deja a veces el encabezado corrido de la página metido dentro del
# rubro ("… POR LOS TESIS AISLADAS TRIBUNALES COLEGIADOS 1417 TRES MAGISTRADOS…").
# Es el título de la sección más el folio; ningún rubro real lo contiene.
RE_ENCABEZADO_CORRIDO = re.compile(
    r"\s+(?:TESIS AISLADAS|JURISPRUDENCIA|SENTENCIAS Y TESIS|EJECUTORIAS Y TESIS|"
    r"PRIMERA PARTE|SEGUNDA PARTE|TERCERA PARTE|CUARTA PARTE|QUINTA PARTE)"
    r"(?:\s+(?:TRIBUNALES COLEGIADOS|PLENOS DE CIRCUITO|PLENOS REGIONALES|"
    r"PRIMERA SALA|SEGUNDA SALA|PLENO|SUPREMA CORTE DE JUSTICIA DE LA NACI[ÓO]N))?"
    r"\s+\d{2,5}\s+")

# Señales de que el rubro se comió datos de la ejecutoria o de los precedentes.
# No se descarta el registro (existe y está en el índice): se marca.
RE_RUBRO_SOSPECHOSO = re.compile(
    r"\bPONENTE\s*:|\bSECRETARI[OA]\s*:|"
    r"\b(?:INCIDENTE DE INEJECUCI[ÓO]N|AMPARO DIRECTO EN REVISI[ÓO]N|"
    r"AMPARO EN REVISI[ÓO]N|AMPARO DIRECTO|CONTRADICCI[ÓO]N DE TESIS|"
    r"CONTRADICCI[ÓO]N DE CRITERIOS)\s+\d+/\d{4}\b")


def partir_rubro(bloque: str):
    bloque = RE_ENCABEZADO_CORRIDO.sub(" ", bloque)
    mascara = _RE_MASCARA.sub(lambda m: "X" * len(m.group()), bloque)
    m = _RE_MINUS.search(mascara)
    corte = m.start() if m else len(bloque)
    punto = bloque.rfind(".", 0, corte)
    if punto == -1:
        return "", bloque
    return bloque[:punto + 1].strip(), bloque[punto + 1:].strip()

# ---------------------------------------------------------------- lectura de un md

RE_ANCLA = re.compile(r"^<!--\s*(p\.|hoja)\s*(\d+)\s*-->$")

def cargar_md(ruta: Path):
    crudo = normalizar(ruta.read_text(encoding="utf-8", errors="replace"))
    cabecera = {}
    cuerpo = crudo
    if crudo.startswith("---\n"):
        fin = crudo.find("\n---\n", 4)
        if fin != -1:
            for linea in crudo[4:fin].split("\n"):
                if ":" in linea:
                    k, v = linea.split(":", 1)
                    v = v.strip().strip('"')
                    cabecera[k.strip()] = None if v in ("null", "") else v
            cuerpo = crudo[fin + 5:]
    lineas, paginas, folio = [], [], None
    pag_actual, es_folio = None, False
    for linea in cuerpo.split("\n"):
        m = RE_ANCLA.match(linea.strip())
        if m:
            pag_actual = int(m.group(2))
            es_folio = (m.group(1) == "p.")
            continue
        lineas.append(linea.rstrip())
        paginas.append(pag_actual if es_folio else None)
    return cabecera, lineas, paginas

# ---------------------------------------------------------------- índice del libro

def claves_del_indice(lineas):
    """Entradas del Índice General Alfabético: línea de clave seguida de un número
    de página suelto. Es la verdad de referencia para medir cobertura."""
    encontradas = {}
    n = len(lineas)
    for i, linea in enumerate(lineas):
        c = parsear_clave(linea)
        if not c:
            continue
        k = i - 1
        while k >= 0 and not lineas[k].strip():
            k -= 1
        if k < 0 or not es_mayuscula(lineas[k]) or not lineas[k].rstrip().rstrip('"').endswith("."):  # noqa: E501
            continue
        j, saltos = i + 1, 0
        while j < n and saltos < 4:
            l = lineas[j].strip()
            if not l or l in ("Número de identificación", "Página"):
                j += 1
                continue
            if re.fullmatch(r"\d{1,5}", l):
                z = i - 1
                while z >= 0 and not lineas[z].strip():
                    z -= 1
                fin_r = z
                while z >= 0 and lineas[z].strip() and es_mayuscula(lineas[z]):
                    z -= 1
                encontradas.setdefault(c["clave"], (int(l), unir(lineas[z + 1:fin_r + 1])))
                break
            saltos += 1
            j += 1
    return encontradas

# ---------------------------------------------------------------- extracción

MAX_ATRAS = 2500

def limpiar_rubro(lineas_rubro):
    while lineas_rubro:
        cab = lineas_rubro[0].strip()
        if (len(cab) <= 2 or cab in ENCABEZADOS or RE_ENCABEZADO.match(cab)
                or re.fullmatch(r"[IVXLCDM]+", cab)):
            lineas_rubro.pop(0)
        else:
            break
    return lineas_rubro

def unir(lineas_):
    return re.sub(r"\s+", " ", " ".join(l.strip() for l in lineas_)).strip()

def formatear_texto(t: str) -> str:
    for etq in ("Criterio jurídico:", "Justificación:"):
        t = t.replace(" " + etq, "\n" + etq)
    return t.strip()

def _bloque(lineas, paginas, i_clave, datos_clave, limite, i_fin):
    """Arma una tesis alrededor de su clave. i_fin es la linea del marcador de
    cierre cuando existe (>=2013); None en la maqueta antigua."""
    # organo: versalitas inmediatamente arriba de la clave. Pleno y Salas no lo
    # imprimen: se infiere de la clave.
    j = i_clave - 1
    while j > limite and not lineas[j].strip():
        j -= 1
    fin_organo = j
    while j > limite and es_mayuscula(lineas[j]):
        j -= 1
    ini_organo = j + 1
    organo = unir(lineas[ini_organo:fin_organo + 1]) if ini_organo <= fin_organo else ""
    organo_inferido = False
    if not organo or not RE_ORGANO.search(organo):
        organo = organo_por_clave(datos_clave["clave"]) or ""
        organo_inferido = True
        ini_organo = i_clave
    if not organo:
        return None, "sin_organo", None

    # frontera del bloque: cierre anterior o clave anterior. Es lo que impide
    # tomar por rubro una tesis CITADA dentro del texto.
    frontera = limite
    for j in range(i_clave - 1, limite, -1):
        if RE_FIN_BLOQUE.match(lineas[j].strip()):
            frontera = j + 1
            break
    for j in range(i_clave - 1, frontera, -1):
        l = lineas[j].strip()
        if not l or len(l) > 60 or RE_PRECEDENTE.match(l) or RE_APROBACION.match(l):
            continue
        k = j + 1
        while k < i_clave and not lineas[k].strip():
            k += 1
        if k < i_clave and RE_PRECEDENTE.match(lineas[k].strip()):
            frontera = j + 1
            break

    # rubro: se prueban los candidatos en orden; el primero con corte valido gana,
    # asi los encabezados de seccion se descartan solos.
    ini_rubro = rubro = texto = None
    j = frontera
    while j < ini_organo:
        l = lineas[j].strip()
        if l and not es_encabezado(l) and l not in ENCABEZADOS and arranca_versalitas(l):
            r_, t_ = partir_rubro(unir(lineas[j:ini_organo]))
            t_ = t_.lstrip("-–— ")
            if 20 <= len(r_) <= 900 and r_.endswith(".") and len(t_) >= 100:
                ini_rubro, rubro, texto = j, r_, t_
                break
            if ini_rubro is None and r_:
                ini_rubro, rubro, texto = j, r_, t_
        j += 1
    if ini_rubro is None:
        return None, "sin_rubro", None

    # limite inferior de los precedentes
    if i_fin is not None:
        tope = i_fin
    else:
        tope = len(lineas)
        for k in range(i_clave + 2, min(i_clave + 400, len(lineas))):
            l = lineas[k].strip()
            if not l:
                continue
            if RE_FIN_BLOQUE.match(l) or parsear_clave(l) or (
                    arranca_versalitas(l) and not es_encabezado(l)):
                tope = k
                break

    precedentes, aprobacion, notas, actual = [], None, [], None
    for l in lineas[i_clave + 1:tope]:
        s_ = l.strip()
        if not s_:
            continue
        if RE_APROBACION.match(s_):
            aprobacion, actual = s_, "aprobacion"
        elif RE_NOTA.match(s_):
            notas.append(s_); actual = "nota"
        elif RE_PRECEDENTE.match(s_):
            precedentes.append(s_); actual = "prec"
        elif actual == "prec" and precedentes:
            precedentes[-1] += " " + s_
        elif actual == "aprobacion":
            aprobacion += " " + s_
        elif actual == "nota" and notas:
            notas[-1] += " " + s_

    pub = obl = None
    if i_fin is not None:
        cierre = unir(lineas[i_fin:min(i_fin + 8, len(lineas))])
        if "Esta tesis se public" in cierre:
            cierre = cierre.split("Esta tesis se public")[1]
        pub, obl = fechas_del_marcador(cierre)

    if len(rubro) < 20 or not rubro.endswith("."):
        return None, "rubro_invalido", rubro
    if len(rubro) > 900:
        return None, "rubro_desproporcionado", rubro
    if len(texto) < 100:
        return None, "texto_corto", rubro
    # En la Novena la Gaceta imprime las ejecutorias completas con su encabezado
    # en versalitas ("CONTRADICCIÓN DE TESIS 123/2010. ENTRE LAS SUSTENTADAS…").
    # No son tesis: sin este filtro entran con 100 KB de sentencia por texto.
    if RE_ENCABEZADO_EJECUTORIA.match(rubro) and re.search(r"\d+/\d{4}", rubro[:130]):
        return None, "es_ejecutoria", rubro
    # Estas dos NO descartan: la tesis existe y esta en el indice del libro.
    # Se conserva y se marca, que es lo revisable; descartarla perdia cobertura.
    texto, texto_recortado = recortar_texto(texto)
    rubro_dudoso = bool(rubro.startswith(('"', "'"))
                        or RE_RUBRO_SOSPECHOSO.search(rubro))
    if rubro_dudoso:
        rubro = rubro.lstrip('"\' ')
    texto_largo = len(texto) > 20000
    if i_fin is not None and not pub:
        return None, "sin_fecha_publicacion", rubro
    if i_fin is None and not precedentes:
        return None, "sin_precedentes", rubro

    pagina = None
    for pg in paginas[ini_rubro:tope + 1]:
        if pg is not None:
            pagina = pg
            break

    return {
        "_ini": ini_rubro, "_fin": tope,
        "clave": datos_clave["clave"], "tipo": datos_clave["tipo"],
        "nivel": datos_clave["nivel"], "organo": organo,
        "organo_inferido": organo_inferido, "rubro": rubro, "texto": texto,
        "precedentes": precedentes, "aprobacion": aprobacion, "notas": notas,
        "publicacion_sjf": pub, "obligatoria_desde": obl, "pagina": pagina,
        "rubro_dudoso": rubro_dudoso, "texto_largo": texto_largo,
        "texto_recortado": texto_recortado,
    }, None, rubro


def extraer_de_libro(ruta: Path, epoca_dir: str):
    cabecera, lineas, paginas = cargar_md(ruta)
    n = len(lineas)
    indice = claves_del_indice(lineas)

    libro = int(cabecera.get("libro") or 0) or None
    anio = int(cabecera.get("anio") or 0) or None
    epoca_libro = epoca_dir.replace("-epoca", "")

    # La Decima Epoca tiene DOS series con la misma numeracion: los libros
    # romanos del "Semanario Judicial de la Federacion y su Gaceta" (Libro I,
    # oct 2011 - Libro XXV, nov 2013) y los arabigos de la "Gaceta del Semanario
    # Judicial de la Federacion", que arranca en Libro 1, diciembre de 2013.
    # Sin distinguirlas la cita queda mal.
    serie = None
    libro_cita = str(libro) if libro else None
    if epoca_libro == "10a" and libro and anio:
        mes_n = MESES.get((cabecera.get("mes") or "").lower(), 0)
        if (anio, mes_n) < (2013, 12):
            serie, libro_cita = "SJFyG", a_romano(libro)
        else:
            serie, libro_cita = "GSJF", str(libro)
    elif epoca_libro == "9a" and libro:
        serie, libro_cita = "SJFyG", a_romano(libro)

    fines = [i for i, l in enumerate(lineas) if RE_FIN_TESIS.match(l.strip())]
    crudas, rechazos, cubierto = [], [], []

    # Pase 1 — anclado en el marcador de cierre (desde el Acuerdo 19/2013).
    for orden, i_fin in enumerate(fines):
        limite = max(fines[orden - 1] if orden else 0, i_fin - MAX_ATRAS)
        i_clave = datos = None
        for j in range(i_fin - 1, limite, -1):
            d = parsear_clave(lineas[j])
            if d:
                i_clave, datos = j, d
                break
        if i_clave is None:
            rechazos.append(rechazo(ruta, lineas, i_fin, "sin_clave"))
            continue
        t, motivo, rubro = _bloque(lineas, paginas, i_clave, datos, limite, i_fin)
        if t is None:
            r = rechazo(ruta, lineas, i_clave, motivo, datos["clave"])
            r["rubro_parcial"] = (rubro or "")[:200]
            rechazos.append(r)
            continue
        t["ancla"] = "marcador"
        crudas.append(t)
        cubierto.append((t["_ini"], t["_fin"]))

    # Pase 2 — anclado en la clave, para la maqueta anterior a 2013 (Novena y
    # Decima temprana), donde no existe el marcador de cierre. Se exige que la
    # linea siguiente sea un precedente: es lo que distingue una tesis real de
    # una clave citada, de un ejemplo de nomenclatura o de una entrada de indice.
    candidatos = []
    for i in range(n):
        if any(a <= i <= b for a, b in cubierto):
            continue
        datos = parsear_clave(lineas[i])
        if not datos:
            continue
        k = i + 1
        while k < n and not lineas[k].strip():
            k += 1
        if k >= n or not (RE_PRECEDENTE.match(lineas[k].strip())
                          or RE_APROBACION.match(lineas[k].strip())):
            continue
        candidatos.append((i, datos))

    # Sin marcador de cierre, el piso del bloque es la clave anterior: despues de
    # ella vienen sus precedentes y luego ya el rubro siguiente. Sin este piso el
    # rubro se cruzaba con el de la tesis vecina.
    anterior = 0
    for i, datos in candidatos:
        limite = max(anterior, i - MAX_ATRAS)
        for a, b in cubierto:
            if b < i:
                limite = max(limite, b)
        anterior = i
        t, motivo, rubro = _bloque(lineas, paginas, i, datos, limite, None)
        if t is None:
            if motivo not in ("sin_rubro", "texto_corto"):
                r = rechazo(ruta, lineas, i, motivo, datos["clave"])
                r["rubro_parcial"] = (rubro or "")[:200]
                rechazos.append(r)
            continue
        t["ancla"] = "clave"
        crudas.append(t)
        cubierto.append((t["_ini"], t["_fin"]))

    crudas.sort(key=lambda t: t["_ini"])
    tesis = []
    for t in crudas:
        clave = t["clave"]
        epoca = epoca_de_clave(clave, epoca_libro)
        tesis.append({
            "id": f"{epoca}:{clave}",
            "clave": clave,
            "tipo": t["tipo"],
            "organo": t["organo"],
            "organo_inferido": t["organo_inferido"],
            "nivel": t["nivel"],
            "epoca": epoca,
            "rubro": t["rubro"],
            "texto": t["texto"],
            "precedentes": t["precedentes"],
            "aprobacion": t["aprobacion"],
            "notas": t["notas"],
            "publicacion_sjf": t["publicacion_sjf"],
            "obligatoria_desde": t["obligatoria_desde"],
            "gaceta": {
                "libro": libro,
                "libro_cita": libro_cita,
                "serie": serie,
                "tomo": cabecera.get("tomo"),
                "parte": cabecera.get("parte"),
                "mes": cabecera.get("mes"),
                "anio": anio,
                "pagina": t["pagina"] if t["pagina"] is not None else (indice[clave][0] if clave in indice else None),
                "pagina_indice": indice[clave][0] if clave in indice else None,
            },
            "fuente_pdf": URLS_PDF.get(cabecera.get("pdf") or "") or cabecera.get("fuente"),
            "archivo_md": f"{epoca_dir}/{ruta.name}",
            "registro_digital": None,
            "materias": [],
            "ancla": t["ancla"],
            "revisar": ([k for k in ("rubro_dudoso", "texto_largo", "texto_recortado") if t[k]]
                        + (["organo_inferido"] if t["organo_inferido"] else [])) or None,
        })

    discrepantes = []
    for t in tesis:
        ent = indice.get(t["clave"])
        if not ent or not ent[1]:
            continue
        a, b = plegar(ent[1]), plegar(t["rubro"])
        if a and a not in b and b not in a:
            discrepantes.append({"clave": t["clave"], "archivo": f"{epoca_dir}/{ruta.name}",
                                 "rubro_cuerpo": t["rubro"], "rubro_indice": ent[1]})

    extraidas = {t["clave"] for t in tesis}
    faltantes = set(indice) - extraidas
    sobrantes = extraidas - set(indice)
    meta = {
        "archivo": f"{epoca_dir}/{ruta.name}",
        "libro": libro, "libro_cita": libro_cita, "serie": serie,
        "anio": anio, "mes": cabecera.get("mes"),
        "tomo": cabecera.get("tomo"), "parte": cabecera.get("parte"),
        "marcadores_fin": len(fines),
        "extraidas": len(tesis),
        "por_ancla": {"marcador": sum(1 for t in tesis if t["ancla"] == "marcador"),
                      "clave": sum(1 for t in tesis if t["ancla"] == "clave")},
        "rechazadas": len(rechazos),
        "indice_entradas": len(indice),
        "cobertura_archivo": round(100.0 * len(extraidas & set(indice)) / len(indice), 2) if indice else None,
        "rubros_contrastados": sum(1 for t in tesis if indice.get(t["clave"], (None, ""))[1]),
        "rubros_discrepantes": len(discrepantes),
        "faltantes_archivo": len(faltantes),
        "sobrantes_archivo": len(sobrantes),
    }
    return tesis, rechazos, meta, set(indice), extraidas, discrepantes


def rechazo(ruta, lineas, i, motivo, clave=None):
    return {
        "archivo": ruta.name, "linea": i, "motivo": motivo, "clave": clave,
        "contexto": [l for l in lineas[max(0, i - 12):i + 8]],
    }

# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epoca", action="append", default=None, help="9a|10a|11a|12a (repetible)")
    ap.add_argument("--libro", type=int, default=None)
    ap.add_argument("--salida", default=str(RAIZ / "datos"))
    ap.add_argument("--sufijo", default="")
    ap.add_argument("--max-segundos", type=float, default=0)
    ap.add_argument("--limite", type=int, default=0)
    args = ap.parse_args()

    epocas = args.epoca or ["9a", "10a", "11a", "12a"]
    archivos = []
    for e in epocas:
        d = DIR_MD / f"{e}-epoca"
        if d.is_dir():
            archivos += sorted(d.glob("*.md"))
    if args.libro:
        archivos = [a for a in archivos if re.search(r"Libro-0*%d[_.]" % args.libro, a.name)]
    if args.limite:
        archivos = archivos[:args.limite]

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    suf = args.sufijo
    f_jsonl = salida / f"sjf_gaceta{suf}.jsonl"
    f_rech = salida / f"sjf_gaceta{suf}.rechazados.jsonl"
    f_meta = salida / f"sjf_gaceta{suf}.meta.json"
    f_discr = salida / f"sjf_gaceta{suf}.rubros_discrepantes.jsonl"

    t0 = time.time()
    vistas, metas, n_ok, n_rech, n_dup = {}, [], 0, 0, 0
    por_libro = {}
    n_discr = 0
    with open(f_jsonl, "w", encoding="utf-8") as fo, open(f_rech, "w", encoding="utf-8") as fr, \
            open(f_discr, "w", encoding="utf-8") as fd:
        for ruta in archivos:
            epoca_dir = ruta.parent.name
            try:
                tesis, rechazos, meta, idx_claves, ext_claves, discr = extraer_de_libro(ruta, epoca_dir)
            except Exception as exc:  # noqa: BLE001
                metas.append({"archivo": f"{epoca_dir}/{ruta.name}", "error": repr(exc)})
                print(f"  ERROR {ruta.name}: {exc!r}", file=sys.stderr)
                continue
            for t in tesis:
                previo = vistas.get(t["id"])
                if previo:
                    t["duplicado_de"] = previo
                    n_dup += 1
                else:
                    vistas[t["id"]] = t["archivo_md"]
                fo.write(json.dumps(t, ensure_ascii=False) + "\n")
                n_ok += 1
            for r in rechazos:
                fr.write(json.dumps(r, ensure_ascii=False) + "\n")
                n_rech += 1
            for x in discr:
                fd.write(json.dumps(x, ensure_ascii=False) + "\n")
                n_discr += 1
            metas.append(meta)
            k = (epoca_dir, meta.get("serie"), meta["libro"])
            agg = por_libro.setdefault(k, {"indice": set(), "extraidas": set(), "archivos": 0, "tesis": 0})
            agg["indice"] |= idx_claves
            agg["extraidas"] |= ext_claves
            agg["archivos"] += 1
            agg["tesis"] += len(tesis)
            cob = meta.get("cobertura_archivo")
            print(f"  {meta['archivo']}: {meta['extraidas']} tesis / índice {meta['indice_entradas']}"
                  f" / cobertura {cob if cob is not None else 's/i'}% / rechazos {meta['rechazadas']}",
                  flush=True)
            if args.max_segundos and time.time() - t0 > args.max_segundos:
                print("  (corte por --max-segundos)", file=sys.stderr)
                break

    libros = []
    for (ep, ser, lib), a in sorted(por_libro.items(),
                                    key=lambda x: (x[0][0], x[0][1] or "", x[0][2] or 0)):
        falt = sorted(a["indice"] - a["extraidas"])
        sob = sorted(a["extraidas"] - a["indice"])
        libros.append({
            "epoca": ep, "serie": ser, "libro": lib, "archivos": a["archivos"], "tesis": a["tesis"],
            "indice_entradas": len(a["indice"]),
            "cobertura": round(100.0 * len(a["indice"] & a["extraidas"]) / len(a["indice"]), 2)
            if a["indice"] else None,
            "faltantes_total": len(falt), "faltantes": falt[:40],
            "sobrantes_total": len(sob), "sobrantes": sob[:40],
        })
    con_indice = [m for m in libros if m.get("indice_entradas")]
    peores = sorted([m for m in libros if m["cobertura"] is not None],
                    key=lambda m: m["cobertura"])[:15]
    resumen = {
        "generado": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "archivos": len(metas),
        "tesis": n_ok,
        "duplicados": n_dup,
        "rechazados": n_rech,
        "pct_rechazo": round(100.0 * n_rech / max(1, n_ok + n_rech), 2),
        "rubros_contrastados": sum(m.get("rubros_contrastados", 0) for m in metas),
        "rubros_discrepantes": n_discr,
        "marcadas_revisar": sum(1 for t in [] ) or None,
        "cobertura_global": round(
            100.0 * sum(m["indice_entradas"] - m["faltantes_total"] for m in con_indice)
            / max(1, sum(m["indice_entradas"] for m in con_indice)), 2) if con_indice else None,
        "libros_bajo_95": [ (m["epoca"], m["serie"], m["libro"], m["cobertura"]) for m in libros
                            if m["cobertura"] is not None and m["cobertura"] < 95 ],
        "peores_libros": [ (m["epoca"], m["serie"], m["libro"], m["cobertura"], m["faltantes_total"]) for m in peores ],
        "segundos": round(time.time() - t0, 1),
        "cobertura_por_libro": libros,
        "archivos_detalle": metas,
    }
    f_meta.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in resumen.items() if k not in ("cobertura_por_libro", "archivos_detalle")}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
