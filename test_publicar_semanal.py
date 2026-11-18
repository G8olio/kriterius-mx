import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import publicar_semanal as p
import sjf_semanal as s


class Publicar(unittest.TestCase):
    def test_codigo_y_borrados_no_se_admiten(self):
        with self.assertRaises(ValueError):p.validar_archivos('.', ['kriterius_mx.py'])
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(OSError):
            p.validar_archivos(tmp,['kriterius_datos/sjf_semanal/2032910.json'])

    def test_valida_integridad_de_documento_antes_del_commit(self):
        path='kriterius_datos/sjf_semanal/2032910.json'
        c=json.loads((Path(__file__).parent/path).read_text())
        c['procedencia']['capturado_utc']=datetime.now(timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/path;dest.parent.mkdir(parents=True)
            dest.write_text(json.dumps(c))
            self.assertEqual(p.validar_archivos(tmp,[path]),['2032910'])
            c['documento']['texto']='Contenido sustituido'
            dest.write_text(json.dumps(c))
            with self.assertRaises(ValueError):p.validar_archivos(tmp,[path])

    def test_no_publica_en_otra_rama(self):
        with patch.object(p,'git',return_value='rama-ajena') as git:
            with self.assertRaises(ValueError):p.publicar('.')
            self.assertEqual(git.call_count,1)


if __name__=='__main__':unittest.main()
