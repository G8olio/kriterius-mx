"""Publica solo fichas validadas desde un checkout limpio de main.

La captura y la importación ocurren antes. El commit reúne el lote; un push no
fast-forward se detiene sin forzar ni rebasar cambios ajenos. No despliega código
nuevo: este comando solo admite modificaciones bajo kriterius_datos/sjf_semanal.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

import sjf_semanal as s


def git(repo,*args):
    return subprocess.run(['git','-C',str(repo),*args],check=True,text=True,
                          capture_output=True).stdout.rstrip('\n')


def validar_archivos(repo, paths):
    if not paths:
        return []
    registros=[]
    for path in paths:
        match=re.fullmatch(r'kriterius_datos/sjf_semanal/([1-9][0-9]{5,7})\.json',path)
        if not match:
            raise ValueError('El checkout contiene cambios ajenos al lote: '+path)
        file=Path(repo)/path
        if file.is_symlink() or file.stat().st_size>s.MAX_BYTES:
            raise ValueError('Archivo de captura inválido: '+path)
        s.leer_captura(json.loads(file.read_text()),match[1])
        registros.append(match[1])
    if len(registros)>100:
        raise ValueError('El lote supera 100 fichas')
    return registros


def publicar(repo):
    repo=Path(repo).resolve()
    if git(repo,'branch','--show-current')!='main':
        raise ValueError('La publicación requiere un checkout dedicado de main')
    if git(repo,'remote','get-url','origin') not in (
        'https://github.com/G8olio/kriterius-mx.git','git@github.com:G8olio/kriterius-mx.git'):
        raise ValueError('Repositorio remoto distinto del autorizado')
    git(repo,'fetch','origin','main')
    if git(repo,'rev-parse','HEAD')!=git(repo,'rev-parse','origin/main'):
        raise ValueError('main cambió o hay commits pendientes; revisar antes de publicar')
    if git(repo,'diff','--cached','--name-only'):
        raise ValueError('Hay cambios preparados previamente en el índice')
    status=git(repo,'status','--porcelain','--untracked-files=all')
    paths=[]
    for line in status.splitlines():
        match=re.fullmatch(r'(?: M|\?\?) (.+)',line)
        if not match:raise ValueError('Estado de archivo no permitido: '+line)
        paths.append(match[1])
    registros=validar_archivos(repo,paths)
    if not registros:return {'estado':'sin_cambios','registros':[]}
    git(repo,'diff','--check')
    git(repo,'add','--',*paths)
    git(repo,'commit','-m',f'SJF: incorpora {len(registros)} capturas semanales verificadas')
    commit=git(repo,'rev-parse','HEAD')
    git(repo,'push','origin','HEAD:main')
    return {'estado':'publicado','commit':commit,'registros':registros,
            'pendiente':'comprobar despliegue y respuestas MCP'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repositorio',required=True,type=Path)
    args=parser.parse_args()
    try:print(json.dumps(publicar(args.repositorio),ensure_ascii=False,indent=2))
    except (ValueError,OSError,subprocess.CalledProcessError,KeyError,TypeError) as error:
        parser.exit(1,f'Publicación detenida: {error}\n')
