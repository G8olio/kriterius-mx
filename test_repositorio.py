#!/usr/bin/env python3
"""
Pruebas del Repositorio de la SCJN como segunda puerta del Semanario y como fuente
de las sentencias del SIJ. Sin red: sjf2 responde 302 (Imperva, como desde el 3 de
septiembre de 2026) y el Repositorio responde con documentos reales guardados el 3 de
octubre de 2026 en fixtures_repositorio/.

Lo que se comprueba, en orden de importancia:

  1. Con sjf2 bloqueado, ver_tesis y ver_ejecutoria entregan el documento por su
     registro digital, con su huella SHA-256, y dicen de dónde salió y por qué.
  2. Una clave se resuelve a su tesis EXACTA y a su registro digital: la búsqueda del
     Repositorio también trae tesis que solo citan esa clave, y esas no cuentan.
  3. Las búsquedas de tesis y ejecutorias conmutan al Repositorio antes que al acervo
     de la Gaceta, mandan los filtros al servidor y no guardan en caché el texto
     íntegro de las sentencias.
  4. buscar_sentencias_scjn separa "amparo en revisión 93/2026" en filtro y número.
  5. Con sjf2 vivo, el Repositorio no se toca: el dato vivo de siempre gana.
  6. Un id inexistente (HTTP 500 en el Repositorio) no se reintenta ni se confunde con
     un bloqueo, y el cliente se identifica con su propio User-Agent.

Uso: python3 test_repositorio.py
"""

import asyncio
import json
import sys
from pathlib import Path

import kriterius_mx as k
import repositorio_scjn as rs

fallos = 0


def comprobar(nombre, condicion, detalle=""):
    global fallos
    if condicion:
        print(f"  [OK]    {nombre}")
    else:
        print(f"  [FALLA] {nombre}" + (f" — {detalle}" if detalle else ""))
        fallos += 1


FIX = Path(__file__).parent / "fixtures_repositorio"


def fixture(nombre):
    return (FIX / nombre).read_text(encoding="utf-8")


class _Resp:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text

    def json(self):
        return json.loads(self.text)


VISITAS: list[tuple[str, str, dict | None]] = []
CABECERAS: list[dict] = []
SJF_VIVO = {"activo": False}

RESPUESTA_VIVA = json.dumps({"total": 1, "documents": [{
    "ius": 2032523, "ta_tj": 0, "rubro": "RUBRO QUE SOLO EXISTE EN SJF2.",
    "localizacion": "[TA]; 12a. Época; T.C.C.; Gaceta; Libro 1; Pág. 1"}]})

# Lo que contesta el Repositorio, por ruta (GET) o por índice y consulta (POST).
DOCUMENTOS = {
    "/api/v1/tesis/2032726": "tesis_2032726.json",
    "/api/v1/ejecutoria/201074": "ejecutoria_201074.json",
    "/api/v1/engroses/236500": "engrose_236500.json",
}
BUSQUEDAS = {
    # La respuesta guardada es la de la clave como texto libre: trae también dos tesis
    # que solo la citan, que es justo lo que el filtro exacto tiene que descartar.
    ("tesis", 'tesis:"P./J. 9/2016 (10a.)"'): "busqueda_tesis_clave_PJ9-2016.json",
    ("tesis", 'tesis:"P./J. 9/2016"'): "busqueda_tesis_clave_PJ9-2016.json",
    ("tesis", '"interés legítimo"'): "busqueda_tesis_interes_legitimo.json",
    ("ejecutoria", '"interés legítimo"'): "busqueda_ejecutoria_interes_legitimo.json",
    ("engroses", '"93/2026"'): "busqueda_engroses_AR_93-2026.json",
}
REPO_BLOQUEADO = {"activo": False}

ERROR_500 = json.dumps({"title": "Internal Server Error", "status": 500,
                        "message": "error.http.500"})


class _ClienteFalso:
    def __init__(self, kw):
        CABECERAS.append(kw.get("headers") or {})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, json=None):
        VISITAS.append((method, url, json))
        if "sjf2.scjn.gob.mx" in url:
            return _Resp(200, RESPUESTA_VIVA) if SJF_VIVO["activo"] else _Resp(302, "")
        if REPO_BLOQUEADO["activo"]:
            return _Resp(200, '<html><script src="/_Incapsula_Resource?x=1"></script></html>')
        ruta = url.split("repositorio-scjn", 1)[1]
        if method == "GET":
            nombre = DOCUMENTOS.get(ruta)
            return _Resp(200, fixture(nombre)) if nombre else _Resp(500, ERROR_500)
        if json["q"].count('"') % 2:
            # Como el de verdad: una comilla sin cerrar no se interpreta y da total null.
            return _Resp(200, '{"from":null,"fromTo":null,"total":null,"resultados":[],"filtros":[]}')
        nombre = BUSQUEDAS.get((json["indice"], json["q"]))
        if not nombre:
            return _Resp(200, '{"from":0,"fromTo":0,"pagina":1,"size":20,"total":0,'
                              '"totalPaginas":0,"resultados":[],"filtros":[]}')
        return _Resp(200, fixture(nombre))

    async def get(self, url):
        VISITAS.append(("GET", url, None))
        return _Resp(200, "<html><body>sin rutas de servicios</body></html>")


