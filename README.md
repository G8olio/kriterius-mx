# KriteriusMX

Conector MCP que pone cinco fuentes oficiales de derecho —mexicano, interamericano y
estadounidense— dentro de Claude, con cita completa y link oficial en cada resultado.

**Sitio:** [kriterius.mx](https://kriterius.mx) · **Endpoint MCP:** `https://mcp.kriterius.mx/mcp`

## Fuentes y herramientas (23)

| Fuente | Herramientas |
|---|---|
| **SJF / SCJN** — Semanario Judicial de la Federación, colección de tesis | `buscar_tesis`, `investigar_criterio`, `ver_tesis` |
| **SJF / SCJN** — ejecutorias y precedentes (sentencias completas) | `buscar_ejecutorias`, `ver_ejecutoria` |
| **TFJA** — Tribunal Federal de Justicia Administrativa | `buscar_tesis_tfja`, `ver_tesis_tfja` |
| **DOF** — Diario Oficial de la Federación | `buscar_dof`, `ver_nota_dof`, `indicadores_dof`, `monitorear_dof` |
| **Corte IDH** — Buscador Jurídico de Derechos Humanos | `buscar_corteidh`, `explorar_corteidh`, `ver_caso_corteidh`, `investigar_criterio_corteidh` |
| **CourtListener** — jurisprudencia de EE.UU. (requiere llave del usuario) | `ayuda_derecho_eeuu`, `configurar_courtlistener`, `buscar_casos_eeuu`, `ver_caso_eeuu`, `quien_cita_eeuu`, `verificar_citas_eeuu` |
| Salud del servicio | `estado_conector`, `diagnosticar_conector` |

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
python test_corteidh.py        # parser de la Corte IDH, sin red
python test_ejecutorias.py     # módulo de ejecutorias, sin red
python test_limites.py         # caché, concurrencia y reintentos, sin red
python test_descubrimiento.py  # freno al auto-descubrimiento de endpoints, sin red
python test_uso.py             # medición de uso y privacidad de /uso, sin red
python test_eeuu.py            # CourtListener y aislamiento del llavero, sin red
```

El servidor expone `GET /salud` para monitoreo y `GET /` como página de estado.

Existe además una versión local en Node (`.mcpb`) para Claude Desktop, que corre en la
computadora del usuario. Ambas comparten formato de salida y prelación: sus pruebas usan
los mismos fixtures y producen salida idéntica.

## Aviso

KriteriusMX no está afiliado ni patrocinado por la SCJN, el TFJA, la Segob o la Corte
Interamericana de Derechos Humanos. Consulta información pública de sus portales.
Los resultados **no sustituyen la consulta directa a la fuente oficial** ni constituyen
asesoría jurídica.

## Licencia

MIT — ver [LICENSE](LICENSE).
