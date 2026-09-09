#!/usr/bin/env python3
"""
Pruebas de la conmutación al acervo local de la Gaceta cuando el API del SJF falla.
Sin red: el cliente HTTP se sustituye por uno que siempre responde 302, que es lo
que devuelve Imperva desde el 3 de septiembre de 2026.

Lo que se comprueba, en orden de importancia:

  1. Con el API caído, `buscar_tesis` e `investigar_criterio` responden con tesis
     de verdad en vez de un mensaje de error, y `ver_tesis` acepta la clave.
  2. NUNCA en silencio: toda respuesta servida del respaldo empieza con el aviso
     "⚠ ACERVO LOCAL", dice el motivo y advierte de la vigencia. Si esta prueba
     se cae, el usuario está citando derecho de respaldo creyendo que consultó el
     SJF, que es el daño más caro que puede hacer este módulo.
  3. El respaldo NO se usa con el API vivo: el dato vivo siempre gana.
  4. Un registro digital no se resuelve desde el respaldo —la Gaceta no lo
     imprime— y en vez de inventarlo se explica el camino por clave.
  5. Sin acervo, la conmutación no tapa el diagnóstico: vuelve el mensaje del WAF.
  6. El bloqueo no dispara auto-descubrimiento ni reintentos (regla de la 2.10.1).

Uso: python3 test_sjf_respaldo.py
"""

import asyncio
import json
import sys

import kriterius_mx as k
import sjf_local

fallos = 0


def comprobar(nombre, condicion, detalle=""):
    global fallos
    if condicion:
        print(f"  [OK]    {nombre}")
    else:
        print(f"  [FALLA] {nombre}" + (f" — {detalle}" if detalle else ""))
        fallos += 1


VISITAS: list[str] = []

RESPUESTA_VIVA = json.dumps({
    "total": 1,
    "documents": [{
        "ius": 2032523, "ta_tj": 0,
        "rubro": "RUBRO QUE SOLO EXISTE EN EL API VIVO.",
        "localizacion": "12a. Época; T.C.C.; Gaceta; Libro 1; Pág. 1; [TA]",
    }],
})


class _Resp:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text

    def json(self):
        return json.loads(self.text)


class _ClienteFalso:
    guion = _Resp(302, "")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, json=None):
        VISITAS.append(url)
        return type(self).guion

    async def get(self, url):
        VISITAS.append(url)
        return _Resp(200, "<html><body>sin rutas de servicios</body></html>")


def _preparar(status, texto=""):
    VISITAS.clear()
    k._CACHE._datos.clear()
    k._PETICIONES.clear()
    _ClienteFalso.guion = _Resp(status, texto)


k.httpx.AsyncClient = lambda *a, **kw: _ClienteFalso()
corre = asyncio.run

print("\n— El acervo carga —")
n = sjf_local.cargar(ruta_indice=None)
comprobar("hay acervo local para respaldar", n > 30000, str(n))


print("\n— buscar_tesis con el API bloqueado —")
_preparar(302)
r = corre(k.buscar_tesis("interés legítimo ambiental"))
comprobar("responde con el aviso de acervo local", r.startswith("⚠ ACERVO LOCAL"), r[:120])
comprobar("dice que fue el cortafuegos y con qué código",
          "cortafuegos" in r and "302" in r, r[:200])
comprobar("advierte que no incluye cambios de vigencia", "vigencia" in r)
comprobar("advierte que no hay registro digital", "registro digital" in r)
comprobar("trae resultados de verdad, no un error",
          "resultado(s) en el acervo local" in r and "Época" in r, r[:200])
comprobar("cada resultado trae el PDF oficial de la Gaceta", "PDF oficial:" in r)
comprobar("no intentó auto-descubrir el endpoint",
          not [u for u in VISITAS if u.endswith(".js") or "/busqueda-principal" in u],
          str(VISITAS))
comprobar("no reintentó la petición bloqueada",
          len([u for u in VISITAS if "/api/public" in u or "/tesis" in u]) <= 1, str(VISITAS))

