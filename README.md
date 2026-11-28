# KriteriusMX

Conector MCP que pone seis fuentes oficiales de derecho —mexicano, interamericano y
estadounidense— dentro de Claude, con cita completa y link oficial en cada resultado.

**Sitio:** [kriterius.mx](https://kriterius.mx) · **Endpoint MCP:** `https://mcp.kriterius.mx/mcp`

## Fuentes y herramientas (28)

| Fuente | Herramientas |
|---|---|
| **SJF / SCJN** — Semanario Judicial de la Federación, colección de tesis | `buscar_tesis`, `investigar_criterio`, `ver_tesis` |
| **SJF / SCJN** — ejecutorias y extractos publicados (acervo permanente, sin red por defecto) | `buscar_ejecutorias`, `ver_ejecutoria` |
| **SIJ / SCJN** — Sistema de Informática Jurídica: la ficha de cada asunto resuelto por la Corte | `buscar_sentencias_scjn`, `ver_sentencia_scjn` |
| **TFJA** — Tribunal Federal de Justicia Administrativa | `buscar_tesis_tfja`, `ver_tesis_tfja` |
| **TEPJF** — Tribunal Electoral del PJF, IUS Electoral (snapshot local, sin red) | `buscar_tesis_tepjf`, `ver_tesis_tepjf`, `temas_tepjf` |
| **DOF** — Diario Oficial de la Federación | `buscar_dof`, `ver_nota_dof`, `indicadores_dof`, `monitorear_dof` |
| **Corte IDH** — Buscador Jurídico de Derechos Humanos | `buscar_corteidh`, `explorar_corteidh`, `ver_caso_corteidh`, `investigar_criterio_corteidh` |
| **CourtListener** — jurisprudencia de EE.UU. (requiere llave del usuario) | `ayuda_derecho_eeuu`, `configurar_courtlistener`, `buscar_casos_eeuu`, `ver_caso_eeuu`, `quien_cita_eeuu`, `verificar_citas_eeuu` |
| Salud del servicio | `estado_conector`, `diagnosticar_conector` |

### El Repositorio de la SCJN: la segunda puerta del Semanario

Desde el 3 de septiembre de 2026 Imperva rechaza a los clientes automatizados en
`sjf2.scjn.gob.mx`. La Corte publica los mismos documentos en su programa de datos
abiertos, el [Repositorio de la SCJN](https://bicentenario.scjn.gob.mx/repositorio-scjn),
con manual para programadores, el mismo registro digital y una huella SHA-256 por
documento. Desde 2.18.0, `buscar_tesis` e `investigar_criterio` consultan por defecto
el acervo propio y devuelven clave, ID local, IUS documentado y texto íntegro con
precedentes y procedencia. `incluir_texto=False` permite pedir resúmenes; las búsquedas
externas se solicitan con `fuente="oficial"`. Para entregar textos completos, `ver_tesis` consulta primero
el **acervo permanente propio**, por clave o registro digital documentado. Una tesis
presente en Kriterius se sirve sin peticiones externas. Solo una tesis ausente requiere
consultar las fuentes oficiales. La respuesta conserva procedencia, fecha y texto
íntegro; la Gaceta no permite inventar registros digitales. El ID local permite leer
una ficha aunque todavía no tenga IUS documentado. El mapa `sjf_gaceta.registros.json`
viaja en la distribución con la evidencia de cada identidad (formato versión 2).
La correspondencia incluye época, clave y rubro completo: un mismo ID antiguo puede
tener reimpresiones con rubros diferentes. El enlace de resultados oficiales con el acervo
exige coincidencia exacta y única; no asocia rubros similares ni elige reimpresiones
ambiguas. El conector se presenta con
su propio User-Agent. El Repositorio también es la fuente de las sentencias del SIJ:
expediente, ponente, fecha, resolutivos y votación de los 106 mil asuntos resueltos por la
SCJN, publicados o no en el Semanario. Detalle del contrato en `repositorio_scjn.py`.

### Enlace de todos los registros digitales (pendiente de completar)

El cambio de consulta local y la cobertura de IUS son comprobaciones distintas.
Al 24 de noviembre de 2026, la auditoría de esta rama encuentra **34,414 fichas**,
**61 enlazadas** y **34,353 pendientes**. No se ha publicado esta corrección ni se
afirma que los tres ejemplos resuelvan el catálogo entero. Hay 104 claves con
rubros distintos que requieren cotejo; no se resuelven comparando solo prefijos.

`resolver_registros.py` reemplaza el antiguo enlazador HTTP parcial por un importador
sin red. Lee el índice real completo (base, incrementales y capturas permanentes),
acepta CSV/JSON/JSONL oficiales, conserva fecha y procedencia y rechaza lotes con
identidades contradictorias. El catálogo original y la auditoría se guardan fuera
del checkout. Ejemplos:

```sh
python resolver_registros.py --verificar --informe /ruta/evidencia/auditoria
python resolver_registros.py --catalogo /ruta/evidencia/tesis.csv \
  --fuente https://bicentenario.scjn.gob.mx/repositorio-scjn/sjf \
  --fecha AAAA-MM-DD --informe /ruta/evidencia/importacion
python resolver_registros.py --verificar --exigir-completo
```

El último comando debe terminar con código **0** antes de declarar enlazado todo
el acervo; devuelve **2** mientras falten registros. CI conserva el inventario de
pendientes y prueba todas las correspondencias disponibles sin red. El manifiesto
de Gacetas expone `fichas_con_registro_digital`, `fichas_sin_registro_digital` y
`enlace_registros_completo` calculados sobre lo realmente cargado.

La obtención masiva está pendiente: la API oficial respondió 403 y la exportación
ZIP en Chrome no entregó archivo. La SCJN documenta la entrega de más de 100
registros mediante su [procedimiento de cita](https://bicentenario.scjn.gob.mx/requisitos-pasos-solicitar-informacion).
No se eluden rechazos ni se exportan sesiones. Una vez recibido el catálogo, las
incidencias de identidad se cotejan antes de incorporarlo y desplegar core y web.

### Control de tráfico SCJN (2.13.1)

El SJF y el Repositorio incluyen `KriteriusMX/2.13.2` en su User-Agent. El SJF conserva
además el formato del cliente web: el 12/11/2026 su API de búsqueda devolvió 403 JSON
«Acceso denegado: Formato inválido.» con un identificador de aplicación simple; con
el formato anterior y el identificador explícito de Kriterius respondió 200. Esto
no elimina los retos de Imperva en las consultas de detalle. Cada fuente admite una
petición controlada a la vez, con al menos un segundo entre inicios. Cada reintento de
`_traer` vuelve a consumir el cupo global de 60 operaciones por minuto; el descubrimiento
del SJF también pasa por esos controles. Son topes propios por proceso, no una cuota
autorizada por la Corte.

Un bloqueo confirmado por la página o redirección de Imperva pausa esa operación durante
cinco minutos. La pausa separa colección y método para conservar las búsquedas cuando
solo falla el detalle. Tras vencer, una consulta vuelve a comprobar la ruta; si sigue
bloqueada, la pausa se renueva. No se ejecutan retos de navegador. Un 302/403 sin huellas
de Imperva se reporta como respuesta HTTP, sin atribuirle una causa no comprobada.

Las respuestas 429/503 respetan `Retry-After`, tanto en segundos como en fecha HTTP,
mediante una pausa compartida por fuente. Esperas superiores a cinco segundos se devuelven
al usuario sin mantener abierta la llamada. Las consultas simultáneas idénticas comparten
una petición. La caché conserva como máximo 400 entradas y 32 MiB de contenido JSON;
este último valor no equivale a la memoria total del proceso. Sigue siendo una caché en
memoria, sin persistir términos de búsqueda en disco.

El Repositorio también puede quedar bloqueado. El respaldo de gacetas declara su cobertura
y fecha; no debe interpretarse como una copia completa o actualizada de todo el SJF.
Una excepción de bots o una credencial requiere autorización de la SCJN: estos controles
no levantan el bloqueo por sí solos.

### Ejecutorias desde el acervo propio

Las ejecutorias publicadas en las Gacetas también se sirven desde el acervo
permanente: 10,193 documentos con búsqueda por tema o expediente, texto por
partes y procedencia hasta el PDF y las páginas. Los extractos se distinguen
de las resoluciones con cierre. Véase [ACERVO-EJECUTORIAS.md](ACERVO-EJECUTORIAS.md)
para cobertura, límites, identificadores y reproducción. La opción `fuente="oficial"`
en las herramientas de ejecutorias solicita expresamente la consulta externa.

### El TEPJF no se consulta: se sirve

Las consultas electorales se resuelven desde el acervo propio, sin salir a la red.
Su API ignora el parámetro de búsqueda y devuelve
siempre el corpus entero —11.7 MB entre jurisprudencias y tesis—, y su host está detrás de
un bot manager, así que consultarla en vivo sería bajar todo en cada búsqueda y ganarse un
bloqueo por IP. En vez de eso, el corpus completo (2 078 criterios, 1997 a la fecha) vive
en `kriterius_datos/tepjf.jsonl`, con corte documentado al **3 de septiembre de 2026**,
versionado en el repo, y se indexa en **SQLite FTS5 en memoria** al
arrancar: búsqueda con BM25 —pesando el rubro diez veces más que el texto— e insensible a
acentos, en milisegundos y sin depender de que el portal esté vivo.

`sincronizar_tepjf.py` intenta actualizarlo cada lunes desde GitHub Actions y abre un PR
si obtiene datos completos. Un error de la fuente conserva el contenido y la fecha anteriores.
La revisión del 18 de noviembre no pudo confirmar novedades posteriores al corte: API 503,
filtros del portal sin conexión y sección del Semanario sin resultados. La compilación
oficial consultada mantiene el corte de septiembre. El manifiesto `/datos/tepjf.json`
expone esta limitación. En el acervo se ven los criterios que dejaron de estar vigentes: de los
2 078, **576 ya no lo están**, y el conector los muestra en un bloque aparte con el motivo
—sentencia, acuerdo general o reiteración— y el link al documento que los tumbó.

### La llave de CourtListener

Las fuentes mexicanas e interamericanas no necesitan credencial alguna. Las de Estados
Unidos sí, y **la pone cada usuario, no el servidor**: los cupos de CourtListener son por
cuenta —125 consultas al día en el plan gratuito— así que una llave compartida entre todos
se agotaría el primer día, y la letra chica de las membresías de Free Law Project excluye
sostener herramientas de terceros.

El usuario la pega con `configurar_courtlistener` y vive **en memoria, atada a su sesión
MCP**, en un `WeakKeyDictionary`: nunca se escribe en disco, no entra a los contadores de
uso ni a los registros, y desaparece al cerrar la conversación. Las referencias débiles no
son un adorno — con un diccionario indexado por `id(sesión)`, el recolector de basura
recicla direcciones y una conversación nueva podría heredar la llave de otra persona.

La versión local en Node no usa este mecanismo: ahí la llave se pide al instalar y la
guarda el llavero del sistema operativo, sin pasar por ninguna conversación.

## Qué lo distingue

**Prelación por obligatoriedad, no por fecha.** En el SJF los criterios se ordenan por
órgano (Pleno → Salas → Plenos Regionales → Plenos de Circuito → TCC), luego jurisprudencia
antes que tesis aislada, después época más reciente. En la Corte IDH van primero los casos
contra México, que vinculan de forma directa al Estado mexicano.

**Cita lista para pegar en un escrito**, con el link a la fuente oficial en la línea
siguiente. En la Corte IDH la unidad de resultado es el párrafo, no la tesis.

**Las dos colecciones del Semanario.** Las tesis y las ejecutorias viven en bases
distintas y solo la segunda contiene las controversias constitucionales, las acciones de
inconstitucionalidad y las declaratorias generales. Una sentencia puede pasar de 180 mil
caracteres, así que `ver_ejecutoria` la entrega por partes o devuelve los fragmentos
donde aparece el término que se busque.

## Cómo se usa

En claude.ai → *Settings → Connectors → Add custom connector* → pegar
`https://mcp.kriterius.mx/mcp`.

## Desarrollo

```bash
pip install -r requirements.txt
python server_http.py          # http://localhost:8000/mcp
python test_tepjf.py           # fuente TEPJF contra el snapshot real, sin red
python test_repositorio.py     # Repositorio de la SCJN y SIJ contra documentos reales, sin red
python test_sincronizar_tepjf.py  # normalización del sincronizador, sin red
python test_corteidh.py        # parser de la Corte IDH, sin red
python test_ejecutorias.py     # módulo de ejecutorias, sin red
python test_limites.py         # caché, concurrencia y reintentos, sin red
python -m unittest test_acceso_scjn  # Retry-After, pausas y solicitudes compartidas, sin red
python test_descubrimiento.py  # freno al auto-descubrimiento de endpoints, sin red
python test_uso.py             # medición de uso y privacidad de /uso, sin red
python test_eeuu.py            # CourtListener y aislamiento del llavero, sin red
```

Regenerar el snapshot del TEPJF a mano (baja ~370 MB del portal y tarda; el bot manager
obliga a reintentar):

```bash
python sincronizar_tepjf.py --resumen /tmp/resumen.md   # todo
python sincronizar_tepjf.py --sin-detalle               # solo texto y vigencia, 17 peticiones
```

El servidor expone `GET /salud` para monitoreo y `GET /` como página de estado.

Existe además una versión local en Node (`.mcpb`) para Claude Desktop, que corre en la
computadora del usuario. Ambas comparten formato de salida y prelación: sus pruebas usan
los mismos fixtures y producen salida idéntica.

## Aviso

KriteriusMX no está afiliado ni patrocinado por la SCJN, el TFJA, el TEPJF, la Segob o la
Corte Interamericana de Derechos Humanos. Consulta información pública de sus portales.
Los resultados **no sustituyen la consulta directa a la fuente oficial** ni constituyen
asesoría jurídica.

## Licencia

MIT — ver [LICENSE](LICENSE).
