#!/usr/bin/env python3
"""
resolver_registros.py — le pone registro digital a las tesis del acervo local.

La Gaceta impresa no publica el registro digital (el "IUS"), solo la clave. Por eso
las tesis servidas del acervo local se citan sin él y sin el link canónico
`sjf2.scjn.gob.mx/detalle/tesis/{registro}`. Este script cierra ese hueco: cuando
el API del SJF vuelva a estar accesible, busca cada clave en el API y guarda el
registro que le corresponde.

**No modifica el JSONL.** Escribe un archivo aparte,
`kriterius_datos/sjf_gaceta.registros.json`, que es un mapa `id -> registro`.
`sjf_local.py` lo carga si existe y, con él, la cita deja de decir "pendiente" y
empieza a llevar el link canónico. Así el JSONL sigue siendo la salida exacta y
determinista del parser, y el enriquecimiento se puede rehacer o tirar sin tocarlo.

Cómo empareja, en orden: se busca la clave como frase exacta; de los resultados se
toma el que tenga la MISMA clave (comparando sin acentos ni puntuación) y, si hay
varios, el que además coincida en rubro. Si nada casa con certeza, no se guarda
nada: un registro equivocado manda al abogado a una tesis que no es, que es peor
que no tener registro.

Es reanudable: relee lo ya resuelto y solo pide lo que falta. Guarda cada 200
claves, así que se puede cortar y volver a correr.

Uso:
    python3 resolver_registros.py                 # todas las pendientes
    python3 resolver_registros.py --epoca 12a     # solo una época
    python3 resolver_registros.py --limite 500 --pausa 1.5
    python3 resolver_registros.py --verificar     # no pide nada, solo reporta
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import httpx

DIR = Path(__file__).parent / "kriterius_datos"
RUTA_JSONL = DIR / "sjf_gaceta.jsonl.gz"
RUTA_REGISTROS = DIR / "sjf_gaceta.registros.json"

BASE = "https://sjf2.scjn.gob.mx/services/sjftesismicroservice/api/public"
HOST = "https://sjf2.scjn.gob.mx"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": f"{HOST}/busqueda-principal-tesis",
}

# Códigos con los que Imperva rechaza a quien no pasó su reto de JavaScript. Si
# aparecen, el API sigue bloqueado y no tiene caso seguir pidiendo.
WAF = {301, 302, 303, 307, 308, 403}


def plegar(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").upper())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Z0-9]", "", s)


def cuerpo(consulta: str) -> dict:
    """El mismo cuerpo de búsqueda que usa el conector, reducido a lo mínimo."""
    return {
        "type": "tesis",
        "classifiers": [],
        "search": consulta,
        "searchType": "todas",
        "filters": [],
    }


def cargar_tesis(ruta: Path, epoca: str | None):
    abrir = gzip.open if str(ruta).endswith(".gz") else open
    with abrir(ruta, "rt", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            r = json.loads(linea)
            if epoca and r.get("epoca") != epoca:
                continue
            yield r


def resolver_una(cliente: httpx.Client, r: dict) -> int | None:
    clave = (r.get("clave") or "").strip()
    if not clave:
        return None
    resp = cliente.post(f"{BASE}/tesis?page=0&size=20", json=cuerpo(f'"{clave}"'))
    if resp.status_code in WAF:
        raise RuntimeError(f"el API sigue bloqueado (HTTP {resp.status_code})")
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    docs = (resp.json() or {}).get("documents") or []
    objetivo, rubro = plegar(clave), plegar(r.get("rubro") or "")
    # 1) misma clave exacta
    exactos = [d for d in docs if plegar(d.get("claveTesis") or "") == objetivo]
    if len(exactos) == 1:
        return int(exactos[0]["ius"])
    # 2) misma clave y además el rubro empieza igual: desempata reimpresiones
    if exactos and rubro:
        finos = [d for d in exactos
                 if plegar(re.sub(r"<[^>]+>", "", d.get("rubro") or ""))[:80] == rubro[:80]]
        if len(finos) == 1:
            return int(finos[0]["ius"])
    # Ambiguo o sin coincidencia: no se inventa.
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default=str(RUTA_JSONL))
    ap.add_argument("--salida", default=str(RUTA_REGISTROS))
    ap.add_argument("--epoca", default=None, help="9a|10a|11a|12a")
    ap.add_argument("--limite", type=int, default=0, help="cuántas resolver en esta corrida")
    ap.add_argument("--pausa", type=float, default=1.0, help="segundos entre peticiones")
    ap.add_argument("--verificar", action="store_true",
                    help="no pide nada: solo dice cuántas faltan")
    args = ap.parse_args()

    salida = Path(args.salida)
    ya: dict = {}
    if salida.exists():
        try:
            ya = json.loads(salida.read_text(encoding="utf-8"))
        except Exception:
            ya = {}

    pendientes = [r for r in cargar_tesis(Path(args.jsonl), args.epoca)
                  if r["id"] not in ya]
    total = len(pendientes) + len(ya)
    print(f"{len(ya)} ya resueltas · {len(pendientes)} pendientes · {total} en el acervo")
    if args.verificar or not pendientes:
        return 0

    if args.limite:
        pendientes = pendientes[:args.limite]

    resueltas = fallidas = 0
    with httpx.Client(headers=HEADERS, timeout=30) as cliente:
        for i, r in enumerate(pendientes, 1):
            try:
                ius = resolver_una(cliente, r)
            except RuntimeError as e:
                print(f"\nSe detiene: {e}. Lo resuelto hasta aquí queda guardado.")
                break
            except Exception as e:
                print(f"  [{r['clave']}] error: {e}")
                fallidas += 1
                ius = None
            if ius:
                ya[r["id"]] = ius
                resueltas += 1
            else:
                fallidas += 1
            if i % 200 == 0:
                salida.write_text(json.dumps(ya, ensure_ascii=False, indent=0),
                                  encoding="utf-8")
                print(f"  {i}/{len(pendientes)} · {resueltas} resueltas · "
                      f"{fallidas} sin coincidencia clara", flush=True)
            time.sleep(max(0.0, args.pausa))

    salida.write_text(json.dumps(ya, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"\nGuardado en {salida}: {len(ya)} registros digitales "
          f"(+{resueltas} en esta corrida, {fallidas} sin coincidencia clara).")
    print("sjf_local.py lo toma en la siguiente carga: las citas dejan de decir "
          "'pendiente' y llevan el link canónico.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
