#!/usr/bin/env python3
"""
Pruebas de las tools de derecho estadounidense en el conector REMOTO. Sin red.

Gemela de sjf-mcpb/test-courtlistener.js en lo que toca al formato de salida, con
una parte que la versión local no necesita y que aquí es lo más importante: el
**llavero por conversación**.

En el conector remoto la llave la pega el usuario en el chat y vive en memoria
atada al objeto de sesión MCP. Eso obliga a comprobar tres cosas que, si fallan,
son un incidente de seguridad y no un error de formato:

  1. Dos conversaciones distintas NO comparten llave.
  2. La llave desaparece cuando la sesión muere (referencias débiles, no `id()`,
     que se recicla y podría heredarle la llave de alguien más a otra sesión).
  3. La llave NUNCA aparece en la salida de ninguna tool ni en un mensaje de error.

Uso: python3 test_eeuu.py
"""

import asyncio
import gc
import sys

import kriterius_mx as k

fallos = 0


def comprobar(nombre, condicion, detalle=""):
    global fallos
    if condicion:
        print(f"  [OK]    {nombre}")
    else:
        print(f"  [FALLA] {nombre}" + (f" — {detalle}" if detalle else ""))
        fallos += 1


LLAVE = "llave-secreta-de-prueba-abc123"
OTRA_LLAVE = "llave-de-otra-persona-xyz789"


class _Sesion:
    """Sustituto del objeto de sesión MCP: lo único que importa es que sea un
    objeto distinto por conversación y que admita referencias débiles."""


def _usar_sesion(s):
    k._cl_sesion = lambda: s


# ---- Fixtures del API ----

BUSQUEDA = {
    "count": 2343,
    "next": "https://www.courtlistener.com/api/rest/v4/search/?cursor=CURSOR123&q=foo",
    "results": [
        {
            "absolute_url": "/opinion/118144/obergefell-v-hodges/",
            "caseName": "Obergefell v. Hodges",
            "citation": ["576 U.S. 644", "135 S. Ct. 2584"],
            "citeCount": 4211, "cluster_id": 118144,
            "court": "Supreme Court of the United States",
            "court_citation_string": "U.S.", "court_id": "scotus",
            "dateFiled": "2015-06-26", "docketNumber": "14-556", "status": "Published",
            "opinions": [
                {"id": 9001, "type": "lead-opinion",
                 "snippet": "The Fourteenth Amendment <mark>requires</mark> a State to license a marriage."},
                {"id": 9002, "type": "dissent", "snippet": "I write separately."},
            ],
        },
        {
            "absolute_url": "/opinion/222/doe/", "caseName": "Doe v. Roe",
            "citation": [], "citeCount": 0, "cluster_id": 222,
            "court": "United States Court of Appeals for the Ninth Circuit",
            "court_citation_string": "9th Cir.", "court_id": "ca9",
            "dateFiled": "2021-03-02", "status": "Unpublished",
            "opinions": [{"id": 9100, "type": "lead-opinion", "snippet": ""}],
        },
    ],
}

CLUSTER = {
    "id": 118144, "absolute_url": "/opinion/118144/obergefell-v-hodges/",
    "case_name": "Obergefell v. Hodges",
    "case_name_full": "James Obergefell, et al. v. Richard Hodges",
    "date_filed": "2015-06-26", "precedential_status": "Published",
    "judges": "Kennedy", "citation_count": 4211,
    "citations": [
        {"volume": 576, "reporter": "U.S.", "page": "644", "type": 1},
        {"volume": 135, "reporter": "S. Ct.", "page": "2584", "type": 2},
    ],
    "sub_opinions": ["https://www.courtlistener.com/api/rest/v4/opinions/9001/"],
}

CUERPO = "El derecho a contraer matrimonio. " * 900
OPINION = {
    "id": 9001, "absolute_url": "/opinion/118144/obergefell-v-hodges/",
    "cluster": "https://www.courtlistener.com/api/rest/v4/clusters/118144/",
    "type": "lead-opinion", "html_with_citations": f"<p>{CUERPO}</p>",
}

CITA_OK = [{
    "citation": "576 U.S. 644", "normalized_citations": ["576 U.S. 644"],
    "status": 200, "error_message": "",
    "clusters": [{"case_name": "Obergefell v. Hodges",
                  "case_name_full": "Obergefell v. Hodges",
                  "date_filed": "2015-06-26",
                  "absolute_url": "/opinion/118144/obergefell-v-hodges/"}],
}]

