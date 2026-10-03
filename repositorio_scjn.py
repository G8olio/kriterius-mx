"""
Repositorio de la SCJN — el programa oficial de datos abiertos de la Corte.

    https://bicentenario.scjn.gob.mx/repositorio-scjn

No es ingeniería inversa: la Corte publica un manual con una sección «Pasos para
utilizar la API de datos (PARA PROGRAMADORES)». Cada colección tiene el mismo
contrato de tres puntos finales:

    GET /api/v1/{indice}/count      total de documentos (entero en texto)
    GET /api/v1/{indice}/ids        ids paginados: ?page=N (desde 0) &size=M (máx. 1 000)
    GET /api/v1/{indice}/{id}       el documento en JSON

Índices observados el 3 de octubre de 2026:

    Semanario (fuentes/1)   tesis 312 056 · ejecutoria 23 058 · votos 10 215 · acuerdos 4 081
    SIJ (fuentes/2)         engroses 106 267 (sentencias) · vtaquigraficas 5 946

La página web busca con un cuarto punto final que el manual no menciona:

    POST /api/reforma/busqueda  {"q", "page" (desde 1), "size", "indice", "filtros"}

`filtros` es {campo: [valores]}: OR dentro de un campo, AND entre campos. Las palabras
de `q` se combinan con AND, las comillas marcan frase y `campo:"valor"` busca en un
campo (`tesis:"P./J. 9/2016 (10a.)"` encuentra la clave exacta). El `total` que reporta
se topa en 100, pero la paginación sigue: la página 6 de 20 trae del 101 al 120. Una
consulta que no puede interpretar (una comilla sin cerrar) devuelve total null.

Dos cosas que importan para citar:

  * `idTesis` es el registro digital del Semanario y `idEjecutoria` el registro de la
    ejecutoria en sjf2. Son los mismos números, así que un registro sirve en las dos
    fuentes.
  * Cada documento trae `huellaDigital`, el SHA-256 con que la Corte certifica el
    documento.

Rarezas: un id inexistente responde HTTP 500 (no 404), los ids llegan unas veces
como número y otras como texto, y la búsqueda de ejecutorias devuelve el texto
íntegro de cada sentencia (2 MB por página de 20), que hay que soltar antes de
guardar nada en caché.

Este módulo no toca la red: arma cuerpos de búsqueda, traduce los documentos al
formato que ya usan las tools del Semanario y da formato a las sentencias del SIJ.
La red vive en kriterius_mx.py, detrás del mismo freno y la misma caché que el resto.
"""

import re
import unicodedata

BASE = "https://bicentenario.scjn.gob.mx/repositorio-scjn"
SITIO_SIJ = f"{BASE}/sij?fuente=Sentencia&tab=1"
TOPE_BUSQUEDA = 100

EPOCAS_NOMBRE = {
    "1a": "Primera Época", "2a": "Segunda Época", "3a": "Tercera Época",
    "4a": "Cuarta Época", "5a": "Quinta Época", "6a": "Sexta Época",
    "7a": "Séptima Época", "8a": "Octava Época", "9a": "Novena Época",
    "10a": "Décima Época", "11a": "Undécima Época", "12a": "Duodécima Época",
}

