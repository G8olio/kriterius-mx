# KriteriusMX

Conector MCP que pone cuatro fuentes oficiales del derecho mexicano e interamericano
dentro de Claude, con cita completa y link oficial en cada resultado.

**Sitio:** [kriterius.mx](https://kriterius.mx) · **Endpoint MCP:** `https://mcp.kriterius.mx/mcp`

## Fuentes y herramientas (15)

| Fuente | Herramientas |
|---|---|
| **SJF / SCJN** — Semanario Judicial de la Federación | `buscar_tesis`, `investigar_criterio`, `ver_tesis` |
| **TFJA** — Tribunal Federal de Justicia Administrativa | `buscar_tesis_tfja`, `ver_tesis_tfja` |
| **DOF** — Diario Oficial de la Federación | `buscar_dof`, `ver_nota_dof`, `indicadores_dof`, `monitorear_dof` |
| **Corte IDH** — Buscador Jurídico de Derechos Humanos | `buscar_corteidh`, `explorar_corteidh`, `ver_caso_corteidh`, `investigar_criterio_corteidh` |
| Salud del servicio | `estado_conector`, `diagnosticar_conector` |

## Qué lo distingue

**Prelación por obligatoriedad, no por fecha.** En el SJF los criterios se ordenan por
órgano (Pleno → Salas → Plenos Regionales → Plenos de Circuito → TCC), luego jurisprudencia
antes que tesis aislada, después época más reciente. En la Corte IDH van primero los casos
contra México, que vinculan de forma directa al Estado mexicano.

**Cita lista para pegar en un escrito**, con el link a la fuente oficial en la línea
siguiente. En la Corte IDH la unidad de resultado es el párrafo, no la tesis.

## Cómo se usa

En claude.ai → *Settings → Connectors → Add custom connector* → pegar
`https://mcp.kriterius.mx/mcp`.

## Desarrollo

```bash
pip install -r requirements.txt
python server_http.py          # http://localhost:8000/mcp
python test_corteidh.py        # pruebas del parser, sin red
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
