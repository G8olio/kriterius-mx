# Muestra real del portal semanal

`2032910.txt` conserva el texto de la ficha oficial leído en Chrome el 13 de
noviembre de 2026, por el recorrido portada → tesis → búsqueda por registro →
ficha. Se retiraron navegación y pie del sitio y se normalizaron líneas vacías.

Fuente: https://sjfsemanal.scjn.gob.mx/detalle/tesis/2032910

El registro, rubro y clave se contrastaron con la búsqueda JSON oficial. Esta
muestra acredita el parseo de **una** ficha real. Las variantes fabricadas en los
tests son casos de estructura, no otras tesis reales ni prueba de acceso remoto.
# Contraste adicional del 16 de noviembre de 2026

`repositorio-20261116.json` conserva las celdas visibles del Repositorio oficial
para 2032910, 2032896 y 2032921, obtenidas mediante Chrome/CUA. No es una descarga
CSV/ZIP oficial. Los originales del portal semanal, fecha y canal viajan dentro
de `kriterius_datos/sjf_semanal/*.json`. Ambos sitios muestran la publicación del
13 de noviembre; el importador contrasta cuerpo completo, precedentes y metadatos.
