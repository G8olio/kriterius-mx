#!/usr/bin/env python3
"""
Pruebas de la sincronización semanal de Gacetas. Ninguna toca la red: el parseo del
portal se prueba contra el markup real guardado en `fixtures_gacetas/`.

Lo que se cuida aquí es lo que puede romper en silencio: que la detección compare por
URL y no por número de libro (la Corte republica libros corregidos), que la publicación
semanal no se pierda, y que un portal caído o bloqueado no pase por "nada nuevo".

    python test_sincronizar_gacetas.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sincronizar_gacetas as sg  # noqa: E402

RAIZ = Path(__file__).resolve().parent
FIXTURE = RAIZ / "fixtures_gacetas" / "gaceta_12a_2026-09-10.html"

fallas: list[str] = []


def ok(cond: bool, que: str, detalle: str = "") -> None:
    if cond:
        print(f"  ok   {que}")
    else:
        print(f"  FALLA {que}{': ' + detalle if detalle else ''}")
        fallas.append(que)


def con_manifest(filas: list[tuple], fn):
    """Corre `fn` con un manifiesto de prueba en lugar del real."""
    cab = ["epoca_id", "carpeta", "anio", "libro", "mes", "tomo", "url", "archivo"]
    with tempfile.TemporaryDirectory() as d:
        ruta = Path(d) / "gacetas_manifest.tsv"
        with ruta.open("w", encoding="utf-8") as f:
            f.write("\t".join(cab) + "\n")
            for fila in filas:
                f.write("\t".join(str(x) for x in fila) + "\n")
        viejo = sg.MANIFEST
        sg.MANIFEST = ruta
        try:
            return fn()
        finally:
            sg.MANIFEST = viejo


URL = "https://www.scjn.gob.mx/coordinacion/sites/default/files/gaceta/documents"


def sin_docstrings(fuente: str) -> str:
    """El código sin comentarios ni docstrings, para poder afirmar cosas sobre lo que
    el script HACE y no sobre lo que explica."""
    import ast
    arbol = ast.parse(fuente)
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            cuerpo = getattr(nodo, "body", None)
            if (cuerpo and isinstance(cuerpo[0], ast.Expr)
                    and isinstance(cuerpo[0].value, ast.Constant)
                    and isinstance(cuerpo[0].value.value, str)):
                cuerpo.pop(0)
    return ast.unparse(arbol)


def main() -> int:
    markup = FIXTURE.read_text(encoding="utf-8")

    print("\n== El markup real del portal se parsea completo ==")
    libros = sg.analizar_portal(markup, 1)
    ok(len(libros) == 4, "los 4 PDF del fixture", f"salieron {len(libros)}")
    ok(all(l["reconocido"] for l in libros), "los 4 traen Libro N legible")
    l13 = next((l for l in libros if l.get("libro") == 13), None)
    ok(l13 is not None, "encuentra el Libro 13")
    if l13:
        ok(l13["anio"] == 2026 and l13["mes"] == "septiembre",
           "Libro 13 = septiembre 2026", f"{l13.get('anio')} {l13.get('mes')}")
        ok(l13["url"] == f"{URL}/2026/01_13_sep_sem1.pdf",
           "la URL se vuelve absoluta", l13["url"])
        ok(l13["archivo"] == "Libro-013_2026-09_septiembre.pdf",
           "nombre de archivo del acervo", l13["archivo"])

    print("\n== Un libro ya conocido no se vuelve a bajar ==")
    filas = [(1, "12a-epoca", 2026, 13, "septiembre", "completo",
              f"{URL}/2026/01_13_sep_sem1.pdf", "Libro-013_2026-09_septiembre.pdf")]
    nuevos, _, _ = con_manifest(filas, lambda: sg.detectar({1: markup}))
    ok(all(l["libro"] != 13 for l in nuevos),
       "el Libro 13 con la misma URL ya no es nuevo")

    print("\n== Republicación: mismo libro, otra URL ==")
    # El caso real: el Libro 12 pasó de _0 a _1. Comparar por número diría "ya lo tengo".
    filas = [(1, "12a-epoca", 2026, 12, "agosto", "completo",
              f"{URL}/2026/12_ago_completo_0.pdf", "Libro-012_2026-08_agosto.pdf")]
    nuevos, _, _ = con_manifest(filas, lambda: sg.detectar({1: markup}))
    l12 = [l for l in nuevos if l["libro"] == 12]
    ok(len(l12) == 1, "el Libro 12 republicado sí se detecta",
       "comparar por número de libro lo habría perdido")
    if l12:
        ok(l12[0]["url"].endswith("12_ago_completo_1.pdf"),
           "detecta la URL corregida (_1), no la vieja")
        ok(l12[0]["archivo"] != "Libro-012_2026-08_agosto.pdf",
           "no pisa el archivo del libro que ya está", l12[0]["archivo"])

    print("\n== Varios archivos del mismo libro no se pisan ==")
    usados = {"Libro-013_2026-09_septiembre.pdf"}
    a = sg.desacomodar_nombre({"archivo": "Libro-013_2026-09_septiembre.pdf"}, usados)
    usados.add(a)
    b = sg.desacomodar_nombre({"archivo": "Libro-013_2026-09_septiembre.pdf"}, usados)
    ok(a == "Libro-013_2026-09_septiembre_parte-2.pdf", "segunda entrega -> _parte-2", a)
    ok(b == "Libro-013_2026-09_septiembre_parte-3.pdf", "tercera entrega -> _parte-3", b)

    print("\n== Un portal vacío o bloqueado no pasa por 'nada nuevo' ==")
    for nombre, basura in [("HTML sin anclas", "<html><body>Servicio no disponible</body></html>"),
                           ("página de reto", "<html><head><title>Radware</title></head></html>"),
                           ("vacío", "")]:
        libros = sg.analizar_portal(basura, 1)
        ok(libros == [], f"{nombre}: no inventa libros", repr(libros)[:80])
    # Y el que pide por red exige anclas antes de dar por buena la respuesta:
    import inspect
    fuente = inspect.getsource(sg.bajar_portal)
    ok('"<a" in r.text' in fuente,
       "bajar_portal exige anclas, no solo HTTP 200")
    ok('!= b"%PDF"' in inspect.getsource(sg.bajar_pdf),
       "bajar_pdf verifica que la respuesta sea PDF")

    print("\n== No se toca el SJF ni se elude nada ==")
    fuente = (RAIZ / "sincronizar_gacetas.py").read_text(encoding="utf-8")
    # Se revisa el CÓDIGO, no la prosa: la documentación sí nombra a sjf.scjn.gob.mx
    # para explicar justamente que no se toca.
    codigo = sin_docstrings(fuente)
    for prohibido in ("sjf.scjn.gob.mx", "playwright", "selenium", "undetected",
                      "cloudscraper", "Incapsula", "webdriver", "2captcha"):
        ok(prohibido.lower() not in codigo.lower(),
           f"el código no menciona «{prohibido}»")
    ok("www.scjn.gob.mx/coordinacion/gaceta" in codigo,
       "el único host que consulta es el portal público de la Gaceta")

    print("\n== El acervo base no se reescribe ==")
    ok("sjf_gaceta_nuevos.jsonl" in fuente, "escribe en el incremental")
    ok('NUEVOS.open("a"' in fuente, "anexa, no reescribe")
    ok('BASE.open("w"' not in fuente and "gzip.open(BASE, \"wt\"" not in fuente,
       "nunca abre el base para escritura")

    print(f"\n{'TODO BIEN' if not fallas else str(len(fallas)) + ' FALLAS'}")
    for f in fallas:
        print(f"  - {f}")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
