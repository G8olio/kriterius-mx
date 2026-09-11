#!/usr/bin/env python3
"""
Revisa semanalmente si la SCJN publicó Gacetas nuevas y las incorpora al acervo local.

El servidor nunca le pega al SJF: sirve `kriterius_datos/sjf_gaceta.jsonl.gz`, el acervo
congelado de 34 041 tesis sacadas de los PDF de la Gaceta. Este script es lo único que
mira hacia scjn.gob.mx, corre en GitHub Actions una vez por semana y propone el cambio
en un PR.

    python sincronizar_gacetas.py --solo-detectar          # ¿hay algo nuevo? (no baja nada)
    python sincronizar_gacetas.py                          # detecta, baja, convierte, extrae
    python sincronizar_gacetas.py --fixture fixtures_gacetas/gaceta_12a_2026-09-10.html

Qué NO hace, y por qué: no toca `sjf.scjn.gob.mx` —la API del Semanario, detrás de
Imperva desde el 3 de septiembre de 2026— ni resuelve retos de bot, ni usa navegador
headless. Solo pide los PDF públicos de `www.scjn.gob.mx/coordinacion/gaceta`, que es
el mismo archivo que cualquiera descarga con el navegador. Si algún día ese host
también cierra, el script falla con un mensaje claro y el acervo se queda como está.

## Por qué compara por URL y no por número de libro

Dos cosas descubiertas leyendo el portal en septiembre de 2026:

1. La Corte **republica** libros corregidos cambiando el sufijo del archivo: el Libro 12
   pasó de `12_ago_completo_0.pdf` a `12_ago_completo_1.pdf` sin cambiar de número.
2. Desde el Libro 13 hay **publicación semanal**: `01_13_sep_sem1.pdf`. Un libro llega en
   varios archivos a lo largo del mes.

Comparar "¿ya tengo el libro 12?" daría "sí" en los dos casos y perdería el contenido.
Por eso el manifiesto es la verdad y la llave es la URL completa.

## Qué escribe

- `kriterius_datos/sjf_gaceta_nuevos.jsonl` — las tesis de los libros nuevos, sin comprimir.
  El acervo base (`sjf_gaceta.jsonl.gz`, 42 MB) **no se reescribe nunca** en estas corridas:
  regrabarlo cada semana le costaría al repo ~2 GB al año en historia de git. `sjf_local.py`
  carga los dos y los nuevos ganan sobre el base cuando repiten libro.
- `gacetas/gacetas_manifest.tsv` — el renglón del libro nuevo.
- `resumen_gacetas.md` — el cuerpo del PR.
"""
from __future__ import annotations

import argparse
import gzip
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

RAIZ = Path(__file__).resolve().parent
DIR_GACETAS = RAIZ / "gacetas"
MANIFEST = DIR_GACETAS / "gacetas_manifest.tsv"
DATOS = RAIZ / "kriterius_datos"
BASE = DATOS / "sjf_gaceta.jsonl.gz"
NUEVOS = DATOS / "sjf_gaceta_nuevos.jsonl"

PORTAL = "https://www.scjn.gob.mx/coordinacion/gaceta"
# epoca_id en el portal -> carpeta del acervo. Solo las dos vivas: la Décima y la
# Novena cerraron y no reciben libros nuevos, revisarlas sería ruido semanal.
EPOCAS = {1: "12a-epoca", 2: "11a-epoca"}

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}

# <a href="...pdf"> ... alt="Gaceta SJF Septiembre 2026 Libro 13" ... </a>
RE_ANCLA = re.compile(r"<a\b[^>]*?href=\"([^\"]+?\.pdf)\"[^>]*>(.*?)</a>",
                      re.I | re.S)
RE_ALT = re.compile(r"alt=\"([^\"]*)\"", re.I)
RE_ALT_DATOS = re.compile(
    r"([A-Za-zÁÉÍÓÚÑáéíóúñ]+)\s+(\d{4})\s+Libro\s+([IVXL]+|\d+)", re.I)
