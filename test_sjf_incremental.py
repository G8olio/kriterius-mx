#!/usr/bin/env python3
"""
Pruebas del acervo en dos piezas: el base congelado y el incremental que escribe la
sincronización semanal.

Lo que se cuida: que las tesis nuevas aparezcan en las búsquedas, que un libro
republicado por la Corte REEMPLACE al viejo en vez de duplicarlo, y —lo más fácil de
romper en silencio— que el índice en disco se reconstruya cuando llega un incremental
nuevo, en vez de seguir sirviendo el acervo de la semana pasada.

    python test_sjf_incremental.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sjf_local  # noqa: E402

fallas: list[str] = []


def ok(cond: bool, que: str, detalle: str = "") -> None:
    if cond:
        print(f"  ok   {que}")
    else:
        print(f"  FALLA {que}{': ' + detalle if detalle else ''}")
        fallas.append(que)


def tesis(clave: str, rubro: str, libro: str, epoca: str = "12a", serie: str = "") -> dict:
    return {
        "id": f"{epoca}-{clave}", "clave": clave, "rubro": rubro,
        "texto": f"Texto de {clave}.", "tipo": "TA", "epoca": epoca,
        "nivel": "TCC", "precedentes": [],
        "gaceta": {"anio": 2026, "libro": libro, "libro_cita": libro, "serie": serie,
                   "mes": "agosto", "tomo": "completo", "pagina": 100},
    }


def escribir(ruta: Path, filas: list[dict]) -> None:
    ruta.write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in filas),
                    encoding="utf-8")


def claves(consulta: str) -> set[str]:
    return {r.get("clave") for r in sjf_local.buscar(consulta, tope=50)}


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        base = d / "base.jsonl"
        nuevos = d / "nuevos.jsonl"
        indice = d / "fts.sqlite"
        meta = d / "meta.json"
        meta.write_text("{}", encoding="utf-8")

        escribir(base, [
            tesis("I.1o.A.1 A (12a.)", "AMPARO CONTRA LEYES. PROCEDENCIA.", "12"),
            tesis("I.2o.C.5 C (12a.)", "CONTRATOS MERCANTILES. INTERPRETACION.", "12"),
            tesis("I.3o.P.9 P (12a.)", "PRISION PREVENTIVA. PLAZO RAZONABLE.", "11"),
        ])

        print("\n== Sin incremental, el acervo es el base ==")
        n = sjf_local.cargar(base, meta, indice, None)
        ok(n == 3, "carga las 3 del base", str(n))
        ok("I.1o.A.1 A (12a.)" in claves("amparo contra leyes"), "busca en el base")

        print("\n== Llega un libro nuevo: se suma ==")
        escribir(nuevos, [
            tesis("I.4o.T.2 L (12a.)", "SALARIO CAIDO. TOPE DE DOCE MESES.", "13"),
        ])
        n = sjf_local.cargar(base, meta, indice, nuevos)
        ok(n == 4, "3 del base + 1 del incremental", str(n))
        ok("I.4o.T.2 L (12a.)" in claves("salario caido"),
           "la tesis nueva ya se busca")
        ok("I.1o.A.1 A (12a.)" in claves("amparo contra leyes"),
           "el base sigue entero")

        print("\n== Libro republicado: reemplaza, no duplica ==")
        # La Corte vuelve a publicar el Libro 12 corregido: una tesis cambia de rubro
        # y otra desaparece. Las dos viejas del Libro 12 tienen que salir del índice.
        escribir(nuevos, [
            tesis("I.4o.T.2 L (12a.)", "SALARIO CAIDO. TOPE DE DOCE MESES.", "13"),
            tesis("I.1o.A.1 A (12a.)",
                  "AMPARO CONTRA LEYES. PROCEDENCIA. (TEXTO CORREGIDO)", "12"),
        ])
        n = sjf_local.cargar(base, meta, indice, nuevos)
        ok(n == 3, "1 del libro 11 + 1 del 13 + 1 del 12 corregido", str(n))
        c = claves("amparo contra leyes")
        ok(len(c) == 1, "la tesis republicada no queda duplicada", str(len(c)))
        rubros = [r["rubro"] for r in sjf_local.buscar("amparo contra leyes", tope=5)]
        ok(any("CORREGIDO" in r for r in rubros), "gana la versión corregida", str(rubros))
        ok("I.2o.C.5 C (12a.)" not in claves("contratos mercantiles"),
           "la tesis que el libro corregido ya no trae desaparece")
        ok("I.3o.P.9 P (12a.)" in claves("prision preventiva"),
           "el libro 11, que nadie republicó, sigue intacto")

        print("\n== El índice en disco no puede quedarse atrasado ==")
        # Este es el que importa: si la huella solo mirara el base, el índice de la
        # semana pasada pasaría por bueno y el libro nuevo nunca se vería.
        ok(sjf_local._indice_sirve(indice, [base, nuevos]),
           "el índice recién construido sirve")
        escribir(nuevos, [
            tesis("I.4o.T.2 L (12a.)", "SALARIO CAIDO. TOPE DE DOCE MESES.", "13"),
            tesis("I.1o.A.1 A (12a.)", "AMPARO CONTRA LEYES. PROCEDENCIA.", "12"),
            tesis("I.5o.A.7 A (12a.)", "VISITA DOMICILIARIA. ORDEN FUNDADA.", "13"),
        ])
        ok(not sjf_local._indice_sirve(indice, [base, nuevos]),
           "con un incremental distinto, el índice viejo se descarta")
        n = sjf_local.cargar(base, meta, indice, nuevos)
        ok("I.5o.A.7 A (12a.)" in claves("visita domiciliaria"),
           "y la corrida siguiente ya trae lo nuevo")

        print("\n== Un incremental vacío o roto no tumba el acervo ==")
        nuevos.write_text("", encoding="utf-8")
        n = sjf_local.cargar(base, meta, indice, nuevos)
        ok(n == 3, "incremental vacío -> queda el base", str(n))
        nuevos.write_text("{esto no es json\n", encoding="utf-8")
        n = sjf_local.cargar(base, meta, indice, nuevos)
        ok(n == 0, "incremental corrupto -> fuente apagada, sin excepción", str(n))

        print("\n== Falta el incremental: todo sigue como antes ==")
        n = sjf_local.cargar(base, meta, indice, d / "no-existe.jsonl")
        ok(n == 3, "el acervo base solo", str(n))

    print(f"\n{'TODO BIEN' if not fallas else str(len(fallas)) + ' FALLAS'}")
    for f in fallas:
        print(f"  - {f}")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
