"""
El snapshot del IUS Electoral, empaquetado.

Esta carpeta es un paquete de Python —con este archivo y todo— por una razón muy
concreta: `pyproject.toml` declara el core como `py-modules`, y un proyecto sin
paquetes no tiene dónde colgar sus datos. Con `datos/` a secas, la rueda que
instala la app web traía `kriterius_mx.py` y `tepjf.py` pero NO el JSONL, y el
TEPJF quedaba apagado en producción sin que nada lo dijera.

Se llama `kriterius_datos` y no `datos` porque al instalarse queda como nombre de
primer nivel en site-packages, junto a los de todo lo demás que esté instalado.
"""