CITAS_MIXTAS = CITA_OK + [
    {"citation": "999 U.S. 1", "normalized_citations": ["999 U.S. 1"], "status": 404,
     "error_message": "Citation not found: '999 U.S. 1'", "clusters": []},
    {"citation": "33 Umbrella 422", "normalized_citations": ["33 Umbrella 422"],
     "status": 400, "error_message": "Reporter not found", "clusters": []},
    {"citation": "1 H. 150", "normalized_citations": ["1 Handy 150", "1 Haw. 150"],
     "status": 300, "error_message": "",
     "clusters": [{"case_name": "Louis v. Steamboat Buckeye", "absolute_url": "/opinion/1/l/"},
                  {"case_name": "Fell v. Parke", "absolute_url": "/opinion/2/f/"}]},
    {"citation": "410 U.S. 113", "normalized_citations": ["410 U.S. 113"], "status": 429,
     "error_message": "Too many citations requested.", "clusters": []},
]


PETICIONES = []


def _simular(respuestas):
    """Sustituye _cl_fetch registrando ruta, forma y llave usada."""
    async def falso(ruta, method="GET", form=None, llave=None):
        PETICIONES.append({"ruta": ruta, "method": method, "form": form,
                           "llave": llave if llave is not None else k._cl_llave()})
        if callable(respuestas):
            return respuestas(ruta, form)
        return respuestas
    k._cl_fetch = falso


_original_fetch = k._cl_fetch
_original_sesion = k._cl_sesion


def por_defecto(ruta, form=None):
    if ruta.startswith("/search/"):
        return BUSQUEDA
    if ruta.startswith("/clusters/"):
        return CLUSTER
    if ruta.startswith("/opinions/"):
        return OPINION
    if ruta.startswith("/citation-lookup/"):
        return CITA_OK
    return None


print("\n— El llavero por conversación —")

sesion_a, sesion_b = _Sesion(), _Sesion()

_usar_sesion(sesion_a)
_simular(por_defecto)
r = asyncio.run(k.configurar_courtlistener(LLAVE))
comprobar("guarda la llave tras comprobarla contra el API", "guardada" in r, r[:80])
comprobar("la comprueba con el endpoint de citas, que tiene cupo aparte",
          PETICIONES[-1]["ruta"] == "/citation-lookup/", str(PETICIONES[-1]["ruta"]))
comprobar("NO repite la llave en la respuesta", LLAVE not in r)
comprobar("la llave quedó disponible para esa conversación", k._cl_llave() == LLAVE)

_usar_sesion(sesion_b)
comprobar("otra conversación NO ve la llave de la primera", k._cl_llave() == "",
          repr(k._cl_llave()))

PETICIONES.clear()
r = asyncio.run(k.configurar_courtlistener(OTRA_LLAVE))
comprobar("cada conversación guarda la suya", k._cl_llave() == OTRA_LLAVE)
_usar_sesion(sesion_a)
comprobar("y la primera conserva la suya, sin pisarse", k._cl_llave() == LLAVE)

# La razón de usar referencias débiles y no id(): id() se recicla.
vivas_antes = len(k._CL_LLAVES)
del sesion_b
gc.collect()
comprobar("al morir una conversación su llave desaparece del servidor",
          len(k._CL_LLAVES) == vivas_antes - 1, f"{len(k._CL_LLAVES)} vs {vivas_antes - 1}")

_usar_sesion(sesion_a)
r = asyncio.run(k.configurar_courtlistener(""))
comprobar("una cadena vacía borra la llave", "borrada" in r and k._cl_llave() == "")

print("\n— Sin llave —")

_usar_sesion(_Sesion())
comprobar("ayuda_derecho_eeuu funciona sin llave y sin red",
          "DERECHO ESTADOUNIDENSE" in asyncio.run(k.ayuda_derecho_eeuu()))
ayuda = asyncio.run(k.ayuda_derecho_eeuu())
comprobar("la ayuda dice si ya hay llave en esta conversación", "configurada en esta conversación: NO" in ayuda)
comprobar("la ayuda explica dónde sacarla", "profile/api-token" in ayuda)
comprobar("la ayuda advierte que la llave queda en el historial del chat",
          "historial del chat" in ayuda and "credencial" in ayuda)
comprobar("la ayuda ofrece la alternativa de escritorio, sin chat de por medio",
          "llavero del sistema" in ayuda)
comprobar("la ayuda aclara que México no necesita llave",
          "no necesitan llave" in ayuda)

k._cl_fetch = _original_fetch
try:
    asyncio.run(k.buscar_casos_eeuu("economic loss rule"))
    err = ""
except RuntimeError as e:
    err = str(e)
comprobar("sin llave, la búsqueda explica por qué no hay una compartida",
          "se agotaría" in err, err[:90])