RE_SPAN = re.compile(r"<span[^>]*>(.*?)</span>", re.I | re.S)
RE_SPAN_DATOS = re.compile(r"([A-Za-zÁÉÍÓÚÑáéíóúñ]+)\s+libro\s+([IVXL]+|\d+)", re.I)
RE_TOMO = re.compile(r"\btomo[\s_-]*([IVXL]+)\b", re.I)

ROMANOS = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
           "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13,
           "XIV": 14, "XV": 15, "XVI": 16, "XVII": 17, "XVIII": 18, "XIX": 19,
           "XX": 20, "XXI": 21, "XXII": 22, "XXIII": 23, "XXIV": 24, "XXV": 25}


def _a_entero(s: str) -> int | None:
    s = (s or "").strip().upper()
    if s.isdigit():
        return int(s)
    return ROMANOS.get(s)


def _sin_etiquetas(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


# ---------------------------------------------------------------- detección

def analizar_portal(markup: str, epoca_id: int, base_url: str = PORTAL) -> list[dict]:
    """Saca de la página del portal los libros publicados. Devuelve dicts con url,
    anio, libro, mes, tomo y el nombre de archivo que le tocaría en el acervo.

    Es puro parseo de texto: se prueba contra `fixtures_gacetas/` sin red."""
    carpeta = EPOCAS.get(epoca_id, f"{epoca_id}-epoca")
    vistos: set[str] = set()
    libros = []
    for m_ancla in RE_ANCLA.finditer(markup):
        href, dentro = m_ancla.group(1), m_ancla.group(2)
        url = urljoin(base_url, html.unescape(href.strip()))
        if url in vistos:
            continue
        vistos.add(url)

        mes = anio = None
        libro = None
        m_alt = RE_ALT.search(dentro)
        if m_alt:
            m = RE_ALT_DATOS.search(html.unescape(m_alt.group(1)))
            if m:
                mes = m.group(1).lower()
                anio = int(m.group(2))
                libro = _a_entero(m.group(3))
        if libro is None:
            # El alt es el dato bueno; el span es el respaldo y no trae año.
            for m_span in RE_SPAN.finditer(dentro):
                m = RE_SPAN_DATOS.search(_sin_etiquetas(m_span.group(1)))
                if m:
                    mes = m.group(1).lower()
                    libro = _a_entero(m.group(2))
                    break
        if libro is None or mes not in MESES:
            # Un PDF colgado de esta página que no es un libro (acuerdos, índices
            # anuales). No se inventa: se ignora y se reporta aparte.
            libros.append({"url": url, "epoca_id": epoca_id, "carpeta": carpeta,
                           "reconocido": False})
            continue
        if anio is None:
            m_anio = re.search(r"/(\d{4})/", url)
            anio = int(m_anio.group(1)) if m_anio else 0

        m_tomo = RE_TOMO.search(url)
        tomo = m_tomo.group(1).upper() if m_tomo else "completo"
        archivo = (f"Libro-{libro:03d}_{anio:04d}-{MESES[mes]:02d}_{mes}"
                   + (f"_Tomo-{tomo}" if tomo != "completo" else "") + ".pdf")
        libros.append({
            "url": url, "epoca_id": epoca_id, "carpeta": carpeta, "anio": anio,
            "libro": libro, "mes": mes, "tomo": tomo, "archivo": archivo,
            "reconocido": True,
        })
    return libros


def leer_manifest(ruta: Path | None = None) -> tuple[list[str], list[dict]]:
    # A propósito se lee MANIFEST al llamar y no al definir: las pruebas lo sustituyen.
    ruta = Path(ruta) if ruta is not None else MANIFEST
    cab: list[str] = []
    filas: list[dict] = []
    with ruta.open(encoding="utf-8") as f:
        cab = f.readline().rstrip("\n").split("\t")
        for linea in f:
            if not linea.strip():
                continue
            c = linea.rstrip("\n").split("\t")
            c += [""] * (len(cab) - len(c))
            filas.append(dict(zip(cab, c)))
    return cab, filas


def desacomodar_nombre(libro: dict, usados: set[str]) -> str:
    """Un libro puede llegar en varios archivos (publicación semanal desde el Libro 13,
    o tomos). Si el nombre ya está tomado por otra URL, se numera: `_parte-2`."""
    base = libro["archivo"]
    if base not in usados:
        return base
    tallo = base[:-4]
    n = 2
    while f"{tallo}_parte-{n}.pdf" in usados:
        n += 1
    return f"{tallo}_parte-{n}.pdf"


def detectar(markups: dict[int, str]) -> tuple[list[dict], list[dict], list[dict]]:
    """Compara el portal contra el manifiesto. Devuelve (nuevos, no_reconocidos, todos)."""
    _, filas = leer_manifest()
    urls_acervo = {f["url"] for f in filas}
    nombres = {f["archivo"] for f in filas}
    nuevos, raros, todos = [], [], []
    for epoca_id, markup in sorted(markups.items()):
        for libro in analizar_portal(markup, epoca_id):
            todos.append(libro)
            if not libro.get("reconocido"):
                if libro["url"] not in urls_acervo:
                    raros.append(libro)
                continue
            if libro["url"] in urls_acervo:
                continue
            libro["archivo"] = desacomodar_nombre(libro, nombres)
            nombres.add(libro["archivo"])
            nuevos.append(libro)
    return nuevos, raros, todos


# ---------------------------------------------------------------- red

def bajar_portal(epoca_id: int, intentos: int = 3) -> str:
    url = f"{PORTAL}?epoca_id={epoca_id}"
    import httpx
    ultimo = None
    for i in range(intentos):
        try:
            r = httpx.get(url, headers={"User-Agent": UA,
                                        "Accept": "text/html,application/xhtml+xml"},
                          timeout=60, follow_redirects=True)
            if r.status_code == 200 and "<a" in r.text:
                return r.text
            ultimo = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001
            ultimo = repr(exc)
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"no se pudo leer {url}: {ultimo}")