def _preparar(sjf_vivo=False, repo_bloqueado=False):
    VISITAS.clear()
    CABECERAS.clear()
    k._CACHE._datos.clear()
    k._PETICIONES.clear()
    SJF_VIVO["activo"] = sjf_vivo
    REPO_BLOQUEADO["activo"] = repo_bloqueado


def _repo(metodo=None):
    return [v for v in VISITAS if "repositorio-scjn" in v[1] and (not metodo or v[0] == metodo)]


k.httpx.AsyncClient = lambda *a, **kw: _ClienteFalso(kw)
corre = asyncio.run


print("\n— Piezas sin red —")
comprobar("'amparo directo' es AMPARO DIRECTO, no también el directo en revisión",
          rs.resolver_tipo_asunto("amparo directo") == ["AMPARO DIRECTO"])
comprobar("las siglas ADR se resuelven",
          rs.resolver_tipo_asunto("ADR") == ["AMPARO DIRECTO EN REVISIÓN"])
comprobar("'contradicción de tesis' es el nombre nuevo, contradicción de criterios",
          rs.resolver_tipo_asunto("contradicción de tesis")
          == ["CONTRADICCIÓN DE CRITERIOS (ANTES CONTRADICCIÓN DE TESIS)"])
comprobar("un tipo desconocido no inventa filtro", rs.resolver_tipo_asunto("xyzzy") == [])
comprobar("el expediente se separa del tipo",
          rs.desarmar_expediente("amparo en revisión 93/2026") == ("93/2026", "amparo en revisión"))
comprobar("una consulta entrecomillada se desarma igual",
          rs.armar_consulta_sentencias('"amparo en revisión 93/2026"')
          == ('"93/2026"', ["AMPARO EN REVISIÓN"], ""))
comprobar("el expediente ya entrecomillado no se duplica",
          rs.armar_consulta_sentencias('"93/2026"')[0] == '"93/2026"')
comprobar("con tipo explícito, las palabras que no son tipo se conservan",
          rs.armar_consulta_sentencias("multas profeco 93/2026", "AR")
          == ('multas profeco "93/2026"', ["AMPARO EN REVISIÓN"], ""))
comprobar("las épocas se traducen a los nombres del Repositorio",
          rs.filtros_tesis(["11a", "12a"], "jurisprudencia")
          == {"epoca": ["Undécima Época", "Duodécima Época"], "tipoTesis": ["Jurisprudencia"]})
t = rs.tesis_a_sjf(json.loads(fixture("tesis_2032726.json")))
comprobar("los precedentes pierden los separadores vacíos ' | | |'",
          "|" not in t["precedentes"] and t["precedentes"].startswith("Queja 151/2026"),
          repr(t["precedentes"][-40:]))
e = rs.ejecutoria_a_sjf(json.loads(fixture("ejecutoria_201074.json")))
comprobar("la localización traducida se desarma igual que la de sjf2",
          k._ejec_loc(e["localizacion"])[:2] == ("Duodécima Época", "Primera Sala"),
          str(k._ejec_loc(e["localizacion"])))


print("\n— ver_tesis por registro con sjf2 bloqueado —")
_preparar()
r = corre(k.ver_tesis(2032726))
comprobar("entrega la tesis", "SUSPENSIÓN PROVISIONAL" in r and "TEXTO:" in r, r[:200])
comprobar("con su clave y su registro", "III.7o.A.2 K (12a.)" in r and "Registro digital: 2032726" in r)
comprobar("con la huella SHA-256 de la Corte",
          "3e7ee2f23489fc2594812ff03dc938584f4511a5feb9002e045cf587cf7fd2cd" in r)
comprobar("dice que salió del Repositorio y por qué",
          "Repositorio de la SCJN" in r and "cortafuegos" in r, r[-400:])
comprobar("no lleva el aviso de acervo local: es dato vivo", "ACERVO LOCAL" not in r)
comprobar("el link sigue siendo el del Semanario",
          "https://sjf2.scjn.gob.mx/detalle/tesis/2032726" in r)
