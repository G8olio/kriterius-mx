# KriteriusMX

Conector MCP que pone seis fuentes oficiales de derecho —mexicano, interamericano y
estadounidense— dentro de Claude, con cita completa y link oficial en cada resultado.

**Sitio:** [kriterius.mx](https://kriterius.mx) · **Endpoint MCP:** `https://mcp.kriterius.mx/mcp`

## Fuentes y herramientas (26)

| Fuente | Herramientas |
|---|---|
| **SJF / SCJN** — Semanario Judicial de la Federación, colección de tesis | `buscar_tesis`, `investigar_criterio`, `ver_tesis` |
| **SJF / SCJN** — ejecutorias y precedentes (sentencias completas) | `buscar_ejecutorias`, `ver_ejecutoria` |
| **TFJA** — Tribunal Federal de Justicia Administrativa | `buscar_tesis_tfja`, `ver_tesis_tfja` |
| **TEPJF** — Tribunal Electoral del PJF, IUS Electoral (snapshot local, sin red) | `buscar_tesis_tepjf`, `ver_tesis_tepjf`, `temas_tepjf` |
| **DOF** — Diario Oficial de la Federación | `buscar_dof`, `ver_nota_dof`, `indicadores_dof`, `monitorear_dof` |
| **Corte IDH** — Buscador Jurídico de Derechos Humanos | `buscar_corteidh`, `explorar_corteidh`, `ver_caso_corteidh`, `investigar_criterio_corteidh` |
| **CourtListener** — jurisprudencia de EE.UU. (requiere llave del usuario) | `ayuda_derecho_eeuu`, `configurar_courtlistener`, `buscar_casos_eeuu`, `ver_caso_eeuu`, `quien_cita_eeuu`, `verificar_citas_eeuu` |
| Salud del servicio | `estado_conector`, `diagnosticar_conector` |

### El TEPJF no se consulta: se sirve

Es la única fuente que no sale a la red. Su API ignora el parámetro de búsqueda y devuelve
siempre el corpus entero —11.7 MB entre jurisprudencias y tesis—, y su host está detrás de
un bot manager, así que consultarla en vivo sería bajar todo en cada búsqueda y ganarse un
bloqueo por IP. En vez de eso, el corpus completo (2 078 criterios, 1997 a la fecha) vive
en `kriterius_datos/tepjf.jsonl`, versionado en el repo, y se indexa en **SQLite FTS5 en memoria** al
arrancar: búsqueda con BM25 —pesando el rubro diez veces más que el texto— e insensible a
acentos, en milisegundos y sin depender de que el portal esté vivo.

`sincronizar_tepjf.py` lo regenera cada lunes desde GitHub Actions y abre un PR con el
diff. Ahí es donde se ven, uno por uno, los criterios que dejaron de estar vigentes: de los
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
python test_sincronizar_tepjf.py  # normalización del sincronizador, sin red
python test_corteidh.py        # parser de la Corte IDH, sin red
python test_ejecutorias.py     # módulo de ejecutorias, sin red
python test_limites.py         # caché, concurrencia y reintentos, sin red
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