comprobar("sin llave, el mensaje dice cómo conseguirla", "profile/api-token" in err)

print("\n— Búsqueda —")

_usar_sesion(sesion_a)
_simular(por_defecto)
asyncio.run(k.configurar_courtlistener(LLAVE))
PETICIONES.clear()

s = asyncio.run(k.buscar_casos_eeuu("same-sex marriage", tribunal="scotus", orden="citas"))
comprobar("arma la cita como la leería un abogado",
          "Obergefell v. Hodges, 576 U.S. 644 (U.S. 2015)" in s, s.split("\n")[2])
comprobar("pasa tribunal y orden al API",
          "court=scotus" in PETICIONES[-1]["ruta"] and "order_by=dateFiled+desc" not in PETICIONES[-1]["ruta"]
          and "citeCount" in PETICIONES[-1]["ruta"], PETICIONES[-1]["ruta"])
comprobar("marca las decisiones NO publicadas", "⚠ Estado: Unpublished" in s)
comprobar("limpia el <mark> del fragmento", "requires a State" in s and "<mark>" not in s)
comprobar("prefiere la mayoritaria sobre el voto disidente",
          "Fourteenth Amendment" in s and "I write separately" not in s)
comprobar("da el link completo", "https://www.courtlistener.com/opinion/118144/" in s)
comprobar("dice cómo seguir cada caso",
          "ver_caso_eeuu(id_cluster=118144)" in s and "quien_cita_eeuu(id_opinion=9001)" in s)
comprobar("ofrece el cursor de la página siguiente", 'cursor="CURSOR123"' in s)

s2 = asyncio.run(k.buscar_casos_eeuu("x", semantica=True, incluir_no_publicadas=True,
                                     desde="2020-01-01"))
comprobar("activa la búsqueda semántica", "semantic=true" in PETICIONES[-1]["ruta"])
comprobar("incluye las no publicadas cuando se piden", "stat_Unpublished=on" in PETICIONES[-1]["ruta"])
comprobar("traduce 'desde' a filed_after", "filed_after=2020-01-01" in PETICIONES[-1]["ruta"])

print("\n— Texto de la decisión —")

PETICIONES.clear()
s = asyncio.run(k.ver_caso_eeuu(id_cluster=118144))
comprobar("pide primero el cluster y luego la opinión",
          PETICIONES[0]["ruta"] == "/clusters/118144/" and PETICIONES[1]["ruta"] == "/opinions/9001/",
          str([p["ruta"] for p in PETICIONES]))
comprobar("encabeza con el nombre completo", "James Obergefell" in s)
comprobar("arma las citas desde volumen, repertorio y página",
          "576 U.S. 644" in s and "135 S. Ct. 2584" in s)
comprobar("entrega el texto por partes", "TEXTO — parte 1 de" in s)
comprobar("saca el texto de html_with_citations y lo limpia",
          "El derecho a contraer matrimonio" in s and "<p>" not in s)

s = asyncio.run(k.ver_caso_eeuu(id_cluster=118144, buscar_en_texto="matrimonio"))
comprobar("devuelve coincidencias en vez de una parte",
          'COINCIDENCIAS DE "matrimonio"' in s and "TEXTO — parte" not in s)
# El tercer argumento de _ejec_coincidencias es el MÁXIMO de extractos, no el tamaño
# del contexto: pasarle EJEC_CONTEXTO devolvía cientos de fragmentos.
comprobar("los extractos están acotados, no son cientos",
          s.count("…\n\n") <= 8, f"{s.count('…')} separadores")

s = asyncio.run(k.ver_caso_eeuu(id_cluster=118144, buscar_en_texto="usucapión"))
comprobar("avisa cuando el término no aparece", "no aparece en el cuerpo" in s)

_simular(lambda ruta, form=None: {**OPINION, "html_with_citations": ""}
         if ruta.startswith("/opinions/") else CLUSTER)
s = asyncio.run(k.ver_caso_eeuu(id_cluster=118144))
comprobar("cuando no hay texto, lo dice y manda al link",
          "no tiene el texto de esta decisión" in s)

print("\n— Quién cita —")

_simular(por_defecto)
PETICIONES.clear()
asyncio.run(k.quien_cita_eeuu(id_opinion=9001))
comprobar("usa el operador cites: con el id de opinión",
          "cites" in PETICIONES[-1]["ruta"] and "9001" in PETICIONES[-1]["ruta"])
comprobar("ordena de la más reciente a la más antigua",
          "order_by=dateFiled+desc" in PETICIONES[-1]["ruta"], PETICIONES[-1]["ruta"])