def bajar_pdf(url: str, destino: Path, intentos: int = 3) -> int:
    """Baja el PDF y verifica que sea PDF. Si el portal contesta HTML —bloqueo, error,
    página de mantenimiento— falla en vez de dejar basura disfrazada de libro."""
    import httpx
    destino.parent.mkdir(parents=True, exist_ok=True)
    ultimo = None
    for i in range(intentos):
        try:
            with httpx.stream("GET", url, headers={"User-Agent": UA}, timeout=300,
                              follow_redirects=True) as r:
                if r.status_code != 200:
                    ultimo = f"HTTP {r.status_code}"
                    r.read()
                    time.sleep(5 * (i + 1))
                    continue
                with destino.open("wb") as fo:
                    for trozo in r.iter_bytes(1 << 20):
                        fo.write(trozo)
            with destino.open("rb") as f:
                if f.read(4) != b"%PDF":
                    destino.unlink(missing_ok=True)
                    raise RuntimeError("la respuesta no es un PDF (¿bloqueo del portal?)")
            return destino.stat().st_size
        except Exception as exc:  # noqa: BLE001
            ultimo = repr(exc)
            destino.unlink(missing_ok=True)
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"no se pudo bajar {url}: {ultimo}")


# ---------------------------------------------------------------- proceso

def procesar_libro(libro: dict, trabajo: Path, manifest_tmp: Path) -> dict:
    """PDF -> Markdown -> tesis. Devuelve el meta del extractor para ese libro."""
    carpeta = trabajo / libro["carpeta"]
    pdf = carpeta / libro["archivo"]
    kb = bajar_pdf(libro["url"], pdf) // 1024

    entorno = dict(os.environ, GACETAS_RAIZ=str(trabajo),
                   GACETAS_MANIFEST=str(manifest_tmp))
    conv = subprocess.run(
        [sys.executable, str(DIR_GACETAS / "convertir_gacetas.py"), libro["carpeta"]],
        env=entorno, capture_output=True, text=True)
    if conv.returncode != 0:
        raise RuntimeError(f"convertir_gacetas falló: {conv.stdout[-800:]}\n{conv.stderr[-800:]}")

    salida = trabajo / "extraido"
    epoca = libro["carpeta"].split("-")[0]
    ext = subprocess.run(
        [sys.executable, str(DIR_GACETAS / "extraer_tesis_gaceta.py"),
         "--epoca", epoca, "--libro", str(libro["libro"]), "--salida", str(salida)],
        env=entorno, capture_output=True, text=True)
    if ext.returncode != 0:
        raise RuntimeError(f"extraer_tesis_gaceta falló: {ext.stdout[-800:]}\n{ext.stderr[-800:]}")

    meta = json.loads((salida / "sjf_gaceta.meta.json").read_text(encoding="utf-8"))
    meta["kb_pdf"] = kb
    meta["jsonl"] = salida / "sjf_gaceta.jsonl"
    return meta


