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
| Reintento de portada, 14 nov. 02:11 CDMX | HTTP 403; pantalla de Imperva `Access denied / Error 15` | [Ejecución 43574555441](https://github.com/G8olio/kriterius-mx/actions/runs/43574555441) |
| Repetición con la misma versión, 14 nov. 02:29 CDMX | HTTP 403; mismo Error 15, cero fichas | [Segundo intento](https://github.com/G8olio/kriterius-mx/actions/runs/43574555441/attempts/2) |

El lote solicitado fue 2032910, 2032896 y 2032921. **Cero capturas remotas válidas**;
las dos últimas fichas no se solicitaron tras el rechazo. El 403 por sí solo no
identificaba qué regla o proveedor lo causó. En el reintento del 14 de noviembre,
la captura de pantalla sí mostró explícitamente **Imperva, Error 15** y el mensaje
`This request was blocked by our security service`.

Detalle verificable del reintento (commit `f5b3cfa`):

- Una navegación principal a `/presentacion`, detenida antes de buscar.
- HTML de 885 bytes; cabecera `x-iinfo` y marcadores `incapsula`,
  `_incapsula_resource` y `request unsuccessful`.
- Incidente mostrado: `10000370911189942-936591440797239663`.
- Fecha del incidente: 2026-11-14 08:11:15 UTC, equivalente a 02:11:15 en CDMX.
- IP del trabajador de GitHub mostrada por Imperva: `4.236.95.147` (no es la Mac).
- [Artefacto de diagnóstico](https://github.com/G8olio/kriterius-mx/actions/runs/43574555441/artifacts/14105422722):
  `informe.json`, `interrupcion.txt` y `interrupcion.png`.
- 17 pruebas de captura e integridad aprobadas antes del intento en el trabajador.

Esta evidencia confirma que intervino el servicio de seguridad de Imperva. No
expone la regla exacta ni demuestra si el motivo fue IP, sesión, automatización
u otra condición. El identificador permite solicitar al administrador que revise
el evento concreto; todavía no acredita que sea obligatoria una excepción.

El diagnóstico se obtiene de la respuesta ya recibida, sin peticiones extra.
Conserva solo cabeceras seleccionadas, tamaño, hash y marcadores; excluye cookies,
parámetros de consulta y el HTML bruto. No se alteraron la identidad del navegador,
sesiones ni configuración de red para este intento.

La repetición solicitada el 14 de noviembre, a las 02:29:46 CDMX, obtuvo el mismo
rechazo de portada con la misma versión del capturador. El trabajador asignado
por GitHub mostró IP `9.234.220.199` e incidente
`2212000061778361281-924838855635632565`. No se seleccionó ni rotó una IP desde el
código. La respuesta fue HTML de 887 bytes con los mismos marcadores de Incapsula.
Las 17 pruebas previas al acceso pasaron; no se buscó ni capturó ninguna tesis.
[Artefacto del segundo intento](https://github.com/G8olio/kriterius-mx/actions/runs/43574555441/artifacts/14105893017).

## Soluciones propuestas

1. **Captura asistida provisional.** Usar la navegación pública ordinaria que ya
   permitió leer una ficha en la Mac, validar otras tres fichas y preparar su
   importación al almacén de Kriterius. Mantener fecha y fuente. Solo hay evidencia
   de una ficha; no está demostrada una captura masiva o desatendida en la Mac.
2. **Integración acordada con la SCJN.** Presentar los incidentes y solicitar la
   revisión de la regla, un acceso identificado o un canal oficial de entrega.
   Proponer consultas de lectura, frecuencia pactada, caché y contacto técnico.
   Usar IP estable o credenciales si la institución define ese mecanismo. Contratar
   una IP fija sin ese acuerdo no garantiza solucionar el rechazo.
3. **Respaldo por publicaciones oficiales.** Evaluar las descargas en formatos
   abiertos del Repositorio y los tomos de la Gaceta para incorporar novedades.
   Comprobar primero contenido, completitud y fecha: los rechazos previos del
   Repositorio y el catálogo consultado de Gaceta hasta octubre impiden prometer
   que estos canales entreguen hoy las tesis nuevas del 13 de noviembre.
4. **Capturador separado del servicio MCP.** Ejecutar la obtención en un trabajador
   aparte y publicar solo fichas validadas. Resuelve el aislamiento de recursos,
   pero no por sí mismo el rechazo del portal. Ya es la arquitectura del borrador.

La hipótesis de un filtro por cliente/red/sesión es compatible con un rechazo
antes de buscar, pero no identifica la causa. Imperva documenta señales de
reputación, cabeceras, IP/ASN, cookies y JavaScript en su
[guía de seguridad](https://www.imperva.com/learn/application-security/website-security/).
No se tuvo acceso a los registros de seguridad de la SCJN ni a la regla activada.

Fuentes oficiales para evaluar el respaldo:
[manual de descarga del Repositorio](https://bicentenario.scjn.gob.mx/manual-usuario-descarga)
(localizado en el índice de búsqueda; la apertura directa devolvió 403) y
[catálogo de la Gaceta](https://www.scjn.gob.mx/coordinacion/gaceta).

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