PETICIONES.clear()
asyncio.run(k.quien_cita_eeuu(id_cluster=118144))
comprobar("desde un cluster, primero resuelve sus opiniones",
          PETICIONES[0]["ruta"] == "/clusters/118144/")

_simular({"count": 0, "results": []})
s = asyncio.run(k.quien_cita_eeuu(id_opinion=1))
comprobar("sin citas, no concluye que el criterio esté muerto", "no prueba" in s)

_simular(por_defecto)
s = asyncio.run(k.quien_cita_eeuu(id_opinion=9001))
comprobar("advierte que no es un citador con señales",
          "no si lo sigue, lo distingue o lo revoca" in s)

print("\n— Verificador de citas —")

_simular(lambda ruta, form=None: CITAS_MIXTAS)
s = asyncio.run(k.verificar_citas_eeuu("Ver 576 U.S. 644 y 999 U.S. 1."))
comprobar("cuenta bien el resumen",
          "5 cita(s) detectada(s): 1 verificada(s), 2 sin respaldo, 1 ambigua(s), 1 sin revisar" in s,
          s.split("\n")[0])
comprobar("pone al frente lo que hay que revisar",
          "REVISAR ANTES DE PRESENTAR: 999 U.S. 1 · 33 Umbrella 422" in s)
comprobar("dice que la inexistente no existe", "✗ 999 U.S. 1" in s and "NO EXISTE" in s)
comprobar("distingue repertorio inválido de cita inexistente",
          "el repertorio citado no es válido" in s)
comprobar("muestra las dos posibilidades de la ambigua",
          "AMBIGUA" in s and "Steamboat Buckeye" in s and "Fell v. Parke" in s)
comprobar("normaliza y lo enseña", "1 H. 150  →  1 Handy 150 / 1 Haw. 150" in s)
comprobar("advierte que verificada no es 'dice lo que afirmo'",
          "NO significa que diga lo que el escrito afirma" in s)

_simular(lambda ruta, form=None: [])
s = asyncio.run(k.verificar_citas_eeuu("Un texto sin citas."))
comprobar("sin citas, explica qué reconoce y qué no",
          "No se encontró ninguna cita" in s and "id. o supra" in s)

_simular(lambda ruta, form=None: [])
asyncio.run(k.verificar_citas_eeuu("x" * 80000))
comprobar("recorta el texto al máximo que acepta el API",
          len(PETICIONES[-1]["form"]["text"]) == k.CL_MAX_TEXTO_CITAS,
          str(len(PETICIONES[-1]["form"]["text"])))

print("\n— La llave no se filtra por ningún lado —")

_simular(por_defecto)
salidas = [
    asyncio.run(k.buscar_casos_eeuu("marriage")),
    asyncio.run(k.ver_caso_eeuu(id_cluster=118144)),
    asyncio.run(k.quien_cita_eeuu(id_opinion=9001)),
    asyncio.run(k.verificar_citas_eeuu("576 U.S. 644")),
    asyncio.run(k.ayuda_derecho_eeuu()),
    asyncio.run(k.estado_conector()),
]
comprobar("ninguna tool repite la llave en su salida",
          all(LLAVE not in x for x in salidas))
comprobar("estado_conector no delata siquiera si hay llave puesta",
          "llave" not in salidas[-1].lower() or LLAVE not in salidas[-1])

k._cl_fetch = _original_fetch
comprobar("la llave no se lee de ninguna variable de entorno",
          "COURTLISTENER_TOKEN" not in open("kriterius_mx.py", encoding="utf-8").read())

fuente = open("kriterius_mx.py", encoding="utf-8").read()
comprobar("el llavero usa referencias débiles, no id()",
          "WeakKeyDictionary" in fuente and "_CL_LLAVES[id(" not in fuente)
comprobar("la llave nunca se escribe a disco",
          "_CL_LLAVES" in fuente and "open(" not in fuente.split("_CL_LLAVES")[1][:4000])
comprobar("el mensaje de cupo agotado nombra los límites que quedan por esperar",
          "50 peticiones por" in fuente and "125 al día" in fuente)
comprobar("una llave rechazada se distingue del cupo agotado",
          "CourtListener rechazó la llave" in fuente)

print("\n— Paridad con la versión local —")

