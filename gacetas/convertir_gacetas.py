#!/usr/bin/env python3
"""
Convierte los PDF de la Gaceta del Semanario Judicial de la Federación a Markdown.

Uso:
    python3 convertir_gacetas.py                # todos los PDF de las carpetas *-epoca
    python3 convertir_gacetas.py 12a-epoca      # solo una carpeta
    python3 convertir_gacetas.py --forzar ...   # reconvierte aunque el .md ya exista
    python3 convertir_gacetas.py --max-segundos=150   # por tandas; continúa donde quedó
    python3 convertir_gacetas.py --paralelo=4         # procesos simultáneos (default 3)

Por cada PDF produce md/<epoca>/<mismo nombre>.md con:
  - encabezado YAML (época, libro, mes, año, tomo, páginas, fuente, hash del PDF)
  - el texto íntegro, página por página, con un ancla <!-- p. N --> por página
    (N es el folio impreso cuando se detecta; si no, el número secuencial)
  - sin los encabezados corridos de cada página ("Primera Parte PLENO",
    "Septiembre, 2025", "Sección Primera Jurisprudencia") ni los folios sueltos

Usa `pdftotext` (poppler) en modo de lectura, que respeta el orden de lectura de una
columna. No inventa estructura: lo que no reconoce como encabezado corrido se conserva
tal cual. Es un convertidor fiel, no un parser de tesis; ese es el siguiente paso.
"""
import hashlib
import os
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

# Igual que en el extractor: GACETAS_RAIZ mueve la raíz de trabajo.
RAIZ = Path(os.environ.get("GACETAS_RAIZ") or Path(__file__).resolve().parent)
SALIDA = RAIZ / "md"

EPOCA_NOMBRE = {
    "9a-epoca": "Novena Época",
    "10a-epoca": "Décima Época",
    "11a-epoca": "Undécima Época",
    "12a-epoca": "Duodécima Época",
}

# Líneas que son encabezado o pie corrido de página, no contenido.
_PARTES = ("Primera|Segunda|Tercera|Cuarta|Quinta|Sexta|Séptima|Octava|Novena|Décima|"
           "Undécima|Duodécima")
RE_CORRIDO = re.compile(
    rf"^\s*(?:({_PARTES}) Parte\b.*"
    rf"|Sección ({_PARTES})\b.*"
    r"|(Enero|Febrero|Marzo|Abril|Mayo|Junio|Julio|Agosto|Septiembre|Octubre|Noviembre|Diciembre)"
    r"(?: de)?,? \d{4}"
    r"|Gaceta del Semanario Judicial de la Federación"
    r"|Semanario Judicial de la Federación y su Gaceta"
    r")\s*$",
    re.IGNORECASE,
)
RE_FOLIO = re.compile(r"^\s*(\d{1,4})\s*$")

# Nombre de archivo: Libro-085_2021-04_abril_Tomo-II[_parte-2].pdf
RE_NOMBRE = re.compile(
    r"^Libro-(?P<libro>\d{3})_(?P<anio>\d{4})-(?P<mm>\d{2})_(?P<mes>[a-zñ]+)"
    r"(?:_Tomo-(?P<tomo>[IVXL]+))?(?:_parte-(?P<parte>\d+))?\.pdf$"
)


def texto_pdf(pdf: Path) -> str:
    r = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "pdftotext falló")
    return r.stdout


def limpiar_pagina(pagina: str) -> tuple[str, int | None]:
    """Quita encabezados corridos y folios en los bordes de la página.
    Devuelve (texto limpio, folio impreso si se detectó)."""
    lineas = pagina.split("\n")
    folio = None

    def es_ruido(l: str) -> bool:
        return not l.strip() or bool(RE_CORRIDO.match(l))

    # Cabecera: hasta 6 líneas de ruido/folio al inicio
    i = 0
    while i < len(lineas) and i < 8:
        l = lineas[i]
        m = RE_FOLIO.match(l)
        if m and folio is None:
            folio = int(m.group(1)); i += 1; continue
        if es_ruido(l):
            i += 1; continue
        break
    # Pie: lo mismo desde el final
    j = len(lineas)
    while j > i and len(lineas) - j < 8:
        l = lineas[j - 1]
        m = RE_FOLIO.match(l)
        if m and folio is None:
            folio = int(m.group(1)); j -= 1; continue
        if es_ruido(l):
            j -= 1; continue
        break
    # Encabezados corridos que quedaron en medio (pdftotext a veces los emite después
    # de una nota al pie). Se quitan donde aparezcan: una línea que es exactamente
    # "Primera Parte PLENO" o "Septiembre, 2025" nunca es contenido. Un folio suelto
    # solo se quita si está pegado a uno de esos encabezados.
    medio = lineas[i:j]
    limpio = []
    for k, l in enumerate(medio):
        if RE_CORRIDO.match(l):
            continue
        if RE_FOLIO.match(l):
            vecinos = medio[max(0, k - 2):k] + medio[k + 1:k + 3]
            if any(RE_CORRIDO.match(v) for v in vecinos):
                if folio is None:
                    folio = int(RE_FOLIO.match(l).group(1))
                continue
        limpio.append(l)
    cuerpo = "\n".join(limpio).strip("\n")
    # Colapsar 3+ saltos en 2 (párrafos), sin tocar el interior
    cuerpo = re.sub(r"\n{3,}", "\n\n", cuerpo)
    return cuerpo, folio


