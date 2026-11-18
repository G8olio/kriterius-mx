"""Regresiones de extracción y conservación ante rechazos del catálogo electoral."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
import httpx
import sincronizar_tepjf as t
from gacetas import extraer_tesis_gaceta as g


class Actualizacion(unittest.TestCase):
    def test_rubro_con_nomenclatura_mixta(self):
        rubro = 'PRESCRIPCIÓN. PLAZO DURANTE LA PANDEMIA SARS-CoV-2 (COVID-19).'
        self.assertEqual(g.partir_rubro(rubro+' Hechos: Una demanda.'),
                         (rubro, 'Hechos: Una demanda.'))

    def test_indice_evita_encabezado_de_ejecutoria_y_recupera_organo(self):
        rubro='OFRECIMIENTO DE TRABAJO. REGLAS PARA VALORARLO.'
        cuerpo='Hechos: '+('La autoridad debe valorar las pruebas y resolver. '*8)
        lines=['', 'RAZONES DE LA CONCURRENCIA 7.', 'Párrafo ajeno. '*20,
               rubro, cuerpo,
               'P L E N O R EG I O N A L E N M AT E R I AS P E N A L Y D E T R A BAJO D E L A R EG I Ó N',
               'CENTRO-NORTE, CON RESIDENCIA EN LA CIUDAD DE MÉXICO.',
               'PR.P.T.CN. J/14 L (12a.)',
               'Contradicción de criterios 31/2026. 15 de julio de 2026. Tres votos.']
        r,reason,_=g._bloque(lines,[331]*len(lines),7,g.parsear_clave(lines[7]),0,None,rubro)
        self.assertIsNone(reason)
        self.assertEqual(r['rubro'],rubro)
        self.assertEqual(r['texto'],cuerpo.strip())
        self.assertTrue(r['organo'].startswith('PLENO REGIONAL'))
        self.assertNotIn('Párrafo ajeno',r['texto'])

    def test_rechazo_interrumpe_sin_reintentar(self):
        async def run():
            for status,body in [(503,''),(403,'denegado'),(200,'<html>reto</html>')]:
                calls=[]
                def handler(req):
                    calls.append(req)
                    return httpx.Response(status,text=body)
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    with self.assertRaises(t.FuenteNoDisponible):
                        await t.Fuente(client,intentos=8,intervalo=0).traer('catalogo',{})
                self.assertEqual(len(calls),1)
        asyncio.run(run())

    def test_error_preserva_snapshot_y_fecha(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); data=root/'tepjf.jsonl'; meta=root/'tepjf.meta.json'
            data.write_text(json.dumps({'clave':'1/2026','tipo':'J'})+'\n')
            meta.write_text('{"fecha_snapshot":"2026-09-03"}')
            before=(data.read_bytes(),meta.read_bytes())
            args=SimpleNamespace(salida=str(data),meta=str(meta),cache='',concurrencia=1,
                                 intentos=1,intervalo=0)
            with patch.object(t,'volcado',AsyncMock(side_effect=t.FuenteNoDisponible('503'))):
                with self.assertRaises(t.FuenteNoDisponible):asyncio.run(t.sincronizar(args))
            self.assertEqual(before,(data.read_bytes(),meta.read_bytes()))


if __name__=='__main__':unittest.main()
