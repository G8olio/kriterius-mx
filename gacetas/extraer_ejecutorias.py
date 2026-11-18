"""Extrae resoluciones publicadas de Gacetas Markdown, sin red y sin inventar IUS.

Cada bloque conserva las líneas originales, página, PDF y huellas del texto y de
su Markdown de origen. Las cabeceras citadas dentro de una sentencia no abren
otro documento. Los bloques sin cabecera, cuerpo o cierre reconocibles se
registran para revisión y no se presentan como sentencias completas.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

VERSION = 1
TIPOS = (r'AMPARO DIRECTO EN REVISI[ÓO]N|AMPARO EN REVISI[ÓO]N|AMPARO DIRECTO|'
         r'CONTRADICCI[ÓO]N DE (?:TESIS|CRITERIOS)|CONTROVERSIA CONSTITUCIONAL|'
         r'ACCI[ÓO]N DE INCONSTITUCIONALIDAD|RECURSO DE (?:RECLAMACI[ÓO]N|QUEJA|REVISI[ÓO]N)|'
         r'RECLAMACI[ÓO]N|QUEJA|INCONFORMIDAD|CONFLICTO COMPETENCIAL|COMPETENCIA|'
         r'INCIDENTE DE (?:INEJECUCI[ÓO]N|INCONFORMIDAD|SUSPENSI[ÓO]N)|'
         r'REVISI[ÓO]N ADMINISTRATIVA|IMPEDIMENTO|VARIOS|SOLICITUD DE '
         r'(?:MODIFICACI[ÓO]N DE JURISPRUDENCIA|SUSTITUCI[ÓO]N DE JURISPRUDENCIA|'
         r'EJERCICIO DE LA FACULTAD DE ATRACCI[ÓO]N)|'
         r'DECLARATORIA GENERAL DE INCONSTITUCIONALIDAD')
CABECERA = re.compile(r'^(' + TIPOS + r')\s+(\d[\d .-]*/\d{2,4}(?:-[A-Z]+)?)\b')
ANCLA = re.compile(r'^<!--\s*(p\.|hoja)\s*(\d+)\s*-->$')
CIERRE = re.compile(r'^Esta (sentencia|ejecutoria|tesis|voto) se public[oó]', re.I)
FIRMA = re.compile(r'^As[ií],?\s+(?:lo\s+|se\s+)?(?:resolvi[oó]|acord[oó]|resolvieron|resuelve|decidi[oó])', re.I | re.M)
RESOLUTIVOS = re.compile(r'R\s*E\s*S\s*U\s*E\s*L\s*V\s*E|RESOLUTIVOS|DECISI[ÓO]N|'
                         r'PRIMERO[. —-]|[ÚU]NICO[. —-]|PRIMERO\.-', re.I)
MESES = {m:i for i,m in enumerate(('enero','febrero','marzo','abril','mayo','junio',
         'julio','agosto','septiembre','octubre','noviembre','diciembre'),1)}


def normalizar(texto):
    texto = re.sub('\u00ad[ \t]*\n[ \t]*', '', texto)
    return texto.replace('\u00ad', '').replace('\r', '')


def plegar(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', texto)
                   if unicodedata.category(c) != 'Mn').upper()


def mayuscula(texto):
    letras = [c for c in texto if c.isalpha()]
    return bool(letras) and all(c.isupper() for c in letras)


def leer(ruta):
    raw = ruta.read_bytes()
    lineas = raw.decode('utf-8').splitlines()
    meta = {}
    inicio = 0
    if lineas and lineas[0] == '---':
        for i in range(1,len(lineas)):
            if lineas[i] == '---':
                inicio = i+1
                break
            if ':' in lineas[i]:
                k,v = lineas[i].split(':',1)
                meta[k] = v.strip().strip('"')
    paginas = []
    actual = None
    for l in lineas:
        m = ANCLA.match(l.strip())
        if m: actual = {'tipo':'pagina' if m[1]=='p.' else 'hoja', 'numero':int(m[2])}
        paginas.append(actual)
    return meta, lineas, paginas, inicio, hashlib.sha256(raw).hexdigest()


def cabeceras(lineas, inicio=0):
    resultado=[]
    for i in range(inicio,len(lineas)):
        l=normalizar(lineas[i]).strip()
        if not l or not mayuscula(l) or not re.match(r'^(AMPARO|CONTRADICCI|CONTROVERSIA|ACCI|'
                r'RECURSO|RECLAMACI|QUEJA|INCONFORMIDAD|CONFLICTO|COMPETENCIA|INCIDENTE|'
                r'REVISI|IMPEDIMENTO|VARIOS|SOLICITUD|DECLARATORIA)',plegar(l)):
            continue
        # Aceptar encabezados partidos por la maquetación, nunca párrafos en prosa.
        piezas=[]
        for extra in lineas[i:i+14]:
            limpio=normalizar(extra).strip()
            if ANCLA.match(limpio) or not limpio: continue
            if not mayuscula(limpio): break
            piezas.append(extra)
        encabezado=re.sub(r'\s+',' ',normalizar('\n'.join(piezas))).strip()
        m=CABECERA.match(encabezado)
        if not m: continue
        # Una cita en una tesis no contiene la ficha editorial completa de la sentencia.
        if not re.search(r'PONENTE|SECRETARI[OA]|VOTOS|UNANIMIDAD|MAYOR[IÍ]A',encabezado): continue
        resultado.append((i,m[1],re.sub(r'\s','',m[2]),encabezado))
    return resultado


def organo(texto):
    # Declaraciones de autoría, no órganos citados en argumentos o precedentes.
    entrada=re.sub(r'\s+',' ',normalizar(texto[:4500]))
    final=re.sub(r'\s+',' ',normalizar(texto[-12000:]))
    final=re.sub(r'\bA S [ÍI]\b','Así',final,flags=re.I)
    candidatos=[]
    for m in re.finditer(r'Acuerdo (?:del|de la)\s+(.{12,350})',entrada,re.I):
        candidatos.append(m[1])
    for m in re.finditer(r'As[ií],? lo (?:resolvi[oó]|acord[oó]|resolvieron),?\s+(.{12,500})',final,re.I):
        cola=m[1]
        # El órgano puede aparecer después de "por unanimidad de votos,".
        sujeto=re.search(r'(?:^|,\s*)(?:el|la|este|esta)\s+(.+)',cola,re.I)
        if sujeto:candidatos.append(sujeto[1])
    for candidato in candidatos:
        candidato=re.split(r',?\s+(?:por (?:mayor[ií]a|unanimidad)|correspondiente|integrado)|'
                           r'[:;]|\.(?: |$)',candidato,flags=re.I)[0].strip(' ,')
        if 'Suprema Corte' in candidato and re.match(r'(?:Tribunal )?Pleno\b',candidato,re.I):
            return 'Pleno de la Suprema Corte de Justicia de la Nación'
        m=re.match(r'(Primera|Segunda) Sala de la Suprema Corte',candidato,re.I)
        if m:return m[1].title()+' Sala de la Suprema Corte de Justicia de la Nación'
        if len(candidato)<280 and re.search(r'Tribunal|Pleno Regional|Pleno en|Pleno del',candidato,re.I):
            return candidato
    return None


def extraer(ruta, urls):
    meta,lines,paginas,inicio,sha=leer(ruta)
    starts=cabeceras(lines,inicio)
    cierres=[i for i,l in enumerate(lines) if CIERRE.match(l)]
    registros=[];rechazos=[];consumido=-1
    for n,(start,tipo,expediente,encabezado) in enumerate(starts):
        if start<=consumido:continue
        siguiente=starts[n+1][0] if n+1<len(starts) else len(lines)
        cierre=next((i for i in cierres if i>start),None)
        limite=min(siguiente,cierre if cierre is not None else len(lines))
        fin_publicado=(cierre is not None and cierre==limite and
                       CIERRE.match(lines[cierre])[1].lower() in ('sentencia','ejecutoria'))
        modo='marcador_publicacion' if fin_publicado else 'firma_y_siguiente_bloque'
        end=limite
        if fin_publicado:
            end=cierre+1
            while end<len(lines) and lines[end].strip() and not ANCLA.match(lines[end]):end+=1
        bloque='\n'.join(lines[start:end]).strip()
        texto=normalizar(bloque)
        firmas=list(FIRMA.finditer(texto))
        estructura=re.search(r'^(?:VISTOS|RESULTANDO|CONSIDERANDO|SENTENCIA|ANTECEDENTES|'
            r'[I1]\. |[1-9]\d*\. |PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|S[ÉE]PTIMO|OCTAVO|'
            r'NOVENO|D[ÉE]CIMO|[ÚU]NICO|ÍNDICE TEMÁTICO|SÍNTESIS CIUDADANA)',texto[:10000],re.I|re.M)
        if len(texto)<2000 or not estructura:
            rechazos.append({'linea':start+1,'asunto':tipo+' '+expediente,'motivo':'sin_cuerpo_reconocible'});continue
        if not fin_publicado:
            if not firmas or not RESOLUTIVOS.search(texto):
                rechazos.append({'linea':start+1,'asunto':tipo+' '+expediente,'motivo':'limite_sin_cierre_verificable'});continue
            firma_linea=next((j for j in range(end-1,start,-1) if FIRMA.search(lines[j])),None)
            if firma_linea is None:continue
            for j in range(firma_linea+2,end):
                limpio=normalizar(lines[j]).strip()
                nuevo_voto=re.match(r'^VOTO[S]? (?:PARTICULAR|CONCURRENTE|ACLARATORIO|MINORITARIO)',limpio,re.I)
                nuevo_rubro=(len(limpio)>45 and mayuscula(limpio) and
                             (j==0 or not lines[j-1].strip()) and
                             not re.match(r'^(PRIMERO|SEGUNDO|TERCERO|CUARTO|[ÚU]NICO)\b',limpio))
                if nuevo_voto or nuevo_rubro:
                    end=j;break
            bloque='\n'.join(lines[start:end]).strip();texto=normalizar(bloque)
        consumido=end-1
        fuente={'archivo_md':ruta.parent.name+'/'+ruta.name,'sha256_md':sha,
                'linea_inicio':start+1,'linea_fin':end,
                'pagina_inicio':paginas[start], 'pagina_fin':paginas[end-1],
                'pdf':meta.get('pdf'),'url_pdf':urls.get(meta.get('pdf')),
                'sha256_pdf_prefijo':meta.get('pdf_sha256_16'),
                'convertido':meta.get('convertido'),'limite':modo}
        fecha=None
        if fin_publicado:
            m=re.search(r'el\s+(?:\w+\s+)?(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})',lines[cierre],re.I)
            if m and m[2].lower() in MESES:fecha=f'{m[3]}-{MESES[m[2].lower()]:02}-{int(m[1]):02}'
        identidad=f"{fuente['archivo_md']}|{tipo}|{expediente}|{start+1}"
        registros.append({'id':'gaceta-ejec-'+hashlib.sha256(identidad.encode()).hexdigest()[:20],
            'tipo_asunto':tipo,'expediente':expediente,'asunto':tipo+' '+expediente,
            'organo':organo(texto),'epoca':ruta.parent.name.replace('-epoca',''),
            'gaceta':{k:meta.get(k) for k in ('titulo','libro','anio','mes','tomo','parte')},
            'publicacion_sjf':fecha,'encabezado':encabezado,'texto':texto,
            'alcance_texto':'resolucion_con_cierre' if FIRMA.search(texto) and RESOLUTIVOS.search(texto) else 'extracto_publicado',
            'sha256_texto':hashlib.sha256(texto.encode()).hexdigest(),
            'sha256_bloque_md':hashlib.sha256(bloque.encode()).hexdigest(),'fuentes':[fuente]})
    return registros,{'archivo':ruta.parent.name+'/'+ruta.name,'sha256_md':sha,
                     'cabeceras':len(starts),'cierres_sentencia':sum(bool(re.match(r'^Esta (sentencia|ejecutoria)',lines[i],re.I)) for i in cierres),
                     'extraidas':len(registros),'rechazos':rechazos}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--md-root',type=Path,action='append',required=True)
    parser.add_argument('--manifest',type=Path,action='append',default=[])
    parser.add_argument('--salida',type=Path,required=True)
    parser.add_argument('--informe',type=Path,required=True)
    args=parser.parse_args();urls={}
    for path in args.manifest:
        with path.open() as f:
            for row in csv.DictReader(f,delimiter='\t'):urls[row['archivo']]=row['url']
    args.salida.mkdir(parents=True,exist_ok=True)
    sources=[];counts=Counter();alcances=Counter();fechas=[];shards=[];stream=None;raw=None;size=0;total=0
    paths=sorted(p for root in args.md_root for p in root.rglob('*.md'))
    def cerrar():
        if stream:
            stream.close();raw.close()
            path=args.salida/shards[-1]['archivo']
            shards[-1].update(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    try:
        for i,path in enumerate(paths,1):
            rows,info=extraer(path,urls);sources.append(info)
            for d in rows:
                line=(json.dumps(d,ensure_ascii=False,separators=(',',':'))+'\n').encode()
                if stream is None or size+len(line)>90_000_000:
                    cerrar();name=f'parte-{len(shards)+1:03}.jsonl.gz'
                    raw=(args.salida/name).open('wb');stream=gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0)
                    shards.append({'archivo':name,'documentos':0});size=0
                stream.write(line);size+=len(line);shards[-1]['documentos']+=1;total+=1
                counts[d['epoca']]+=1
                alcances[d['alcance_texto']]+=1
                fechas.append(f"{d['gaceta']['anio']}-{MESES[d['gaceta']['mes'].lower()]:02}")
            if i%20==0:print(f'{i}/{len(paths)} libros; {total} ejecutorias',flush=True)
    finally:cerrar()
    report={'esquema':VERSION,'documentos':total,'conservacion':'permanente','archivos_fuente':len(paths),
            'por_epoca':dict(counts),'partes':shards,'fuentes':sources,
            'por_alcance_texto':dict(alcances),'gaceta_desde':min(fechas),'gaceta_hasta':max(fechas),
            'alcance':'Resoluciones publicadas extraídas de las Gacetas disponibles; no es la totalidad de las sentencias SCJN. No contiene IUS inferidos.',
            'rechazos_por_motivo':dict(Counter(r['motivo'] for s in sources for r in s['rechazos']))}
    args.informe.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('fuentes','partes')},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