# Valores del filtro tipoAsunto de las sentencias, tal como los devolvió el
# Repositorio el 3 de octubre de 2026. El documento de una sentencia NO trae su tipo
# de asunto, así que la única manera de distinguir el amparo en revisión 93/2026 de
# la acción de inconstitucionalidad 93/2026 es filtrar con uno de estos.
TIPOS_ASUNTO = [
    "ACCIÓN DE INCONSTITUCIONALIDAD",
    "ACLARACIÓN DE JURISPRUDENCIA",
    "ACLARACIÓN DE SENTENCIA",
    "AMPARO DIRECTO",
    "AMPARO DIRECTO EN REVISIÓN",
    "AMPARO EN REVISIÓN",
    "ARTÍCULO 100 PÁRRAFO OCTAVO DE LA CONSTITUCIÓN POLÍTICA DE LOS ESTADOS UNIDOS MEXICANOS",
    "ARTÍCULO 11 DE LEY ORGÁNICA PJF FRACCIÓN IX",
    "ARTÍCULO 11 FRACCIÓN XVII DE LA LEY ÓRGANICA DEL PJF",
    "CONFLICTO COMPETENCIAL",
    "CONSULTA A TRÁMITE PREVISTA EN LA PARTE SEGUNDA DE LA FRACCIÓN II DEL ART. 20 DE LA LEY ORGÁNICA DEL PODER JUDICIAL DE LA FEDERACIÓN",
    "CONSULTA A TRÁMITE PREVISTO EN EL PÁRRAFO SEGUNDO DE LA FRACCIÓN II DEL ART. 14 DE LA LEY ORGÁNICA DEL PODER JUDICIAL DE LA FEDERACIÓN",
    "CONTRADICCIÓN DE CRITERIOS (ANTES CONTRADICCIÓN DE TESIS)",
    "CONTROVERSIA CONSTITUCIONAL",
    "CONTROVERSIA PREVISTA EN EL ARTÍCULO 11, FRACCIÓN XX, DE LA LEY ORGÁNICA DEL PODER JUDICIAL DE LA FEDERACIÓN",
    "CONTROVERSIA PREVISTA EN EL ARTÍCULO 11, FRACCIÓN XXII DE LA LEY ORGÁNICA DEL PODER JUDICIAL DE LA FEDERACIÓN",
    "DECLARATORIA GENERAL DE INCONSTITUCIONALIDAD",
    "DENUNCIA DE INCUMPLIMIENTO POR APLICACIÓN DE NORMAS O ACTOS DECLARADOS INVÁLIDOS EN LA CONTROVERSIA CONSTITUCIONAL",
    "DENUNCIA DE REPETICIÓN DEL ACTO RECLAMADO",
    "DICTAMEN FINAL DE SOLICITUD DE EJERCICIO DE LA FACULTAD DE INVESTIGACIÓN (ART. 97)",
    "EXCEPCIÓN DE CONEXIDAD",
    "EXCEPCIÓN DE FALTA DE PERSONALIDAD",
    "EXCEPCIÓN DE IMPROCEDENCIA DE LA VÍA",
    "EXCEPCIÓN DE INCOMPETENCIA",
    "EXCEPCIÓN DE LITISPENDENCIA",
    "EXPEDIENTE SOBRE RECEPCIÓN DE SENTENCIAS DE TRIBUNALES INTERNACIONALES",
    "IMPEDIMENTO",
    "INCIDENTE DE ACLARACIÓN DE SENTENCIA EN LA CONTROVERSIA CONSTITUCIONAL",
    "INCIDENTE DE ACUMULACIÓN",
    "INCIDENTE DE CUMPLIMIENTO SUSTITUTO",
    "INCIDENTE DE FALSEDAD",
    "INCIDENTE DE INCUMPLIMIENTO DE SENTENCIA DERIVADO DE CONTROVERSIA CONSTITUCIONAL",
    "INCIDENTE DE INEJECUCIÓN DE SENTENCIA",
    "INCIDENTE DE INEJECUCIÓN DERIVADO DE DENUNCIA DE REPETICIÓN DEL ACTO RECLAMADO",
    "INCIDENTE DE INEJECUCIÓN DERIVADO DE DENUNCIA FUNDADA DE REPETICIÓN DE LA APLICACIÓN EN PERJUICIO DEL DENUNCIANTE DE UNA NORMA GENERAL DECLARADA INCONSTITUCIONAL CON EFECTOS GENERALES",
    "INCIDENTE DE INEJECUCIÓN DERIVADO DE INCIDENTE DE CUMPLIMIENTO SUSTITUTO",
    "INCIDENTE DE INEJECUCIÓN DERIVADO DEL INCUMPLIMIENTO DE UNA DECLARATORIA GENERAL DE INCONSTITUCIONALIDAD",
    "INCIDENTE DE LIQUIDACIÓN DE INTERESES",
    "INCIDENTE DE NULIDAD DE ACTUACIONES DE J. ORD. F.",
    "INCIDENTE DE PAGO DE HONORARIOS",
    "INCIDENTES DERIVADOS DE JUICIOS ORD. FED.",
    "INCONFORMIDAD",
    "INCONFORMIDAD EN CUMPLIMIENTO DE REVISIONES ADMINISTRATIVAS",
    "JUICIO DE INCONFORMIDAD EN MATERIA ELECTORAL",
    "JUICIO ORDINARIO FEDERAL",
    "JUICIO SOBRE CUMPLIMIENTO DE LOS CONVENIOS DE COORDINACIÓN FISCAL",
    "MODIFICACIÓN DE JURISPRUDENCIA",
    "QUEJA",
    "QUEJA EN CONTROVERSIAS CONSTITUCIONALES Y ACCIONES DE INCONSTITUCIONALIDAD",
    "RECONOCIMIENTO DE INOCENCIA",
    "RECURSO DE APELACIÓN",
    "RECURSO DE DENEGADA APELACIÓN",
    "RECURSO DE INCONFORMIDAD (PROCESO DE SELECCIÓN 2025)",
    "RECURSO DE INCONFORMIDAD PREVISTO EN LA FRACCIÓN IV DEL ARTÍCULO 201 DE LA LEY DE AMPARO",
    "RECURSO DE INCONFORMIDAD PREVISTO EN LAS FRACCIONES I A III DEL ARTÍCULO 201 DE LA LEY DE AMPARO",
    "RECURSO DE RECLAMACIÓN",
    "RECURSO DE RECLAMACIÓN DERIVADO DE JUICIO CONTENCIOSO ADMINISTRATIVO",
    "RECURSO DE RECLAMACIÓN DERIVADO DEL JUICIO SOBRE EL CONVENIO DE COORDINACIÓN FISCAL",
    "RECURSO DE RECLAMACIÓN EN ACCIONES DE INCONST.",
    "RECURSO DE RECLAMACIÓN EN LA CONTROVERSIA CONST.",
    "RECURSO DE REVISIÓN EN MATERIA DE SEGURIDAD NACIONAL PREVISTO EN LA LEY GENERAL DE TRANSPARENCIA Y ACCESO A LA INFORMACIÓN PÚBLICA",
    "RECURSO DE REVOCACIÓN",
    "REVISIÓN ADMINISTRATIVA",
    "REVISIÓN ADMINISTRATIVA (LEY FEDERAL DE PROCEDIMIENTO CONTENCIOSO ADMINISTRATIVO)",
    "REVISIÓN DE CONSTITUCIONALIDAD DE LA MATERIA DE UNA CONSULTA POPULAR CONVOCADA POR EL CONGRESO DE LA UNIÓN",
    "REVISIÓN EN INCIDENTE DE SUSPENSIÓN",
    "SOLICITUD DE EJERCICIO DE LA FACULTAD DE ATRACCIÓN",
    "SOLICITUD DE EJERCICIO DE LA FACULTAD DE ATRACCIÓN PREVISTA EN LA FRACCIÓN III DEL ARTÍCULO 105 DE LA CONSTITUCIÓN POLÍTICA DE LOS ESTADOS UNIDOS MEXICANOS",
    "SOLICITUD DE EJERCICIO DE LA FACULTAD PREVISTA EN EL ARTÍCULO 97, PÁRRAFO SEGUNDO, DE LA CONSTITUCIÓN POLÍTICA DE LOS ESTADOS UNIDOS MEXICANOS",
    "SOLICITUD DE REASUNCIÓN DE COMPETENCIA",
    "SOLICITUD DE SUSTITUCIÓN DE JURISPRUDENCIA",
    "VARIOS",
]