def convertir(pdf: Path, destino: Path) -> dict:
    t0 = time.time()
    crudo = texto_pdf(pdf)
    paginas = crudo.split("\f")
    if paginas and not paginas[-1].strip():
        paginas.pop()

    m = RE_NOMBRE.match(pdf.name)
    meta = m.groupdict() if m else {}
    carpeta = pdf.parent.name
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()[:16]

    partes = []
    vacias = 0
    for n, pag in enumerate(paginas, start=1):
        cuerpo, folio = limpiar_pagina(pag)
        if not cuerpo:
            vacias += 1
            continue
        etiqueta = f"p. {folio}" if folio is not None else f"hoja {n}"
        partes.append(f"<!-- {etiqueta} -->\n{cuerpo}")

    tomo = meta.get("tomo")
    titulo = (f"Gaceta del Semanario Judicial de la Federación — {EPOCA_NOMBRE.get(carpeta, carpeta)}, "
              f"Libro {int(meta['libro']) if meta.get('libro') else '?'}, "
              f"{meta.get('mes', '?').capitalize()} de {meta.get('anio', '?')}"
              + (f", Tomo {tomo}" if tomo else "")
              + (f" (parte {meta['parte']})" if meta.get("parte") else ""))
    frente = "\n".join([
        "---",
        f'titulo: "{titulo}"',
        f"epoca: {EPOCA_NOMBRE.get(carpeta, carpeta)}",
        f"libro: {int(meta['libro']) if meta.get('libro') else 'null'}",
        f"anio: {meta.get('anio', 'null')}",
        f"mes: {meta.get('mes', 'null')}",
        f"tomo: {tomo or 'null'}",
        f"parte: {meta.get('parte') or 'null'}",
        f"paginas_pdf: {len(paginas)}",
        f"pdf: {pdf.name}",
        f"pdf_sha256_16: {sha}",
        "fuente: https://www.scjn.gob.mx/coordinacion/gaceta",
        f"convertido: {date.today().isoformat()}",
        "---",
        "",
        f"# {titulo}",
        "",
    ])
    destino.parent.mkdir(parents=True, exist_ok=True)
    # Escritura atómica: si el proceso muere a medias no queda un .md truncado que
    # después se tome por terminado.
    temporal = destino.with_suffix(".md.tmp")
    temporal.write_text(frente + "\n\n".join(partes) + "\n", encoding="utf-8")
    temporal.replace(destino)
    return {"paginas": len(paginas), "vacias": vacias, "seg": round(time.time() - t0, 1),
            "kb_pdf": pdf.stat().st_size // 1024, "kb_md": destino.stat().st_size // 1024}


def main(argv: list[str]) -> int:
    forzar = "--forzar" in argv
    # --max-segundos N: parar limpio al pasar N segundos (para correrlo por tandas);
    # la siguiente corrida continúa donde quedó porque salta los .md ya escritos.
    max_seg = None
    for a in argv:
        if a.startswith("--max-segundos="):
            max_seg = float(a.split("=", 1)[1])
    filtros = [a for a in argv if not a.startswith("--")]
    inicio = time.time()
    carpetas = [RAIZ / f for f in filtros] if filtros else sorted(RAIZ.glob("*-epoca"))
    SALIDA.mkdir(exist_ok=True)
    log = (SALIDA / "conversion.log").open("a", encoding="utf-8")

    def out(s: str):
        print(s, flush=True); log.write(s + "\n"); log.flush()

    hechos = saltados = fallas = 0
    # pdftotext es CPU puro (los libros de la Décima Época tardan ~8 s cada uno), así
    # que se convierten varios a la vez. --paralelo=N ajusta el número de procesos.
    paralelo = 3
    for a in argv:
        if a.startswith("--paralelo="):
            paralelo = max(1, int(a.split("=", 1)[1]))
    from concurrent.futures import ProcessPoolExecutor, as_completed

    pendientes = []
    for carpeta in carpetas:
        pdfs = sorted(carpeta.glob("*.pdf"))
        out(f"== {carpeta.name}: {len(pdfs)} PDF ==")
        for pdf in pdfs:
            destino = SALIDA / carpeta.name / (pdf.stem + ".md")
            if destino.exists() and not forzar:
                saltados += 1; continue
            if pdf.with_name(pdf.name + ".part").exists():
                out(f"PEND {carpeta.name}/{pdf.name}: descarga en curso, se convierte después")
                continue
            pendientes.append((pdf, destino))

    with ProcessPoolExecutor(max_workers=paralelo) as pool:
        futuros = {}
        cola = list(pendientes)
        # Se alimenta de a `paralelo` para poder parar limpio por --max-segundos sin
        # dejar cientos de trabajos encolados.
        while cola or futuros:
            while cola and len(futuros) < paralelo:
                if max_seg is not None and time.time() - inicio > max_seg:
                    cola.clear(); break
                pdf, destino = cola.pop(0)
                futuros[pool.submit(convertir, pdf, destino)] = pdf
            if not futuros:
                break
            hecho = next(as_completed(list(futuros)))
            pdf = futuros.pop(hecho)
            try:
                r = hecho.result()
                hechos += 1
                out(f"OK   {pdf.parent.name}/{pdf.name}  {r['paginas']} págs "
                    f"({r['vacias']} vacías)  {r['kb_pdf']} KB → {r['kb_md']} KB  {r['seg']}s")
            except Exception as e:  # noqa: BLE001 — se registra y se sigue con el siguiente
                fallas += 1
                out(f"FALLA {pdf.parent.name}/{pdf.name}: {e}")
    if max_seg is not None and time.time() - inicio > max_seg and (cola or pendientes[hechos + fallas:]):
        out("== pausa por --max-segundos; la siguiente corrida continúa ==")
    out(f"== fin: {hechos} convertidos, {saltados} ya existían, {fallas} fallas ==")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
