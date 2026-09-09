#!/usr/bin/env python3
"""
Pruebas de la conducta del conector cuando el WAF de la SCJN (Imperva Incapsula, delante
de sjf2.scjn.gob.mx desde el 3 de septiembre de 2026) rechaza la petición. Sin red.

Lo que se vio en producción el 4 de septiembre de 2026, desde el servidor remoto:
`buscar_tesis` devolvía el crudo "Error executing tool buscar_tesis: respondió 302",
`ver_tesis` decía "No se encontró la tesis con registro digital N" —un falso negativo
con apariencia de verificación— y el diagnóstico sugería "re-mapear el API", que era el
remedio equivocado: el endpoint no cambió, lo que hay es un cortafuegos delante.

Reglas que se comprueban aquí:
  1. Un 302/403 del SJF, o su página HTML de verificación, se reporta como bloqueo del
     WAF con un mensaje que explica qué pasó y qué hacer.
  2. Ese bloqueo NO se reintenta (no es pasajero) NI dispara el auto-descubrimiento.
  3. `ver_tesis` y `ver_ejecutoria` no lo confunden con "no se encontró" y no gastan
     el segundo intento de isSemanal.
  4. Un 404 sigue significando "no existe ese registro" (no se rompió la regla vieja).
  5. Las etapas de `investigar_criterio` usan los rangos reales de `_rango_organo`.

Uso: python3 test_waf.py
"""

import asyncio
import json
import sys

import httpx

import kriterius_mx as k

fallos = 0


def comprobar(nombre, condicion, detalle=""):
    global fallos
    if condicion:
        print(f"  [OK]    {nombre}")
    else:
        print(f"  [FALLA] {nombre}" + (f" — {detalle}" if detalle else ""))
        fallos += 1


VISITAS: list[str] = []

PAGINA_INCAPSULA = (
    '<html><head><META NAME="robots" CONTENT="noindex,nofollow">'
    '<script src="/_Incapsula_Resource?SWJIYLWA=5074a744e2e3d891814e9a2dace20bd4,719d34d31c8e3a6e6fffd425f7e032f3">'
    '</script></head><body></body></html>'
)

RESPUESTA_BUSQUEDA = json.dumps({
    "total": 1,
    "documents": [{
        "ius": 2032523, "ta_tj": 0,
        "rubro": "INTERÉS LEGÍTIMO EN AMPARO INDIRECTO.",
        "localizacion": "12a. Época; T.C.C.; Gaceta; Libro 1; Pág. 1; [TA]",
    }],
})


class _Resp:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text

    def json(self):
        return json.loads(self.text)


class _ClienteFalso:
    guion = _Resp(302, "")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, json=None):
        VISITAS.append(url)
        return type(self).guion

    async def get(self, url):
        VISITAS.append(url)
        return _Resp(200, "<html><body>sin rutas de servicios</body></html>")


def _preparar(status, texto=""):
    VISITAS.clear()
    k._CACHE._datos.clear()
    k._PETICIONES.clear()
    _ClienteFalso.guion = _Resp(status, texto)


def _descubrimiento():
    return [u for u in VISITAS if "/busqueda-principal" in u or u.endswith(".js")]


def _api():
    return [u for u in VISITAS if "/api/public" in u]


k.httpx.AsyncClient = lambda *a, **kw: _ClienteFalso()

print("\n— Clasificación de la respuesta —")

for status in (302, 403):
    _preparar(status)
    try:
        asyncio.run(k._sjf_fetch("/tesis?page=0&size=1", method="POST", json_body={}))
        error = None
    except Exception as e:
        error = e
    comprobar(f"un {status} llega como _BloqueoWAF",
              isinstance(error, k._BloqueoWAF) and error.status == status, repr(error))
    comprobar(f"el {status} no se reintenta: una sola petición",
              len(_api()) == 1, str(VISITAS))
    comprobar(f"el {status} no dispara el auto-descubrimiento",
              not _descubrimiento(), str(_descubrimiento()))
    comprobar(f"el mensaje del {status} explica el bloqueo y qué hacer",
              error is not None and "Imperva" in str(error) and "escritorio" in str(error)
              and "re-mapear" not in str(error), str(error)[:200])

_preparar(200, PAGINA_INCAPSULA)
try:
    asyncio.run(k._sjf_fetch("/tesis?page=0&size=1", method="POST", json_body={}))
    error = None
except Exception as e:
    error = e
comprobar("la página HTML de Incapsula con 200 también es _BloqueoWAF",
          isinstance(error, k._BloqueoWAF), repr(error))
comprobar("y tampoco dispara el auto-descubrimiento",
          not _descubrimiento(), str(_descubrimiento()))
comprobar("el mensaje dice que llegó la página de verificación",
          error is not None and "página de verificación" in str(error), str(error)[:200])

# La regla anterior sigue viva: un HTML cualquiera sí huele a API movida
_preparar(200, "<html><h1>Not Found</h1></html>")
try:
    asyncio.run(k._sjf_fetch("/tesis?page=0&size=1", method="POST", json_body={}))
