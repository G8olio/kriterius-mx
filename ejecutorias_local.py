"""Acervo permanente de resoluciones publicadas en Gacetas. Nunca usa la red.

Los datos comprimidos viajan en el repositorio y en la rueda. SQLite FTS5 se
construye durante el build y se abre en solo lectura en producción; ni el corpus
ni el índice completo se cargan en RAM. Los identificadores locales no son IUS.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sqlite3
import threading
import unicodedata
from pathlib import Path

DATOS = Path(__file__).parent/'kriterius_datos'/'sjf_ejecutorias'
INDICE = Path(os.environ.get('KRITERIUS_CACHE_DIR') or
              os.environ.get('XDG_CACHE_HOME') or Path.home()/'.cache')/'kriteriusmx'/'ejecutorias.fts.sqlite'
ESQUEMA = 2


def tramos(texto,tamano=12000):
    limites=[];i=0
    while i<len(texto):
        fin=min(i+tamano,len(texto))
        if fin<len(texto):
            corte=texto.rfind('\n',i+int(tamano*.7),fin)
            if corte>i:fin=corte
        limites.append((i,fin));i=fin
    return limites or [(0,0)]


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for bloque in iter(lambda:f.read(1<<20),b''):h.update(bloque)
    return h.hexdigest()


def plegar(s):
    s=''.join(c for c in unicodedata.normalize('NFD',str(s)) if unicodedata.category(c)!='Mn')
    return re.sub(r'\s+',' ',s).strip().upper()


def consulta_fts(s):
    tokens=re.findall(r'"([^"\n]+)"|(\S+)',s)
    return ' AND '.join('"'+(a or b).replace('"','""')+'"' for a,b in tokens)


class Repositorio:
    def __init__(self,datos=DATOS,indice=INDICE,solo_lectura=None):
        self.datos=Path(datos);self.indice=Path(indice);self.db=None;self.error=None
        self.solo_lectura=(os.environ.get('KRITERIUS_SJF_SOLO_LECTURA')=='1'
                           if solo_lectura is None else solo_lectura)
        self.candado=threading.RLock();self.info={};self.intentado=False

    def _origen(self):
        info=json.loads((self.datos/'meta.json').read_text())
        for parte in info['partes']:
            name=parte['archivo']
            if Path(name).name!=name:raise ValueError('Ruta de parte inválida')
            p=self.datos/name
            if p.stat().st_size!=parte['bytes'] or sha256(p)!=parte['sha256']:
                raise ValueError('Huella incorrecta: '+name)
        huella=hashlib.sha256((str(ESQUEMA)+json.dumps(info,sort_keys=True)).encode()).hexdigest()
        return info,huella

    def construir(self,info,huella):
        self.indice.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.indice.with_name(self.indice.name+f'.{os.getpid()}.tmp')
        tmp.unlink(missing_ok=True)
        db=sqlite3.connect(tmp)
        try:
            db.executescript('''PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
                PRAGMA cache_size=-16384; PRAGMA temp_store=FILE;
                CREATE TABLE docs(id TEXT UNIQUE NOT NULL, asunto TEXT, expediente TEXT,
                    organo TEXT, epoca TEXT, fecha TEXT, anio INTEGER, datos TEXT, texto TEXT,
                    asunto_busqueda TEXT, ius TEXT);
                CREATE VIRTUAL TABLE fts USING fts5(asunto,organo,texto,
                    content='docs',content_rowid='rowid', tokenize='unicode61 remove_diacritics 2');
                CREATE TABLE info(clave TEXT PRIMARY KEY,valor TEXT);
                CREATE INDEX por_expediente ON docs(expediente);
                CREATE INDEX por_asunto ON docs(asunto_busqueda);
                CREATE INDEX por_ius ON docs(ius);''')
            n=0
            for parte in info['partes']:
                cuenta=0
                with gzip.open(self.datos/parte['archivo'],'rt',encoding='utf-8') as f:
                    for linea in f:
                        d=json.loads(linea);texto=d.pop('texto')
                        if hashlib.sha256(texto.encode()).hexdigest()!=d['sha256_texto']:
                            raise ValueError('Texto alterado: '+d['id'])
                        if not re.fullmatch(r'gaceta-ejec-[0-9a-f]{20}',d['id']):
                            raise ValueError('Identificador local inválido')
                        d['_tramos']=tramos(texto)
                        d['_caracteres']=len(texto)
                        cur=db.execute('INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            (d['id'],d['asunto'],d['expediente'],d.get('organo') or '',d['epoca'],
                             d.get('publicacion_sjf') or '',int(d['gaceta']['anio']),
                             json.dumps(d,ensure_ascii=False),texto,plegar(d['asunto']),
                             str(d['registro_digital']) if d.get('registro_digital') else None))
                        db.execute('INSERT INTO fts(rowid,asunto,organo,texto) VALUES (?,?,?,?)',
                                   (cur.lastrowid,d['asunto'],d.get('organo') or '',texto))
                        n+=1;cuenta+=1
                        if n%100==0:db.commit()
                if cuenta!=parte['documentos']:raise ValueError('Conteo de parte incorrecto')
            if n!=info['documentos'] or not n:raise ValueError('Conteo de corpus incorrecto')
            db.execute('INSERT INTO info VALUES (?,?)',('origen',huella))
            db.execute('INSERT INTO info VALUES (?,?)',('documentos',str(n)))
            db.commit()
            db.execute("INSERT INTO fts(fts) VALUES('optimize')")
            db.commit()
        except Exception:
            db.close();tmp.unlink(missing_ok=True);raise
        else:db.close()
        tmp.replace(self.indice)

    def cargar(self):
        with self.candado:
            if self.db:return self.info['documentos']
            if self.intentado:return 0
            self.intentado=True
            try:
                info,huella=self._origen()
                sirve=False
                if self.indice.exists():
                    check=sqlite3.connect(f'file:{self.indice}?mode=ro',uri=True)
                    try:
                        row=check.execute("SELECT valor FROM info WHERE clave='origen'").fetchone()
                        sirve=bool(row and row[0]==huella)
                    finally:check.close()
                if not sirve:
                    if self.solo_lectura:raise ValueError('El índice de ejecutorias debe construirse durante el build')
                    self.construir(info,huella)
                self.db=sqlite3.connect(f'file:{self.indice}?mode=ro',uri=True,check_same_thread=False)
                self.db.execute('PRAGMA cache_size=-8192')
                self.db.row_factory=sqlite3.Row
                self.info=info
                self.error=None
                return info['documentos']
            except Exception as exc:
                self.error=str(exc);return 0

    def cerrar(self):
        with self.candado:
            if self.db:self.db.close()
            self.db=None;self.intentado=False

    def meta(self):
        self.cargar()
        claves=('documentos','conservacion','archivos_fuente','por_epoca','alcance',
                'gaceta_desde','gaceta_hasta','por_alcance_texto','rechazos_por_motivo')
        return {**{k:self.info.get(k) for k in claves},'disponible':self.db is not None,
                'consulta':'local_sin_red','error':self.error}

    def buscar(self,consulta,epocas=None,pagina=1,por_pagina=20,orden='jerarquia'):
        if not self.cargar():return [],0
        q=consulta_fts(consulta)
        if not q:return [],0
        pagina=max(1,int(pagina));por_pagina=min(50,max(1,int(por_pagina)))
        filtro='fts MATCH ?';args=[q]
        if epocas:
            filtro+=' AND d.epoca IN ('+','.join('?' for _ in epocas)+')';args.extend(epocas)
        jerarquia=("CASE WHEN d.organo LIKE 'Pleno de la Suprema%' THEN 0 "
                   "WHEN d.organo LIKE 'Primera Sala de la Suprema%' THEN 1 "
                   "WHEN d.organo LIKE 'Segunda Sala de la Suprema%' THEN 2 "
                   "WHEN d.organo LIKE 'Pleno Regional%' THEN 4 "
                   "WHEN d.organo LIKE 'Pleno %Circuito%' THEN 5 ELSE 6 END")
        orden_sql=jerarquia+',bm25(fts,12,2,1),d.fecha DESC,d.id' if orden!='fecha' else 'd.anio DESC,d.fecha DESC,bm25(fts,12,2,1),d.id'
        with self.candado:
            total=self.db.execute('SELECT count(*) FROM fts JOIN docs d ON d.rowid=fts.rowid WHERE '+filtro,args).fetchone()[0]
            rows=self.db.execute('SELECT d.datos,snippet(fts,2,\'\',\'\',\' … \',28) AS extracto '
                'FROM fts JOIN docs d ON d.rowid=fts.rowid WHERE '+filtro+' ORDER BY '+orden_sql+' LIMIT ? OFFSET ?',
                args+[por_pagina,(pagina-1)*por_pagina]).fetchall()
        return [{**json.loads(r['datos']),'extracto':r['extracto']} for r in rows],total

    def obtener(self,identificador,texto=True):
        if not self.cargar():return []
        s=str(identificador).strip()
        if s.startswith('gaceta-ejec-'):campo,valor='id',s
        elif s.isdigit():campo,valor='ius',s
        elif re.fullmatch(r'\d[\d.-]*/\d{2,4}(?:-[A-Z]+)?',s):campo,valor='expediente',s
        else:
            aliases={'ADR':'AMPARO DIRECTO EN REVISIÓN','AR':'AMPARO EN REVISIÓN',
                     'AD':'AMPARO DIRECTO','AI':'ACCIÓN DE INCONSTITUCIONALIDAD',
                     'CC':'CONTROVERSIA CONSTITUCIONAL','CT':'CONTRADICCIÓN DE TESIS'}
            tokens=s.split(maxsplit=1)
            if len(tokens)==2 and tokens[0].upper() in aliases:s=aliases[tokens[0].upper()]+' '+tokens[1]
            campo,valor='asunto_busqueda',plegar(s)
        with self.candado:
            # Un expediente puede corresponder a órganos o versiones diferentes.
            rows=self.db.execute('SELECT datos'+(',texto' if texto else '')+' FROM docs WHERE '+campo+'=? ORDER BY fecha DESC,id',(valor,)).fetchall()
        return [{**json.loads(r['datos']),**({'texto':r['texto']} if texto else {})} for r in rows]

    def leer_tramo(self,identificador,inicio,fin):
        if not self.cargar():return ''
        with self.candado:
            row=self.db.execute('SELECT substr(texto,?,?) FROM docs WHERE id=?',
                                (inicio+1,fin-inicio,identificador)).fetchone()
        return row[0] if row else ''


_repo=Repositorio()


def cargar():return _repo.cargar()
def meta():return _repo.meta()
def buscar(*args,**kwargs):return _repo.buscar(*args,**kwargs)
def obtener(identificador,texto=True):return _repo.obtener(identificador,texto=texto)


def cobertura():
    m=meta()
    if not m['disponible']:return 'El acervo local de ejecutorias no está disponible en este despliegue.'
    return (f"Acervo permanente de Kriterius: {m['documentos']} resoluciones publicadas, "
            f"Gacetas {m.get('gaceta_desde')} a {m.get('gaceta_hasta')}. "
            'Cobertura parcial del SJF, con los huecos de las Gacetas conservadas; no representa todas las sentencias de la SCJN.')


def referencia(d):
    partes=[d['asunto'],d.get('organo') or 'Órgano no determinado en la extracción',
            'Identificador local: '+d['id']+' (no es un registro digital IUS)',
            'Alcance: '+('resolución publicada con cierre' if d['alcance_texto']=='resolucion_con_cierre'
                        else 'extracto publicado en la Gaceta; la fuente no incluye una resolución íntegra')]
    if d.get('publicacion_sjf'):partes.append('Publicación SJF: '+d['publicacion_sjf'])
    for f in d['fuentes']:
        ini,fin=f.get('pagina_inicio') or {},f.get('pagina_fin') or {}
        localizacion=f"{d['gaceta']['titulo']}; {ini.get('tipo','página')} {ini.get('numero','?')} a {fin.get('numero','?')}"
        partes.extend([localizacion,'PDF oficial: '+(f.get('url_pdf') or 'https://www.scjn.gob.mx/coordinacion/gaceta'),
                       f"Origen conservado: {f['archivo_md']}, líneas {f['linea_inicio']}–{f['linea_fin']}."])
    return '\n'.join(partes)


def formatear_busqueda(rows,total,consulta,pagina):
    out=['EJECUTORIAS — ACERVO PERMANENTE DE KRITERIUS (sin consultas externas).',cobertura(),
         f'Búsqueda: {consulta}. Coincidencias locales: {total}. Página {pagina}.']
    for i,d in enumerate(rows,1):
        out.extend(['',f'{i}. '+referencia(d),d.get('extracto',''),
                    f"Lectura: ver_ejecutoria('{d['id']}', parte=1)."])
    if not rows:out.append('Sin coincidencias en este acervo; esto no prueba que el asunto no exista en la fuente oficial.')
    return '\n'.join(out)


def formatear_detalle(d,parte=1,buscar_en_texto=None):
    # Reutilizar la paginación que ya conocen los clientes del conector.
    from kriterius_mx import _ejec_coincidencias
    limites=d['_tramos']
    n=min(max(1,int(parte)),len(limites))
    out=['EJECUTORIA — ACERVO PERMANENTE DE KRITERIUS (sin consultas externas).',referencia(d),
         'SHA-256 del texto conservado: '+d['sha256_texto']]
    if buscar_en_texto:
        # Ventanas acotadas: el mapa de acentos de una resolución de millones de
        # caracteres no debe consumir la RAM del contenedor para una coincidencia.
        extractos=[]
        for inicio in range(0,d['_caracteres'],16000):
            bloque=_repo.leer_tramo(d['id'],max(0,inicio-1200),min(d['_caracteres'],inicio+17200))
            for x in _ejec_coincidencias(bloque,buscar_en_texto):
                if x not in extractos:extractos.append(x)
            if len(extractos)>=8:break
        extractos=extractos[:8]
        out.append(f"Coincidencias de {buscar_en_texto!r}: {len(extractos)}.")
        out.extend(extractos)
    else:
        ini,fin=limites[n-1]
        out.extend([f'TEXTO — parte {n} de {len(limites)} ({d["_caracteres"]} caracteres en total):',
                    _repo.leer_tramo(d['id'],ini,fin).strip()])
    out.append(f"Lectura por partes: ver_ejecutoria('{d['id']}', parte=N), de 1 a {len(limites)}. No hay caducidad.")
    out.append('Se conserva la versión publicada; no certifica modificaciones posteriores ni sustituye el expediente original.')
    return '\n\n'.join(out)


if __name__=='__main__':
    n=cargar()
    print(json.dumps(meta(),ensure_ascii=False,indent=2))
    raise SystemExit(0 if n else 1)
