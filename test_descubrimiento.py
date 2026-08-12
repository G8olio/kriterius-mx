#!/usr/bin/env python3
"""
Pruebas del freno al auto-descubrimiento de endpoints. Sin red.

El error que arreglan (visto en los registros de producción del 6 de agosto de 2026):
cada consulta a una tesis histórica devolvía 404 en el primer intento —esperado, porque
`ver_tesis` prueba primero la colección en curso— y ese 404 disparaba
`_sjf_descubrir_base`, que se bajaba la página del SJF y sus dos bundles de JavaScript.
Tres peticiones tiradas por cada tesis vieja, y encima el descubrimiento nunca encontraba
nada. En las ejecutorias el disparador era además un 500.

La regla que se comprueba aquí: un código HTTP es una respuesta del API —el endpoint
sigue ahí, lo que no está es el registro—; solo un fallo de conexión o una respuesta que
no sea JSON justifican re-descubrir.

Uso: python3 test_descubrimiento.py
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


# ---- Cliente HTTP falso: registra cada URL pedida y devuelve lo que se le indique ----

VISITAS: list[str] = []


class _Resp:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text

    def json(self):
        return json.loads(self.text)


class _ClienteFalso:
    """Sustituto de httpx.AsyncClient. `guion` es la respuesta que se dará a cualquier
    petición al API; los GET del auto-descubrimiento devuelven un HTML sin rutas, que
    es justo lo que devolvía en producción."""

    guion = _Resp(404, "")

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


class _ClienteCaido(_ClienteFalso):
    """No hay red: ni siquiera se llega a ver un código de respuesta."""

    async def request(self, method, url, json=None):
        VISITAS.append(url)
        raise httpx.ConnectError("sin red")


def _usar(cliente):
    k.httpx.AsyncClient = lambda *a, **kw: cliente()


def _preparar(status_o_texto=404, texto=""):
    """Deja el cliente falso listo y borra caché y registro de visitas."""
    VISITAS.clear()
    k._CACHE._datos.clear()
    if isinstance(status_o_texto, int):
        _ClienteFalso.guion = _Resp(status_o_texto, texto)
    else:
        _ClienteFalso.guion = _Resp(200, status_o_texto)


def _descubrimiento():
    """Las peticiones del auto-descubrimiento son las que van a la página de búsqueda
    del SJF o a sus bundles de JavaScript, no al /services/.../api/public."""
    return [u for u in VISITAS if "/busqueda-principal" in u or u.endswith(".js")]


_original = httpx.AsyncClient
_usar(_ClienteFalso)

print("\n— Tesis —")

# Un 404 es "no existe ese registro", no "el endpoint se movió"
_preparar(404)
try:
    asyncio.run(k._sjf_fetch("/tesis/184518?isSemanal=true"))
    error = None
except Exception as e:
    error = e
comprobar("un 404 NO dispara el auto-descubrimiento",
          not _descubrimiento(), f"pidió {_descubrimiento()}")
comprobar("el 404 llega como _RespuestaHTTP con su código",
          isinstance(error, k._RespuestaHTTP) and error.status == 404, repr(error))
comprobar("solo hubo una petición, la del registro", len(VISITAS) == 1, str(VISITAS))

# El 500 que devuelven algunas ejecutorias tampoco
_preparar(500)
try:
    asyncio.run(k._sjf_fetch("/tesis/34143?isSemanal=true"))
except Exception:
    pass
comprobar("un 500 tampoco lo dispara", not _descubrimiento(), str(_descubrimiento()))

# Una respuesta que no es JSON sí: eso sí huele a API movida
_preparar("<html><h1>Not Found</h1></html>")
try:
    asyncio.run(k._sjf_fetch("/tesis/2012594?isSemanal=true"))
except Exception:
    pass
comprobar("una respuesta no-JSON SÍ lo dispara", bool(_descubrimiento()))

# Un fallo de conexión también
_preparar()
_usar(_ClienteCaido)
try:
    asyncio.run(k._sjf_fetch("/tesis/2012594?isSemanal=true"))
except Exception:
    pass
comprobar("un fallo de conexión SÍ lo dispara", bool(_descubrimiento()))
_usar(_ClienteFalso)

print("\n— Ejecutorias —")

# Misma regla en la otra colección, donde el disparador era el 500
_preparar(500)
try:
    asyncio.run(k._ejec_fetch("/ejecutorias/201074?isSemanal=true"))
    error = None
except Exception as e:
    error = e
comprobar("un 500 NO dispara el auto-descubrimiento",
          not _descubrimiento(), f"pidió {_descubrimiento()}")
comprobar("el 500 llega como _RespuestaHTTP con su código",
          isinstance(error, k._RespuestaHTTP) and error.status == 500, repr(error))

_preparar(404)
try:
    asyncio.run(k._ejec_fetch("/ejecutorias/34143?isSemanal=true"))
except Exception:
    pass
comprobar("un 404 tampoco lo dispara", not _descubrimiento(), str(_descubrimiento()))

_preparar("<html>página del SJF</html>")
try:
    asyncio.run(k._ejec_fetch("/ejecutorias/34143?isSemanal=true"))
except Exception:
    pass
comprobar("una respuesta no-JSON SÍ lo dispara", bool(_descubrimiento()))

print("\n— El caso completo: ver_tesis de una tesis histórica —")

# La secuencia real que ensuciaba los registros: isSemanal=true → 404, false → 200.
# Deben ser dos peticiones, ni una más.
HISTORICA = json.dumps({
    "rubro": "INTERÉS LEGÍTIMO. ALCANCE.",
    "localizacion": "9a. Época; Pleno; S.J.F. y su Gaceta; Tomo XXV; Pág. 1",
    "texto": "Texto de la tesis.",
    "clave": "P./J. 1/2007",
})


class _ClienteHistorica(_ClienteFalso):
    async def request(self, method, url, json=None):
        VISITAS.append(url)
        if "isSemanal=true" in url:
            return _Resp(404, "")
        return _Resp(200, HISTORICA)


_preparar()
_usar(_ClienteHistorica)
salida = asyncio.run(k.ver_tesis(184518))
comprobar("devuelve la tesis histórica", "INTERÉS LEGÍTIMO" in salida, salida[:120])
comprobar("exactamente dos peticiones, isSemanal=true y false",
          len(VISITAS) == 2, str(VISITAS))
comprobar("ninguna a la página del SJF ni a sus bundles",
          not _descubrimiento(), str(_descubrimiento()))

print("\n— Versión —")

import server_http  # noqa: E402  (se importa aquí para no cargar Starlette antes de tiempo)

comprobar("server_http.py toma la versión de kriterius_mx.py",
          server_http.VERSION == k.VERSION, f"{server_http.VERSION} vs {k.VERSION}")
comprobar("estado_conector anuncia esa misma versión",
          f"Versión {k.VERSION}" in asyncio.run(k.estado_conector()))

k.httpx.AsyncClient = _original

print(f"\n{'TODO OK' if fallos == 0 else str(fallos) + ' FALLA(S)'}\n")
sys.exit(0 if fallos == 0 else 1)
