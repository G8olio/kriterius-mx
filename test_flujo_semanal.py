import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import flujo_semanal as f
import sjf_semanal as s

ROOT=Path(__file__).parent


class Flujo(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.datos=self.root/'datos'
        self.origen=self.root/'origen'
        self.origen.mkdir()
        self.ids=['2032910','2032896','2032921']
        self.catalogo={'url':s.BASE+'/listado-resultado-tesis',
            'capturado_utc':datetime.now(timezone.utc).isoformat(),
            'texto_visible':'Registro(s): 1 al 3 de 3',
            'enlaces':[{'href':f'/detalle/tesis/{r}','texto':f'Registro digital: {r}'} for r in self.ids]}
        for r in self.ids:
            c=json.loads((s.DIRECTORIO_DISTRIBUIDO/f'{r}.json').read_text())
            c['procedencia']['capturado_utc']=datetime.now(timezone.utc).isoformat()
            (self.origen/f'{r}.txt').write_text(c['texto_visible'])
            (self.origen/f'{r}.meta.json').write_text(json.dumps(c['procedencia']))

    def test_catalogo_parcial_duplicado_y_url_inyectada(self):
        for variant in ['parcial','duplicado','url']:
            c=copy.deepcopy(self.catalogo)
            if variant=='parcial': c['enlaces'].pop()
            elif variant=='duplicado': c['enlaces'][1]=c['enlaces'][0]
            else:c['enlaces'][0]['href']='https://otro.test/detalle/tesis/2032910'
            with self.subTest(variant=variant),self.assertRaises(ValueError):
                f.planificar(c,directorio=self.datos)

    def test_reanuda_sin_reconsultar_capturas_validas(self):
        (self.origen/'2032921.meta.json').unlink()
        p=f.planificar(self.catalogo,directorio=self.datos,origen=self.origen)
        self.assertEqual(p['pendientes'],['2032921'])
        self.assertEqual(p['capturadas_por_importar'],self.ids[:2])

    def test_importacion_idempotente_y_sin_renovar_fechas(self):
        result=f.incorporar(self.catalogo,self.origen,self.datos)
        self.assertEqual(result['importadas'],self.ids)
        archivos={p.name:p.read_bytes() for p in self.datos.iterdir()}
        self.assertEqual(f.incorporar(self.catalogo,self.origen,self.datos)['estado'],'sin_cambios')
        self.assertEqual(archivos,{p.name:p.read_bytes() for p in self.datos.iterdir()})
        self.assertEqual(f.planificar(self.catalogo,directorio=self.datos)['pendientes'],[])

    def test_una_ficha_incompleta_no_publica_lote(self):
        p=self.origen/'2032921.txt';p.write_text(p.read_text().split('Esta tesis se publicó')[0])
        with self.assertRaises(ValueError):f.incorporar(self.catalogo,self.origen,self.datos)
        self.assertFalse(self.datos.exists())

    def test_captura_cache_corrupta_se_vuelve_a_solicitar(self):
        f.incorporar(self.catalogo,self.origen,self.datos)
        (self.datos/'2032910.json').write_text('{')
        self.assertEqual(f.planificar(self.catalogo,directorio=self.datos)['pendientes'],['2032910'])


if __name__=='__main__':unittest.main()