_preparar(403)
r403 = corre(k.buscar_tesis("prisión preventiva oficiosa"))
comprobar("también conmuta con 403", r403.startswith("⚠ ACERVO LOCAL") and "403" in r403)

_preparar(302)
rf = corre(k.buscar_tesis("expropiación", epocas=["12a"], tipo="jurisprudencia"))
comprobar("respeta los filtros de época y tipo al conmutar",
          rf.startswith("⚠ ACERVO LOCAL")
          and ("Duodécima Época" in rf or "Sin resultados" in rf), rf[:160])


print("\n— investigar_criterio con el API bloqueado —")
_preparar(302)
ri = corre(k.investigar_criterio("interés legítimo", limite=5))
comprobar("conmuta y avisa", ri.startswith("⚠ ACERVO LOCAL"), ri[:120])
comprobar("respeta el límite pedido", ri.count("PDF oficial:") <= 5, str(ri.count("PDF oficial:")))


print("\n— ver_tesis por clave —")
_preparar(302)
rc = corre(k.ver_tesis("(IV Región)1o. J/1 A (12a.)"))
comprobar("resuelve la clave y avisa", rc.startswith("⚠ ACERVO LOCAL"), rc[:120])
comprobar("trae el rubro completo", "DERECHOS POR REVALIDACIÓN ANUAL" in rc)
comprobar("trae texto, precedentes y cita",
          "TEXTO" in rc and "PRECEDENTES" in rc and "CITA" in rc)
comprobar("la cita dice que el registro digital está pendiente",
          "Registro digital: pendiente" in rc)
comprobar("la clave se acepta aunque venga con otro espaciado",
          corre(k.ver_tesis("(IV Region)1o.J/1 A (12a.)")).startswith("⚠ ACERVO LOCAL"))
rx = corre(k.ver_tesis("XX.9o.Z.999 Z (12a.)"))
comprobar("una clave inexistente lo dice sin inventar",
          "No se encontró la clave" in rx, rx[:140])

print("\n— ver_tesis por registro digital con el API bloqueado —")
_preparar(302)
rr = corre(k.ver_tesis(2032415))
comprobar("no dice el falso negativo 'no se encontró la tesis'",
          "No se encontró la tesis" not in rr, rr[:140])
comprobar("explica que la Gaceta no publica el registro digital",
          "no lo publica" in rr or "no se puede consultar por" in rr, rr[:250])
comprobar("ofrece el camino por clave", "ver_tesis(" in rr and "buscar_tesis" in rr)
comprobar("gastó un solo intento, no los dos de isSemanal",
          len([u for u in VISITAS if "/tesis/" in u]) == 1, str(VISITAS))


print("\n— Con el API vivo el respaldo no se toca —")
_preparar(200, RESPUESTA_VIVA)
rv = corre(k.buscar_tesis("interés legítimo"))
comprobar("responde el API, no el acervo", not rv.startswith("⚠ ACERVO LOCAL"), rv[:120])
comprobar("y el contenido es el del API",
          "RUBRO QUE SOLO EXISTE EN EL API VIVO" in rv, rv[:200])


print("\n— Sin acervo, la conmutación no tapa el diagnóstico —")
sjf_local.cargar("no_existe.jsonl.gz", "tampoco.json", None)
sjf_local._INTENTADO = True   # que `asegurar` no lo vuelva a cargar solo
_preparar(302)
rs = corre(k.buscar_tesis("amparo"))
comprobar("vuelve el mensaje del bloqueo, no un aviso vacío",
          not rs.startswith("⚠ ACERVO LOCAL") and "cortafuegos" in rs.lower(), rs[:180])
sjf_local._INTENTADO = False
comprobar("y recargar deja el respaldo listo otra vez",
          sjf_local.cargar(ruta_indice=None) > 0)


print("\n— Estado y diagnóstico —")
e = corre(k.estado_conector())
comprobar("estado_conector reporta el respaldo local", "SJF respaldo local:" in e, e[:400])
comprobar("y dice hasta cuándo llega", "hasta" in e.split("SJF respaldo local:")[1][:200])

print(f"\n{'TODO BIEN' if not fallos else f'{fallos} FALLA(S)'}")
sys.exit(1 if fallos else 0)
