"""Comprueba las capturas permanentes instaladas y guarda sus respuestas, sin red.

Ejecutar desde fuera del checkout tras instalar la rueda. No modifica el reloj ni renueva las fechas originales de captura.
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
import sjf_local as local
import tepjf


async def validar(destino):
    destino.mkdir(parents=True, exist_ok=True)
    fichas = []
    registros = []
    for path in sorted(s.DIRECTORIO_DISTRIBUIDO.glob('*.json')):
        captura = json.loads(path.read_text(encoding='utf-8'))
        s.leer_captura(captura, path.stem)
        registros.append(int(path.stem))
    assert registros, 'No hay capturas permanentes en la distribución'
    # Bloquea cualquier petición HTTP saliente durante las consultas del core.
    with patch.object(httpx.AsyncClient, "send", side_effect=AssertionError("Red no permitida")) as red:
        assert local.cargar() >= 34414
        for ius in registros:
            doc = s.obtener(ius)
            assert doc, f"Captura ausente o inválida: {ius}"
            respuesta = await k.ver_tesis(ius)
            for campo in ("rubro", "texto", "textoPublicacion"):
                assert doc[campo] in respuesta, f"Falta {campo} completo: {ius}"
            assert doc["_procedencia"]["url"] in respuesta
            assert 'ACERVO DE KRITERIUS' in respuesta
            por_clave = await k.ver_tesis(doc['claveTesis'])
            assert doc['texto'] in por_clave, f'Falta texto por clave: {ius}'
            assert 'ACERVO DE KRITERIUS' in por_clave
            (destino / f"{ius}.txt").write_text(respuesta, encoding="utf-8")
            fichas.append({"registro": ius, "texto_completo": True,
                          "caracteres_texto": len(doc["texto"]),
                          "sha256_respuesta": hashlib.sha256(respuesta.encode()).hexdigest()})
        gaceta = local.obtener('(IV Región)1o. J/1 A (12a.)')[0]
        respuesta = await k.ver_tesis(gaceta['clave'])
        assert gaceta['texto'] in respuesta and 'ACERVO DE KRITERIUS' in respuesta
        assert tepjf.cargar() >= 2078
        criterio = tepjf.obtener('10/2026', 'J')
        assert criterio['texto'] in await k.ver_tesis_tepjf('10/2026')
        red.assert_not_called()
    report = {"version": k.VERSION, "consultas_http": 0, "fichas": fichas,
              "lecturas_por_registro": len(registros), "lecturas_por_clave": len(registros)+1,
              "lecturas_tepjf": 1, "conservacion": "permanente",
              "alcance": "Consulta de ver_tesis sin red; no prueba adquisición ni despliegue en producción"}
    (destino / "informe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(validar(Path(sys.argv[1])))
