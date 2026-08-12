"""
Medición de uso de KriteriusMX. Contadores en memoria, sin base de datos.

Qué cuenta y por qué así:

- **Sesiones MCP**, no usuarios. Claude se conecta desde la infraestructura de Anthropic:
  todas las peticiones llegan del mismo origen, así que no hay forma honesta de contar
  personas. Una sesión MCP se parece a una conversación.
- **Sesiones donde solo se listaron herramientas y nunca se llamó ninguna.** Es la métrica
  de adopción más franca que existe aquí: separa "lo tiene instalado" de "lo usa".
- **Llamadas por herramienta.**
- **Visitas al sitio**, que llegan por el faro `/visita` (ver abajo), para tener las dos
  mitades del embudo: cuántos llegaron a la página de instalación contra cuántos
  terminaron conectando.

Qué NO guarda, a propósito:

- Términos de búsqueda. Son estrategia de litigio ajena, y guardarlos crea obligaciones
  de datos personales que hoy no existen.
- IP, user-agent, cookies ni nada que identifique a una persona. Solo se cuenta.

Los contadores viven en memoria: un despliegue nuevo los deja en cero. Por eso la página
`/uso` reporta `desde`, y por eso el plan incluye una tarea semanal que se lleve los
números a un archivo. Un contenedor de 512 MB no aguanta historial, y meter una base de
datos por ~600 filas al mes no se paga.
"""

import base64
import json
import os
import secrets
from collections import Counter, OrderedDict
from datetime import datetime, timezone

from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

CLAVE_ENV = "CLAVE_USO"

# Techos para que nadie infle la memoria mandando basura variada
MAX_SESIONES_VIVAS = 5000
MAX_ETIQUETAS = 100
MAX_DIAS = 90
MAX_CUERPO = 256 * 1024

# Las únicas páginas del sitio que se cuentan; cualquier otra cae en "otra".
# Sin esta lista, un `?p=` distinto en cada petición haría crecer el diccionario sin fin.
PAGINAS = {"inicio", "instalacion", "otra"}


def _sumar_acotado(contador: Counter, clave: str, cuanto: int = 1) -> None:
    """Suma en un contador que no debe crecer sin límite: lo que no cabe va a 'otras'."""
    if clave not in contador and len(contador) >= MAX_ETIQUETAS:
        contador["otras"] += cuanto
        return
    contador[clave] += cuanto


class Contadores:
    """Todo el estado de la medición. Una sola instancia por proceso."""

    def __init__(self):
        self.desde = datetime.now(timezone.utc)
        # sesión viva -> [listados, llamadas]; se desalojan las más viejas
        self._vivas: OrderedDict[str, list[int]] = OrderedDict()
        # Al desalojar una sesión no se pierde: su resumen se pliega aquí
        self._plegadas_solo_listado = 0
        self._plegadas_con_llamadas = 0
        self._plegadas_mudas = 0
        self.sesiones_totales = 0
        self.metodos: Counter = Counter()
        self.herramientas: Counter = Counter()
        self.visitas_sitio: Counter = Counter()
        self.visitas_por_dia: OrderedDict[str, int] = OrderedDict()

    # ---- registro ----

    def _registro(self, sid: str) -> list[int]:
        rec = self._vivas.get(sid)
        if rec is None:
            rec = [0, 0]
            self._vivas[sid] = rec
            self.sesiones_totales += 1
            while len(self._vivas) > MAX_SESIONES_VIVAS:
                _, viejo = self._vivas.popitem(last=False)
                self._plegar(viejo)
        self._vivas.move_to_end(sid)
        return rec

    def _plegar(self, rec: list[int]) -> None:
        listados, llamadas = rec
        if llamadas:
            self._plegadas_con_llamadas += 1
        elif listados:
            self._plegadas_solo_listado += 1
        else:
            self._plegadas_mudas += 1

    def registrar(self, sid: str | None, eventos: list[tuple[str, str | None]]) -> None:
        """`eventos` son los pares (método JSON-RPC, herramienta) de una petición."""
        rec = self._registro(sid) if sid else None
        for metodo, herramienta in eventos:
            _sumar_acotado(self.metodos, metodo)
            if metodo == "tools/list" and rec is not None:
                rec[0] += 1
            elif metodo == "tools/call":
                if rec is not None:
                    rec[1] += 1
                _sumar_acotado(self.herramientas, herramienta or "(sin nombre)")

    def registrar_visita(self, pagina: str) -> None:
        pagina = pagina if pagina in PAGINAS else "otra"
        self.visitas_sitio[pagina] += 1
        dia = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.visitas_por_dia[dia] = self.visitas_por_dia.get(dia, 0) + 1
        while len(self.visitas_por_dia) > MAX_DIAS:
            self.visitas_por_dia.popitem(last=False)

    # ---- lectura ----

    def resumen(self, version: str = "") -> dict:
        solo_listado = self._plegadas_solo_listado
        con_llamadas = self._plegadas_con_llamadas
        mudas = self._plegadas_mudas
        for listados, llamadas in self._vivas.values():
            if llamadas:
                con_llamadas += 1
            elif listados:
                solo_listado += 1
            else:
                mudas += 1

        visitas_instalacion = self.visitas_sitio.get("instalacion", 0)
        return {
            "servicio": "KriteriusMX",
            "version": version,
            "desde": self.desde.isoformat(timespec="seconds"),
            "ahora": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sesiones": {
                "total": self.sesiones_totales,
                "con_llamadas": con_llamadas,
                "solo_listado": solo_listado,
                "sin_actividad": mudas,
                "vivas_en_memoria": len(self._vivas),
            },
            "llamadas_por_herramienta": dict(self.herramientas.most_common()),
            "metodos": dict(self.metodos.most_common()),
            "sitio": {
                "total": sum(self.visitas_sitio.values()),
                "por_pagina": dict(self.visitas_sitio.most_common()),
                "por_dia": dict(self.visitas_por_dia),
            },
            "embudo": {
                "visitas_instalacion": visitas_instalacion,
                "sesiones": self.sesiones_totales,
                "sesiones_con_llamadas": con_llamadas,
                "conversion_a_sesion": (
                    round(self.sesiones_totales / visitas_instalacion, 3)
                    if visitas_instalacion else None),
            },
        }


