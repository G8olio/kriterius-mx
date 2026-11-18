# Ejecutorias permanentes de las Gacetas

Las consultas `buscar_ejecutorias` y `ver_ejecutoria` usan el acervo propio por
defecto y no hacen peticiones a la SCJN, incluso cuando no hay coincidencias.
La opción explícita `fuente="oficial"` conserva la consulta remota anterior.

Ejemplos:

```python
await buscar_ejecutorias('6897/2025')
await ver_ejecutoria('ADR 6897/2025', parte=1)
await ver_ejecutoria('gaceta-ejec-IDENTIFICADOR_DEVUELTO', buscar_en_texto='competencia')
```

El identificador `gaceta-ejec-…` es propio de Kriterius y nunca se presenta como
IUS. Una búsqueda por expediente puede encontrar órganos o publicaciones
distintos: la herramienta devuelve opciones, no selecciona una arbitrariamente.
El texto se conserva completo respecto del tramo publicado, con lectura por
partes. Algunos documentos son extractos publicados, no sentencias íntegras;
esa condición aparece tanto en la búsqueda como en el detalle.

## Fuentes y límites

Se procesaron 451 Markdown: 448 del acervo original y tres de agosto–octubre de
2026. Hay 10,193 documentos (8,846 resoluciones con cierre y 1,347 extractos),
de Gacetas de marzo de 2011 a octubre de 2026. No es una colección exhaustiva
de todas las sentencias de la SCJN. Conserva los huecos de los libros disponibles
y aparta 421 cabeceras cuyos límites o cuerpo no se pudieron validar.

Cada documento conserva asunto, expediente, órgano cuando se puede determinar
de una declaración de autoría, alcance, fecha de publicación cuando consta,
páginas/hojas, URL del PDF oficial, archivo y líneas Markdown, y SHA-256 del
Markdown, del tramo y del texto normalizado. Solo se normalizan guiones suaves
de la conversión PDF y retornos de carro. No se completan secciones ausentes.

Los identificadores distinguen las publicaciones. Dos apariciones del mismo
expediente se conservan; no se fusionan automáticamente posibles correcciones.
No se infiere el IUS de los registros de tesis citadas dentro del texto.

## Distribución y operación

`kriterius_datos/sjf_ejecutorias/` contiene las partes JSONL comprimidas y
`meta.json`, que incluye conteos, huellas, fuentes y rechazos. Viaja tanto en la
imagen MCP como en la rueda `kriterius-core` utilizada por la aplicación web.
El acervo no tiene TTL ni fecha de caducidad.

`python -m ejecutorias_local` construye SQLite FTS5 durante el build. En runtime
se abre en solo lectura con `KRITERIUS_SJF_SOLO_LECTURA=1`. Los datos se leen
por documento, sin cargar el corpus completo en RAM. Una discrepancia de huella
o un índice ausente deja la fuente indisponible; no dispara una consulta remota.

- `/datos/ejecutorias.json`: cobertura real del índice MCP.
- `/datos/ejecutorias/{identificador}.json`: documento completo y procedencia.
- `/acervo` web: cobertura de ejecutorias, tesis y TEPJF instaladas.

## Reproducción de la extracción

Desde el checkout, con las fuentes originales disponibles en la Mac:

```sh
python gacetas/extraer_ejecutorias.py \
  --md-root ../gacetas-sjf/md \
  --md-root ../actualizacion-acervo-20261118/gacetas/md \
  --manifest ../gacetas-sjf/gacetas_manifest.tsv \
  --manifest gacetas/ejecutorias_fuentes_adicionales.tsv \
  --salida /ruta/nueva/extraccion \
  --informe /ruta/nueva/extraccion/meta.json
```

Este comando genera un corpus de las fuentes indicadas. Para una actualización,
conservar las fuentes anteriores y comparar los conteos y rechazos antes de
reemplazar los datos versionados; no generar un reemplazo a partir de un solo
libro nuevo. La sincronización actual de tesis no modifica este corpus.

## Validación

`test_ejecutorias_local.py` prueba fuentes reales de 2011 y 2026, documentos
truncados, citas, extractos, separación de asuntos, opciones ante ambigüedad,
integridad de hashes, modo de solo lectura y ausencia de HTTP.
`validar_ejecutorias_distribuidas.py` prueba la rueda instalada y la imagen sin
red, incluyendo todas las partes de una resolución de casi tres millones de
caracteres. CI ejecuta la imagen con límite de 512 MiB.