# Cómo los escriben los abogados. Se comparan ya plegados (sin acentos ni puntos).
SIGLAS_ASUNTO = {
    "AR": "AMPARO EN REVISIÓN", "ADR": "AMPARO DIRECTO EN REVISIÓN",
    "AD": "AMPARO DIRECTO", "AI": "ACCIÓN DE INCONSTITUCIONALIDAD",
    "CC": "CONTROVERSIA CONSTITUCIONAL",
    "CT": "CONTRADICCIÓN DE CRITERIOS (ANTES CONTRADICCIÓN DE TESIS)",
    "CONTRADICCION DE TESIS": "CONTRADICCIÓN DE CRITERIOS (ANTES CONTRADICCIÓN DE TESIS)",
    "CONTRADICCION DE CRITERIOS": "CONTRADICCIÓN DE CRITERIOS (ANTES CONTRADICCIÓN DE TESIS)",
    "RI": "RECURSO DE RECLAMACIÓN", "RECLAMACION": "RECURSO DE RECLAMACIÓN",
    "FA": "SOLICITUD DE EJERCICIO DE LA FACULTAD DE ATRACCIÓN",
    "DGI": "DECLARATORIA GENERAL DE INCONSTITUCIONALIDAD",
}

ORGANOS = {"PLENO": "PLENO", "PRIMERA SALA": "PRIMERA SALA", "1A SALA": "PRIMERA SALA",
           "SEGUNDA SALA": "SEGUNDA SALA", "2A SALA": "SEGUNDA SALA"}

