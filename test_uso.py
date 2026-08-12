#!/usr/bin/env python3
"""
Pruebas de la medición de uso. Sin red: se habla con la app ASGI en memoria.

Lo que se comprueba, además de que los números salgan bien:

- Que el middleware **no estorbe**: el cuerpo de la petición tiene que llegar intacto al
  transporte MCP después de haberlo leído para contar.
- Que una sesión que solo lista herramientas y nunca llama ninguna quede clasificada
  aparte. Esa es la métrica de adopción que se quiere.
- Que `/uso` **no exista** para quien no trae la clave, y que se apague sola si no hay
  `CLAVE_USO` configurada.
- Que los escaneos de rutas al azar no ensucien las cuentas.

Uso: python3 test_uso.py
"""

import json
import os
import sys

os.environ["CLAVE_USO"] = "clave-de-prueba"

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

import uso  # noqa: E402

fallos = 0


def comprobar(nombre, condicion, detalle=""):
    global fallos
    if condicion:
        print(f"  [OK]    {nombre}")
    else:
        print(f"  [FALLA] {nombre}" + (f" — {detalle}" if detalle else ""))
        fallos += 1


# ---- App de prueba: un /mcp que devuelve el cuerpo que recibió, para verificar
#      que el middleware lo entrega intacto, más las rutas reales de uso.py ----

CUERPOS_VISTOS = []
SESION_ASIGNADA = ["s-1"]


async def falso_mcp(request):
    cuerpo = await request.body()
    CUERPOS_VISTOS.append(cuerpo)
    # El transporte real devuelve el id de sesión en la cabecera al hacer initialize
    return JSONResponse({"ok": True},
                        headers={"mcp-session-id": SESION_ASIGNADA[0]})


async def panel(request):
    return await uso.pagina_uso(request, "version-de-prueba")


app = Starlette(routes=[
    Route("/mcp", falso_mcp, methods=["POST"]),
    Route("/uso", panel, methods=["GET"]),
    Route("/visita", uso.visita, methods=["GET"]),
])
app.add_middleware(uso.Medicion, ruta="/mcp")
cliente = TestClient(app)

C = uso.CONTADORES


def rpc(metodo, sid=None, herramienta=None, lote=False):
    m = {"jsonrpc": "2.0", "id": 1, "method": metodo}
    if herramienta:
        m["params"] = {"name": herramienta, "arguments": {"q": "interés legítimo"}}
    cabeceras = {"mcp-session-id": sid} if sid else {}
    return cliente.post("/mcp", json=[m] if lote else m, headers=cabeceras)


print("\n— El middleware no estorba —")

CUERPOS_VISTOS.clear()
r = rpc("initialize")
comprobar("la petición sigue llegando bien", r.status_code == 200 and r.json()["ok"])
comprobar("el cuerpo llega intacto al transporte",
          json.loads(CUERPOS_VISTOS[-1])["method"] == "initialize",
          CUERPOS_VISTOS[-1][:80].decode())

print("\n— Sesiones —")

# Sesión 1: se conecta, lista y nunca llama nada. Es el caso que importa medir.
SESION_ASIGNADA[0] = "s-solo-listado"
rpc("initialize")
rpc("tools/list", sid="s-solo-listado")
rpc("tools/list", sid="s-solo-listado")

# Sesión 2: se conecta, lista y sí usa el conector
SESION_ASIGNADA[0] = "s-activa"
rpc("initialize")
rpc("tools/list", sid="s-activa")
rpc("tools/call", sid="s-activa", herramienta="buscar_tesis")
rpc("tools/call", sid="s-activa", herramienta="ver_tesis")

# Sesión 3: se conecta y no hace nada más
SESION_ASIGNADA[0] = "s-muda"
rpc("initialize")

d = C.resumen("version-de-prueba")
s = d["sesiones"]
comprobar("cuenta las sesiones, no las peticiones", s["total"] == 4, str(s))
comprobar("aparta la que solo listó herramientas", s["solo_listado"] == 1, str(s))
comprobar("aparta la que sí llamó", s["con_llamadas"] == 1, str(s))
comprobar("aparta la que no hizo nada", s["sin_actividad"] == 2, str(s))
comprobar("cuenta llamadas por herramienta",
          d["llamadas_por_herramienta"] == {"buscar_tesis": 1, "ver_tesis": 1},
          str(d["llamadas_por_herramienta"]))
