#!/usr/bin/env python3
"""
Pruebas del Dockerfile. Sin red y sin Docker: lee el archivo y ejecuta sus ENV y
sus RUN de Python en el MISMO orden, que es donde estuvo el error.

Por qué existe: la 2.11.1 puso `ENV KRITERIUS_SJF_SOLO_LECTURA=1` una línea ANTES
del `RUN` que construye el índice. La variable es justo la que prohíbe construirlo,
así que el propio build se rehusó, devolvió 0 tesis y salió con código 1. Ni las
pruebas del módulo ni las del servidor podían verlo: el módulo estaba bien, lo que
estaba mal era el ORDEN de las instrucciones. Un build de DigitalOcean cuesta
minutos y un despliegue fallido; esta prueba cuesta diez segundos.

Lo que vigila:
  1. Que el índice se construya en tiempo de build y no en el arranque.
  2. Que el cinturón de solo lectura quede DESPUÉS de esa construcción.
  3. Que cada archivo que el servidor importa esté en algún COPY: lo que no se
     copia no existe en el contenedor y el arranque truena en el import.

Uso: python3 test_dockerfile.py
"""

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

fallos = 0


def comprobar(nombre, condicion, detalle=""):
    global fallos
    if condicion:
        print(f"  [OK]    {nombre}")
    else:
        print(f"  [FALLA] {nombre}" + (f" — {detalle}" if detalle else ""))
        fallos += 1


RAIZ = pathlib.Path(__file__).parent
lineas = (RAIZ / "Dockerfile").read_text(encoding="utf-8").splitlines()

# Instrucciones en orden, ignorando comentarios y líneas en blanco.
instrucciones = [l.strip() for l in lineas
                 if l.strip() and not l.strip().startswith("#")]


def indice_de(patron):
    """Posición de la primera instrucción que casa, o -1."""
    for i, l in enumerate(instrucciones):
        if re.search(patron, l):
            return i
    return -1


print("\n— Orden de las instrucciones —")

i_run = indice_de(r"^RUN python .*sjf_local\.cargar")
i_solo = indice_de(r"^ENV KRITERIUS_SJF_SOLO_LECTURA")
i_cache = indice_de(r"^ENV KRITERIUS_CACHE_DIR")
i_datos = indice_de(r"^COPY kriterius_datos/")

comprobar("el build construye el índice del SJF", i_run != -1)
comprobar("se declara la ruta de la caché", i_cache != -1)
comprobar("la caché se declara ANTES de construir", -1 < i_cache < i_run,
          f"ENV en {i_cache}, RUN en {i_run}")
comprobar("los datos se copian ANTES de construir", -1 < i_datos < i_run,
          f"COPY en {i_datos}, RUN en {i_run}")
# El de siempre: el cinturón va después de abrocharlo.
comprobar("el modo solo lectura se activa DESPUÉS de construir el índice",
          i_solo != -1 and i_solo > i_run,
          f"ENV solo-lectura en {i_solo}, RUN en {i_run} — así el build no puede construir")

print("\n— Todo lo que se importa, se copia —")
copiados = set()
for l in instrucciones:
    if l.startswith("COPY "):
        for token in l[5:].split():
            copiados.add(token.rstrip("/"))
importados = set()
for archivo in ("server_http.py",):
    texto = (RAIZ / archivo).read_text(encoding="utf-8")
    for m in re.finditer(r"^\s*import (\w+)|^\s*from (\w+) import", texto, re.M):
        importados.add((m.group(1) or m.group(2)))
locales = {n for n in importados if (RAIZ / f"{n}.py").exists()}
faltantes = sorted(n for n in locales if f"{n}.py" not in copiados)
comprobar("cada módulo local que importa el servidor está en un COPY",
          not faltantes, f"faltan: {', '.join(faltantes)}")

print("\n— Ejecución real de los ENV y RUN, en su orden —")
tmp = tempfile.mkdtemp(prefix="test-dockerfile-")
try:
    entorno = {k: v for k, v in os.environ.items()
               if not k.startswith("KRITERIUS_")}
    paso = 0
    for l in instrucciones:
        if l.startswith("ENV "):
            clave, _, valor = l[4:].partition("=")
            clave, valor = clave.strip(), valor.strip()
            # La ruta del contenedor no existe aquí; se redirige al temporal.
            if clave == "KRITERIUS_CACHE_DIR":
                valor = tmp
            entorno[clave] = valor
        elif l.startswith("RUN python"):
            paso += 1
            r = subprocess.run(l[4:], shell=True, env=entorno, cwd=RAIZ,
                               capture_output=True, text=True, timeout=900)
            salida = (r.stdout + r.stderr).strip().splitlines()
            comprobar(f"RUN #{paso} termina con código 0", r.returncode == 0,
                      salida[-1] if salida else f"código {r.returncode}")
            if "sjf_local" in l and salida:
                comprobar("el índice se construyó con el acervo completo",
                          any("34041" in s or "34 041" in s for s in salida),
                          salida[-1])

    # Y con el entorno final —el de runtime— el índice solo se abre, no se rehace.
    r = subprocess.run(
        [sys.executable, "-c",
         "import time, sjf_local; t=time.time(); n=sjf_local.cargar(); "
         "print(n, round((time.time()-t)*1000))"],
        env=entorno, cwd=RAIZ, capture_output=True, text=True, timeout=300)
    partes = r.stdout.split()
    comprobar("en runtime el índice se abre, ya hecho",
              len(partes) == 2 and int(partes[0]) > 30000, r.stdout.strip() or r.stderr[-200:])
    if len(partes) == 2:
        ms = int(partes[1])
        # Si tardara segundos, el readiness probe de App Platform volvería a matarlo.
        comprobar(f"y se abre rápido ({ms} ms, tope 3000)", ms < 3000, f"{ms} ms")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{'TODO BIEN' if not fallos else f'{fallos} FALLA(S)'}")
sys.exit(1 if fallos else 0)
