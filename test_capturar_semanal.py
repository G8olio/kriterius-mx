import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import capturar_semanal as c


class Captura(unittest.IsolatedAsyncioTestCase):
    def test_lote_acotado_sin_duplicados(self):
        for values in [[], [2032910] * 2, list(range(2032900, 2032906)), ["--salida"], ["../tesis"]]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                c.validar_registros(values)

    def test_rechazos_http_no_se_aceptan_como_texto(self):
        for status in [401, 403, 429, 500, 503]:
            with self.assertRaises(c.CapturaInterrumpida):
                c.verificar_http(SimpleNamespace(status=status))

    async def test_403_detiene_lote_y_cierra_navegador(self):
        page = Mock()
        page.url = c.BASE + "/presentacion"
        page.goto = AsyncMock(return_value=SimpleNamespace(status=403,
            url=page.url, all_headers=AsyncMock(return_value={"content-type":"text/html"}),
            body=AsyncMock(return_value=b"Acceso rechazado")))
        page.locator.return_value.inner_text = AsyncMock(return_value="Acceso rechazado")
        page.screenshot = AsyncMock()
        context = SimpleNamespace(new_page=AsyncMock(return_value=page))
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
        pw = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
        manager = AsyncMock()
        manager.__aenter__.return_value = pw
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(c.CapturaInterrumpida):
                await c.capturar_lote([2032910, 2032896, 2032921], Path(tmp) / "datos", Path(tmp) / "evidencia", fabrica=lambda: manager)
            report = json.loads((Path(tmp) / "evidencia/informe.json").read_text())
            self.assertEqual(report["estado"], "interrumpido")
            self.assertEqual(report["capturados"], [])
            self.assertEqual(report["navegaciones"][0]["status"], 403)
            self.assertFalse((Path(tmp) / "datos").exists())
        page.goto.assert_awaited_once()
        browser.close.assert_awaited_once()

    async def test_diagnostico_no_guarda_cookies_tokens_ni_html(self):
        response = SimpleNamespace(status=403, url=c.BASE + "/presentacion?token=privado",
            all_headers=AsyncMock(return_value={"set-cookie":"sesion=privada", "x-token":"privado",
                "server":"ejemplo", "content-type":"text/html"}),
            body=AsyncMock(return_value=b"<html>Incapsula Request unsuccessful token=privado</html>"))
        d = await c.diagnosticar_respuesta(response)
        self.assertEqual(d["marcadores"], ["incapsula", "request unsuccessful"])
        self.assertNotIn("privado", json.dumps(d))
        self.assertNotIn("set-cookie", d["cabeceras"])
        self.assertEqual(d["url"], c.BASE + "/presentacion")

    async def test_diagnostico_fallido_conserva_rechazo_http(self):
        response = SimpleNamespace(status=403, url=c.BASE + "/presentacion",
            all_headers=AsyncMock(side_effect=RuntimeError("fallo")))
        d = await c.diagnosticar_respuesta(response)
        self.assertEqual(d["status"], 403)
        self.assertEqual(d["diagnostico_incompleto"], "RuntimeError")
        with self.assertRaises(c.CapturaInterrumpida):
            c.verificar_http(response)

    async def test_sin_registros_no_inicia_navegador(self):
        factory = Mock()
        with self.assertRaises(ValueError):
            await c.capturar_lote([], "no_crear", "no_crear", fabrica=factory)
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