def claves_del_base(libros: set[tuple]) -> int:
    """Cuántas tesis del acervo base pertenecen a los libros que estamos reemplazando.
    Solo para el resumen: el base no se toca."""
    if not libros or not BASE.exists():
        return 0
    n = 0
    with gzip.open(BASE, "rt", encoding="utf-8") as f:
        for linea in f:
            try:
                g = (json.loads(linea).get("gaceta") or {})
            except Exception:  # noqa: BLE001
                continue
            if (g.get("epoca"), g.get("serie"), str(g.get("libro_cita") or g.get("libro"))) in libros:
                n += 1
    return n


def anexar(jsonl_nuevo: Path) -> int:
    """Agrega las tesis al incremental. No reescribe el base: el repo no aguanta
    42 MB de .gz regrabados cada semana."""
    NUEVOS.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with NUEVOS.open("a", encoding="utf-8") as fo, \
            jsonl_nuevo.open(encoding="utf-8") as fi:
        for linea in fi:
            if linea.strip():
                fo.write(linea if linea.endswith("\n") else linea + "\n")
                n += 1
    return n


def actualizar_meta(nuevos: list[dict], metas: list[dict], total_incremental: int) -> None:
    """El meta pesa 1 KB: este sí se reescribe cada semana. Es lo que lee el aviso
    «⚠ ACERVO LOCAL» para decirle al usuario hasta dónde llega lo que está citando,
    y un «hasta agosto de 2026» viejo sería una mentira en la cara del abogado."""
    ruta = DATOS / "sjf_gaceta.meta.json"
    try:
        m = json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    # El conteo del base se guarda una vez y no se toca: sumarle el incremental
    # sobre el total de la semana pasada lo inflaría corrida tras corrida.
    base = int(m.get("tesis_base") or m.get("tesis") or 0)
    m["tesis_base"] = base
    m["tesis"] = base + total_incremental
    m["incremental"] = {
        "archivo": NUEVOS.name,
        "tesis": total_incremental,
        "actualizado": time.strftime("%Y-%m-%d"),
        "libros": [f"{l['carpeta'].replace('-epoca', '')} Libro {l['libro']} "
                   f"({l['mes']} {l['anio']})" for l in nuevos],
        "nota": "tesis = base + incremental. Si la Corte republicó un libro, el "
                "total real es menor: el conteo bueno lo da el índice al cargar.",
    }
    ultimo = max(nuevos, key=lambda l: (l["anio"], MESES.get(l["mes"], 0)))
    epoca = {"12a": "Duodécima", "11a": "Undécima"}.get(
        ultimo["carpeta"].replace("-epoca", ""), ultimo["carpeta"])
    m["hasta"] = (f"{epoca} Época, Libro {ultimo['libro']}, "
                  f"{ultimo['mes']} de {ultimo['anio']}")
    lim = [x for x in (m.get("limitaciones") or [])
           if not x.startswith("no incluye tesis publicadas después")]
    lim.insert(0, f"no incluye tesis publicadas después de {ultimo['mes']} "
                  f"de {ultimo['anio']}")
    m["limitaciones"] = lim
    ruta.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def escribir_manifest(nuevos: list[dict]) -> None:
    cab, filas = leer_manifest()
    for libro in nuevos:
        filas.append({
            "epoca_id": str(libro["epoca_id"]), "carpeta": libro["carpeta"],
            "anio": str(libro["anio"]), "libro": str(libro["libro"]),
            "mes": libro["mes"], "tomo": libro["tomo"], "url": libro["url"],
            "archivo": libro["archivo"],
        })
    with MANIFEST.open("w", encoding="utf-8") as f:
        f.write("\t".join(cab) + "\n")
        for fila in filas:
            f.write("\t".join(fila.get(c, "") for c in cab) + "\n")


