#!/usr/bin/env python3
"""
Pruebas de caché, freno de concurrencia, reintentos y tope global. Sin red.

Todo se ejercita con funciones falsas que cuentan cuántas veces se las llama, así
que las pruebas corren en menos de un segundo y no molestan a ningún portal.

Uso: python3 test_limites.py
"""

import asyncio
import sys
import time

import kriterius_mx as k

fallos = 0


def comprobar(nombre, condicion, detalle=""):
    global fallos
    if condicion:
        print(f"  [OK]    {nombre}")
    else:
        print(f"  [FALLA] {nombre}" + (f" — {detalle}" if detalle else ""))
        fallos += 1


def limpiar():
    """Deja el estado global como recién arrancado."""
    k._CACHE._datos.clear()
    k._CACHE.acierto = k._CACHE.fallo = 0
    k._PETICIONES.clear()
    k._SEMAFOROS.clear()


# ---- Caché ----

print("\nCACHÉ")
limpiar()
k._CACHE.guardar("a", {"v": 1}, ttl=60)
comprobar("guarda y devuelve", k._CACHE.obtener("a") == {"v": 1})
comprobar("una clave ausente devuelve None", k._CACHE.obtener("no-existe") is None)

k._CACHE.guardar("b", "x", ttl=-1)
comprobar("una entrada vencida no se entrega", k._CACHE.obtener("b") is None)

comprobar("no guarda valores nulos",
          (k._CACHE.guardar("c", None, ttl=60), k._CACHE.obtener("c"))[1] is None)

limpiar()
for i in range(k.CACHE_MAX_ENTRADAS + 25):
    k._CACHE.guardar(f"k{i}", i, ttl=60)
comprobar("respeta el tope de entradas",
          len(k._CACHE._datos) == k.CACHE_MAX_ENTRADAS, str(len(k._CACHE._datos)))
comprobar("descarta lo más viejo primero", k._CACHE.obtener("k0") is None)
comprobar("conserva lo más reciente", k._CACHE.obtener(f"k{k.CACHE_MAX_ENTRADAS + 24}") is not None)

comprobar("la clave distingue argumentos distintos",
          k._clave("sjf", "/tesis", {"q": 1}) != k._clave("sjf", "/tesis", {"q": 2}))
comprobar("la clave es estable ante el orden de las llaves",
          k._clave("x", {"a": 1, "b": 2}) == k._clave("x", {"b": 2, "a": 1}))


# ---- _traer: caché + reintentos ----

print("\nSALIDA A LA RED (_traer)")


async def prueba_traer():
    limpiar()
    llamadas = {"n": 0}

    async def hacer():
        llamadas["n"] += 1
        return {"dato": "resultado"}

    r1 = await k._traer("sjf", hacer, clave="c1", ttl=60)
    r2 = await k._traer("sjf", hacer, clave="c1", ttl=60)
    comprobar("la segunda consulta sale de la caché", llamadas["n"] == 1, f"{llamadas['n']} llamadas")
    comprobar("y entrega exactamente lo mismo", r1 == r2 == {"dato": "resultado"})

    # sin clave, siempre va a la fuente
    limpiar()
    llamadas["n"] = 0
    await k._traer("sjf", hacer)
    await k._traer("sjf", hacer)
    comprobar("sin clave de caché no se guarda nada", llamadas["n"] == 2, f"{llamadas['n']}")

    # reintento ante fallo pasajero
    limpiar()
    intentos = {"n": 0}

    async def falla_dos_veces():
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise RuntimeError("el sitio respondió 503, temporal")
        return "por fin"

    t0 = time.monotonic()
    r = await k._traer("dof", falla_dos_veces)
    tardanza = time.monotonic() - t0
    comprobar("reintenta los fallos pasajeros", r == "por fin" and intentos["n"] == 3, str(intentos["n"]))
    comprobar("espera entre intentos (1s y 2s)", tardanza >= 2.9, f"{tardanza:.1f}s")

    # error definitivo: no insiste
    limpiar()
    intentos["n"] = 0

    async def error_de_fondo():
        intentos["n"] += 1
        raise RuntimeError("404 no encontrado")

    try:
        await k._traer("sjf", error_de_fondo)
        comprobar("propaga los errores definitivos", False, "no lanzó excepción")
    except RuntimeError:
        comprobar("propaga los errores definitivos", True)
    comprobar("y no reintenta cuando el error es definitivo", intentos["n"] == 1, str(intentos["n"]))

    comprobar("clasifica 503 como pasajero", k._es_pasajero(RuntimeError("respondió 503")))
    comprobar("clasifica el bloqueo de Imperva como pasajero",
              k._es_pasajero(RuntimeError("el WAF del BJDH (Imperva) bloqueó la petición")))
    comprobar("clasifica 404 como definitivo", not k._es_pasajero(RuntimeError("404")))


