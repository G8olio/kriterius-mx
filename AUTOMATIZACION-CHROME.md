# Importación semanal con Chrome y Codex

Objetivo: cada viernes a las 12:00 de Ciudad de México, obtener las tesis de la
última publicación y publicarlas en Kriterius sin pasos manuales. El ejecutor es
una automatización local de Codex que controla el Chrome habitual mediante CUA.
Requiere la Mac despierta, Codex y la conexión de Chrome disponibles. No es un
cron en el servidor ni un proceso Playwright separado del navegador habitual.

## Rutas de esta instalación

- Proyecto: directorio local `KriteriusMX` indicado en la automatización.
- Código inicial y Python: `kriterius-mx-semanal-20261113/` y su `.venv/bin/python`.
- Verificador: `cliente-pruebas-scjn/cliente.py`, con su `.venv/bin/python`.
- Evidencia de cada ejecución: `cliente-pruebas-scjn/automaticas/AAAA-MM-DD-HHMM/`.
- Checkout exclusivo para sincronizar main: `kriterius-sincronizacion/`.
- Repositorio autorizado: `https://github.com/G8olio/kriterius-mx.git`.
- Servicio: `https://mcp.kriterius.mx/mcp`; estado: `/salud`.

No modificar `kriterius-mx-clon`: puede contener trabajo del usuario.

## Secuencia autónoma

1. Comprobar que no hay otra ejecución del mismo lote. Sincronizar el checkout
   exclusivo con `git pull --ff-only`. Si no existe, clonar main del repositorio
   autorizado. Ante cambios de código locales inesperados, detenerse sin reset,
   stash ni push forzado. Usar los helpers de la versión sincronizada.
2. Ejecutar `cliente.py verificar-vpn`. Ante VPN desconectada, abrir ProtonVPN
   con CUA y efectuar una conexión normal con Quick Connect, una sola vez.
   Volver a verificar. Si falla, no abrir fuentes judiciales. No rotar servidores
   ni desactivar la VPN para responder a un rechazo del portal.
3. Abrir una pestaña propia de Chrome en `https://api.ipify.org/` y comprobar que
   su texto coincide con `public_ipv4` del informe reciente. Conservar la ruta del
   informe y la IP solo en evidencia privada, nunca en commits.
4. Seleccionar Chrome con las APIs documentadas de CUA, nombrar la sesión y crear
   una pestaña propia. Importar `captura_chrome.mjs` dentro de `cua_repl`. Obtener
   el handle DOM con `browser.tabs.get(tab.id)` conforme a la documentación.
5. Emitir un permiso para `catalogo` ejecutando `guardia_vpn.py` (ejemplo abajo).
   Llamar `descubrir(tabDOM, carpetaEvidencia, rutaPermiso)` desde el módulo.
   Recorre portada → TESIS → total de Ambas → lista de 50 resultados, con hasta
   dos páginas y máximo 100 registros. Conserva `catalogo.json`. Si la UI cambió
   o aparece un rechazo/reto, detener; no reinterpretar la página como una ficha.
6. Ejecutar `flujo_semanal.py planificar --catalogo .../catalogo.json --datos
   <checkout>/kriterius_datos/sjf_semanal --origen <evidencia> --informe .../plan.json`.
   Obtener los enlaces pendientes con `pendientes(carpetaEvidencia)` del módulo.
   No construir registros por rangos ni usar solo el mayor número como cursor.
7. Por cada enlace pendiente, en orden y sin concurrencia: ejecutar de nuevo
   `guardia_vpn.py --paso REGISTRO`, y después `capturarFicha(tabDOM, enlace,
   carpetaEvidencia, rutaPermiso)`. El permiso dura 30 segundos y se consume una
   sola vez. Esperar al menos 15 segundos entre aperturas de fichas (se puede
   validar lo ya obtenido durante ese intervalo). Renovar la verificación externa
   y comprobar de nuevo la IP de Chrome cada 15 minutos. Ante desconexión, rechazo,
   CAPTCHA o cambio de plantilla, detener la captura; nunca insistir ni suplantar
   clientes. No se promete que estos controles eviten toda carrera de desconexión:
   Chrome utiliza la configuración de red de macOS, no un socket propio vinculado.
8. Capturar una pantalla como evidencia del resultado. Cerrar solo las pestañas
   propias cuando finalice la parte de navegador y enviar `completion_summary`
   por CUA conforme a sus instrucciones. No trasladar cookies o sesiones al servidor.
9. Cuando estén completas todas las fichas pendientes, ejecutar `flujo_semanal.py
   incorporar` con los mismos catálogo, origen y datos. Ante una ficha inválida,
   no publicar el lote. Repetir `planificar`: debe devolver cero pendientes. Un
   segundo `incorporar` debe devolver `sin_cambios` y conservar las fechas.