# ---------------------------------------------------------------- resumen

def resumen(nuevos: list[dict], metas: list[dict], raros: list[dict],
            total_incremental: int, solo_detectar: bool = False) -> str:
    L = ["# Gacetas nuevas del Semanario", ""]
    if not nuevos:
        L += ["Sin novedades: el portal no publicó libros que no estén ya en el acervo.", ""]
        return "\n".join(L)

    if solo_detectar:
        L += [f"El portal tiene {len(nuevos)} archivo(s) que el acervo no trae. "
              "Esta corrida fue solo de detección: no se bajó ni se procesó nada.", "",
              "| Época | Libro | Mes | Archivo |", "|---|---:|---|---|"]
        L += [f"| {l['carpeta'].replace('-epoca','')} | {l['libro']} | "
              f"{l['mes']} {l['anio']} | `{l['url'].rsplit('/', 1)[-1]}` |" for l in nuevos]
        L.append("")
        return "\n".join(L)

    L += [f"La SCJN publicó {len(nuevos)} archivo(s) que el acervo no tenía.", "",
          "| Época | Libro | Mes | Tesis | Índice | Cobertura | PDF |",
          "|---|---:|---|---:|---:|---:|---:|"]
    for libro, meta in zip(nuevos, metas):
        det = (meta.get("archivos_detalle") or [{}])[0]
        cob = det.get("cobertura_archivo")
        L.append(f"| {libro['carpeta'].replace('-epoca','')} | {libro['libro']} | "
                 f"{libro['mes']} {libro['anio']} | {meta.get('tesis', 0)} | "
                 f"{det.get('indice_entradas', 0)} | "
                 f"{cob if cob is not None else 's/í'}% | {meta.get('kb_pdf', 0)} KB |")
    L += ["", f"`kriterius_datos/sjf_gaceta_nuevos.jsonl` queda con {total_incremental} tesis. "
              "El acervo base no se tocó.", ""]

    republicados = [l for l in nuevos if l.get("republicado")]
    if republicados:
        L += ["## Republicaciones", "",
              "La Corte volvió a subir estos libros con otro archivo. Las tesis nuevas "
              "**sustituyen** a las del base al cargar el índice:", ""]
        L += [f"- Libro {l['libro']} ({l['mes']} {l['anio']}): `{l['url'].rsplit('/', 1)[-1]}`"
              for l in republicados]
        L.append("")

    bajos = [(l, m) for l, m in zip(nuevos, metas)
             if ((m.get("archivos_detalle") or [{}])[0].get("cobertura_archivo") or 100) < 95]
    if bajos:
        L += ["## Revisar antes de mezclar", "",
              "Estos libros quedaron por debajo del 95 % contra su propio índice. "
              "Puede ser maqueta nueva del PDF: conviene mirar el diff antes de mezclar.", ""]
        L += [f"- Libro {l['libro']}: "
              f"{(m.get('archivos_detalle') or [{}])[0].get('cobertura_archivo')} %"
              for l, m in bajos]
        L.append("")

    if raros:
        L += ["## PDF del portal que no se reconocieron", "",
              "Cuelgan de la misma página pero no traen «Libro N» legible (acuerdos, "
              "índices anuales). No se bajaron.", ""]
        L += [f"- `{r['url']}`" for r in raros[:15]]
        L.append("")

    L += ["---", "", "Generado por `sincronizar_gacetas.py`. El servidor no consulta el SJF "
          "en vivo: este PR es la única vía por la que entra material nuevo."]
    return "\n".join(L)


