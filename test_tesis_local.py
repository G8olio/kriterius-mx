"""Contrato de consulta del acervo: íntegro, identificable y sin red judicial."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import kriterius_mx as k
import sjf_local as s

CASOS = {2023937: '2a./J. 31/2021 (11a.)',
         2029821: 'PR.A.C.CS. J/19 K (11a.)',
         2001863: 'I.1o.P.1 K (10a.)'}


class TesisLocales(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        assert s.cargar() >= 34414

    async def test_tres_registros_claves_ids_busquedas_con_texto_integro_sin_red(self):
        with patch.object(httpx.AsyncClient, 'send', side_effect=AssertionError('sin red')) as red:
            for ius, clave in CASOS.items():
                d, = s.obtener_registro(ius)
                self.assertEqual(d['clave'], clave)
                for identificador in (ius, clave, d['id']):
                    for respuesta in (await k.ver_tesis(identificador),
                                      await k.buscar_tesis(str(identificador))):
                        self.assertIn(d['texto'], respuesta)
                        self.assertIn(clave, respuesta)
                        self.assertIn(str(ius), respuesta)
                        self.assertIn(d['id'], respuesta)
                        self.assertIn(d['fuente_pdf'], respuesta)
                        self.assertNotIn('cortafuegos', respuesta)
                        for p in d.get('precedentes') or []:
                            self.assertIn(p, respuesta)
                # Tema en vez de clave: el agente recibe el texto, no solo rubro.
                respuesta = await k.buscar_tesis('"' + d['rubro'] + '"')
                self.assertIn(d['texto'], respuesta)
                respuesta = await k.investigar_criterio(clave)
                self.assertIn(d['texto'], respuesta)
            red.assert_not_called()

    async def test_resumen_es_optativo_y_lectura_por_id_sin_red(self):
        d, = s.obtener_registro(2001863)
        r = await k.buscar_tesis('2001863', incluir_texto=False)
        self.assertNotIn(d['texto'], r)
        self.assertIn(d['id'], r)
        self.assertIn(d['texto'], await k.ver_tesis(d['id']))

    async def test_sin_coincidencia_no_consulta_ni_afirma_bloqueo(self):
        with patch.object(httpx.AsyncClient, 'send', side_effect=AssertionError('sin red')) as red:
            r = await k.buscar_tesis('xyzsincoincidencias123456789')
            self.assertIn('Sin resultados en el acervo local', r)
            self.assertNotIn('cortafuegos', r)
            red.assert_not_called()

    async def test_todos_los_ius_enlazados_devuelven_su_cuerpo_sin_red(self):
        # Recorre el mapa real completo, no una muestra fija de tres registros.
        with patch.object(httpx.AsyncClient, 'send', side_effect=AssertionError('sin red')) as red:
            for ius in s._por_registro:
                documentos=s.obtener_registro(ius)
                self.assertTrue(documentos)
                r=await k.ver_tesis(ius)
                for d in documentos:
                    self.assertEqual(s.registro(d),ius)
                    self.assertIn(d['clave'],r)
                    self.assertIn(d['texto'],r)
            red.assert_not_called()
        m=s.meta()
        self.assertEqual(m['fichas_con_registro_digital']+m['fichas_sin_registro_digital'],m['tesis'])
        self.assertEqual(m['enlace_registros_completo'],m['fichas_sin_registro_digital']==0)

    async def test_resultados_oficiales_recuperan_clave_y_texto_local(self):
        d, = s.obtener_registro(2023937)
        for metadatos in ({'ius':2023937, 'rubro':d['rubro']},
                          {'ius':None, 'rubro':d['rubro'], 'claveTesis':d['clave']},
                          {'rubro':d['rubro']}):
            with patch.object(k, '_sjf_fetch', AsyncMock(return_value={'total':1, 'documents':[metadatos]})):
                r = await k.buscar_tesis('desistimiento', fuente='oficial')
                self.assertIn(d['clave'], r)
                self.assertIn(d['texto'], r)
                self.assertIn(d['id'], r)

    def test_no_confundir_identidades_ni_reimpresiones(self):
        d, = s.obtener_registro(2023937)
        self.assertIsNone(s.corresponder(ius=2023937, clave=CASOS[2029821]))
        self.assertIsNone(s.corresponder(ius=2023937, rubro=d['rubro']+' CAMBIADO'))
        self.assertIsNone(s.corresponder(clave='2a./J. 31/2021', rubro=d['rubro']))
        with patch.object(s, 'obtener_registro', return_value=[d, {**d,'id':'reimpresa'}]):
            self.assertIsNone(s.corresponder(ius=2023937, rubro=d['rubro']))
        self.assertEqual(s.obtener_registro(9999999), [])

    def test_identidades_empacadas_tienen_evidencia_y_coinciden(self):
        pruebas = json.loads((s._DIR/'sjf_gaceta.registros.json').read_text())['correspondencias']
        for p in pruebas:
            d, = s.obtener_id(p['id'])
            self.assertEqual(s.registro(d), p['registro_digital'])
            self.assertEqual(d['clave'], p['clave'])
            self.assertEqual(d['rubro'], p['rubro'])
            self.assertIn('scjn.gob.mx/', p['fuente'])


if __name__ == '__main__':
    unittest.main()
