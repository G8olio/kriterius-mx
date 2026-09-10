"""
KriteriusMX — servidor MCP remoto sobre HTTP.

Expone las tools de kriterius_mx.py como conector remoto para claude.ai.
El endpoint MCP queda en https://<dominio>/mcp

Además publica rutas para humanos, para el monitoreo del hosting, para medir uso y
para que el conector de escritorio se descargue el acervo local del SJF:
    GET /                una página mínima que confirma que el servicio está vivo
    GET /salud           respuesta JSON para el health check automático del hosting
    GET /uso             panel de adopción, privado (requiere ?clave=...)
    GET /visita          faro de 1x1 que el sitio carga para contar visitas
    GET /datos/sjf_gaceta.jsonl.gz    el acervo de la Gaceta (42 MB)
    GET /datos/sjf_gaceta.json        su huella: tamaño, sha256 y cobertura

Variables de entorno:
    PORT       puerto de escucha (el hosting la define solo; por defecto 8000)
    CLAVE_USO  clave de /uso. Sin ella, /uso responde 503 y no expone nada.
"""

import os
import threading as _threading
from datetime import datetime, timezone

from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

# La versión vive en kriterius_mx.py y solo ahí. Cuando estaba duplicada aquí,
# /salud siguió anunciando la 2.6.0 con la 2.7.0 ya desplegada.
from kriterius_mx import mcp, VERSION
import sjf_local
import tepjf
import uso

# El snapshot del TEPJF se indexa una vez, al arrancar, antes de aceptar tráfico: es
# un segundo de trabajo que ahorra la latencia de la primera consulta. Si el archivo
# faltara, `cargar` devuelve 0 y el servidor arranca igual: las tools del TEPJF avisan
# que la fuente no está disponible y las demás no se enteran.
#
# flush explícito: stdout va a un pipe en el contenedor y se queda en el búfer, así que
# sin esto la línea no aparece en el log del despliegue —justo cuando más sirve, que es
# cuando hay que averiguar si el snapshot llegó a la imagen.
print(f"TEPJF: {tepjf.cargar()} criterios indexados", flush=True)

# El acervo de la Gaceta se abre en SEGUNDO PLANO, nunca bloqueando el arranque.
#
# La 2.11.0 lo cargaba aquí, en línea, y el despliegue murió: abrir 34 041 tesis
# tarda lo suficiente para que el readiness probe de App Platform falle tres veces
# ("connection refused" en el 8080, porque el puerto todavía no estaba abierto) y la
# plataforma mate el contenedor. El servidor tiene que escuchar primero y cargar
# después; mientras tanto las tools del SJF avisan que el respaldo se está
# preparando, que es información honesta y no un error.
def _cargar_acervo_sjf():
    try:
        n = sjf_local.cargar()
        print(f"SJF respaldo local: {n} tesis de la Gaceta listas"
              if n else "SJF respaldo local: no disponible (sin índice ni acervo)",
              flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"SJF respaldo local: falló al abrirse ({e})", flush=True)


_hilo_sjf = _threading.Thread(target=_cargar_acervo_sjf, name="sjf-local",
                              daemon=True)
_hilo_sjf.start()

_SHA_ACERVO: str | None = None


def _sha256_acervo() -> str:
    """El sha256 del acervo, calculado una sola vez. El archivo no cambia mientras el
    proceso viva: viene dentro de la imagen."""
    global _SHA_ACERVO
    if _SHA_ACERVO is None:
        import hashlib
        h = hashlib.sha256()
        try:
            with open(sjf_local.RUTA_DATOS, "rb") as f:
                for trozo in iter(lambda: f.read(1 << 20), b""):
                    h.update(trozo)
            _SHA_ACERVO = h.hexdigest()
        except Exception:
            _SHA_ACERVO = ""
    return _SHA_ACERVO


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


@mcp.custom_route("/datos/sjf_gaceta.json", methods=["GET"])
async def acervo_sjf_huella(request):
    """La huella del acervo, para que el conector de escritorio sepa si ya tiene la
    versión buena antes de bajarse 42 MB."""
    ruta = sjf_local.RUTA_DATOS
    if not ruta.exists():
        return JSONResponse({"error": "el acervo no está en este despliegue"}, status_code=404)
    return JSONResponse({
        "archivo": ruta.name,
        "bytes": ruta.stat().st_size,
        "sha256": _sha256_acervo(),
        "url": "/datos/sjf_gaceta.jsonl.gz",
        "meta": sjf_local.meta(),
    })


@mcp.custom_route("/datos/sjf_gaceta.jsonl.gz", methods=["GET"])
async def acervo_sjf(request):
    """El acervo de la Gaceta. Lo descarga el .mcpb la primera vez que el API del SJF
    le falla; de ahí en adelante vive en su caché en disco. Es contenido público —son
    PDF oficiales de la Corte procesados—, así que no lleva llave; lo que sí lleva es
    un ETag, para que una segunda descarga no repita los 42 MB."""
    ruta = sjf_local.RUTA_DATOS
    if not ruta.exists():
        return JSONResponse({"error": "el acervo no está en este despliegue"}, status_code=404)
    etag = f'"{_sha256_acervo()[:32]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return FileResponse(ruta, media_type="application/gzip",
                        filename="sjf_gaceta.jsonl.gz",
                        headers={"ETag": etag, "Cache-Control": "public, max-age=86400"})


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
