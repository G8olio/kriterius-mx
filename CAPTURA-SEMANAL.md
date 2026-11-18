# Captura del portal semanal

## Flujo Chrome/CUA — 18 de noviembre de 2026

La detección programable recorrió portada, TESIS y el total de la semana y obtuvo
los 30 registros publicados el 13 de noviembre. Reconoció las tres copias ya
existentes y capturó las otras **27 fichas completas** usando el Chrome de la Mac.
El importador incorporó las 27; al repetirlo devolvió `sin_cambios`. La validación
local consultó las 30 sin HTTP saliente. Se comprobaron 46 tests de Python, tres
de Node y la construcción/lectura del índice prevista por el Dockerfile.

Los helpers y la secuencia para el ejecutor local de Codex están en
[AUTOMATIZACION-CHROME.md](AUTOMATIZACION-CHROME.md). Incluyen comprobación VPN por
paso, permisos breves de un solo uso, intervalo entre fichas, continuación de
lotes, importación idempotente y publicación restringida a datos. La periodicidad
elegida es viernes a las 12:00 de Ciudad de México. La ejecución requiere la Mac,
Codex y Chrome disponibles; no modifica la configuración del navegador para
imitar otro cliente.

`/datos/sjf_semanal.json` expone las identidades y huellas válidas instaladas,
sin tráfico judicial. `verificar_servicio_semanal.py` comprueba ese manifiesto y
las respuestas reales de `ver_tesis` por MCP HTTP antes de dar por terminada una
publicación. La adquisición desde Chromium headless remoto sigue sin validarse;
es un camino distinto del flujo de Chrome/CUA probado el 18 de noviembre.

## Estado de validación — 16 de noviembre de 2026

Implementación en revisión, **no desplegada en producción**. La captura asistida
en Chrome obtuvo tres fichas completas del 13 de noviembre y el Repositorio
oficial permitió contrastar las mismas tres. Se incorporan copias fechadas a la
distribución de Kriterius. No se ha logrado capturar un lote desde un servidor.

| Registro | Clave | Texto y precedentes | Contraste con Repositorio |
| --- | --- | ---: | --- |
| 2032910 | P./J. 222/2026 (12a.) | 3,689 caracteres | Coincide |
| 2032896 | P. VII/2026 (12a.) | 4,548 caracteres | Coincide |
| 2032921 | P./J. 228/2026 (12a.) | 3,500 caracteres | Coincide |

Se verificó la VPN antes de navegar. Se usó portada → búsqueda por registros →
detalle, conservando texto visible, URL, fecha y capturas de pantalla. El canal
se registra como Chrome mediante CUA; no se atribuye a Playwright remoto.

