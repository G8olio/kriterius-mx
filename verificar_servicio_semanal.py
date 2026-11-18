"""Comprueba por MCP HTTP que producción sirve las copias esperadas completas.

Solo contacta mcp.kriterius.mx. Exige versión con respaldo semanal y comprueba
que cada respuesta contiene la procedencia de captura antes de aceptar el lote.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

import sjf_semanal as s


async def verificar(registros):
    esperadas={r:s.obtener(r) for r in registros}
    if not registros or any(d is None for d in esperadas.values()):
        raise ValueError('Falta una captura local válida para comparar')
    async with httpx.AsyncClient(timeout=20) as client:
        response=await client.get('https://mcp.kriterius.mx/salud')
        response.raise_for_status()
        salud=response.json()
        response=await client.get('https://mcp.kriterius.mx/datos/sjf_semanal.json')
        response.raise_for_status()
        disponibles=response.json()['registros']
    version=tuple(int(v) for v in salud['version'].split('.'))
    if version<(2,14,0):raise ValueError('El servicio aún no tiene soporte de capturas semanales')
    for registro, doc in esperadas.items():
        publicado=disponibles.get(registro,{})
        for campo in ('capturado_utc','sha256_texto_visible'):
            if publicado.get(campo)!=doc['_procedencia'][campo]:
                raise ValueError(f'El despliegue aún no contiene la copia esperada: {registro}')
    resultados=[]
    async with streamablehttp_client('https://mcp.kriterius.mx/mcp',timeout=30,sse_read_timeout=45) as (reader,writer,_):
        async with ClientSession(reader,writer) as session:
            await session.initialize()
            for registro,doc in esperadas.items():
                result=await session.call_tool('ver_tesis',{'registro_digital':int(registro)})
                if result.isError:raise ValueError(f'Error MCP en {registro}')
                texto='\n'.join(c.text for c in result.content if c.type=='text')
                for campo in ('rubro','texto','textoPublicacion'):
                    if doc[campo] not in texto:raise ValueError(f'Contenido distinto o incompleto: {registro}, {campo}')
                for evidencia in (doc['_procedencia']['capturado_utc'],doc['_procedencia']['sha256_texto_visible']):
                    if evidencia not in texto:raise ValueError(f'El servicio no está usando la captura esperada: {registro}')
                resultados.append({'registro':registro,'texto_completo':True,
                                   'sha256_respuesta':hashlib.sha256(texto.encode()).hexdigest()})
    return {'estado':'verificado','salud':salud,'registros':resultados}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('registros',nargs='+')
    parser.add_argument('--informe',type=Path,required=True)
    args=parser.parse_args()
    report=asyncio.run(verificar([s.registro_valido(r) for r in args.registros]))
    args.informe.parent.mkdir(parents=True,exist_ok=True)
    args.informe.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