import json as _json_
import pathlib as _pl_
mcpb = _pl_.Path(__file__).resolve().parent.parent / "sjf-mcpb" / "manifest.json"
if mcpb.exists():
    locales = {t["name"] for t in _json_.loads(mcpb.read_text())["tools"]}
    remotas = {t.name for t in asyncio.run(k.mcp.list_tools())}
    consulta = {"buscar_casos_eeuu", "ver_caso_eeuu", "quien_cita_eeuu", "verificar_citas_eeuu"}
    comprobar("las cuatro tools de consulta existen en las dos versiones",
              consulta <= locales and consulta <= remotas)
    comprobar("las de la llave por chat son SOLO del remoto: en local va en el llavero del sistema",
              {"ayuda_derecho_eeuu", "configurar_courtlistener"} & locales == set())
else:
    print("  [—]     manifest.json local no encontrado; se omite la paridad")


print("\n— Llave pegada con ruido —")

comprobar("el marcador del manifiesto sin sustituir se trata como AUSENTE",
          k._cl_limpiar_llave("${user_config.courtlistener_token}") == "")
comprobar("los espacios de sobra se limpian", k._cl_limpiar_llave("  abc123  ") == "abc123")
comprobar("si pegan 'Token abc123' se guarda solo el token",
          k._cl_limpiar_llave("Token abc123") == "abc123"
          and k._cl_limpiar_llave("token abc123") == "abc123")
comprobar("una llave normal pasa intacta", k._cl_limpiar_llave("a1b2c3d4e5") == "a1b2c3d4e5")

_usar_sesion(_Sesion())
_simular(por_defecto)
r = asyncio.run(k.configurar_courtlistener("${user_config.courtlistener_token}"))
comprobar("configurar con el marcador no lo guarda ni sale a la red",
          "borrada" in r and k._cl_llave() == "", r[:70])



print("\n— Lo que salió de la primera prueba real —")

# 1) Extractos solapados: el término repetido en párrafos vecinos devolvía ocho
#    recortes que eran casi el mismo párrafo. Se comprueba que se fundan.
seguidas = "Preludio. " + ("La regla de la pérdida económica aplica aquí. " * 6) + " Epílogo."
tramos = k._ejec_coincidencias(seguidas, "pérdida económica")
comprobar("las apariciones vecinas se funden en un solo extracto",
          len(tramos) == 1, f"{len(tramos)} extractos")
lejos = "pérdida económica" + ("x" * 5000) + "pérdida económica"
comprobar("las apariciones lejanas siguen separadas",
          len(k._ejec_coincidencias(lejos, "pérdida económica")) == 2)
comprobar("ningún extracto se repite dentro del resultado",
          len(set(tramos)) == len(tramos))

# 2) El 429 de 5/min no debe anunciarse como cupo diario agotado
import re as _re
fuente_k = open("kriterius_mx.py", encoding="utf-8").read()
comprobar("el mensaje distingue el límite por minuto del diario",
          "5 peticiones por minuto" in fuente_k and "Se libera en" in fuente_k)
comprobar("el aviso por minuto aclara que el cupo diario sigue vivo",
          "No se agotó tu cupo diario" in fuente_k)

# 3) Clusters duplicados de la misma decisión
DUP = {"count": 4, "results": [
    dict(BUSQUEDA["results"][0]),
    dict(BUSQUEDA["results"][0], cluster_id=999, absolute_url="/opinion/999/otra/", citeCount=3),
    dict(BUSQUEDA["results"][1]),
]}
_usar_sesion(sesion_a)
_simular(lambda ruta, form=None: DUP)
s = asyncio.run(k.buscar_casos_eeuu("x"))
comprobar("colapsa la misma decisión publicada dos veces",
          "1 repetidos omitidos" in s, s.split(chr(10))[0])
comprobar("de las copias conserva la de más citas", "4211" in s and "Citada por 3 " not in s)

# 4) Nombres larguísimos de asuntos consolidados
largo = {"case_name": "Obergefell v. Hodges",
         "case_name_full": "James Obergefell, Et Al., Petitioners v. Richard Hodges, " * 4}
comprobar("prefiere el nombre corto cuando el completo es kilométrico",
          k._cl_nombre(largo) == "Obergefell v. Hodges")
comprobar("usa el completo cuando es razonable",
          k._cl_nombre({"case_name": "Roe v. Wade",
                        "case_name_full": "Roe et al. v. Wade, District Attorney"})
          == "Roe et al. v. Wade, District Attorney")

# 5) La consulta ya no sale con comillas duplicadas
_simular(lambda ruta, form=None: {"count": 0, "results": []})
s = asyncio.run(k.buscar_casos_eeuu('"economic loss rule"'))
comprobar("no duplica las comillas de la consulta", '""economic' not in s, s.split(chr(10))[0])


k._cl_sesion = _original_sesion
print(f"\n{'TODO OK' if fallos == 0 else str(fallos) + ' FALLA(S)'}\n")
sys.exit(0 if fallos == 0 else 1)
