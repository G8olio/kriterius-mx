"""Plan e importación idempotente de novedades observadas en Chrome; sin red."""
import argparse
import hashlib
import json
import re
from pathlib import Path

import importar_semanal as importar
import sjf_semanal as s


def planificar(catalogo, *, directorio=None, origen=None):
    if catalogo.get("url") != s.BASE + "/listado-resultado-tesis":
        raise ValueError("El catálogo no corresponde al listado oficial")
    texto = catalogo["texto_visible"]
    totals = re.findall(r"Registro\(s\):\s*\d+\s+al\s+\d+\s+de\s+(\d+)", texto)
    if not totals or len(set(totals)) != 1:
        raise ValueError("No se pudo comprobar el total del catálogo")
    total = int(totals[0])
    if not 1 <= total <= 100:
        raise ValueError("El lote debe contener entre 1 y 100 registros")
    registros = []
    for link in catalogo["enlaces"]:
        match = re.fullmatch(r"/detalle/tesis/([1-9][0-9]{5,7})", link["href"])
        if not match or not re.search(rf"Registro digital:\s*{match[1]}\b", link["texto"]):
            raise ValueError("Enlace e identidad del catálogo no coinciden")
        registros.append(match[1])
    if len(set(registros)) != total or len(registros) != total:
        raise ValueError("Catálogo parcial o duplicado; completar la paginación")
    directory = Path(directorio) if directorio else s.DIRECTORIO_DISTRIBUIDO
    existentes, pendientes, capturadas = [], [], []
    for registro in registros:
        try:
            s.leer_captura(json.loads(importar.leer(directory / f"{registro}.json")), registro)
            existentes.append(registro)
        except (OSError, ValueError, KeyError, TypeError):
            if origen:
                try:
                    path = Path(origen)
                    raw = importar.leer(path / f"{registro}.txt")
                    meta = json.loads(importar.leer(path / f"{registro}.meta.json"))
                    if meta["url"] != f"{s.BASE}/detalle/tesis/{registro}" or not meta["capturado_utc"]:
                        raise ValueError("Procedencia inválida")
                    c = s.empaquetar(raw, registro, capturado=meta["capturado_utc"], canal=meta["canal"])
                    s.leer_captura(c, registro)
                    capturadas.append(registro)
                    continue
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            pendientes.append(registro)
    return {"schema": 1, "total": total, "registros": registros,
            "existentes": existentes, "pendientes": pendientes,
            "capturadas_por_importar": capturadas,
            "catalogo_capturado_utc": catalogo["capturado_utc"],
            "sha256_catalogo": hashlib.sha256(json.dumps(catalogo, sort_keys=True,
                                               ensure_ascii=False).encode()).hexdigest()}


def incorporar(catalogo, origen, destino):
    plan = planificar(catalogo, directorio=destino)
    if not plan["pendientes"]:
        return {"estado": "sin_cambios", "importadas": [], "total": plan["total"]}
    # El importador valida todas las capturas antes de publicar una sola ficha.
    report = importar.importar(origen, destino, plan["pendientes"])
    return {"estado": "importado", "importadas": plan["pendientes"],
            "total": plan["total"], "validacion": report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("accion", choices=["planificar", "incorporar"])
    parser.add_argument("--catalogo", required=True, type=Path)
    parser.add_argument("--datos", type=Path, default=s.DIRECTORIO_DISTRIBUIDO)
    parser.add_argument("--origen", type=Path)
    parser.add_argument("--informe", required=True, type=Path)
    args = parser.parse_args()
    try:
        catalogo = json.loads(importar.leer(args.catalogo))
        if args.accion == "planificar":
            report = planificar(catalogo, directorio=args.datos, origen=args.origen)
        else:
            if args.origen is None:
                raise ValueError("Falta --origen")
            report = incorporar(catalogo, args.origen, args.datos)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Flujo detenido: {error}\n")
    raw = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.informe.parent.mkdir(parents=True, exist_ok=True)
    args.informe.write_text(raw, encoding="utf-8")
    print(raw)


if __name__ == "__main__":
    main()
