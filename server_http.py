"""
KriteriusMX — servidor MCP remoto sobre HTTP.

Expone las tools de kriterius_mx.py como conector remoto para claude.ai.
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

import asyncio as _asyncio
import os
from datetime import datetime, timezone

from starlette.responses import HTMLResponse, JSONResponse

# La versión vive en kriterius_mx.py y solo ahí. Cuando estaba duplicada aquí,
# /salud siguió anunciando la 2.6.0 con la 2.7.0 ya desplegada.
from kriterius_mx import mcp, VERSION
import tepjf
import cobros
import correo
import expedientes
import historial
import padron as padron_mod
import plus_tools
import promos
import suscripcion
import uso
import vigilancia

# El snapshot del TEPJF se indexa una vez, al arrancar, antes de aceptar tráfico:
# es un segundo de trabajo que ahorra la latencia de la primera consulta. Si el
# archivo faltara, `cargar` devuelve 0 y el servidor arranca igual: las tools del
# TEPJF avisan que la fuente no está disponible y las demás no se enteran.
print(f"TEPJF: {tepjf.cargar()} criterios indexados")

# El padrón de Kriterius+. Con DATABASE_URL usa Postgres; sin ella, memoria (los
# suscriptores durarían lo que dure el proceso: solo sirve para probar en local).
PADRON = padron_mod.desde_entorno()

# Los servicios de Kriterius+: vigilancia de expedientes e historial. Comparten
# el pool del padrón (o su modo memoria si no hay DATABASE_URL), y sus tools se
# registran aquí y solo aquí: el paquete .mcpb local no las conoce a propósito —
# Kriterius+ vive en el conector remoto.
VIGILANCIAS = vigilancia.Vigilancias(PADRON.almacen.pool)
HISTORIAL = historial.Historial(PADRON.almacen.pool)
plus_tools.registrar(mcp, PADRON, VIGILANCIAS, HISTORIAL)

# La ronda de vigilancia corre a esta hora de México, cuando las listas de
# sesión ya suelen estar publicadas.
HORA_RONDA_MX = 19


async def _ronda_diaria():
    """Bucle eterno: espera a la hora de la ronda y revisa. Cualquier tropiezo
    se registra y se vuelve a esperar; este bucle no debe morir nunca."""
    from datetime import timedelta
    try:
        from zoneinfo import ZoneInfo
        zona = ZoneInfo("America/Mexico_City")
    except Exception:
        zona = timezone(timedelta(hours=-6))
    while True:
        ahora_mx = datetime.now(zona)
        objetivo = ahora_mx.replace(hour=HORA_RONDA_MX, minute=0, second=0,
                                    microsecond=0)
        if objetivo <= ahora_mx:
            objetivo += timedelta(days=1)
        await _asyncio.sleep((objetivo - ahora_mx).total_seconds())
        try:
            conteos = await vigilancia.revisar(VIGILANCIAS, expedientes, correo)
            print("ronda de vigilancia:", conteos)
        except Exception as e:
            print("AVISO: la ronda de vigilancia tropezó:", e)


async def _arrancar_padron():
    """Abre el pool y asegura el esquema al arrancar, para enterarse de una base
    mal configurada en el despliegue y no a la primera venta. Si falla, el
    servidor arranca igual: el conector gratuito no depende del padrón."""
    try:
        await PADRON.almacen.conectar()
        print("padrón listo:", type(PADRON.almacen).__name__)
        pool = await PADRON.almacen.pool()
        if pool is not None:
            # Las tablas de los servicios de Kriterius+ viven junto al padrón;
            # sus esquemas son idempotentes, igual que el del padrón.
            async with pool.acquire() as con:
                await con.execute(vigilancia.ESQUEMA)
                await con.execute(historial.ESQUEMA)
            print("tablas de vigilancia e historial aseguradas")
    except Exception as e:
        print("AVISO: el padrón no conectó al arrancar:", e)

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
  body {{ font-family: "Avenir Next", Avenir, "Helvetica Neue", -apple-system, system-ui, sans-serif;
         max-width: 40rem; margin: 0 auto; padding: 4rem 1.5rem; line-height: 1.6;
         background: #101A0C; color: #E7E3D6; }}
  h1 {{ font-family: "Palatino Linotype", "Book Antiqua", Palatino, serif; color: #EFE7D8; }}
  a {{ color: #8FBF7A; }}
  code {{ background: #182312; border: 1px solid #2C3A24; padding: .15rem .4rem;
          border-radius: 2px; color: #EBA345; }}
  .ok {{ color: #8FBF7A; font-weight: 600; }}
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


@mcp.custom_route("/licencia/validar", methods=["POST"])
async def validar_licencia(request):
    """El candado del componente local de Kriterius+.

    El paquete .mcpb Plus corre en la máquina del abogado y hace las consultas
    autenticadas al Portal del PJF con la e.firma del propio usuario —que nunca
    sale de su equipo—. Pero la autorización de USAR ese componente no puede vivir
    en el paquete, que es código que el usuario puede editar: vive aquí.

    Recibe {"licencia": "kplus_..."} y responde si está vigente. Nada más: ni el
    correo ni datos personales viajan de vuelta, solo el sí/no y hasta cuándo.
    Deliberadamente parco: este endpoint es público y no debe filtrar el padrón."""
    try:
        cuerpo = await request.json()
    except Exception:
        cuerpo = {}
    token = (cuerpo or {}).get("licencia", "") if isinstance(cuerpo, dict) else ""
    suscriptor = None
    if token:
        try:
            suscriptor = await PADRON.resolver(token)
        except Exception:
            suscriptor = None
    if suscriptor is not None and suscriptor.vigente:
        fin = suscriptor.fin_periodo
        return JSONResponse({
            "vigente": True,
            "plan": suscriptor.plan,
            "hasta": fin.isoformat() if fin else None,
        })
    # Mismo cuerpo para licencia inexistente, revocada o vencida: a quien tantea
    # no se le confirma cuál de las tres cosas pasó.
    return JSONResponse({"vigente": False}, status_code=200)


# El middleware se cuelga envolviendo el constructor de la app, no cambiando el arranque:
# `mcp.run` sigue siendo quien levanta uvicorn con su propia configuración, que ya está
# probada en producción. Tocar esa parte para meter la medición sería cambiar lo que
# funciona por lo que apenas se estrena.
_construir_app = mcp.streamable_http_app


def _app_con_medicion():
    app = _construir_app()
    app.add_middleware(uso.Medicion, ruta=mcp.settings.streamable_http_path)
    # Prefijo se agrega DESPUÉS de Medicion a propósito: en Starlette el último
    # middleware agregado queda por fuera, así que Prefijo reescribe
    # /s/<licencia>/mcp a /mcp antes de que Medicion mire la ruta, y la medición
    # no necesita saber que las licencias existen.
    app.add_middleware(suscripcion.Prefijo, padron=PADRON,
                       ruta=mcp.settings.streamable_http_path)
    # Las rutas del cobro (/suscribirse, /stripe/webhook, /gracias, /mi-cuenta).
    app.router.routes.extend(cobros.rutas(PADRON))
    app.router.routes.extend(promos.rutas(PADRON))
    # Starlette moderno ya no tiene add_event_handler; si hay un loop corriendo
    # (lo hay: mcp.run construye la app dentro de él), la conexión temprana va
    # como tarea. Si no lo hubiera, el almacén se conecta solo en la primera
    # consulta, así que no se pierde nada más que el aviso temprano.
    try:
        lazo = _asyncio.get_running_loop()
        lazo.create_task(_arrancar_padron())
        lazo.create_task(_ronda_diaria())
    except RuntimeError:
        pass
    return app


mcp.streamable_http_app = _app_con_medicion


if __name__ == "__main__":
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http")
