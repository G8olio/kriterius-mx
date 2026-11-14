# Captura del portal semanal

## Estado de validación — 14 de noviembre de 2026 UTC

Implementación en revisión, **no desplegada**. No se ha logrado capturar un lote
de tesis nuevas desde un servidor. La ficha 2032910 sí se leyó completa en Chrome
en la Mac el 13 de noviembre; esa evidencia no demuestra acceso automatizado.

Pruebas remotas reales en GitHub Actions (Chromium limpio, Playwright 1.64.1):

| Entrada | Resultado | Evidencia |
| --- | --- | --- |
| Ficha 2032910 directa | HTTP 403, texto visible vacío, lote detenido | [Ejecución 43563230794](https://github.com/G8olio/kriterius-mx/actions/runs/43563230794) |
| Portada `/presentacion` | HTTP 403 antes de buscar, lote detenido | [Ejecución 43563390938](https://github.com/G8olio/kriterius-mx/actions/runs/43563390938) |

El lote solicitado fue 2032910, 2032896 y 2032921. **Cero capturas remotas válidas**;
las dos últimas fichas no se solicitaron tras el rechazo. El 403 demuestra rechazo
desde ese entorno, pero no identifica por sí solo qué regla o proveedor lo causó.

La preparación de Playwright y sus dependencias en la consola temporal de la
instancia de producción coincidió con la terminación del contenedor y un 504.
DigitalOcean reinició el servicio: `/salud` volvió a 200 en versión 2.13.2 y los
logs confirmaron el acervo de 34,041 tesis. No se dispone de diagnóstico que
demuestre OOM; la instancia es demasiado ajustada para recomendar instalar allí
el navegador. No se cambió su tamaño ni se contrató infraestructura.

## Arquitectura

- `capturar_semanal.py`: proceso aislado, una pestaña, máximo cinco registros,
  60 segundos entre fichas, sin reintentos. Sigue portada, búsqueda por registro y
  detalle. Produce texto, metadatos, procedencia, hash local y reporte.
- `sjf_semanal.py`: valida identidad, metadatos, cierre de publicación, fecha y
  correspondencia entre campos y texto. Escritura atómica; lectura sin red.
- `ver_tesis`: usa primero una captura íntegra con antigüedad máxima de 30 días.
  Indica la URL y fecha de obtención. Si no existe una captura válida, conserva
  los respaldos previos. No inicia Chromium desde una consulta MCP.
- El texto conserva juntos cuerpo, precedentes y notas anteriores al cierre;
  no se inventa una división jurídica por buscar palabras como `PLENO`.
- El SHA-256 comprueba integridad local; no se presenta como firma de la SCJN.

## Ejecución en un trabajador separado

Instalar desde PyPI y los binarios oficiales de Playwright, fuera del contenedor
que atiende usuarios. No requiere sesiones del usuario ni cookies de su Mac.

```sh
python -m venv .venv-captura
.venv-captura/bin/pip install -r requirements-captura.txt
.venv-captura/bin/python -m playwright install --with-deps --only-shell chromium
.venv-captura/bin/python capturar_semanal.py 2032910 2032896 2032921
```

El workflow `Capturar tesis del portal semanal` permite pasar entre uno y cinco
registros y conserva artefactos durante 30 días. Es manual: no se habilitó un cron
contra el origen que está rechazando acceso. Las capturas de un lote interrumpido
no se deben incorporar como si fuera un lote validado.

Para probar una captura válida con el conector sin modificar el acervo:

```sh
export KRITERIUS_SEMANAL_DIR=/ruta/absoluta/capturas_semanal/datos
python -c 'import asyncio, kriterius_mx; print(asyncio.run(kriterius_mx.ver_tesis(2032910)))'
```

Para distribuir fichas verificadas, incorporarlas a
`kriterius_datos/sjf_semanal/` mediante revisión y despliegue normal. Tanto la rueda
Python como la imagen del servidor incluyen esa carpeta; no incluyen Playwright.

## Validación pendiente para activar

Se necesita acceso aceptado por el portal desde el entorno del capturador. Una
excepción institucional o un canal de entrega aprobado puede resolverlo; no se
ha confirmado ninguno. Después hay que comprobar los selectores de búsqueda
contra la página real, capturar tres fichas actuales completas y compararlas con
las respuestas de `ver_tesis` en el servidor. No se promete que contratar más RAM
resuelva el 403.
