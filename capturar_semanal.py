"""Captura serial del portal semanal en un proceso separado del servicio MCP.

Sin cookies externas, proxies, cambios de identidad, resolución de retos o
reintentos de acceso. Un bloqueo o una ficha incompleta detiene todo el lote.
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import sjf_semanal as sjf

BASE = sjf.BASE


class CapturaInterrumpida(RuntimeError):
    pass


def validar_registros(registros):
    values = [sjf.registro_valido(r) for r in registros]
    if not 1 <= len(values) <= 5 or len(set(values)) != len(values):
        raise ValueError("Se requieren entre 1 y 5 registros distintos")
    return values


def verificar_http(response):
    if response is None or response.status >= 400:
        status = response.status if response else "sin respuesta"
        raise CapturaInterrumpida(f"Acceso rechazado o fuente no disponible: HTTP {status}; sin reintentos")


async def diagnosticar_respuesta(response):
    """Describe la respuesta ya recibida; no hace otra petición ni guarda cookies."""
    if response is None:
        return {"status": None}
    parts = urlsplit(response.url)
    result = {"status": response.status,
              "url": urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))}
    try:
        permitidas = {"server", "date", "content-type", "content-length", "retry-after",
                      "x-cdn", "x-iinfo", "x-cache", "via", "cf-ray",
                      "x-mitmproxy-blocked-reason"}
        headers = await asyncio.wait_for(response.all_headers(), timeout=10)
        result["cabeceras"] = {k.lower(): v for k, v in headers.items() if k.lower() in permitidas}
        body = await asyncio.wait_for(response.body(), timeout=10)
        result["bytes_respuesta"] = len(body)
        result["sha256_respuesta"] = hashlib.sha256(body).hexdigest()
        sample = body[:1024 * 1024].lower()
        result["marcadores"] = [marker for marker in
            ("incapsula", "imperva", "captcha", "access denied", "acceso denegado",
             "request unsuccessful", "_incapsula_resource", "robots_denied")
            if marker.encode() in sample]
    except Exception as error:
        result["diagnostico_incompleto"] = type(error).__name__
    return result


async def abrir(page, url, navegaciones=None):
    response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    if navegaciones is not None:
        navegaciones.append(await diagnosticar_respuesta(response))
    verificar_http(response)
    if not page.url.startswith(BASE + "/"):
        raise CapturaInterrumpida("La navegación salió del portal semanal oficial")
    return response.status


async def capturar_lote(registros, salida, evidencia, *, fabrica=None):
    registros = validar_registros(registros)
    if fabrica is None:
        from playwright.async_api import async_playwright
        fabrica = async_playwright
    evidencia = Path(evidencia)
    evidencia.mkdir(parents=True, exist_ok=True)
    report = {"inicio_utc": datetime.now(timezone.utc).isoformat(),
              "solicitados": registros, "capturados": [], "estado": "en_curso",
              "navegaciones": [],
              "modo": "portal visible, navegador nuevo, una pestaña, serial",
              "intervalo_entre_fichas_s": 60}
    page = None
    try:
        async with fabrica() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(locale="es-MX")
                page = await context.new_page()
                # Si la aplicación devuelve 403/429/5xx en su navegación SPA,
                # recordarlo antes de aceptar cualquier texto que siga visible.
                fallos = []
                def revisar_respuesta(response):
                    if (response.url.startswith(BASE + "/") and response.status >= 400
                            and response.request.resource_type in {"document", "xhr", "fetch"}):
                        fallos.append(response.status)
                page.on("response", revisar_respuesta)
                await abrir(page, f"{BASE}/presentacion", report["navegaciones"])
                await page.get_by_text("TESIS", exact=True).click(timeout=30000)
                for i, registro in enumerate(registros):
                    if i:
                        await asyncio.sleep(60)
                        await abrir(page, f"{BASE}/busqueda-principal-tesis", report["navegaciones"])
                    if fallos:
                        raise CapturaInterrumpida(f"El portal rechazó una petición: HTTP {fallos[0]}")
                    await page.get_by_text("Búsqueda por número de registro", exact=False).click(timeout=30000)
                    inputs = page.locator("input:visible:not([type=checkbox]):not([type=radio])")
                    if await inputs.count() != 1:
                        raise CapturaInterrumpida("Cambió la plantilla de búsqueda; no se eligió un campo a ciegas")
                    await inputs.fill(registro)
                    await page.get_by_role("button", name="Buscar", exact=True).click()
                    await page.get_by_text(re.compile(rf"Registro digital:\s*{registro}")).first.click(timeout=30000)
                    await page.wait_for_url(f"{BASE}/detalle/tesis/{registro}", timeout=30000)
                    await page.get_by_text("Esta tesis se publicó", exact=False).first.wait_for(timeout=30000)
                    if fallos:
                        raise CapturaInterrumpida(f"El portal rechazó una petición: HTTP {fallos[0]}")
                    visible = await page.locator("body").inner_text()
                    captura = sjf.empaquetar(visible, registro)
                    sjf.guardar(captura, salida)
                    await page.screenshot(path=str(evidencia / f"{registro}.png"), full_page=True)
                    report["capturados"].append({"registro": registro,
                        "texto_caracteres": len(captura["documento"]["texto"]),
                        **captura["procedencia"]})
                    print(json.dumps(report["capturados"][-1], ensure_ascii=False), flush=True)
                report["estado"] = "completo"
            except Exception:
                if page is not None:
                    # Solo DOM visible y screenshot: no cookies, tokens ni estado
                    # interno de la página en los artefactos de diagnóstico.
                    try:
                        report["ultima_url"] = page.url
                        (evidencia / "interrupcion.txt").write_text(await page.locator("body").inner_text(), encoding="utf-8")
                        await page.screenshot(path=str(evidencia / "interrupcion.png"), full_page=True)
                    except Exception:
                        pass
                raise
            finally:
                await browser.close()
    except Exception as error:
        report["estado"] = "interrumpido"
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        report["fin_utc"] = datetime.now(timezone.utc).isoformat()
        (evidencia / "informe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registros", nargs="*")
    parser.add_argument("--salida", default="capturas_semanal/datos")
    parser.add_argument("--evidencia", default="capturas_semanal/evidencia")
    args = parser.parse_args()
    values = args.registros or os.environ.get("KRITERIUS_REGISTROS_CAPTURA", "").split()
    try:
        values = validar_registros(values)
    except ValueError as error:
        parser.error(str(error))
    asyncio.run(capturar_lote(values, args.salida, args.evidencia))


if __name__ == "__main__":
    main()