CONTADORES = Contadores()


# ---- Middleware: cuenta sin tocar el transporte ----

def _cabecera(scope, nombre: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k.lower() == nombre:
            return v.decode("latin-1")
    return None


def _cabecera_respuesta(mensaje, nombre: bytes) -> str | None:
    for k, v in mensaje.get("headers", []):
        if k.lower() == nombre:
            return v.decode("latin-1")
    return None


def _leer_jsonrpc(cuerpo: bytes) -> list[tuple[str, str | None]]:
    """Saca los pares (método, herramienta) de un cuerpo JSON-RPC, que puede venir
    como objeto suelto o como lote. Si no se entiende, no se cuenta nada: la medición
    nunca debe tumbar una petición."""
    try:
        datos = json.loads(cuerpo)
    except Exception:
        return []
    mensajes = datos if isinstance(datos, list) else [datos]
    eventos = []
    for m in mensajes:
        if not isinstance(m, dict):
            continue
        metodo = m.get("method")
        if not isinstance(metodo, str):
            continue
        herramienta = None
        if metodo == "tools/call":
            params = m.get("params")
            if isinstance(params, dict):
                nombre = params.get("name")
                if isinstance(nombre, str):
                    herramienta = nombre[:64]
        eventos.append((metodo[:64], herramienta))
    return eventos


class Medicion:
    """Middleware ASGI puro, no BaseHTTPMiddleware: este transporte usa respuestas en
    streaming y envolverlas en Request/Response rompe el flujo.

    Solo mira los POST al endpoint MCP. Los escaneos de vulnerabilidades que tantean
    rutas al azar no entran en las cuentas, que es justo lo que inflaba las visitas
    crudas de los registros."""

    def __init__(self, app, ruta: str = "/mcp", contadores: Contadores = CONTADORES):
        self.app = app
        self.ruta = ruta.rstrip("/") or "/mcp"
        self.c = contadores

    async def __call__(self, scope, receive, send):
        if (scope.get("type") != "http" or scope.get("method") != "POST"
                or (scope.get("path") or "").rstrip("/") != self.ruta):
            return await self.app(scope, receive, send)

        # Se guarda el cuerpo para poder leerlo y volver a entregarlo intacto
        guardados, cuerpo = [], bytearray()
        while True:
            mensaje = await receive()
            guardados.append(mensaje)
            if mensaje["type"] != "http.request":
                break
            cuerpo += mensaje.get("body", b"")
            if not mensaje.get("more_body") or len(cuerpo) > MAX_CUERPO:
                break

        pendiente = iter(guardados)

        async def repetir():
            for mensaje in pendiente:
                return mensaje
            return await receive()

        eventos = _leer_jsonrpc(bytes(cuerpo))
        sid_peticion = _cabecera(scope, b"mcp-session-id")
        contado = False

        async def enviar(mensaje):
            nonlocal contado
            if mensaje["type"] == "http.response.start" and not contado:
                contado = True
                # En `initialize` el id de sesión no viene en la petición: lo asigna el
                # servidor y lo devuelve en la respuesta. Por eso se cuenta aquí.
                sid = _cabecera_respuesta(mensaje, b"mcp-session-id") or sid_peticion
                try:
                    self.c.registrar(sid, eventos)
                except Exception:
                    pass  # medir jamás debe romper una petición
            await send(mensaje)

        await self.app(scope, repetir, enviar)


# ---- Rutas ----

# GIF transparente de 1x1: el faro del sitio. Un <img> no necesita CORS ni JavaScript,
# y si el navegador lo bloquea no se rompe nada de la página.
_GIF = base64.b64decode(b"R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


async def visita(request):
    """Faro del sitio: `<img src="https://mcp.kriterius.mx/visita?p=instalacion">`.
    Cuenta cargas de página, no personas: sin cookies no hay forma de distinguirlas,
    y ponerlas no vale lo que cuesta en avisos de privacidad."""
    try:
        CONTADORES.registrar_visita(request.query_params.get("p", "otra"))
    except Exception:
        pass
    return Response(_GIF, media_type="image/gif", headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    })


def _autorizado(request) -> tuple[bool, Response | None]:
    esperada = os.environ.get(CLAVE_ENV, "").strip()
    if not esperada:
        # Sin clave configurada la página se apaga, no se abre: es más seguro
        # equivocarse hacia el silencio.
        return False, JSONResponse(
            {"error": f"Medición apagada: falta la variable de entorno {CLAVE_ENV}."},
            status_code=503)
    dada = request.query_params.get("clave", "")
    if not secrets.compare_digest(dada, esperada):
        # 404 y no 401: a quien tantea rutas no hay por qué confirmarle que existe
        return False, PlainTextResponse("No encontrado", status_code=404)
    return True, None


async def pagina_uso(request, version: str = ""):
    ok, respuesta = _autorizado(request)
    if not ok:
        return respuesta
    datos = CONTADORES.resumen(version)
    if request.query_params.get("formato") == "json":
        return JSONResponse(datos)
    return HTMLResponse(_html(datos))


def _fila(k, v):
    return f"<tr><td>{k}</td><td class='n'>{v}</td></tr>"


def _html(d: dict) -> str:
    s = d["sesiones"]
    tot = s["total"] or 1
    herramientas = "".join(_fila(k, v) for k, v in d["llamadas_por_herramienta"].items()) \
        or "<tr><td colspan='2'>Todavía ninguna.</td></tr>"
    dias = "".join(_fila(k, v) for k, v in reversed(list(d["sitio"]["por_dia"].items()))) \
        or "<tr><td colspan='2'>Todavía ninguna.</td></tr>"
    paginas = "".join(_fila(k, v) for k, v in d["sitio"]["por_pagina"].items()) \
        or "<tr><td colspan='2'>Todavía ninguna.</td></tr>"
    conv = d["embudo"]["conversion_a_sesion"]
    return f"""<!doctype html>
<html lang="es"><meta charset="utf-8">
<title>KriteriusMX — uso</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 46rem;
         margin: 3rem auto; padding: 0 1.5rem; line-height: 1.55; color: #1a1a1a; }}
  h2 {{ margin-top: 2.5rem; font-size: 1.05rem; text-transform: uppercase;
        letter-spacing: .04em; color: #555; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: .5rem; }}
  td {{ border-bottom: 1px solid #eee; padding: .4rem .2rem; }}
  td.n {{ text-align: right; font-variant-numeric: tabular-nums; width: 7rem; }}
  .grande {{ font-size: 2rem; font-weight: 600; }}
  .nota {{ color: #666; font-size: .88rem; }}
</style>
<h1>KriteriusMX — uso</h1>
<p class="nota">Versión {d['version']} · contando desde {d['desde']} · corte {d['ahora']}<br>
Los contadores viven en memoria: un despliegue nuevo los reinicia.</p>

<h2>Sesiones</h2>
<p class="grande">{s['total']}</p>
<table>
  {_fila("Con llamadas a herramientas", s['con_llamadas'])}
  {_fila("Solo listaron herramientas, nunca llamaron", s['solo_listado'])}
  {_fila("Sin actividad", s['sin_actividad'])}
</table>
<p class="nota">Adopción real: {s['con_llamadas'] * 100 // tot}% de las sesiones llegó a
usar alguna herramienta. Una sesión MCP se parece a una conversación, no a una persona.</p>

<h2>Llamadas por herramienta</h2>
<table>{herramientas}</table>

<h2>Embudo</h2>
<table>
  {_fila("Visitas a la página de instalación", d['embudo']['visitas_instalacion'])}
  {_fila("Sesiones del conector", d['embudo']['sesiones'])}
  {_fila("Sesiones que usaron algo", d['embudo']['sesiones_con_llamadas'])}
  {_fila("Sesiones por visita", conv if conv is not None else "—")}
</table>

<h2>Sitio, por página</h2>
<table>{paginas}</table>

<h2>Sitio, por día</h2>
<table>{dias}</table>

<p class="nota">Agrega <code>&amp;formato=json</code> para leerlo desde un programa.
No se guardan términos de búsqueda, ni IP, ni user-agent, ni cookies.</p>
</html>"""
