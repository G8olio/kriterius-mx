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
10. Ejecutar las pruebas de importación y consultar todos los documentos vigentes
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
semanas anteriores no está implementada en este helper. Las copias tienen vigencia
de 30 días y el servicio deja de preferirlas al vencer; esto no es un acervo
histórico perpetuo. Los tests históricos usan fechas controladas y la validación
operativa excluye las copias caducadas, sin renovar sus fechas.

El módulo existente `capturar_semanal.py` es la alternativa separada de Playwright
headless, cuyos intentos remotos fueron bloqueados. No forma parte de esta
automatización sobre Chrome/CUA.
