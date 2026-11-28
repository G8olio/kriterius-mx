"""Importación y auditoría del catálogo IUS completo, sin consultas de red.

Exige época, clave y rubro completos. Conserva procedencia y versiones, importa
solo lotes sin incidencias y devuelve salida 2 si --exigir-completo detecta huecos.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
import sjf_local as s


def documentos():
    if not s.asegurar():
        raise ValueError('Acervo propio no disponible')
    # El índice real incluye reemplazos de libros, incrementales y semanales.
    return [json.loads(r[0]) for r in s._db.execute('SELECT json FROM docs ORDER BY rowid')]


def epoca(valor):
    v = s._plegar(str(valor))
    nombres = {'NOVENA':9, 'DECIMA':10, 'UNDECIMA':11, 'DUODECIMA':12,
               'QUINTA':5, 'SEXTA':6, 'SEPTIMA':7, 'OCTAVA':8}
    for nombre, n in nombres.items():
        if v in (nombre, nombre+'EPOCA'):
            return f'{n}a'
    m = re.fullmatch(r'(\d{1,2})A?(?:EPOCA)?', v)
    if m:
        return f'{int(m[1])}a'
    raise ValueError('Época ausente o no reconocida')


def _desenvolver(v):
    if isinstance(v, str):
        v = v.strip()
        if v.startswith(('"', '[')):
            try:
                return json.loads(v)
            except ValueError:
                pass
    return v


def leer_catalogo(path):
    path = Path(path)
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as stream:
            muestra = stream.read(8192)
            stream.seek(0)
            dialect = csv.Sniffer().sniff(muestra, delimiters=',;\t')
            return list(csv.DictReader(stream, dialect=dialect))
    if path.suffix.lower() == '.jsonl':
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    data = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(data, list):
        return data
    if 'filas' in data:
        if not all(isinstance(r, list) and len(r) == 17 for r in data['filas']):
            raise ValueError('La tabla SCJN cambió de columnas')
        return [dict(zip(('registro_digital','epoca','localizacion','anio','mes',
                         'instancia','organo','publicacion','materias','tipo',
                         'clave','rubro','texto','precedentes','notas','anexos','certificado'),
                        map(_desenvolver, r))) for r in data['filas']]
    for key in ('documents', 'documentos', 'tesis', 'correspondencias'):
        if isinstance(data.get(key), list):
            return data[key]
    raise ValueError('Formato de catálogo no reconocido')


def normalizar_fila(r):
    campos = {s._plegar(k):_desenvolver(v) for k,v in r.items()}
    def campo(*claves):
        return next((campos[s._plegar(c)] for c in claves
                     if campos.get(s._plegar(c)) not in (None,'')), '')
    ius = str(campo('registro_digital','idTesis','ius','Registro digital'))
    if not re.fullmatch(r'[1-9][0-9]{5,7}', ius):
        raise ValueError('Registro digital ausente o inválido')
    clave = campo('clave','tesis','claveTesis','Número de Identificación')
    rubro = campo('rubro','Rubro (Título / Subtítulo)')
    if not isinstance(clave,str) or not clave.strip() or not isinstance(rubro,str) or not rubro.strip():
        raise ValueError('Falta clave o rubro completo')
    return {'registro_digital':int(ius), 'epoca':epoca(campo('epoca')),
            'clave':clave.strip(), 'rubro':rubro.strip()}


def cruzar(docs, catalogo, anteriores, *, fuente, fecha, sha256):
    s.validar_correspondencias(anteriores)
    existentes = {s._identidad(r):r for r in anteriores.get('correspondencias', [])}
    por_identidad, por_clave = defaultdict(list), defaultdict(list)
    for d in docs:
        por_identidad[s._identidad(d)].append(d)
        por_clave[(d.get('epoca'),s._plegar(d.get('clave')))].append(d)
    nuevas, incidencias, fuera = [], [], 0
    for numero, fila in enumerate(catalogo,1):
        try:
            r = normalizar_fila(fila)
        except (ValueError,TypeError,AttributeError) as exc:
            incidencias.append({'fila':numero,'motivo':str(exc)})
            continue
        key = s._identidad(r)
        candidatos = por_identidad.get(key, [])
        if not candidatos:
            if por_clave.get(key[:2]):
                incidencias.append({'fila':numero,'motivo':'clave_con_rubro_distinto',**r})
            else:
                fuera += 1
            continue
        if any(d.get('registro_digital') and d['registro_digital'] != r['registro_digital']
               for d in candidatos):
            incidencias.append({'fila':numero,'motivo':'conflicto_con_captura_permanente',**r})
            continue
        previa = existentes.get(key)
        if previa:
            if previa['registro_digital'] != r['registro_digital']:
                incidencias.append({'fila':numero,'motivo':'conflicto_con_mapa_anterior',**r})
            continue
        evidencia = {**r, 'id':candidatos[0]['id'], 'fuente':fuente,
                     'verificado':fecha, 'archivo_sha256':sha256, 'fila_catalogo':numero,
                     'metodo':'Catálogo oficial: época, clave y rubro completos coincidentes'}
        existentes[key] = evidencia
        nuevas.append(evidencia)
    resultado = {'version':2,'correspondencias':sorted(existentes.values(),
                   key=lambda r:(r['registro_digital'],r['id']))}
    # Una colisión global detiene el lote entero.
    s.validar_correspondencias(resultado)
    return resultado, {'nuevas_identidades':len(nuevas), 'fuera_del_acervo':fuera,
                       'incidencias':incidencias}


def auditar(docs, datos):
    mapa = s.validar_correspondencias(datos)
    pendientes, grupos, por_ius, identidades = [], defaultdict(list), defaultdict(set), set()
    enlaces, discordancias = [], []
    for d in docs:
        key = s._identidad(d)
        identidades.add(key)
        grupos[d['id']].append(d)
        guardado, cruzado = d.get('registro_digital'), mapa.get(key)
        if guardado and cruzado and guardado != cruzado:
            discordancias.append({'id':d['id'],'motivo':'IUS propio y catálogo distintos'})
        ius = guardado or cruzado
        fila = {k:d.get(k) for k in ('id','epoca','clave','rubro','gaceta','fuente_pdf')}
        if ius:
            enlaces.append({**fila,'registro_digital':ius})
            por_ius[ius].add(key)
        else:
            pendientes.append(fila)
    conflictos = [{'registro_digital':ius,'identidades':sorted(keys)}
                  for ius,keys in por_ius.items() if len(keys)>1] + discordancias
    distintos = [{'id':i,'rubros':sorted({d['rubro'] for d in grupo})}
                 for i,grupo in grupos.items() if len({s._identidad(d) for d in grupo})>1]
    huerfanas = [r for r in datos.get('correspondencias',[]) if s._identidad(r) not in identidades]
    return {'resumen':{
        'fichas_totales':len(docs),'identidades_distintas':len(identidades),
        'claves_por_epoca':len(grupos),'ius_distintos':len(por_ius),
        'fichas_enlazadas':len(enlaces),'fichas_pendientes':len(pendientes),
        'claves_con_rubros_distintos':len(distintos),'conflictos':len(conflictos),
        'correspondencias_huerfanas':len(huerfanas),
        'completo':bool(docs) and not (pendientes or conflictos or huerfanas),
        'fichas_por_epoca':dict(Counter(d.get('epoca') for d in docs))},
        'pendientes':pendientes,'enlaces':enlaces,'conflictos':conflictos,
        'claves_con_rubros_distintos':distintos,'huerfanas':huerfanas}


def escribir(path, valor):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_suffix(path.suffix+'.tmp')
    temporal.write_text(json.dumps(valor,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporal.replace(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--verificar', action='store_true')
    ap.add_argument('--catalogo', type=Path)
    ap.add_argument('--fuente')
    ap.add_argument('--fecha')
    ap.add_argument('--salida', type=Path, default=s.RUTA_REGISTROS)
    ap.add_argument('--informe', type=Path)
    ap.add_argument('--exigir-completo', action='store_true')
    args = ap.parse_args()
    if args.catalogo and (args.verificar or not args.fuente or not args.fecha):
        ap.error('La importación requiere --fuente y --fecha, sin --verificar')
    docs = documentos()
    datos = json.loads(args.salida.read_text()) if args.salida.exists() else {'version':2,'correspondencias':[]}
    importacion = None
    if args.catalogo:
        datos, importacion = cruzar(docs, leer_catalogo(args.catalogo), datos,
            fuente=args.fuente, fecha=args.fecha,
            sha256=hashlib.sha256(args.catalogo.read_bytes()).hexdigest())
    informe = auditar(docs, datos)
    if importacion is not None:
        informe['importacion'] = importacion
    if args.informe:
        for nombre,contenido in informe.items():
            escribir(args.informe/(nombre+'.json'), contenido)
    hay_errores = informe['conflictos'] or (importacion and importacion['incidencias'])
    if args.catalogo and not hay_errores:
        escribir(args.salida, datos)
    print(json.dumps(informe['resumen'],ensure_ascii=False,indent=2))
    if hay_errores:
        print('Lote no importado: revisar incidencias/conflictos del informe.')
        return 1
    return 2 if args.exigir_completo and not informe['resumen']['completo'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
