"""Extracción real, aislamiento de asuntos, persistencia y consultas sin HTTP."""
import asyncio
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import ejecutorias_local as e
import kriterius_mx as k
from gacetas.extraer_ejecutorias import extraer,normalizar

FIXTURES=Path(__file__).parent/'fixtures_ejecutorias'


def fixture(root,name='reciente.md',epoca='12a-epoca'):
    p=root/epoca/name;p.parent.mkdir(exist_ok=True)
    p.write_bytes((FIXTURES/name).read_bytes())
    return p


def corpus(root,docs):
    root.mkdir(exist_ok=True)
    p=root/'parte-001.jsonl.gz'
    with gzip.open(p,'wt',encoding='utf-8') as f:
        for d in docs:f.write(json.dumps(d,ensure_ascii=False)+'\n')
    m={'esquema':1,'documentos':len(docs),'conservacion':'permanente','archivos_fuente':2,
       'gaceta_desde':'2011-10','gaceta_hasta':'2026-07','por_epoca':{},'alcance':'muestra',
       'partes':[{'archivo':p.name,'bytes':p.stat().st_size,'sha256':e.sha256(p),'documentos':len(docs)}]}
    (root/'meta.json').write_text(json.dumps(m))


class Extraccion(unittest.TestCase):
    def test_resolucion_reciente_completa_con_procedencia(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=fixture(Path(tmp));docs,report=extraer(p,{})
            self.assertEqual(len(docs),1)
            d=docs[0];self.assertEqual(d['asunto'],'AMPARO DIRECTO EN REVISIÓN 6897/2025')
            self.assertEqual(d['publicacion_sjf'],'2026-07-03')
            self.assertEqual(d['organo'],'Pleno de la Suprema Corte de Justicia de la Nación')
            self.assertEqual(d['alcance_texto'],'resolucion_con_cierre')
            f=d['fuentes'][0]
            self.assertEqual((f['pagina_inicio']['numero'],f['pagina_fin']['numero']),(5,36))
            raw='\n'.join(p.read_text().splitlines()[f['linea_inicio']-1:f['linea_fin']]).strip()
            self.assertEqual(d['texto'],normalizar(raw))
            self.assertIn('SEGUNDO. Se sobresee en el juicio de amparo directo',d['texto'])
            self.assertTrue(d['texto'].endswith('7/2025 (12a.).'))
            self.assertNotIn('registro_digital',d)

    def test_historica_sin_marcador_moderno(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=fixture(Path(tmp),'historica.md','10a-epoca');docs,_=extraer(p,{})
            self.assertEqual(len(docs),1)
            d=docs[0];self.assertEqual(d['asunto'],'CONTRADICCIÓN DE TESIS 448/2010')
            self.assertEqual(d['fuentes'][0]['pagina_fin']['numero'],92)
            self.assertIn('Así, lo resolvió el Pleno',d['texto'])
            self.assertIsNone(d['publicacion_sjf'])

    def test_cita_y_documento_truncado_no_se_vuelven_sentencias(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=fixture(Path(tmp));texto=p.read_text()
            p.write_text(texto[:texto.index('VI. DECISIÓN')])
            docs,report=extraer(p,{})
            self.assertEqual(docs,[])
            self.assertTrue(report['rechazos'])
            p.write_text('---\nanio: 2026\n---\nAMPARO DIRECTO 1/2026. PONENTE: ALGUIEN.\n'
                         'Una cita breve.\nEsta tesis se publicó el viernes 3 de julio de 2026.\n')
            self.assertEqual(extraer(p,{})[0],[])

    def test_dos_asuntos_y_un_extracto_conservan_sus_limites(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=fixture(Path(tmp));texto=p.read_text()
            extracto=('\nAMPARO DIRECTO 8/2026. PONENTE: PERSONA PONENTE.\nCONSIDERANDO:\n'+
                      ('Texto del extracto publicado sin resolutivos.\n'*70)+
                      'Esta ejecutoria se publicó el viernes 10 de julio de 2026.\n')
            p.write_text(texto+extracto)
            docs,_=extraer(p,{})
            self.assertEqual(len(docs),2)
            self.assertNotIn('8/2026',docs[0]['texto'])
            self.assertEqual(docs[1]['alcance_texto'],'extracto_publicado')


class Consulta(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.docs=extraer(fixture(self.root),{})[0]
        self.docs+=extraer(fixture(self.root,'historica.md','10a-epoca'),{})[0]
        self.data=self.root/'datos';corpus(self.data,self.docs)
        self.repo=e.Repositorio(self.data,self.root/'indice.sqlite',False)
        self.assertEqual(self.repo.cargar(),2)

    def tearDown(self):
        self.repo.cerrar();self.tmp.cleanup()

    def test_readonly_busqueda_por_expediente_partes_y_sin_red(self):
        self.repo.cerrar();self.repo.solo_lectura=True
        with patch.object(e,'_repo',self.repo), \
             patch.object(httpx.AsyncClient,'send',side_effect=AssertionError('sin red')) as red:
            self.assertEqual(self.repo.cargar(),2)
            r=asyncio.run(k.buscar_ejecutorias('6897/2025'))
            self.assertIn(self.docs[0]['id'],r)
            detalle=asyncio.run(k.ver_ejecutoria('ADR 6897/2025'))
            self.assertIn('sin consultas externas',detalle)
            d=self.docs[0]
            for i,parte in enumerate(k._ejec_partir(d['texto']),1):
                self.assertIn(parte,asyncio.run(k.ver_ejecutoria(d['id'],parte=i)))
            self.assertIn('Coincidencias',asyncio.run(k.ver_ejecutoria(d['id'],buscar_en_texto='reclasifica')))
            self.assertIn('No consta',asyncio.run(k.ver_ejecutoria(123456)))
            self.assertIn('Sin coincidencias',asyncio.run(k.buscar_ejecutorias('zzzzinexistente')))
            red.assert_not_called()
        self.assertEqual(self.repo.buscar('6897/2025',epocas=['10a'])[1],0)
        self.assertEqual(self.repo.buscar('6897/2025',epocas=['12a'])[1],1)

    def test_ambiguedad_no_elige_una_version_por_el_usuario(self):
        d={**self.docs[0],'id':'gaceta-ejec-'+'f'*20,'organo':'Otro órgano documentado'}
        self.repo.cerrar();corpus(self.data,self.docs+[d])
        with patch.object(e,'_repo',self.repo):
            r=asyncio.run(k.ver_ejecutoria('ADR 6897/2025'))
            self.assertIn('varias publicaciones',r)
            self.assertIn(d['id'],r)
            self.assertIn(self.docs[0]['id'],r)

    def test_lectura_acotada_y_busqueda_con_acentos_entre_ventanas(self):
        d=dict(self.docs[0])
        d['texto']='á'*15994+' protección jurídica singular '+'ñ'*18000
        d['sha256_texto']=hashlib.sha256(d['texto'].encode()).hexdigest()
        self.repo.cerrar();corpus(self.data,[d])
        with patch.object(e,'_repo',self.repo), \
             patch.object(self.repo,'obtener',wraps=self.repo.obtener) as obtener, \
             patch.object(self.repo,'leer_tramo',wraps=self.repo.leer_tramo) as leer:
            for i,parte in enumerate(k._ejec_partir(d['texto']),1):
                self.assertIn(parte,asyncio.run(k.ver_ejecutoria(d['id'],parte=i)))
            r=asyncio.run(k.ver_ejecutoria(d['id'],buscar_en_texto='proteccion juridica singular'))
            self.assertIn('protección jurídica singular',r)
            self.assertTrue(all(c.kwargs.get('texto') is False for c in obtener.call_args_list))
            self.assertTrue(all(c.args[2]-c.args[1]<=18400 for c in leer.call_args_list))

    def test_datos_alterados_no_se_sirven_y_runtime_no_reconstruye(self):
        self.repo.cerrar()
        p=self.data/'parte-001.jsonl.gz';p.write_bytes(p.read_bytes()+b'alterado')
        self.assertEqual(self.repo.cargar(),0)
        self.assertIn('Huella incorrecta',self.repo.error)
        corpus(self.data,self.docs);self.repo.cerrar();self.repo.indice.unlink()
        self.repo.solo_lectura=True
        self.assertEqual(self.repo.cargar(),0)
        self.assertFalse(self.repo.indice.exists())


if __name__=='__main__':unittest.main()
