"""Comprueba las capturas vigentes instaladas y guarda sus respuestas, sin red.

Ejecutar desde fuera del checkout tras instalar la rueda. No modifica el reloj:
si las capturas vencen, esta comprobación operativa falla y pide renovarlas.
"""
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import kriterius_mx as k
import sjf_semanal as s


async def validar(destino):
    destino.mkdir(parents=True, exist_ok=True)
    fichas = []
    registros, caducadas = [], []
    for path in sorted(s.DIRECTORIO_DISTRIBUIDO.glob('*.json')):
        captura = json.loads(path.read_text(encoding='utf-8'))
        fecha = datetime.fromisoformat(captura['procedencia']['capturado_utc'])
        if fecha.tzinfo and datetime.now(timezone.utc) - fecha > s.VIGENCIA:
            caducadas.append(path.stem)
            continue
        s.leer_captura(captura, path.stem)
        registros.append(int(path.stem))
    assert registros, 'No hay capturas vigentes en la distribución'
    # Bloquea cualquier petición HTTP saliente durante las consultas del core.
    with patch.object(httpx.AsyncClient, "send", side_effect=AssertionError("Red no permitida")) as red:
        for ius in registros:
            doc = s.obtener(ius)
            assert doc, f"Captura ausente, inválida o vencida: {ius}"
            respuesta = await k.ver_tesis(ius)
            for campo in ("rubro", "texto", "textoPublicacion"):
                assert doc[campo] in respuesta, f"Falta {campo} completo: {ius}"
            assert doc["_procedencia"]["url"] in respuesta
            (destino / f"{ius}.txt").write_text(respuesta, encoding="utf-8")
            fichas.append({"registro": ius, "texto_completo": True,
                          "caracteres_texto": len(doc["texto"]),
                          "sha256_respuesta": hashlib.sha256(respuesta.encode()).hexdigest()})
        red.assert_not_called()
    report = {"version": k.VERSION, "consultas_http": 0, "fichas": fichas, "caducadas": caducadas,
              "alcance": "Consulta de ver_tesis sin red; no prueba adquisición ni despliegue en producción"}
    (destino / "informe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(validar(Path(sys.argv[1])))