comprobar("se presenta con su propio User-Agent",
          any("KriteriusMX/" in (c.get("User-Agent") or "") for c in CABECERAS), str(CABECERAS[-1:]))

_preparar()
rn = corre(k.ver_tesis(1999999))
comprobar("un registro inexistente no se confunde con un bloqueo del Repositorio",
          "tampoco la entregó" in rn, rn[:300])
comprobar("y el 500 del Repositorio no se reintenta", len(_repo("GET")) == 1, str(_repo()))


print("\n— ver_tesis por clave —")
_preparar()
rc = corre(k.ver_tesis("P./J. 9/2016 (10a.)"))
comprobar("resuelve la clave a su registro digital", "Registro digital: 2012594" in rc, rc[:200])
comprobar("y SOLO a la tesis con esa clave, no a las que la citan",
          "2024222" not in rc and "2019151" not in rc and "=" * 60 not in rc)
comprobar("busca la clave por su campo, no como texto libre",
          _repo("POST")[0][2]["q"] == 'tesis:"P./J. 9/2016 (10a.)"', str(_repo("POST")[0][2]))
comprobar("dice que salió del Repositorio", "Repositorio de la SCJN" in rc)
comprobar("no tocó sjf2: sjf2 no busca por clave", not [v for v in VISITAS if "sjf2" in v[1]])
_preparar()
rp = corre(k.ver_tesis("P./J. 9/2016"))
comprobar("la clave sin época también la encuentra", "Registro digital: 2012594" in rp, rp[:200])


print("\n— ver_ejecutoria con sjf2 bloqueado —")
_preparar()
re_ = corre(k.ver_ejecutoria(201074, parte=1))
comprobar("entrega la sentencia", "CONTROVERSIA CONSTITUCIONAL 267/2024" in re_, re_[:200])
comprobar("con su órgano", "Órgano: Primera Sala" in re_, re_[:300])
comprobar("por partes", "TEXTO — parte 1 de" in re_)
comprobar("con huella y origen",
          "Huella digital (SHA-256): 2c0ab48a" in re_ and "Repositorio de la SCJN" in re_)
_preparar()
rb = corre(k.ver_ejecutoria(201074, buscar_en_texto="Fiscalía"))
comprobar("la búsqueda dentro del texto funciona igual", "COINCIDENCIAS DE 'Fiscalía'" in rb, rb[:300])
_preparar(repo_bloqueado=True)
rbb = corre(k.ver_ejecutoria(201074))
comprobar("si el Repositorio también bloquea, vuelve el mensaje del WAF",
          "cortafuegos" in rbb and "TEXTO" not in rbb, rbb[:200])


print("\n— buscar_tesis e investigar_criterio con sjf2 bloqueado —")
_preparar()
rt = corre(k.buscar_tesis('"interés legítimo"', tipo="jurisprudencia", epocas=["11a"]))
comprobar("conmuta al Repositorio, no al acervo",
          rt.startswith("Fuente de esta respuesta: Repositorio") and "ACERVO LOCAL" not in rt, rt[:200])
comprobar("con registros y links del Semanario", "https://sjf2.scjn.gob.mx/detalle/tesis/" in rt)
cuerpo = _repo("POST")[0][2]
comprobar("los filtros van al servidor",
          cuerpo["filtros"] == {"epoca": ["Undécima Época"], "tipoTesis": ["Jurisprudencia"]},
          str(cuerpo))
comprobar("el total topado se dice como piso, no como techo",
          "100 o más resultados" in rt and "página siguiente" in rt, rt[:600])
_preparar()
ri = corre(k.investigar_criterio('"interés legítimo"', limite=3))
comprobar("investigar_criterio también conmuta y avisa",
          "Repositorio de la SCJN" in ri and "INVESTIGACIÓN" in ri, ri[:300])
_preparar()
rf = corre(k.buscar_tesis('"interés legítimo"', pagina=6, por_pagina=20))
comprobar("la página 6 se pide al Repositorio: la paginación sigue después del 100",
          _repo("POST")[0][2]["page"] == 6 and "No hay página" not in rf, rf[:300])
_preparar()
rz = corre(k.investigar_criterio("tema que no existe en ningún lado"))
comprobar("investigar_criterio sin resultados también dice de dónde salió",
          rz.startswith("Fuente de esta respuesta: Repositorio") and "Sin criterios" in rz, rz[:200])


print("\n— buscar_ejecutorias con sjf2 bloqueado —")
_preparar()
rj = corre(k.buscar_ejecutorias('"interés legítimo"', por_pagina=3))
comprobar("conmuta al Repositorio", "Repositorio de la SCJN" in rj and "201074" in rj, rj[:300])
comprobar("el fragmento muestra dónde cayó la consulta",
          "Coincidencia: …" in rj and "interés legítimo" in rj.lower(), rj[:900])
