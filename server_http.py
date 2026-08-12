"""
KriteriusMX — servidor MCP remoto sobre HTTP.

Expone las 15 tools de kriterius_mx.py como conector remoto para claude.ai.
El endpoint MCP queda en https://<dominio>/mcp

Además publica cuatro rutas para humanos, para el monitoreo del hosting y para medir uso:
    GET /         una página mínima que confirma que el servicio está vivo
    GET /salud    respuesta JSON para el health check automático del hosting
    GET /uso      panel de adopción, privado (requiere ?clave=...)
    GET /visita   faro de 1x1 que el sitio carga para contar visitas

Variables de entorno:
    PORT       puerto de escucha (el hosting la define solo; por defecto 8000)
    CLAVE_USO  clave de /uso. Sin ella, /uso responde 503 y no expone nada.
"""

import os
from datetime import datetime, timezone

from starlette.responses import HTMLResponse, JSONResponse

# La versión vive en kriterius_mx.py y solo ahí. Cuando estaba duplicada aquí,
# /salud siguió anunciando la 2.6.0 con la 2.7.0 ya desplegada.
from kriterius_mx import mcp, VERSION
import uso

ARRANQUE = datetime.now(timezone.utc)


@mcp.custom_route("/salud", methods=["GET"])
async def salud(request):
    """Health check. Render lo consulta para saber si debe reiniciar el servicio."""
    return JSONResponse({
        "servicio": "KriteriusMX",
        "estado": "vivo",
        "version": VERSION,
        "endpoint_mcp": "/mcp",
        "segundos_encendido": int((datetime.now(timezone.utc) - ARRANQUE).total_seconds()),
    })


@mcp.custom_route("/", methods=["GET"])
async def inicio(request):
    """Página mínima: quien abra el dominio en el navegador ve algo con sentido,
    no un error 404."""
    return HTMLResponse(f"""<!doctype html>
<html lang="es"><meta charset="utf-8">
<title>KriteriusMX — servidor activo</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 40rem;
         margin: 4rem auto; padding: 0 1.5rem; line-height: 1.6; color: #1a1a1a; }}
  code {{ background: #f4f4f5; padding: .15rem .4rem; border-radius: .25rem; }}
  .ok {{ color: #15803d; font-weight: 600; }}
</style>
<h1>KriteriusMX</h1>
<p class="ok">● Servidor activo — versión {VERSION}</p>
<p>Este es el servidor del conector. No es la página del proyecto:
   visita <a href="https://kriterius.mx">kriterius.mx</a> para las instrucciones.</p>
<p>Para agregarlo en Claude, la dirección del conector es:<br>
   <code>https://mcp.kriterius.mx/mcp</code></p>
</html>""")


@mcp.custom_route("/uso", methods=["GET"])
async def panel_uso(request):
    """Panel de adopción. Privado: sin la clave correcta responde 404."""
    return await uso.pagina_uso(request, VERSION)


@mcp.custom_route("/visita", methods=["GET"])
async def visita(request):
    """Faro del sitio. Devuelve un GIF transparente de 1x1 y cuenta la carga."""
    return await uso.visita(request)


# El middleware se cuelga envolviendo el constructor de la app, no cambiando el arranque:
# `mcp.run` sigue siendo quien levanta uvicorn con su propia configuración, que ya está
# probada en producción. Tocar esa parte para meter la medición sería cambiar lo que
# funciona por lo que apenas se estrena.
_construir_app = mcp.streamable_http_app


def _app_con_medicion():
    app = _construir_app()
    app.add_middleware(uso.Medicion, ruta=mcp.settings.streamable_http_path)
    return app


mcp.streamable_http_app = _app_con_medicion


if __name__ == "__main__":
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http")