_EXPEDIENTE = re.compile(r"\b(\d{1,5}/\d{4})\b")


def _plegar(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").upper())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[.,;:]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _sin_espacios(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", _plegar(s))


def _id(v) -> int | str:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return v


# ---- Búsqueda ----

def cuerpo_busqueda(consulta: str, indice: str, pagina: int = 1, por_pagina: int = 20,
                    filtros: dict | None = None) -> dict:
    return {"q": (consulta or "").strip() or "*", "page": max(1, int(pagina)),
            "size": max(1, int(por_pagina)), "indice": indice, "filtros": filtros or {}}


def filtros_epocas(epocas: list[str] | None) -> dict:
    nombres = [EPOCAS_NOMBRE[e.lower().strip()] for e in (epocas or [])
               if e.lower().strip() in EPOCAS_NOMBRE]
    return {"epoca": nombres} if nombres else {}


def filtros_tesis(epocas: list[str] | None, tipo: str | None) -> dict:
    f = filtros_epocas(epocas)
    if tipo:
        f["tipoTesis"] = ["Jurisprudencia" if tipo.lower().startswith("j") else "Aislada"]
    return f


def resolver_tipo_asunto(texto: str) -> list[str]:
    """Traduce lo que escribió el usuario a valores del filtro tipoAsunto.

    Exacto primero, luego siglas, luego los que contienen la expresión. Si nada
    coincide devuelve [] y quien llama busca sin el filtro, diciéndolo: filtrar por un
    valor que el Repositorio no conoce daría cero resultados sin explicación."""
    p = _plegar(texto)
    if not p:
        return []
    for t in TIPOS_ASUNTO:
        if _plegar(t) == p:
            return [t]
    if p in SIGLAS_ASUNTO:
        return [SIGLAS_ASUNTO[p]]
    if p.replace(" ", "") in SIGLAS_ASUNTO:
        return [SIGLAS_ASUNTO[p.replace(" ", "")]]
    return [t for t in TIPOS_ASUNTO if p in _plegar(t)]


def resolver_organo(texto: str) -> list[str]:
    p = _plegar(texto)
    return [ORGANOS[p]] if p in ORGANOS else []


def desarmar_expediente(consulta: str) -> tuple[str | None, str]:
    """'amparo en revisión 93/2026' → ('93/2026', 'amparo en revisión'). El resto sale
    sin comillas: el modelo suele entrecomillar la consulta entera."""
    m = _EXPEDIENTE.search(consulta or "")
    if not m:
        return None, (consulta or "").strip()
    resto = (consulta[:m.start()] + " " + consulta[m.end():]).replace('"', " ")
    return m.group(1), re.sub(r"\s+", " ", resto).strip(" .,-")


