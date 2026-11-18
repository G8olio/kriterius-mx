"""Prueba la rueda o imagen instalada: ejecutorias completas, paginadas, sin HTTP."""
import asyncio
import hashlib
import json
import re
import resource
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import ejecutorias_local as e
import kriterius_mx as k


async def validar(destino):
    destino.mkdir(parents=True,exist_ok=True)
    with patch.object(httpx.AsyncClient,'send',side_effect=AssertionError('HTTP prohibido')) as red:
        assert e.cargar()>=10000,e.meta()
        casos=['ADR 6897/2025','CONTRADICCIÓN DE TESIS 448/2010',
               'AMPARO DIRECTO 33/2008','ACCIÓN DE INCONSTITUCIONALIDAD 11/2011',
               'CONTRADICCIÓN DE CRITERIOS 47/2026']
        resultados=[]
        for caso in casos:
            docs=e.obtener(caso)
            assert docs,caso
            for d in docs:
                assert hashlib.sha256(d['texto'].encode()).hexdigest()==d['sha256_texto']
                busqueda=await k.buscar_ejecutorias('"'+d['asunto']+'"',por_pagina=50)
                assert d['id'] in busqueda,(caso,'no apareció en búsqueda')
                partes=k._ejec_partir(d['texto'])
                assert re.sub(r'\s','', ''.join(partes))==re.sub(r'\s','',d['texto'])
                for i,t in enumerate(partes,1):
                    r=await k.ver_ejecutoria(d['id'],parte=i)
                    assert t in r and d['sha256_texto'] in r and 'sin consultas externas' in r
                assert all(f['url_pdf'] for f in d['fuentes'])
                resultados.append({'id':d['id'],'asunto':d['asunto'],'partes':len(partes),
                                   'caracteres':len(d['texto']),'sha256_texto':d['sha256_texto']})
        assert 'No consta' in await k.ver_ejecutoria(123456789)
        red.assert_not_called()
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    mib=rss/(1024*1024 if sys.platform=='darwin' else 1024)
    report={'version':k.VERSION,'meta':e.meta(),'peticiones_http':0,
            'documentos_verificados':resultados,'memoria_maxima_mib':round(mib,1)}
    (destino/'ejecutorias.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':asyncio.run(validar(Path(sys.argv[1])))
