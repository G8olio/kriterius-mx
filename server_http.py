"""
KriteriusMX — servidor MCP remoto sobre HTTP.

Expone las 15 tools de kriterius_mx.py como conector remoto para claude.ai.
El endpoint MCP queda en https://<dominio>/mcp

Además publica dos rutas para humanos y para el monitoreo del hosting:
    GET /        una página mínima que confirma que el servicio está vivo
    GET /salud   respuesta JSON para el health check automático de Render

Variables de entorno:
    PORT   puerto de escucha (Render la define sola; por defecto 8000)
"""

import os
from datetime import datetime, timezone

from starlette.responses import HTMLResponse, JSONResponse

from kriterius_mx import mcp

VERSION = "2.6.0"
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


if __name__ == "__main__":
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http")
