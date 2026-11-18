"""Regresiones de integridad y lectura del portal semanal; sin tráfico judicial."""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, AsyncMock

import sjf_semanal as s

VISIBLE = (Path(__file__).parent / "fixtures_semanal/2032910.txt").read_text()


class Fichas(unittest.TestCase):
    def test_conserva_texto_precedentes_y_cierre(self):
        d = s.interpretar(VISIBLE, 2032910)
        self.assertEqual(d["claveTesis"], "P./J. 222/2026 (12a.)")
        self.assertIn("ultima ratio", d["texto"])
        self.assertIn("Impedimento 33/2024.", d["texto"])
        self.assertIn("El Comité de Revisión", d["texto"])
        self.assertIn("17 de noviembre de 2026", d["textoPublicacion"])
        # Todo lo que aparece entre rubro y cierre queda íntegro, en su orden.
        lines = [x.strip() for x in VISIBLE.splitlines() if x.strip()]
        self.assertEqual(d["texto"], "\n\n".join(lines[9:-1]))

    def test_no_depende_de_pleno_ni_de_secciones_nuevas(self):
        variante = VISIBLE.replace("PLENO.", "TRIBUNAL COLEGIADO.").replace("Hechos: ", "").replace("Criterio jurídico: ", "").replace("Justificación: ", "")
        self.assertIn("TRIBUNAL COLEGIADO.", s.interpretar(variante, 2032910)["texto"])

    def test_rechaza_identidad_distinta_y_duplicada(self):
        for raw, ius in [(VISIBLE, 2032921), (VISIBLE + "\nRegistro digital: 2032910", 2032910)]:
            with self.subTest(ius=ius), self.assertRaises(ValueError):
                s.interpretar(raw, ius)

    def test_rechaza_ficha_cortada_y_html_de_bloqueo(self):
        for raw in [VISIBLE.split("Esta tesis se publicó")[0], "Request unsuccessful. Incapsula incident ID", "<script>challenge</script>"]:
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):
                s.interpretar(raw, 2032910)

    def test_rechaza_fecha_contradictoria(self):
        with self.assertRaises(ValueError):
            s.interpretar(VISIBLE.replace("Publicación: Viernes 13", "Publicación: Viernes 20"), 2032910)

    def test_rechaza_falta_metadata(self):
        with self.assertRaises(ValueError):
            s.interpretar(VISIBLE.replace("Instancia: Pleno", ""), 2032910)

    def test_integridad_documento_y_raw(self):
        for alteracion in ("texto", "raw", "url", "schema"):
            c = s.empaquetar(VISIBLE, 2032910)
            if alteracion == "texto":
                c["documento"]["texto"] = "Texto sustituido"
            elif alteracion == "raw":
                c["texto_visible"] += "alterado"
            elif alteracion == "url":
                c["procedencia"]["url"] = "https://otro.example/2032910"
            else:
                c["schema"] = 99
            with self.subTest(alteracion=alteracion), self.assertRaises(ValueError):
                s.leer_captura(c, 2032910)

    def test_conserva_capturas_antiguas_y_rechaza_futuras(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=3650)).isoformat()
        c = s.empaquetar(VISIBLE, 2032910, capturado=old)
        self.assertEqual(s.leer_captura(c, 2032910, ahora=now)['_procedencia']['capturado_utc'], old)
        for fecha in [(now + timedelta(hours=1)).isoformat(), now.replace(tzinfo=None).isoformat()]:
            c = s.empaquetar(VISIBLE, 2032910, capturado=fecha)
            with self.assertRaises(ValueError):
                s.leer_captura(c, 2032910, ahora=now)

    def test_no_traversal(self):
        for ius in ["../2032910", "2032910/otro", "0", "x", "2032910.0"]:
            self.assertIsNone(s.obtener(ius))

    def test_guardado_atomico_y_lectura(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"KRITERIUS_SEMANAL_DIR": tmp}), patch.object(s, "DIRECTORIO_DISTRIBUIDO", Path(tmp) / "sin_snapshot"):
            c = s.empaquetar(VISIBLE, 2032910)
            s.guardar(c, tmp)
            self.assertEqual(list(Path(tmp).iterdir()), [Path(tmp) / "2032910.json"])
            self.assertEqual(s.obtener(2032910)["texto"], c["documento"]["texto"])
            (Path(tmp) / "2032910.json").write_text("{parcial")
            self.assertIsNone(s.obtener(2032910))

    def test_mcp_devuelve_completa_sin_red(self):
        import kriterius_mx as k
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"KRITERIUS_SEMANAL_DIR": tmp}):
            c = s.empaquetar(VISIBLE, 2032910)
            s.guardar(c, tmp)
            with patch.object(k, "_sjf_fetch", AsyncMock(side_effect=AssertionError("No debe hacer red"))), patch.object(k, "_repo_documento", AsyncMock(side_effect=AssertionError("No debe hacer red"))):
                result = asyncio.run(k.ver_tesis(2032910))
            self.assertIn(c["documento"]["texto"], result)
            self.assertIn(c["documento"]["textoPublicacion"], result)
            self.assertIn(c["procedencia"]["url"], result)
            self.assertIn("no firma de la SCJN", result)

    def test_manifiesto_excluye_capturas_invalidas_y_no_publica_textos(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"KRITERIUS_SEMANAL_DIR": tmp}), patch.object(s, "DIRECTORIO_DISTRIBUIDO", Path(tmp)):
            s.guardar(s.empaquetar(VISIBLE,2032910),tmp)
            (Path(tmp)/'2032921.json').write_text('{parcial')
            manifest=s.manifiesto()
            self.assertEqual(list(manifest['registros']),['2032910'])
            self.assertNotIn('texto',manifest['registros']['2032910'])
            self.assertEqual(manifest['total'],1)


if __name__ == "__main__":
    unittest.main()