def armar_consulta_sentencias(consulta: str, tipo_asunto: str | None = None
                              ) -> tuple[str, list[str], str]:
    """De lo que escribió el usuario a (q, tipos de asunto para el filtro, nota).

    'amparo en revisión 93/2026' → ('"93/2026"', ['AMPARO EN REVISIÓN'], ''): el tipo va
    como filtro y el número entre comillas, para que la diagonal no se lea como sintaxis.
    Las palabras que no son un tipo de asunto se conservan: el Repositorio combina con
    AND, así que acotan en vez de perderse."""
    expediente, resto = desarmar_expediente(consulta)
    tipos_resto = resolver_tipo_asunto(resto) if (expediente and resto) else []
    nota = ""
    if tipo_asunto:
        tipos = resolver_tipo_asunto(tipo_asunto)
        if not tipos:
            nota = f"No reconocí el tipo de asunto '{tipo_asunto}'; se buscó sin ese filtro."
    else:
        tipos = tipos_resto
    if not expediente:
        q = (consulta or "").strip()
    elif not resto or tipos_resto:
        q = f'"{expediente}"'
    else:
        q = f'{resto} "{expediente}"'
    return q, tipos, nota


# ---- Traducción al formato de las tools del Semanario ----
#
# ver_tesis, buscar_tesis, ver_ejecutoria y buscar_ejecutorias ya saben presentar los
# documentos de sjf2. En vez de duplicar esa presentación, el documento del
# Repositorio se traduce a los mismos nombres de campo y la presentación es una sola.

def tesis_a_sjf(r: dict) -> dict:
    materias = r.get("materias")
    if isinstance(materias, str):
        materias = [m.strip() for m in materias.split(",") if m.strip()]
    tipo = r.get("tipoTesis") or ""
    # Los precedentes traen " | | |" como separador de secciones vacías.
    precedentes = re.sub(r"(\s*\|\s*)+$", "", r.get("precedentes") or "")
    precedentes = re.sub(r"\s*\|\s*(\|\s*)*", "\n", precedentes).strip()
    return {
        "ius": _id(r.get("idTesis")),
        "rubro": r.get("rubro") or "",
        "localizacion": (r.get("localizacion") or "").strip(),
        "instancia": r.get("instancia") or "",
        "organoJuris": r.get("organoJuris") or "",
        "epoca": r.get("epoca") or "",
        "tipoTesis": tipo,
        "ta_tj": 1 if tipo.lower().startswith("juris") else 0,
        "claveTesis": r.get("tesis") or "",
        "materias": materias or [],
        "texto": r.get("texto") or "",
        "precedentes": precedentes,
        "notasGenericas": r.get("notaPublica") or "",
        "huellaDigital": r.get("huellaDigital") or "",
    }


def ejecutoria_a_sjf(r: dict, coincidencia: str | None = None) -> dict:
    """En un resultado de búsqueda, `coincidencia` sustituye al rubro: en sjf2 el rubro
    de un resultado es el fragmento donde cayó la consulta, y _ejec_linea lo presenta
    como tal. Aquí el rubro son los criterios de la sentencia, que no son coincidencia;
    si no se halló fragmento, va vacío. En el detalle (None) el rubro sí son los
    criterios, igual que en sjf2."""
    epoca = r.get("epoca") or ""
    organo = r.get("organoJuris") or r.get("instancia") or ""
    fuente = r.get("fuente") or ""
    volumen = r.get("volumen") or ""
    loc = ". ".join(x for x in (epoca, organo, f"{fuente}, {volumen}" if volumen else fuente) if x)
    tesis = r.get("tesis") or ""
    if isinstance(tesis, str):
        tesis = [t.strip() for t in re.split(r"[|;\n]", tesis) if t.strip()]
    return {
        "ius": _id(r.get("idEjecutoria")),
        "tipoAsunto": r.get("asunto") or "",
        "tipoAsuntoE": r.get("asunto") or "",
        "promovente": r.get("promovente") or "",
        "localizacion": (loc + ".") if loc else "",
        "instancia": organo,
        "epoca": epoca,
        "fuente": fuente,
        "volumen": volumen,
        "rubro": (r.get("rubro") or "") if coincidencia is None else coincidencia,
        "texto": r.get("texto") or "",
        "tesis": tesis,
        "huellaDigital": r.get("huellaDigital") or "",
    }