comprobar("varias peticiones de una sesión no la duplican",
          C.sesiones_totales == 4, str(C.sesiones_totales))

print("\n— Ruido que no debe contarse —")

antes = dict(C.metodos)
cliente.get("/salud")
cliente.get("/.env")
cliente.get("/wp-admin/setup-config.php")
comprobar("los escaneos de rutas no tocan los contadores", dict(C.metodos) == antes)

sesiones_antes = C.sesiones_totales
SESION_ASIGNADA[0] = "s-ilegible"
r = cliente.post("/mcp", content=b"esto no es JSON")
comprobar("un cuerpo ilegible no rompe la petición ni inventa métodos",
          r.status_code == 200 and dict(C.metodos) == antes)
comprobar("pero la sesión sí se registra, porque el transporte la asignó",
          C.sesiones_totales == sesiones_antes + 1, str(C.sesiones_totales))

print("\n— Lotes JSON-RPC —")

llamadas_antes = C.herramientas.get("buscar_dof", 0)
SESION_ASIGNADA[0] = "s-lote"
rpc("tools/call", sid="s-lote", herramienta="buscar_dof", lote=True)
comprobar("cuenta los mensajes dentro de un lote",
          C.herramientas.get("buscar_dof", 0) == llamadas_antes + 1)

print("\n— Faro del sitio —")

r = cliente.get("/visita?p=instalacion")
comprobar("devuelve un GIF", r.status_code == 200 and r.headers["content-type"] == "image/gif")
comprobar("no se guarda en caché", "no-store" in r.headers.get("cache-control", ""))
cliente.get("/visita?p=inicio")
cliente.get("/visita?p=" + "x" * 500)
sitio = C.resumen()["sitio"]
comprobar("cuenta por página", sitio["por_pagina"].get("instalacion") == 1, str(sitio))
comprobar("las páginas desconocidas caen en 'otra'", sitio["por_pagina"].get("otra") == 1,
          str(sitio))
comprobar("lleva el conteo por día", sum(sitio["por_dia"].values()) == 3, str(sitio))

print("\n— La página es privada —")

comprobar("sin clave, no existe", cliente.get("/uso").status_code == 404)
comprobar("con la clave equivocada, tampoco",
          cliente.get("/uso?clave=otra").status_code == 404)
r = cliente.get("/uso?clave=clave-de-prueba")
comprobar("con la clave correcta, responde", r.status_code == 200 and "Sesiones" in r.text)
r = cliente.get("/uso?clave=clave-de-prueba&formato=json")
comprobar("y en JSON para leerla desde un programa",
          r.status_code == 200 and r.json()["sesiones"]["total"] == 6, r.text[:120])

os.environ.pop("CLAVE_USO")
comprobar("sin CLAVE_USO configurada se apaga sola, no se abre",
          cliente.get("/uso?clave=clave-de-prueba").status_code == 503)
os.environ["CLAVE_USO"] = "clave-de-prueba"

print("\n— Lo que NO se guarda —")

texto = json.dumps(C.resumen("version-de-prueba"), ensure_ascii=False)
comprobar("no aparecen términos de búsqueda", "interés legítimo" not in texto)
comprobar("no aparecen ids de sesión", "s-activa" not in texto)

print("\n— Techos de memoria —")

c2 = uso.Contadores()
for i in range(uso.MAX_SESIONES_VIVAS + 250):
    c2.registrar(f"s{i}", [("tools/list", None)])
comprobar("las sesiones vivas no crecen sin límite",
          len(c2._vivas) == uso.MAX_SESIONES_VIVAS, str(len(c2._vivas)))
comprobar("las desalojadas siguen contando en el total",
          c2.resumen()["sesiones"]["total"] == uso.MAX_SESIONES_VIVAS + 250)
comprobar("y siguen contando como 'solo listado'",
          c2.resumen()["sesiones"]["solo_listado"] == uso.MAX_SESIONES_VIVAS + 250)

c3 = uso.Contadores()
for i in range(uso.MAX_ETIQUETAS + 50):
    c3.registrar("s", [("tools/call", f"inventada_{i}")])
comprobar("los nombres de herramienta inventados no inflan la memoria",
          len(c3.herramientas) <= uso.MAX_ETIQUETAS + 1
          and c3.herramientas.get("otras", 0) > 0, str(len(c3.herramientas)))

print(f"\n{'TODO OK' if fallos == 0 else str(fallos) + ' FALLA(S)'}\n")
sys.exit(0 if fallos == 0 else 1)
