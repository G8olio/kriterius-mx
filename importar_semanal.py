"""Importa capturas asistidas y contrasta la tabla pública del Repositorio, sin red.

Cada registro requiere N.txt (texto visible) y N.meta.json (url, capturado_utc,
canal). No crea fechas de captura nuevas para documentos ya obtenidos.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

import sjf_semanal as s

REPOSITORIO = "https://bicentenario.scjn.gob.mx/repositorio-scjn/sjf"
COLUMNAS = ["Registro digital", "Época", "Localización", "Año", "Mes", "Instancia",
            "Órgano", "Publicación", "Materia", "Tipo de Tesis",
            "Número de Identificación", "Rubro (Título / Subtítulo)", "Texto",
            "Precedentes", "Nota de publicación", "Anexos", "Certificado Digital"]


def leer(path):
    path = Path(path)
    if path.stat().st_size > s.MAX_BYTES:
        raise ValueError(f"Archivo demasiado grande: {path.name}")
    return path.read_text(encoding="utf-8")


def normalizar(texto):
    return " ".join(texto.split())


def tabla_repositorio(path):
    raw = leer(path)
    tabla = json.loads(raw)
    if tabla["url"] != REPOSITORIO or tabla["filas"][0] != COLUMNAS:
        raise ValueError("Fuente o columnas del Repositorio distintas")
    registros = {}
    for cells in tabla["filas"][1:]:
        if len(cells) != len(COLUMNAS):
            raise ValueError("Fila incompleta del Repositorio")
        row = dict(zip(COLUMNAS, [json.loads(c) for c in cells]))
        ius = s.registro_valido(row["Registro digital"])
        if ius in registros:
            raise ValueError("Registro duplicado en el Repositorio")
        registros[ius] = row
    return tabla, registros, hashlib.sha256(raw.encode()).hexdigest()


def contrastar(doc, row):
    # El Repositorio muestra tres separadores vacíos al final de precedentes.
    # Solo se retiran para comparar; el archivo original se conserva intacto.
    precedentes = re.sub(r"(?:\s+\|){3}\s*$", "", row["Precedentes"])
    checks = {
        "registro": str(row["Registro digital"]) == str(doc["ius"]),
        "rubro": normalizar(row["Rubro (Título / Subtítulo)"]) == normalizar(doc["rubro"]),
        "clave": row["Número de Identificación"] == doc["claveTesis"],
        "texto_y_precedentes": normalizar(row["Texto"] + " " + precedentes) == normalizar(doc["texto"]),
        "nota_publicacion": normalizar(row["Nota de publicación"]) == normalizar(doc["textoPublicacion"]),
        "epoca": row["Época"] == doc["epoca"],
        "tipo": row["Tipo de Tesis"] == doc["tipoTesis"],
        "materias": row["Materia"] == doc["materias"],
        # El encabezado semanal añade un punto final al nombre de la fuente.
        "fuente": row["Publicación"].rstrip(".") == doc["localizacion"].rstrip("."),
        "instancia": doc["instancia"] in (row["Instancia"], row["Órgano"]),
    }
    if not all(checks.values()):
        raise ValueError(f"Diferencia en {doc['ius']}: " + ", ".join(k for k, v in checks.items() if not v))
    if not re.fullmatch(r"[a-f0-9]{64}", row["Certificado Digital"]):
        raise ValueError("Huella del Repositorio mal formada")
    return checks


def importar(origen, destino, registros, *, repositorio=None):
    registros = [s.registro_valido(r) for r in registros]
    if not 1 <= len(registros) <= 100 or len(set(registros)) != len(registros):
        raise ValueError("Se requieren de 1 a 100 registros únicos")
    origen = Path(origen)
    if repositorio:
        tabla, filas, huella = tabla_repositorio(repositorio)
    capturas, resultados = [], []
    for registro in registros:
        raw = leer(origen / f"{registro}.txt")
        meta = json.loads(leer(origen / f"{registro}.meta.json"))
        if meta["url"] != f"{s.BASE}/detalle/tesis/{registro}":
            raise ValueError(f"URL de captura incorrecta: {registro}")
        if not meta.get("capturado_utc"):
            raise ValueError("Falta la fecha original de captura")
        captura = s.empaquetar(raw, registro, capturado=meta["capturado_utc"], canal=meta["canal"])
        doc = s.leer_captura(captura, registro)
        resultado = {"registro": registro, "caracteres_texto": len(doc["texto"]),
                     "url": meta["url"], "capturado_utc": meta["capturado_utc"]}
        if repositorio:
            checks = contrastar(doc, filas[registro])
            evidencia = {"url": REPOSITORIO, "capturado_utc": tabla["capturado_utc"],
                         "canal": "Tabla visible en Chrome mediante CUA",
                         "sha256_tabla_local": huella,
                         "huella_mostrada_por_repositorio": filas[registro]["Certificado Digital"],
                         "comprobaciones": checks,
                         "alcance": "Coincidencia de contenido; no verificación criptográfica de la huella oficial"}
            captura["procedencia"]["contraste_repositorio"] = evidencia
            resultado["contraste_repositorio"] = evidencia
        capturas.append(captura)
        resultados.append(resultado)
    # Un fallo de identidad, completitud o contraste impide escribir todo el lote.
    # Cada ficha se escribe atómicamente; esto no es una transacción de filesystem.
    for captura in capturas:
        s.guardar(captura, destino)
    return {"estado": "validado", "consultas_judiciales": 0, "fichas": resultados}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registros", nargs="+")
    parser.add_argument("--origen", required=True, type=Path)
    parser.add_argument("--salida", required=True, type=Path)
    parser.add_argument("--repositorio", type=Path)
    parser.add_argument("--informe", type=Path)
    args = parser.parse_args()
    try:
        report = importar(args.origen, args.salida, args.registros, repositorio=args.repositorio)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(1, f"Importación rechazada: {exc}\n")
    raw = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.informe:
        args.informe.parent.mkdir(parents=True, exist_ok=True)
        args.informe.write_text(raw, encoding="utf-8")
    print(raw)


if __name__ == "__main__":
    main()
