"""Regresiones de tráfico SCJN, sin peticiones de red."""
import asyncio
import unittest
from email.utils import formatdate
from unittest.mock import patch

import httpx
import kriterius_mx as k


class AccesoSCJN(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        k._CACHE._datos.clear()
        k._PETICIONES.clear()
        k._SEMAFOROS.clear()
        k._PAUSAS.clear()
        k._ULTIMA_SCJN.clear()
        k._EN_CURSO.clear()
        self.intervalo = patch.object(k, "INTERVALO_SCJN_S", 0)
        self.intervalo.start()
        self.addCleanup(self.intervalo.stop)

    async def test_reintentos_consumen_cupo(self):
        intentos = 0

        async def falla():
            nonlocal intentos
            intentos += 1
            raise k._RespuestaHTTP(503)

        with patch.object(k, "LIMITE_PETICIONES_MINUTO", 2), \
             patch.object(k, "ESPERA_MAX_CUPO_S", 0):
            with self.assertRaises(k._CupoAgotado):
                await k._traer("sjf", falla)
        self.assertEqual(intentos, 2)
        self.assertEqual(len(k._PETICIONES), 2)

    async def test_retry_after_largo_pausa_otras_consultas(self):
        llamadas = 0

        async def respuesta():
            nonlocal llamadas
            llamadas += 1
            r = httpx.Response(429, headers={"Retry-After": "120"})
            return k._clasificar_respuesta_scjn(r, "tesis")

        with self.assertRaises(k._RespuestaHTTP):
            await k._traer("sjf", respuesta, pausa="sjf:tesis:GET")
        with self.assertRaises(k._PausaFuente):
            await k._traer("sjf", respuesta, pausa="sjf:tesis:POST")
        self.assertEqual(llamadas, 1)
        self.assertFalse(k._merece_redescubrir(k._PausaFuente(429, 120, "cuota")))

    async def test_retry_after_corto_espera_y_recupera(self):
        intentos = []

        async def respuesta():
            intentos.append(k._time.monotonic())
            if len(intentos) == 1:
                raise k._RespuestaHTTP(503, retry_after=1)
            return "ok"

        self.assertEqual(await k._traer("repo", respuesta), "ok")
        self.assertGreaterEqual(intentos[1] - intentos[0], 1)
        self.assertEqual(len(k._PETICIONES), 2)

    async def test_pausa_waf_preserva_busquedas_y_cache(self):
        llamadas = 0

        async def bloqueo():
            nonlocal llamadas
            llamadas += 1
            raise k._BloqueoWAF(200, pagina_html=True)

        with self.assertRaises(k._BloqueoWAF):
            await k._traer("sjf", bloqueo, pausa="sjf:tesis:GET")
        with self.assertRaises(k._PausaFuente):
            await k._traer("sjf", bloqueo, pausa="sjf:tesis:GET")
        k._CACHE.guardar("guardada", "tesis", 60)
        self.assertEqual(await k._traer("sjf", bloqueo, clave="guardada",
                                       pausa="sjf:tesis:GET"), "tesis")

        async def buscar():
            return "resultado"

        self.assertEqual(await k._traer("sjf", buscar, pausa="sjf:tesis:POST"), "resultado")
        self.assertEqual(llamadas, 1)

    async def test_solo_una_comprobacion_tras_vencer_pausa(self):
        k._PAUSAS["detalle"] = (k._time.monotonic() - 1, 403, "WAF")
        llamadas = 0

        async def bloqueo():
            nonlocal llamadas
            llamadas += 1
            await asyncio.sleep(0.01)
            raise k._BloqueoWAF(403)

        resultados = await asyncio.gather(*[
            k._traer("sjf", bloqueo, clave=f"tesis-{i}", pausa="detalle")
            for i in range(10)
        ], return_exceptions=True)
        self.assertEqual(llamadas, 1)
        self.assertEqual(sum(isinstance(e, k._PausaFuente) for e in resultados), 9)

    async def test_consultas_identicas_comparten_peticion(self):
        llamadas = 0

        async def pedir():
            nonlocal llamadas
            llamadas += 1
            await asyncio.sleep(0.01)
            return {"id": 2012594}

        resultados = await asyncio.gather(*[
            k._traer("sjf", pedir, clave="misma", ttl=60) for _ in range(12)
        ])
        self.assertEqual(llamadas, 1)
        self.assertTrue(all(r == {"id": 2012594} for r in resultados))
        self.assertFalse(k._EN_CURSO)

    async def test_cancelar_un_consumidor_no_cancela_otro(self):
        inicio, terminar = asyncio.Event(), asyncio.Event()
        llamadas = 0

        async def pedir():
            nonlocal llamadas
            llamadas += 1
            inicio.set()
            await terminar.wait()
            return "ok"

        a = asyncio.create_task(k._traer("sjf", pedir, clave="misma", ttl=60))
        await inicio.wait()
        b = asyncio.create_task(k._traer("sjf", pedir, clave="misma", ttl=60))
        await asyncio.sleep(0)
        a.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await a
        terminar.set()
        self.assertEqual(await b, "ok")
        self.assertEqual(llamadas, 1)

    async def test_fallo_compartido_no_se_guarda_en_cache(self):
        async def falla():
            await asyncio.sleep(0.01)
            raise k._RespuestaHTTP(404)

        resultados = await asyncio.gather(*[
            k._traer("sjf", falla, clave="misma", ttl=60) for _ in range(4)
        ], return_exceptions=True)
        self.assertTrue(all(isinstance(r, k._RespuestaHTTP) for r in resultados))
        self.assertIsNone(k._CACHE.obtener("misma"))
        self.assertFalse(k._EN_CURSO)

    async def test_intervalo_y_concurrencia_scjn(self):
        momentos = []

        async def pedir():
            momentos.append(k._time.monotonic())
            return "ok"

        with patch.object(k, "INTERVALO_SCJN_S", 0.04):
            await asyncio.gather(*[k._traer("sjf", pedir) for _ in range(3)])
        self.assertTrue(all(b - a >= 0.039 for a, b in zip(momentos, momentos[1:])))

    def test_redireccion_normal_no_se_atribuye_a_imperva(self):
        # Un documento válido puede mencionar el nombre de un proveedor.
        datos = {"texto": "Incapsula: Request unsuccessful"}
        self.assertEqual(k._clasificar_respuesta_scjn(httpx.Response(200, json=datos),
                                                     "tesis"), datos)
        for status in (301, 302, 403):
            with self.assertRaises(k._RespuestaHTTP) as err:
                k._clasificar_respuesta_scjn(httpx.Response(status, headers={
                    "Location": "/nueva-ruta"}), "tesis")
            self.assertNotIsInstance(err.exception, k._BloqueoWAF)
        with self.assertRaises(k._BloqueoWAF):
            k._clasificar_respuesta_scjn(httpx.Response(302, headers={
                "Location": "/_Incapsula_Resource?x=1"}), "tesis")

    def test_retry_after_fecha_y_valores_invalidos(self):
        self.assertEqual(k._retry_after("120"), 120)
        with patch.object(k._time, "time", return_value=1_700_000_000):
            self.assertEqual(k._retry_after(formatdate(1_700_000_060, usegmt=True)), 60)
            self.assertEqual(k._retry_after(formatdate(1_699_999_000, usegmt=True)), 0)
        for invalido in (None, "", "no-es-fecha", "-1", "NaN"):
            self.assertIsNone(k._retry_after(invalido))

    def test_cache_limita_bytes_y_conserva_lo_mas_usado(self):
        cache = k._Cache(max_entradas=10, max_bytes=20)
        cache.guardar("a", "a" * 6, 60)  # ocho bytes JSON con las comillas
        cache.guardar("b", "b" * 6, 60)
        self.assertEqual(cache.obtener("a"), "a" * 6)
        cache.guardar("c", "c" * 6, 60)
        self.assertIsNone(cache.obtener("b"))
        self.assertEqual(cache.obtener("a"), "a" * 6)
        cache.guardar("grande", "x" * 30, 60)
        self.assertIsNone(cache.obtener("grande"))
        cache.guardar("a", "x" * 30, 60)
        self.assertIsNone(cache.obtener("a"))  # no conservar versión anterior
        self.assertLessEqual(sum(cache._pesos.values()), 20)


if __name__ == "__main__":
    unittest.main()