# ---------------------------------------------------------------- main

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--solo-detectar", action="store_true",
                   help="reporta qué hay de nuevo y termina; no baja PDF")
    p.add_argument("--fixture", action="append", default=None,
                   help="archivo HTML guardado en vez de pedirle al portal (pruebas)")
    p.add_argument("--resumen", default="", help="dónde escribir el cuerpo del PR")
    p.add_argument("--limite", type=int, default=0,
                   help="procesar a lo más N libros nuevos en esta corrida")
    args = p.parse_args()

    if args.fixture:
        markups = {i + 1: Path(f).read_text(encoding="utf-8")
                   for i, f in enumerate(args.fixture)}
    else:
        markups = {}
        for epoca_id in EPOCAS:
            markups[epoca_id] = bajar_portal(epoca_id)

    nuevos, raros, todos = detectar(markups)
    print(f"portal: {len(todos)} PDF listados · acervo al día salvo {len(nuevos)} "
          f"archivo(s) · {len(raros)} sin reconocer", flush=True)
    for libro in nuevos:
        print(f"  NUEVO {libro['carpeta']} Libro {libro['libro']} "
              f"{libro['mes']} {libro['anio']} -> {libro['url']}", flush=True)
    for r in raros:
        print(f"  ? {r['url']}", flush=True)

    if args.solo_detectar or not nuevos:
        if args.resumen:
            Path(args.resumen).write_text(
                resumen(nuevos, [], raros, 0, solo_detectar=True), encoding="utf-8")
        return 0

    if args.limite:
        nuevos = nuevos[:args.limite]

    # Marca de republicación: ese libro ya estaba en el acervo con otra URL.
    _, filas = leer_manifest()
    ya = {(f["carpeta"], f["libro"]) for f in filas}
    for libro in nuevos:
        libro["republicado"] = (libro["carpeta"], str(libro["libro"])) in ya

    trabajo = Path(tempfile.mkdtemp(prefix="gacetas-"))
    manifest_tmp = trabajo / "gacetas_manifest.tsv"
    try:
        # El extractor necesita la URL del PDF para la cita: se le da un manifiesto
        # que ya incluye los libros de esta corrida.
        cab, filas = leer_manifest()
        with manifest_tmp.open("w", encoding="utf-8") as f:
            f.write("\t".join(cab) + "\n")
            for fila in filas:
                f.write("\t".join(fila.get(c, "") for c in cab) + "\n")
            for libro in nuevos:
                f.write("\t".join([str(libro["epoca_id"]), libro["carpeta"],
                                   str(libro["anio"]), str(libro["libro"]), libro["mes"],
                                   libro["tomo"], libro["url"], libro["archivo"]]) + "\n")

        metas, total = [], 0
        for libro in nuevos:
            print(f"== {libro['archivo']}", flush=True)
            meta = procesar_libro(libro, trabajo, manifest_tmp)
            det = (meta.get("archivos_detalle") or [{}])[0]
            print(f"   {meta.get('tesis', 0)} tesis · índice {det.get('indice_entradas', 0)}"
                  f" · cobertura {det.get('cobertura_archivo')}%", flush=True)
            if meta.get("tesis", 0) == 0:
                raise RuntimeError(
                    f"{libro['archivo']}: 0 tesis extraídas. El PDF bajó bien pero el "
                    "parser no reconoció nada; probablemente cambió la maqueta. "
                    "No se mezcla nada hasta revisarlo a mano.")
            total += anexar(meta["jsonl"])
            metas.append(meta)
            # Cada libro deja su Markdown y su PDF; con publicación semanal son
            # cientos de MB si se acumulan en el runner.
            shutil.rmtree(trabajo / "md", ignore_errors=True)
            for pdf in (trabajo / libro["carpeta"]).glob("*.pdf"):
                pdf.unlink(missing_ok=True)

        escribir_manifest(nuevos)
        n_incremental = sum(1 for _ in NUEVOS.open(encoding="utf-8")) if NUEVOS.exists() else 0
        actualizar_meta(nuevos, metas, n_incremental)
        texto = resumen(nuevos, metas, raros, n_incremental)
        if args.resumen:
            Path(args.resumen).write_text(texto, encoding="utf-8")
        print(texto)
        print(f"\nanexadas {total} tesis · incremental con {n_incremental}")
        return 0
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