# ---- Sentencias del SIJ ----

_INICIALES = re.compile(r"^\s*[A-ZÑ]{2,6}(/[A-Za-zñÑ]{2,6})+\s*$")


def _limpiar_tema(tema: str) -> str:
    """El tema trae al final las iniciales de quien capturó ('JOVT/izso'); no son tema."""
    lineas = [l.strip() for l in (tema or "").replace("\r", "").split("\n")]
    return " · ".join(l for l in lineas if l and not _INICIALES.match(l))


def _corto(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def linea_sentencia(r: dict, tipo_asunto: str = "") -> list[str]:
    expediente = r.get("expediente") or "sin expediente"
    asunto = f"{tipo_asunto} {expediente}" if tipo_asunto else f"Expediente {expediente}"
    organo = r.get("organoResolvio") or r.get("pertenencia") or "—"
    lineas = [
        f"[{asunto}], [{organo}], [Ponente: {r.get('ministro') or '—'}], "
        f"[Resuelto el {r.get('fechaResolucion') or '—'}], [Id SIJ {r.get('engroseId')}]",
    ]
    tema = _limpiar_tema(r.get("tema") or "")
    if tema:
        lineas.append(f"Tema: {_corto(tema, 240)}")
    if r.get("resolucion"):
        lineas.append(f"Resolutivos: {_corto(r['resolucion'], 320)}")
    if r.get("urlInternet"):
        lineas.append(f"Documento: {r['urlInternet']}")
    lineas.append("")
    return lineas


def formatear_sentencia(r: dict, id_sij) -> str:
    def limpio(campo):
        return (r.get(campo) or "").replace("\r\n", "\n").strip()

    partes = [
        f"SENTENCIA DE LA SCJN — Sistema de Informática Jurídica, id {id_sij}",
        f"Expediente: {r.get('expediente') or '—'}",
        f"Órgano de radicación: {r.get('pertenencia') or '—'} | "
        f"Órgano que resolvió: {r.get('organoResolvio') or '—'}",
        f"Ponente: {r.get('ministro') or '—'}",
        f"Fecha de resolución: {r.get('fechaResolucion') or '—'}",
    ]
    tema = _limpiar_tema(r.get("tema") or "")
    if tema:
        partes.append(f"Tema: {tema}")
    if limpio("organoJurisdiccionalOrigen"):
        partes.append("Origen: " + " · ".join(
            l.strip() for l in limpio("organoJurisdiccionalOrigen").split("\n") if l.strip()))
    if limpio("asuntosAcumulados"):
        partes.append(f"Asuntos acumulados: {limpio('asuntosAcumulados')}")
    partes += ["", "RESOLUTIVOS:", limpio("resolucion") or "—"]
    if limpio("votacion"):
        partes += ["", "VOTACIÓN:", limpio("votacion")]
    partes.append("")
    if r.get("urlInternet"):
        partes.append(f"Documento de la sentencia (versión pública, Word): {r['urlInternet']}")
    if r.get("huellaDigital"):
        partes.append(f"Huella digital (SHA-256): {r['huellaDigital']}")
    partes += [
        f"Fuente: Repositorio de la SCJN, Sistema de Informática Jurídica ({SITIO_SIJ})",
        "",
        "El conector no lee el documento Word: el servidor que lo aloja pide verificación "
        "de navegador. Ábrelo con el enlace. Si la sentencia se publicó en el Semanario, "
        "su texto íntegro también está en buscar_ejecutorias con el número de expediente.",
    ]
    return "\n".join(partes)


def coincide_clave(clave_doc: str, clave_buscada: str) -> bool:
    """Misma regla que el acervo local: sin acentos, espacios ni puntuación."""
    a, b = _sin_espacios(clave_doc), _sin_espacios(clave_buscada)
    return bool(a) and a == b
