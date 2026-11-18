"""Lectura sin red de fichas capturadas del portal semanal oficial.

El capturador corre separado del servicio MCP. El texto se vuelve a validar al
leerlo; la huella prueba integridad local, no es una firma de la SCJN.
"""
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = "https://sjfsemanal.scjn.gob.mx"
MAX_BYTES = 2 * 1024 * 1024
VIGENCIA = timedelta(days=30)
DIRECTORIO_DISTRIBUIDO = Path(__file__).parent / "kriterius_datos" / "sjf_semanal"


def registro_valido(registro):
    valor = str(registro)
    if not re.fullmatch(r"[1-9][0-9]{5,7}", valor):
        raise ValueError("Registro digital inválido")
    return valor


def interpretar(visible, registro):
    """Valida la plantilla visible y conserva todo el contenido hasta el cierre.

    No supone que todas las tesis contienen Hechos/Criterio/Justificación ni que
    terminan con PLENO. Texto y precedentes permanecen juntos para no truncarlos.
    """
    registro = registro_valido(registro)
    if not isinstance(visible, str) or len(visible.encode()) > MAX_BYTES:
        raise ValueError("Tamaño de ficha inválido")
    lines = [s.strip() for s in visible.splitlines() if s.strip()]
    starts = [i for i, s in enumerate(lines) if s.startswith("Registro digital:")]
    if len(starts) != 1:
        raise ValueError("Falta una única identidad en la ficha")
    lines = lines[starts[0]:]
    if len(lines) < 12 or lines[0] != f"Registro digital: {registro}":
        raise ValueError("La identidad no coincide con el registro solicitado")
    prefixes = {2: "Materia(s): ", 3: "Tesis: ", 4: "Instancia: ",
                5: "Tipo: ", 6: "Fuente: ", 7: "Publicación: "}
    fields = {}
    for i, prefix in prefixes.items():
        if not lines[i].startswith(prefix) or not lines[i][len(prefix):].strip():
            raise ValueError(f"Ficha incompleta o plantilla distinta: {prefix}")
        fields[i] = lines[i][len(prefix):].strip()
    if not lines[1].endswith("Época") or "Semanario Judicial" not in fields[6]:
        raise ValueError("Época o fuente inválida")
    ends = [i for i, s in enumerate(lines) if s.startswith("Esta tesis se publicó")]
    if len(ends) != 1 or ends[0] < 10:
        raise ValueError("Falta el cierre de publicación; no se acepta texto parcial")
    end = ends[0]
    publication = lines[end]
    # El encabezado y el cierre deben expresar el mismo día; evita mezclar fichas.
    date_re = r"\d{1,2} de [a-záéíóú]+ de \d{4}"
    date_head = re.search(date_re, fields[7], re.I)
    date_end = re.search(date_re, publication, re.I)
    if not date_head or not date_end or date_head[0].lower() != date_end[0].lower():
        raise ValueError("Fechas de publicación contradictorias")
    body = "\n\n".join(lines[9:end])
    if len(lines[8]) < 15 or len(body) < 150:
        raise ValueError("Faltan el rubro o el texto completo")
    return {"ius": int(registro), "rubro": lines[8], "epoca": lines[1],
            "materias": [s.strip() for s in fields[2].split(",")],
            "claveTesis": fields[3], "instancia": fields[4], "tipoTesis": fields[5],
            "localizacion": fields[6], "fechaPublicacion": fields[7],
            "texto": body, "textoPublicacion": publication}


def empaquetar(visible, registro, *, capturado=None,
               canal="DOM visible, Chromium mediante Playwright"):
    doc = interpretar(visible, registro)
    if not isinstance(canal, str) or not canal.strip() or len(canal) > 300:
        raise ValueError("Canal de captura inválido")
    return {"schema": 1, "documento": doc, "texto_visible": visible,
            "procedencia": {"url": f"{BASE}/detalle/tesis/{doc['ius']}",
                "capturado_utc": capturado or datetime.now(timezone.utc).isoformat(),
                "canal": canal,
                "sha256_texto_visible": hashlib.sha256(visible.encode()).hexdigest()}}


def guardar(captura, destino):
    """Escritura atómica: una interrupción nunca deja una ficha parcial publicada."""
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    registro = registro_valido(captura["documento"]["ius"])
    leer_captura(captura, registro)
    fd, tmp = tempfile.mkstemp(prefix=f".{registro}-", suffix=".tmp", dir=destino)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(captura, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(tmp, destino / f"{registro}.json")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def leer_captura(captura, registro, *, ahora=None):
    registro = registro_valido(registro)
    if captura.get("schema") != 1:
        raise ValueError("Versión de captura desconocida")
    p = captura["procedencia"]
    visible = captura["texto_visible"]
    if p["url"] != f"{BASE}/detalle/tesis/{registro}":
        raise ValueError("URL de procedencia no corresponde")
    if hashlib.sha256(visible.encode()).hexdigest() != p["sha256_texto_visible"]:
        raise ValueError("Huella local incorrecta")
    captured = datetime.fromisoformat(p["capturado_utc"])
    now = ahora or datetime.now(timezone.utc)
    if captured.tzinfo is None or not -timedelta(minutes=5) <= now - captured <= VIGENCIA:
        raise ValueError("Captura vencida o fecha inválida")
    doc = interpretar(visible, registro)
    if captura["documento"] != doc:
        raise ValueError("Los campos no corresponden al texto conservado")
    return {**doc, "_procedencia": p}


def directorios():
    cache = Path(os.environ.get("KRITERIUS_CACHE_DIR", str(Path.home() / ".cache/kriterius")))
    return [Path(os.environ.get("KRITERIUS_SEMANAL_DIR", str(cache / "sjf_semanal"))),
            DIRECTORIO_DISTRIBUIDO]


def obtener(registro):
    """Sin peticiones judiciales ni carga de navegador desde el proceso MCP."""
    try:
        registro = registro_valido(registro)
    except ValueError:
        return None
    for directory in directorios():
        try:
            path = directory / f"{registro}.json"
            if path.stat().st_size > MAX_BYTES:
                continue
            return leer_captura(json.loads(path.read_text(encoding="utf-8")), registro)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return None


def manifiesto():
    """Solo identidades y huellas de copias que ver_tesis puede servir ahora."""
    ids = {p.stem for directory in directorios() for p in directory.glob('*.json')}
    registros = {}
    for registro in sorted(ids):
        doc = obtener(registro)
        if doc:
            p = doc['_procedencia']
            registros[registro] = {k: p[k] for k in ('url', 'capturado_utc', 'sha256_texto_visible')}
    return {'schema': 1, 'total': len(registros), 'registros': registros}
