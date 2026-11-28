#!/usr/bin/env python3
"""Enlaza todos los documentos con un catálogo oficial, sin consultas de red.

El resolver anterior consultaba solo el base y aceptaba prefijos de rubro. Esta
entrada conserva el nombre del comando, pero exige evidencia exacta para evitar
asignar un IUS ajeno. La adquisición masiva se hace por los canales de la SCJN.

python resolver_registros.py --verificar --informe /ruta/auditoria
python resolver_registros.py --catalogo tesis.csv --fuente URL --fecha AAAA-MM-DD
python resolver_registros.py --verificar --exigir-completo
"""
from enlazar_registros import main

if __name__ == '__main__':
    raise SystemExit(main())