except Exception:
    pass
comprobar("un HTML sin huella de Incapsula sigue disparando el auto-descubrimiento",
          bool(_descubrimiento()))

# Y el 404 sigue siendo "no existe ese registro"
_preparar(404)
try:
    asyncio.run(k._sjf_fetch("/tesis/184518?isSemanal=true"))
    error = None
except Exception as e:
    error = e
comprobar("un 404 sigue siendo _RespuestaHTTP, no bloqueo",
          isinstance(error, k._RespuestaHTTP) and not isinstance(error, k._BloqueoWAF),
          repr(error))

print("\n— _es_pasajero decide por código, no por texto —")

comprobar("un _BloqueoWAF 302 no es pasajero", not k._es_pasajero(k._BloqueoWAF(302)))
comprobar("un _BloqueoWAF 403 no es pasajero", not k._es_pasajero(k._BloqueoWAF(403)))
comprobar("un 503 sí es pasajero", k._es_pasajero(k._RespuestaHTTP(503)))
comprobar("un 429 sí es pasajero", k._es_pasajero(k._RespuestaHTTP(429)))
comprobar("un 404 no es pasajero", not k._es_pasajero(k._RespuestaHTTP(404)))
comprobar("un timeout sigue siendo pasajero",
          k._es_pasajero(httpx.ReadTimeout("lento")))

print("\n— Las tools —")

_preparar(302)
salida = asyncio.run(k.buscar_tesis("interés legítimo"))
comprobar("buscar_tesis devuelve el aviso como contenido, no una excepción",
          "Imperva" in salida and "respondió 302" not in salida, salida[:160])

_preparar(403)
salida = asyncio.run(k.investigar_criterio("interés legítimo"))
comprobar("investigar_criterio también", "Imperva" in salida, salida[:160])

_preparar(302)
salida = asyncio.run(k.buscar_ejecutorias("interés legítimo"))
comprobar("buscar_ejecutorias también", "Imperva" in salida, salida[:160])
comprobar("y menciona la colección de ejecutorias", "ejecutorias" in salida, salida[:160])

_preparar(302)
salida = asyncio.run(k.ver_tesis(2012594))
comprobar("ver_tesis NO dice 'No se encontró' ante un bloqueo",
          "No se encontró" not in salida and "Imperva" in salida, salida[:160])
comprobar("ver_tesis no gasta el segundo isSemanal contra el WAF",
          len(_api()) == 1, str(VISITAS))

_preparar(403)
salida = asyncio.run(k.ver_ejecutoria(201074))
comprobar("ver_ejecutoria NO dice 'No se encontró' ante un bloqueo",
          "No se encontró" not in salida and "Imperva" in salida, salida[:160])
comprobar("ver_ejecutoria tampoco gasta el segundo isSemanal", len(_api()) == 1, str(VISITAS))

# Con el API sano todo sigue igual
_preparar(200, RESPUESTA_BUSQUEDA)
salida = asyncio.run(k.buscar_tesis("interés legítimo"))
comprobar("con el API sano buscar_tesis sigue devolviendo resultados",
          "2032523" in salida and "Imperva" not in salida, salida[:160])

print("\n— Etapas de investigar_criterio alineadas con _rango_organo —")

# Se leen las etapas del código fuente: son una constante local de la función.
import inspect
import re as _re
fuente = inspect.getsource(k.investigar_criterio)
etapas = _re.findall(r'\("Jurisprudencia (?:del|de la|de) ([^"]+)", (\d+), True\)', fuente)
esperado = {
    "Pleno de la SCJN": 0, "Primera Sala": 1, "Segunda Sala": 2,
    "Plenos Regionales": 4, "Plenos de Circuito": 5, "Tribunales Colegiados": 6,
}
comprobar("hay una etapa por cada órgano que emite jurisprudencia",
          {n for n, _ in etapas} == set(esperado), str(etapas))
for nombre, rango in etapas:
    comprobar(f"la etapa '{nombre}' usa el rango {esperado.get(nombre)}",
              int(rango) == esperado.get(nombre), f"tiene {rango}")
# Y cada rango corresponde de verdad a ese órgano en _rango_organo
comprobar("Plenos Regionales → 4",
          k._rango_organo("12a. Época; Plenos Regionales; Gaceta; [J]") == 4)
comprobar("Plenos de Circuito → 5",
          k._rango_organo("10a. Época; Plenos de Circuito; Gaceta; [J]") == 5)
comprobar("Tribunales Colegiados → 6",
          k._rango_organo("12a. Época; T.C.C.; Gaceta; [J]") == 6)

print("\n— El diagnóstico —")
comprobar("la guía del diagnóstico menciona el bloqueo del SJF por Imperva",
          "Imperva" in inspect.getsource(k.diagnosticar_conector)
          and "cortafuegos" in inspect.getsource(k.diagnosticar_conector))

print()
if fallos:
    print(f"FALLARON {fallos} comprobaciones")
    sys.exit(1)
print("TODO OK")
