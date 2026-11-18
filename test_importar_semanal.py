"""Importación de las tres fichas reales y rechazo de mezclas/truncamientos."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import importar_semanal as i
import sjf_semanal as s

ROOT = Path(__file__).parent
REGISTROS = ["2032910", "2032896", "2032921"]
TABLA = ROOT / "fixtures_semanal/repositorio-20261116.json"


class RelojCaptura(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 16, 8, tzinfo=timezone.utc)


class Importacion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origen = self.root / "origen"
        self.origen.mkdir()
        self.salida = self.root / "datos"
        for registro in REGISTROS:
            c = json.loads((s.DIRECTORIO_DISTRIBUIDO / f"{registro}.json").read_text())
            (self.origen / f"{registro}.txt").write_text(c["texto_visible"])
            (self.origen / f"{registro}.meta.json").write_text(json.dumps(c["procedencia"]))
        self.clock = patch.object(s, "datetime", RelojCaptura)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def importar(self):
        return i.importar(self.origen, self.salida, REGISTROS, repositorio=TABLA)

    def test_tres_reales_coinciden_y_conservan_canal_y_fecha(self):
        report = self.importar()
        self.assertEqual(report["consultas_judiciales"], 0)
        self.assertEqual(len(list(self.salida.glob("*.json"))), 3)
        for r in REGISTROS:
            c = json.loads((self.salida / f"{r}.json").read_text())
            meta = json.loads((self.origen / f"{r}.meta.json").read_text())
            self.assertEqual(c["procedencia"]["capturado_utc"], meta["capturado_utc"])
            self.assertIn("Chrome mediante CUA", c["procedencia"]["canal"])
            checks = c["procedencia"]["contraste_repositorio"]["comprobaciones"]
            self.assertTrue(all(checks.values()))
            self.assertEqual(s.leer_captura(c, r)["ius"], int(r))

    def test_ultima_ficha_incompleta_impide_publicar_lote(self):
        p = self.origen / "2032921.txt"
        p.write_text(p.read_text().split("Esta tesis se publicó")[0])
        with self.assertRaises(ValueError):
            self.importar()
        self.assertFalse(self.salida.exists())

    def test_texto_alterado_se_detecta_en_contraste(self):
        p = self.origen / "2032910.txt"
        p.write_text(p.read_text().replace("ultima ratio", "otra expresión"))
        with self.assertRaisesRegex(ValueError, "texto_y_precedentes"):
            self.importar()
        self.assertFalse(self.salida.exists())

    def test_url_y_fecha_original_son_obligatorias(self):
        p = self.origen / "2032910.meta.json"
        original = json.loads(p.read_text())
        for field, value in [("url", s.BASE + "/detalle/tesis/2032921"), ("capturado_utc", "")]:
            with self.subTest(field=field):
                p.write_text(json.dumps({**original, field: value}))
                with self.assertRaises(ValueError):
                    self.importar()
                self.assertFalse(self.salida.exists())

    def test_columnas_distintas_se_rechazan(self):
        tabla = json.loads(TABLA.read_text())
        tabla["filas"][0][12] = "Resumen"
        path = self.root / "tabla.json"
        path.write_text(json.dumps(tabla))
        with self.assertRaises(ValueError):
            i.tabla_repositorio(path)

    def test_importa_capturas_antiguas_sin_renovar_fecha(self):
        p = self.origen / "2032910.meta.json"
        meta = json.loads(p.read_text())
        meta["capturado_utc"] = "2026-10-01T00:00:00+00:00"
        p.write_text(json.dumps(meta))
        self.importar()
        guardada = json.loads((self.salida / "2032910.json").read_text())
        self.assertEqual(guardada['procedencia']['capturado_utc'], meta['capturado_utc'])


if __name__ == "__main__":
    unittest.main()