El [Repositorio](https://bicentenario.scjn.gob.mx/repositorio-scjn/sjf) abrió en
la misma sesión y la búsqueda `2032910 OR 2032896 OR 2032921` dio tres resultados.
Su tabla visible contiene cuerpo, precedentes, nota de publicación y huella.
Coinciden diez comprobaciones por ficha, incluidos rubro, clave, materias y
texto íntegro. Para comparar se normalizan espacios, se retiran tres separadores
vacíos al final de precedentes y se ignora el punto final del nombre de la fuente.
Los originales permanecen intactos. La huella mostrada por el Repositorio se
conserva como metadato: no se afirma haber verificado su algoritmo o firma.

El botón Descargar no produjo un archivo confirmado por la herramienta; **no se
declara éxito de CSV/ZIP**. El respaldo obtenido es una captura de la tabla visible,
conservada en `fixtures_semanal/repositorio-20261116.json`. La pestaña API muestra
los endpoints documentados de conteo, IDs y detalle; su lectura no demuestra
que estén accesibles desde un trabajador remoto. El manual permite exportar
hasta 100 registros y remite a una cita para solicitudes mayores.

La [Gaceta](https://www.scjn.gob.mx/coordinacion/gaceta?epoca_id=1) sigue mostrando
octubre de 2026 como último libro. No se usa como prueba de las novedades de noviembre.

## Antecedentes de acceso remoto

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

1. **Captura asistida provisional: probada.** Tres fichas reales completas,
   contrastadas e importadas el 16 de noviembre. Mantener fecha y fuente.
   No está demostrada una captura masiva o desatendida en la Mac.
2. **Integración acordada con la SCJN.** Presentar los incidentes y solicitar la
   revisión de la regla, un acceso identificado o un canal oficial de entrega.
   Proponer consultas de lectura, frecuencia pactada, caché y contacto técnico.
   Usar IP estable o credenciales si la institución define ese mecanismo. Contratar
   una IP fija sin ese acuerdo no garantiza solucionar el rechazo.
3. **Respaldo por publicaciones oficiales: contenido comprobado.** El Repositorio
   mostró los tres textos recientes y coincidentes. Falta confirmar el ZIP/CSV y
   el acceso desde el servidor. La Gaceta mensual aún no cubre noviembre.
4. **Capturador separado del servicio MCP.** Ejecutar la obtención en un trabajador
   aparte y publicar solo fichas validadas. Resuelve el aislamiento de recursos,
   pero no por sí mismo el rechazo del portal. Ya es la arquitectura del borrador.

La hipótesis de un filtro por cliente/red/sesión es compatible con un rechazo
antes de buscar, pero no identifica la causa. Imperva documenta señales de
reputación, cabeceras, IP/ASN, cookies y JavaScript en su
[guía de seguridad](https://www.imperva.com/learn/application-security/website-security/).
No se tuvo acceso a los registros de seguridad de la SCJN ni a la regla activada.

Fuentes oficiales consultadas el 16 de noviembre:
[manual de descarga del Repositorio](https://bicentenario.scjn.gob.mx/manual-usuario-descarga)
(abierto correctamente en Chrome; anteriormente había devuelto 403) y
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
- `importar_semanal.py`: importa texto visible y metadatos de una captura asistida;
  puede exigir contraste con la tabla del Repositorio. Valida todo el lote antes
  de escribir. Conserva la fecha original: importar no renueva la vigencia.
- `ver_tesis`: usa primero una captura íntegra con antigüedad máxima de 30 días.
  Indica la URL y fecha de obtención. Si no existe una captura válida, conserva
  los respaldos previos. No inicia Chromium desde una consulta MCP.
- El texto conserva juntos cuerpo, precedentes y notas anteriores al cierre;
  no se inventa una división jurídica por buscar palabras como `PLENO`.
- El SHA-256 comprueba integridad local; no se presenta como firma de la SCJN.

## Importación asistida sin consultas judiciales

Cada ficha requiere `REGISTRO.txt` y `REGISTRO.meta.json`, con los campos `url`,
`capturado_utc` y `canal`. Debe conservarse todo el texto visible hasta la nota
final de publicación. La tabla de contraste es una captura DOM, no un CSV oficial:
`url`, `capturado_utc` y `filas` (encabezados y celdas visibles codificadas en JSON).

```sh
python importar_semanal.py 2032910 2032896 2032921 \
  --origen /ruta/capturas-asistidas \
  --repositorio /ruta/capturas-asistidas/repositorio-tabla-visible.json \
  --salida kriterius_datos/sjf_semanal \
  --informe /ruta/informe-importacion.json
python -m unittest test_sjf_semanal test_capturar_semanal test_importar_semanal
```

El workflow `Validar capturas asistidas sin consultas judiciales` construye e
instala la rueda en Linux y consulta las tres fichas desde fuera del checkout.
Bloquea peticiones HTTP salientes durante la consulta y conserva las respuestas
completas como artefacto. No instala Chromium ni prueba adquisición desde SCJN.
La comprobación operativa respeta la caducidad de 30 días, por lo que fallará si
las copias no se renuevan antes de vencer; las pruebas históricas fijan su fecha.

Resultado real: [ejecución 43826282835](https://github.com/G8olio/kriterius-mx/actions/runs/43826282835),
commit `6c954f2`, **correcta** el 16 de noviembre a las 07:47 UTC. Pasaron 23 pruebas
en Linux, se instaló `kriterius-core-2.14.0` desde la rueda y se devolvieron las tres
tesis completas con cero llamadas HTTP durante las consultas. Los SHA-256 de las
tres respuestas coinciden con la ejecución local. El
[artefacto con respuestas e informe](https://github.com/G8olio/kriterius-mx/actions/runs/43826282835/artifacts/14206772104)
se conserva 30 días. Es una prueba del core instalado en un trabajador remoto;
no es una prueba del endpoint HTTP de producción ni de adquisición remota.

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

## Pendientes del capturador headless remoto (estado del 16 de noviembre)

Se necesita acceso aceptado por el portal desde el entorno del capturador. Una
excepción institucional o un canal de entrega aprobado puede resolverlo; no se
ha confirmado ninguno. Las tres capturas asistidas ya están contrastadas, pero
esto no valida el capturador Playwright ni una sincronización desatendida. Falta
desplegar la distribución y comprobar las respuestas en el servidor de producción.
No se promete que contratar más RAM resuelva el 403.