pesados = [v for v, _ in k._CACHE._datos.values() if isinstance(v, dict)
           for r in (v.get("resultados") or []) if len(r.get("texto") or "") > 0]
comprobar("la caché no guarda el texto íntegro de las sentencias", not pesados)
_preparar()
BUSQUEDAS[("ejecutoria", "zzqxjvw")] = "busqueda_ejecutoria_interes_legitimo.json"
rjn = corre(k.buscar_ejecutorias("zzqxjvw", por_pagina=3))
comprobar("sin fragmento hallado, los criterios no se presentan como 'Coincidencia'",
          "Coincidencia:" not in rjn and "201074" in rjn, rjn[:900])


print("\n— Sentencias del SIJ —")
_preparar()
rsij = corre(k.buscar_sentencias_scjn("amparo en revisión 93/2026"))
cuerpo = _repo("POST")[0][2]
comprobar("separa el tipo de asunto en filtro y el número en consulta",
          cuerpo["q"] == '"93/2026"' and cuerpo["filtros"] == {"tipoAsunto": ["AMPARO EN REVISIÓN"]},
          str(cuerpo))
comprobar("la ficha trae asunto, ponente, fecha e id",
          "[Amparo en revisión 93/2026], [PLENO], [Ponente: GIOVANNI AZAEL FIGUEROA MEJÍA], "
          "[Resuelto el 26/05/2026], [Id SIJ 236500]" in rsij, rsij[:600])
comprobar("y el enlace al documento", "https://www2.scjn.gob.mx/juridica/engroses/" in rsij)
comprobar("el tema no arrastra las iniciales de captura", "JOVT/izso" not in rsij)
_preparar()
corre(k.buscar_sentencias_scjn("93/2026", tipo_asunto="AR", organo="Pleno", anio=2026))
cuerpo = _repo("POST")[0][2]
comprobar("siglas, órgano y año también van como filtros",
          cuerpo["filtros"] == {"tipoAsunto": ["AMPARO EN REVISIÓN"], "pertenencia": ["PLENO"],
                                "anio": ["2026"]}, str(cuerpo))
_preparar()
rv = corre(k.ver_sentencia_scjn(236500))
comprobar("la ficha completa trae resolutivos íntegros y votación",
          "RESOLUTIVOS:" in rv and "CUARTO. Se reserva jurisdicción" in rv and "VOTACIÓN:" in rv)
comprobar("y la huella digital", "a4451fb382817168c1e690c19e48fb476547ca13a868f65fb65447f6fb7d912e" in rv)
_preparar()
rx = corre(k.ver_sentencia_scjn(1))
comprobar("un id inexistente lo dice y orienta", rx.startswith("No existe una sentencia"), rx[:150])
_preparar()
rq = corre(k.buscar_sentencias_scjn('"amparo en revisión 93/2026"'))
comprobar("la consulta entrecomillada encuentra el asunto", "[Id SIJ 236500]" in rq, rq[:300])
_preparar()
rmal = corre(k.buscar_sentencias_scjn('"multas profeco'))
comprobar("una comilla sin cerrar se explica, no se dice 'sin sentencias'",
          "no pudo interpretar" in rmal and "Sin sentencias" not in rmal, rmal[:200])
_preparar(repo_bloqueado=True)
rbq = corre(k.buscar_sentencias_scjn("amparo en revisión 93/2026"))
comprobar("con el Repositorio bloqueado lo explica y da la página oficial",
          "cortafuegos" in rbq and rs.SITIO_SIJ in rbq, rbq[:300])


print("\n— Con sjf2 vivo el Repositorio no se toca —")
_preparar(sjf_vivo=True)
rvv = corre(k.buscar_tesis("interés legítimo"))
comprobar("responde sjf2", "RUBRO QUE SOLO EXISTE EN SJF2" in rvv, rvv[:200])
comprobar("sin aviso de Repositorio", "Repositorio" not in rvv)
comprobar("y sin petición al Repositorio", not _repo(), str(_repo()))


print("\n— Registro de tools —")
nombres = {t.name for t in corre(k.mcp.list_tools())}
comprobar("buscar_sentencias_scjn y ver_sentencia_scjn registradas",
          {"buscar_sentencias_scjn", "ver_sentencia_scjn"} <= nombres)

print(f"\n{'TODO BIEN' if not fallos else f'{fallos} FALLA(S)'}")
sys.exit(1 if fallos else 0)