asyncio.run(prueba_traer())


# ---- Freno de concurrencia ----

print("\nFRENO DE CONCURRENCIA")


async def prueba_semaforo():
    limpiar()
    activas = {"ahora": 0, "pico": 0}

    async def tarda():
        activas["ahora"] += 1
        activas["pico"] = max(activas["pico"], activas["ahora"])
        await asyncio.sleep(0.05)
        activas["ahora"] -= 1
        return "ok"

    await asyncio.gather(*[k._traer("bjdh", tarda) for _ in range(12)])
    tope = k.CONCURRENCIA["bjdh"]
    comprobar(f"nunca hay más de {tope} peticiones simultáneas a la Corte IDH",
              activas["pico"] <= tope, f"pico de {activas['pico']}")
    comprobar("aun así, las 12 consultas se completan", activas["ahora"] == 0)

    limpiar()
    activas["pico"] = 0
    await asyncio.gather(*[k._traer("dof", tarda) for _ in range(15)])
    comprobar(f"el DOF admite hasta {k.CONCURRENCIA['dof']} a la vez",
              activas["pico"] <= k.CONCURRENCIA["dof"], f"pico de {activas['pico']}")


asyncio.run(prueba_semaforo())


# ---- Tope global ----

print("\nTOPE GLOBAL POR MINUTO")


async def prueba_tope():
    limpiar()
    for _ in range(k.LIMITE_PETICIONES_MINUTO):
        await k._esperar_cupo()
    comprobar("permite el cupo completo", len(k._PETICIONES) == k.LIMITE_PETICIONES_MINUTO)

    k.ESPERA_MAX_CUPO_S = 0.3   # para no demorar la prueba
    t0 = time.monotonic()
    try:
        await k._esperar_cupo()
        comprobar("rechaza al pasarse del cupo", False, "dejó pasar una de más")
    except RuntimeError as e:
        comprobar("rechaza al pasarse del cupo", "tope" in str(e).lower())
        comprobar("y avisa con un mensaje entendible", "vuelve a intentar" in str(e))
    comprobar("espera un poco antes de rendirse", time.monotonic() - t0 >= 0.25)
    k.ESPERA_MAX_CUPO_S = 3.0

    # la ventana se libera con el tiempo
    limpiar()
    k._PETICIONES.append(time.monotonic() - 61)
    await k._esperar_cupo()
    comprobar("descarta las peticiones de hace más de un minuto",
              len(k._PETICIONES) == 1, str(len(k._PETICIONES)))


asyncio.run(prueba_tope())


# ---- Tiempos de vida ----

print("\nTIEMPOS DE VIDA")
comprobar("el detalle de una tesis se guarda un mes", k.TTL["sjf_detalle"] == 30 * 24 * 3600)
comprobar("una búsqueda del SJF dura menos que el detalle",
          k.TTL["sjf_busqueda"] < k.TTL["sjf_detalle"])
comprobar("el DOF del día vence antes que el de días pasados",
          k.TTL["dof_hoy"] < k.TTL["dof_pasado"])
comprobar("la Corte IDH se guarda una semana", k.TTL["bjdh"] == 7 * 24 * 3600)

print(f"\n{'TODO OK' if fallos == 0 else str(fallos) + ' FALLA(S)'}\n")
sys.exit(0 if fallos == 0 else 1)