10. Ejecutar las pruebas de importación y consultar todos los documentos permanentes
    con `validar_semanal_distribuido.py`, que bloquea HTTP durante esas consultas.
    En el checkout exclusivo de main, ejecutar `publicar_semanal.py --repositorio
    <checkout>`. Solo permite fichas JSON validadas y no admite cambios de código,
    archivos borrados, índice preparado previamente ni historia divergente.
11. Esperar a que la comprobación CI del commit sea correcta y a que termine el
    despliegue normal de DigitalOcean. Consultar `/datos/sjf_semanal.json` hasta
    que sus huellas y fechas coincidan con las nuevas copias (máximo 10 minutos,
    comprobaciones separadas por 30 segundos). Después ejecutar
    `verificar_servicio_semanal.py REGISTROS --informe <evidencia>/produccion.json`.
    Verifica `/salud`, el manifiesto y cada respuesta MCP completa. No declarar
    publicación terminada solo porque hubo push.
    Si despliegue o comprobación falla, informar el commit y el punto exacto; no
    cambiar RAM, contratar infraestructura ni instalar Chrome en producción.

Ejemplo de permiso, sustituyendo rutas y el paso por los datos de la ejecución:

```sh
python guardia_vpn.py \
  --cliente /ruta/cliente-pruebas-scjn/cliente.py \
  --verificacion /ruta/informe-vpn-reciente.json \
  --ip-chrome IP_OBSERVADA_EN_CHROME \
  --paso REGISTRO_O_catalogo \
  --salida /ruta/evidencia/permiso.json
```

## Continuación y cobertura

Los archivos `.txt` y `.meta.json` permiten continuar un lote interrumpido. Hay
que reutilizar el catálogo de ese lote y volver a ejecutar el planificador con
`--origen`: las capturas válidas no se solicitan otra vez. No importar un lote
parcial como si estuviera completo. Terminar los lotes pendientes antes de
descubrir otra publicación.

El descubrimiento toma la última semana seleccionada por el portal. Si la Mac no
ejecuta una semana, hay que detectar y registrar el hueco; la recuperación de
semanas anteriores no está implementada en este helper. Las copias se conservan permanentemente y el índice común las incorpora a la búsqueda
por rubro y texto, sin cambiar su fecha original. Conservación permanente no equivale
a vigencia jurídica: las respuestas muestran la procedencia fechada y no garantizan
que no haya modificaciones posteriores.

El módulo existente `capturar_semanal.py` es la alternativa separada de Playwright
headless, cuyos intentos remotos fueron bloqueados. No forma parte de esta
automatización sobre Chrome/CUA.

## Permanencia y revisión electoral

Las capturas importadas no caducan. Validar su procedencia e integridad al importar,
conservar su fecha real y comprobar que el índice común las encuentre. El manifiesto
`/datos/sjf_gaceta.json` expone `capturas_permanentes`, `publicaciones_semanales` y
`semanas_pendientes`; no confundir la última fecha disponible con cobertura continua.
Al 18 de noviembre de 2026 se recuperaron las 28 fichas del 6 de noviembre usando
el selector visible de semanas. Las Gacetas llegan al 30 de octubre y el lote del
13 de noviembre contiene 30 fichas: 58 capturas permanentes en total. El importador
compara fechas reales, por lo que acepta el mismo día escrito como 6 o 06.

Revisar además el resultado de Sincronizar TEPJF y su manifiesto `/datos/tepjf.json`.
Los criterios electorales, incluidos los no vigentes, se conservan permanentemente.
La API anterior devolvió 503 el 18 de noviembre y el portal actual
https://sicje.te.gob.mx/IUSE/ mostró error de conexión. La compilación oficial
https://www.te.gob.mx/iuse_old2025/media/compilacion/compilacion2.htm indicaba corte
al 3 de septiembre. En la segunda revisión con Chrome se contrastaron sus 1502
claves vigentes con el acervo: ninguna faltante ni extra; los 26 textos de 2026
coincidieron completos. Hay 12 diferencias de texto y 2 de rubro en criterios
antiguos, registradas en `tepjf.meta.json`, que requieren contraste antes de
sustituir contenido. El portal actual cargó 1502 vigentes, pero las consultas de
Todas y de 2026 terminaron en error de conexión. No se ha confirmado de manera
exhaustiva el catálogo posterior al corte.
No cambiar `fecha_snapshot` por la fecha del intento fallido.
Ante rechazo, conservar los datos y registrar la actualización pendiente. No inferir
bajas por ausencia del catálogo ni declarar que no hubo publicaciones nuevas.

La app web fija el core en su Dockerfile. Después de una publicación del core
validada, actualizar ese SHA en un checkout exclusivo de G8olio/kriterius-web,
conservar la construcción del índice durante el build y verificar CI y `/salud`
(`version_core`) y `/acervo`. No modificar el checkout `app-web` del usuario.
