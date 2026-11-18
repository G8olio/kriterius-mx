"""La captura sigue disponible y buscable años después, sin red ni duplicación."""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import sjf_local as local
import sjf_semanal as semanal

VISIBLE = (Path(__file__).parent / 'fixtures_semanal/2032910.txt').read_text()


class AcervoPermanente(unittest.TestCase):
    def test_busqueda_y_lectura_despues_de_diez_anios(self):
        import kriterius_mx as k
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, meta, capturas = root/'base.jsonl', root/'meta.json', root/'capturas'
            base.write_text(''); meta.write_text('{}')
            c = semanal.empaquetar(VISIBLE, 2032910, capturado='2016-11-18T00:00:00+00:00')
            semanal.guardar(c, capturas)
            self.assertEqual(local.cargar(base, meta, root/'index.sqlite', None, capturas), 1)
            self.assertEqual(local.buscar('ultima ratio')[0]['registro_digital'], 2032910)
            d = local.obtener('P./J. 222/2026 (12a.)')[0]
            self.assertEqual(d['texto'], c['documento']['texto'])
            self.assertIn('2016-11-18', local.formatear_tesis(d, 'prueba'))
            with patch.object(semanal, 'directorios', return_value=[capturas]), \
                 patch.object(k, '_sjf_fetch', AsyncMock(side_effect=AssertionError('sin red'))), \
                 patch.object(k, '_repo_tesis_busqueda', AsyncMock(return_value=None)):
                self.assertIn(c['documento']['texto'], asyncio.run(k.ver_tesis(2032910)))
                self.assertIn(c['documento']['rubro'], asyncio.run(k.buscar_tesis('ultima ratio')))

    def test_fusion_no_duplica_y_conserva_correcciones_de_rubro(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); base=root/'base.jsonl'; capturas=root/'capturas'
            c = semanal.empaquetar(VISIBLE, 2032910)
            semanal.guardar(c, capturas)
            d = semanal.para_acervo(semanal.leer_captura(c,2032910))
            gaceta = {**d, 'id':'gaceta', 'texto':'Texto de Gaceta',
                      'gaceta':{'libro':'15','anio':2026,'pagina':3},
                      'fuente_pdf':'https://www.scjn.gob.mx/libro.pdf'}
            corregida = {**gaceta, 'id':'corregida', 'rubro':'RUBRO CORREGIDO DISTINTO'}
            base.write_text('\n'.join(json.dumps(x) for x in [gaceta,corregida])+'\n')
            self.assertEqual(local.cargar(base, root/'meta.json', None, None, capturas), 2)
            ds=local.obtener(d['clave'])
            self.assertEqual(len(ds),2)
            fusionada=next(x for x in ds if x['rubro']==d['rubro'])
            self.assertEqual(fusionada['texto'],d['texto'])
            self.assertEqual(fusionada['gaceta']['pagina'],3)

    def test_republicar_no_elimina_otro_libro_del_mismo_numero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); base=root/'base.jsonl'; extra=root/'extra.jsonl'
            viejo={'clave':'histórica','rubro':'ANTERIOR','texto':'conservar',
                   'epoca':'11a','gaceta':{'libro':12,'anio':2022,'mes':'abril'}}
            republicado={**viejo,'clave':'republicada','gaceta':{'libro':12,'anio':2026,'mes':'agosto'}}
            base.write_text('\n'.join(json.dumps(r) for r in [viejo,republicado])+'\n')
            extra.write_text(json.dumps({**republicado,'texto':'corrección'})+'\n')
            self.assertEqual(local.cargar(base,root/'meta.json',None,extra,root/'capturas'),2)
            self.assertEqual(local.obtener('histórica')[0]['texto'],'conservar')
            self.assertEqual(local.obtener('republicada')[0]['texto'],'corrección')

    def test_huella_resiste_exportar_capas_y_detecta_cambio_mismo_tamano(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'datos.jsonl';path.write_text('contenido A')
            inicial=local._huella(path)
            os.utime(path,(1000000000,1000000000))
            self.assertEqual(inicial,local._huella(path))
            path.write_text('contenido B')
            os.utime(path,(1000000000,1000000000))
            self.assertNotEqual(inicial,local._huella(path))

    def test_agregar_captura_invalida_indice_anterior(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); base=root/'base.jsonl'; capturas=root/'capturas'; idx=root/'idx.sqlite'
            base.write_text(json.dumps({'id':'base','rubro':'HISTORICA','texto':'contenido','clave':'X'})+'\n')
            self.assertEqual(local.cargar(base,root/'meta.json',idx,None,capturas),1)
            semanal.guardar(semanal.empaquetar(VISIBLE,2032910),capturas)
            self.assertEqual(local.cargar(base,root/'meta.json',idx,None,capturas),2)
            with patch.object(local,'SOLO_LECTURA',True):
                self.assertEqual(local.cargar(base,root/'meta.json',idx,None,capturas),2)


if __name__ == '__main__':
    unittest.main()
