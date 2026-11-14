"""Captura serial del texto visible del portal semanal. Sin evasión ni cookies externas."""
import argparse
import asyncio
import json
from pathlib import Path

BASE = "https://sjfsemanal.scjn.gob.mx"


async def main():
    from playwright.async_api import async_playwright
    parser = argparse.ArgumentParser()
    parser.add_argument("registros", nargs="+", type=int)
    parser.add_argument("--salida", default="capturas_semanal")
    args = parser.parse_args()
    if not 1 <= len(args.registros) <= 5:
        parser.error("La prueba admite de 1 a 5 registros")
    out = Path(args.salida)
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(locale="es-MX")
            page = await context.new_page()
            for i, registro in enumerate(args.registros):
                if i:
                    await asyncio.sleep(20)
                url = f"{BASE}/detalle/tesis/{registro}"
                response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    await page.get_by_text(f"Registro digital: {registro}", exact=False).first.wait_for(timeout=45000)
                except Exception:
                    pass
                visible = await page.locator("body").inner_text()
                (out / f"{registro}.txt").write_text(visible, encoding="utf-8")
                await page.screenshot(path=str(out / f"{registro}.png"), full_page=True)
                dom = await page.locator("[id]").evaluate_all("els => els.filter(e => e.innerText && e.getClientRects().length).map(e => ({tag:e.tagName,id:e.id,text:e.innerText.slice(0,250)}))")
                result = {"registro": registro, "status": response.status if response else None,
                          "url": page.url, "caracteres": len(visible), "dom": dom}
                (out / f"{registro}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
                print(json.dumps({k:v for k,v in result.items() if k != "dom"}), flush=True)
                if f"Registro digital: {registro}" not in visible or "Esta tesis se publicó" not in visible:
                    raise RuntimeError("La ficha no está completa o el portal requiere intervención; lote detenido")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
