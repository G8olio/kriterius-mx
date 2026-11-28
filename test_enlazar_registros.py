"""Impide que un cruce parcial o ambiguo se publique como catálogo completo."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import enlazar_registros as e
import sjf_local as s


def doc(rubro='PRIMER RUBRO', **extra):
    return {'id':'11a:1a./J. 1/2025 (11a.)','epoca':'11a',
            'clave':'1a./J. 1/2025 (11a.)','rubro':rubro,'texto':'Texto propio',**extra}


class EnlaceTotal(unittest.TestCase):
    def cruzar(self, docs, filas, anterior=None):
        return e.cruzar(docs,filas,anterior or {'version':2,'correspondencias':[]},
                       fuente='https://bicentenario.scjn.gob.mx/repositorio-scjn',
                       fecha='2026-11-24',sha256='a'*64)

    def test_reimpresiones_y_clave_igual_rubro_distinto(self):
        docs=[doc(),doc(texto='Otra publicación'),doc('SEGUNDO RUBRO')]
        mapa, resultado=self.cruzar(docs,[doc(registro_digital=2020001)])
        self.assertEqual(resultado['nuevas_identidades'],1)
        auditoria=e.auditar(docs,mapa)
        self.assertEqual(auditoria['resumen']['fichas_enlazadas'],2)
        self.assertEqual(auditoria['resumen']['fichas_pendientes'],1)
        self.assertFalse(auditoria['resumen']['completo'])
        self.assertNotIn(s._identidad(docs[-1]),s.validar_correspondencias(mapa))
        mapa2,_=self.cruzar(docs,[doc('SEGUNDO RUBRO',registro_digital=2020002)],mapa)
        self.assertTrue(e.auditar(docs,mapa2)['resumen']['completo'])

    def test_nunca_desempata_por_prefijo_ni_por_clave_sola(self):
        mapa, resultado=self.cruzar([doc('A'*100+' FINAL UNO')],
                         [doc('A'*100+' FINAL DOS',registro_digital=2020001)])
        self.assertEqual(mapa['correspondencias'],[])
        self.assertEqual(resultado['incidencias'][0]['motivo'],'clave_con_rubro_distinto')

    def test_duplicados_conflictivos_y_procedencia(self):
        mapa,_=self.cruzar([doc()],[doc(registro_digital=2020001)])
        for cambio in ({'registro_digital':True},{'fuente':'https://scjn.gob.mx.ejemplo.com/'},
                       {'fuente':''},{'verificado':'ayer'}):
            roto=copy.deepcopy(mapa);roto['correspondencias'][0].update(cambio)
            with self.assertRaises(ValueError):s.validar_correspondencias(roto)
        with self.assertRaises(ValueError):s.validar_correspondencias({'id':2020001})
        with self.assertRaises(ValueError):
            self.cruzar([doc(),doc('OTRO')],
                        [doc(registro_digital=2020001),doc('OTRO',registro_digital=2020001)])

    def test_reanudacion_conserva_fecha_evidencia_y_detecta_cambios(self):
        mapa,_=self.cruzar([doc()],[doc(registro_digital=2020001)])
        repetido, r=self.cruzar([doc()],[doc(registro_digital=2020001)],mapa)
        self.assertEqual(repetido,mapa);self.assertEqual(r['nuevas_identidades'],0)
        sin_cambio,r=self.cruzar([doc()],[doc(registro_digital=2020002)],mapa)
        self.assertEqual(sin_cambio,mapa)
        self.assertEqual(r['incidencias'][0]['motivo'],'conflicto_con_mapa_anterior')

    def test_no_sobrescribe_captura_ni_oculta_correspondencias_huerfanas(self):
        mapa,r=self.cruzar([doc(registro_digital=2020001)],[doc(registro_digital=2020002)])
        self.assertEqual(mapa['correspondencias'],[])
        self.assertTrue(r['incidencias'])
        mapa,_=self.cruzar([doc()],[doc(registro_digital=2020001)])
        self.assertFalse(e.auditar([doc('RUBRO CORREGIDO',registro_digital=2020002)],mapa)['resumen']['completo'])

    def test_csv_oficial_y_lote_invalido_sin_escritura_parcial(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);csv=base/'catalogo.csv';salida=base/'mapa.json'
            csv.write_text('Registro digital,Época,Número de Identificación,Rubro (Título / Subtítulo)\n'
                           '2020001,Undécima Época,1a./J. 1/2025 (11a.),PRIMER RUBRO\n'
                           '2020002,Undécima Época,1a./J. 1/2025 (11a.),INCORRECTO\n')
            self.assertEqual(e.normalizar_fila(e.leer_catalogo(csv)[0])['epoca'],'11a')
            args=['enlazar','--catalogo',str(csv),'--fuente',
                  'https://bicentenario.scjn.gob.mx/repositorio-scjn',
                  '--fecha','2026-11-24','--salida',str(salida),'--informe',str(base/'informe')]
            with patch('sys.argv',args), patch.object(e,'documentos',return_value=[doc()]):
                self.assertEqual(e.main(),1)
            self.assertFalse(salida.exists())
            self.assertTrue((base/'informe/importacion.json').exists())

    def test_completo_es_obligatorio_aun_con_todos_los_ejemplos_resueltos(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=['enlazar','--verificar','--exigir-completo','--salida',str(Path(tmp)/'mapa.json')]
            with patch('sys.argv',args),patch.object(e,'documentos',return_value=[doc()]):
                self.assertEqual(e.main(),2)


if __name__=='__main__':
    unittest.main()
