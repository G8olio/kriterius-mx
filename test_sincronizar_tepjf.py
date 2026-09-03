#!/usr/bin/env python3
"""
Pruebas del sincronizador del TEPJF. Sin red, contra los fixtures de
`fixtures_tepjf/` (respuestas reales recortadas del API del IUS Electoral).

Lo que se prueba aquí es la normalización, que es donde un error se convierte en
un snapshot equivocado que nadie nota: la vigencia leída del sufijo de la clave,
el año de dos dígitos, los romanos de las tesis, el orden determinista del archivo
y el guardia que impide escribir un corpus mermado.

Uso: python3 test_sincronizar_tepjf.py
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import sincronizar_tepjf as s

FIXTURES = Path(__file__).parent / "fixtures_tepjf"
fallos = 0


def comprobar(nombre, condicion, detalle=""):
    global fallos
    if condicion:
        print(f"  [OK]    {nombre}")
    else:
        print(f"  [FALLA] {nombre}" + (f" — {detalle}" if detalle else ""))
        fallos += 1


def cargar(nombre):
    return json.loads((FIXTURES / nombre).read_text(encoding="utf-8"))


print("\n== Claves ==")
comprobar("quita el sufijo de vigencia",
          s._clave_limpia("47/2016--No Vigente por Sentencia") == "47/2016")
comprobar("quita el sufijo de reiteración",
          s._clave_limpia("LIII/2024--No Vigente por Reiteración") == "LIII/2024")
comprobar("expande el año de dos dígitos", s._clave_limpia("1/97") == "1/1997")
comprobar("expande el año de dos dígitos con romano", s._clave_limpia("IX/99") == "IX/1999")
comprobar("no toca una clave normal", s._clave_limpia("11/2018") == "11/2018")
comprobar("tolera una clave sin diagonal", s._clave_limpia("rarísima") == "rarísima")

comprobar("lee un número arábigo", s._numero("47/2016") == 47)
comprobar("lee un romano simple", s._numero("XVI/2026") == 16)
comprobar("lee un romano sustractivo", s._numero("XLIX/2015") == 49)
comprobar("un número ilegible no truena", s._numero("ABC/2020") == 0)


print("\n== Vigencia ==")
vivo = s._vigencia("11/2018", "tesisjur.aspx?idtesis=11/2018&tpoBusqueda=S")
comprobar("un criterio vigente se marca vigente", vivo["vigente"] is True)
comprobar("un criterio vigente no trae motivo", vivo["motivo_no_vigente"] is None)

html_sentencia = ('#&De conformidad con lo determinado en la sentencia SUP-JRC-158/2018, el '
                  'presente criterio se declaró no vigente.<a href="http://www.te.gob.mx/EE/'
                  'SUP/2018/JRC/158/SUP_2018_JRC_158-799672.pdf" target=\'_blank\'> VER '
                  'SENTENCIA</a>&tesisjur.aspx?idtesis=47/2016&tpoBusqueda=S')
muerto = s._vigencia("47/2016--No Vigente por Sentencia", html_sentencia)
comprobar("un criterio derogado se marca no vigente", muerto["vigente"] is False)
comprobar("se extrae el motivo", muerto["motivo_no_vigente"] == "Sentencia")
comprobar("se extrae la nota", "SUP-JRC-158/2018" in (muerto["nota_vigencia"] or ""))
comprobar("la nota va sin HTML", "<" not in (muerto["nota_vigencia"] or ""))
comprobar("se extrae el link a la sentencia",
          (muerto["url_vigencia"] or "").endswith("799672.pdf"))

reiterado = s._vigencia(
    "LIII/2024--No Vigente por Reiteración",
    "#&La Tesis LIII/2024 fue reiterada mediante la jurisprudencia 7/2026. "
    "<a href=\"https://www.te.gob.mx/ius2021/#/7-2026\" target='_blank'> VER "
    "JURISPRUDENCIA</a>&tesisjur.aspx?idtesis=LIII/2024&tpoBusqueda=S")
comprobar("el tercer motivo (Reiteración) se reconoce",
          reiterado["motivo_no_vigente"] == "Reiteración")
comprobar("se extrae el link a la jurisprudencia que reiteró",
          "7-2026" in (reiterado["url_vigencia"] or ""))

vacio = s._vigencia("9/2003--No Vigente por Acuerdo General",
                    '#&Se declaró no vigente.<a href="" target=\'_blank\'> VER</a>&tesisjur.aspx')
comprobar("un href vacío no truena y deja el link en None", vacio["url_vigencia"] is None)
comprobar("sin HTML de nota tampoco truena",
          s._vigencia("9/2003--No Vigente por Sentencia", "")["nota_vigencia"] is None)
comprobar("un motivo desconocido igual marca no vigente",
          s._vigencia("1/2030--No Vigente por Motivo Nuevo", "")["vigente"] is False)


print("\n== Normalización de un registro real ==")
tipo1 = cargar("volcado_tipo1_recorte.json")
tipo2 = cargar("volcado_tipo2_recorte.json")
registros = [s.normalizar(c) for c in tipo1 + tipo2]
por_clave = {r["clave_cruda"]: r for r in registros}

j = por_clave["11/2018"]
comprobar("tipo J para jurisprudencia", j["tipo"] == "J")
comprobar("año como entero", j["anio"] == 2018)
comprobar("época extraída del textoAyuda", (j["epoca"] or "").endswith("Época"), str(j["epoca"]))
comprobar("localización extraída", "Gaceta" in (j["localizacion"] or ""), str(j["localizacion"]))
comprobar("rubro sin HTML y en un solo renglón",
          "<" not in j["rubro"] and "\n" not in j["rubro"])
comprobar("texto sin HTML y sin retornos de carro",
          "<" not in j["texto"] and "\r" not in j["texto"])
comprobar("los campos de detalle nacen vacíos",
          j["precedentes"] == [] and j["detalle_ok"] is False)
comprobar("todas las líneas tienen las mismas claves",
          len({tuple(sorted(r)) for r in registros}) == 1)

nv = por_clave["47/2016--No Vigente por Sentencia"]
comprobar("la clave limpia y la cruda conviven",
          nv["clave"] == "47/2016" and "--" in nv["clave_cruda"])
comprobar("el no vigente trae nota", bool(nv["nota_vigencia"]), str(nv["nota_vigencia"])[:80])

t = por_clave["XVI/2026"]
comprobar("tipo T para tesis", t["tipo"] == "T")
comprobar("la clave romana se conserva", t["clave"] == "XVI/2026")

viejo = por_clave.get("9/99") or por_clave.get("8/99--No Vigente por Acuerdo General")
comprobar("el año de dos dígitos se expande en el registro",
          viejo is not None and viejo["clave"].endswith("/1999"),
          viejo["clave"] if viejo else "sin fixture")

anterior = next((r for r in registros if r["clave_anterior"]), None)
comprobar("se rescata la clave anterior cuando la hay",
          anterior is not None and "S3EL" in anterior["clave_anterior"],
          str(anterior["clave_anterior"]) if anterior else "ninguno en el recorte")


print("\n== Enriquecimiento con el detalle ==")


class FuenteFalsa:
    """Devuelve el fixture que toque sin tocar la red."""

    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.llamadas = []

    async def traer(self, ruta, params):
        self.llamadas.append((ruta, params))
        return self.respuestas.get(params.get("Clave") or params.get("tema") or ruta)


detalle_j = cargar("detalle_11_2018.json")
r = s.normalizar(next(c for c in tipo1 if c["claveNueva"] == "11/2018"))
asyncio.run(s.enriquecer(FuenteFalsa({"11/2018": detalle_j}), r, None))
comprobar("detalle_ok queda en True", r["detalle_ok"] is True)
comprobar("se rescatan los precedentes", len(r["precedentes"]) >= 1, str(len(r["precedentes"])))
comprobar("los precedentes van sin HTML", all("<" not in p for p in r["precedentes"]))
comprobar("se rescata la aprobación", bool(r["aprobacion"]))
comprobar("se rescata la publicación", bool(r["publicacion"]))
comprobar("se rescatan los artículos citados", len(r["articulos"]) >= 1)
comprobar("los artículos traen ley y artículo",
          all(set(a) == {"ley", "articulo"} for a in r["articulos"]))
comprobar("se rescatan las ligas a las sentencias de los precedentes",
          any(x.get("url") for x in r["sentencias"]), str(r["sentencias"])[:120])

r2 = s.normalizar(next(c for c in tipo1 if c["claveNueva"] == "11/2018"))
asyncio.run(s.enriquecer(FuenteFalsa({}), r2, None))
comprobar("si el detalle falla, el criterio entra igual con detalle_ok False",
          r2["detalle_ok"] is False and r2["rubro"] == r["rubro"])
asyncio.run(s.enriquecer(FuenteFalsa({"11/2018": {"_event_transid": "x"}}), r2, None))
comprobar("una respuesta del bot manager no se toma por detalle",
          r2["detalle_ok"] is False)

detalle_nv = cargar("detalle_XXVI_2004_no_vigente.json")
r3 = s.normalizar({"claveNueva": "XXVI/2004--No Vigente por Reiteración",
                   "clasificacion": "T", "anio": "2004", "rubro": "X", "texto": "Y",
                   "textoAyuda": "Tesis. Clave anterior: , 5a Época, Localización: Gaceta.",
                   "idTesis": "1", "sala": "SALA SUPERIOR", "urL_tesisjurRel": ""})
asyncio.run(s.enriquecer(FuenteFalsa({"XXVI/2004--No Vigente por Reiteración": detalle_nv}),
                         r3, None))
comprobar("un no vigente también se enriquece", r3["detalle_ok"] is True)
comprobar("el enriquecimiento no revive un criterio muerto", r3["vigente"] is False)


print("\n== El parámetro url del detalle ==")
nv_html = por_clave["47/2016--No Vigente por Sentencia"]
comprobar("de un no vigente se manda solo la ruta, sin la nota en HTML",
          s._url_detalle(nv_html) == "tesisjur.aspx?idtesis=47/2016&tpoBusqueda=S",
          s._url_detalle(nv_html))
comprobar("y sin rastro de HTML, que es lo que rechaza el WAF",
          "<" not in s._url_detalle(nv_html) and "#" not in s._url_detalle(nv_html))
comprobar("de un vigente se manda tal cual",
          s._url_detalle(j) == j["url_relacionada"], s._url_detalle(j))
comprobar("sin url_relacionada se arma una con la clave",
          s._url_detalle({"url_relacionada": None, "clave": "1/2020"})
          == "tesisjur.aspx?idtesis=1/2020&tpoBusqueda=S")

registrado = []


class FuenteEspía(FuenteFalsa):
    async def traer(self, ruta, params):
        registrado.append(params)
        return None


asyncio.run(s.enriquecer(FuenteEspía({}), s.normalizar(
    next(c for c in tipo1 if c["claveNueva"] == "47/2016--No Vigente por Sentencia")), None))
comprobar("la Clave sí va cruda, con su sufijo",
          registrado and registrado[0]["Clave"].endswith("No Vigente por Sentencia"),
          str(registrado[:1]))
comprobar("y la url va cortada", registrado and "<" not in registrado[0]["url"])


print("\n== Orden y escritura ==")
ordenados = sorted(registros, key=s._orden)
comprobar("las jurisprudencias van antes que las tesis",
          [r["tipo"] for r in ordenados] == sorted((r["tipo"] for r in ordenados)))
js = [r for r in ordenados if r["tipo"] == "J"]
comprobar("dentro de cada tipo, el año más reciente primero",
          [r["anio"] for r in js] == sorted((r["anio"] for r in js), reverse=True))

with tempfile.TemporaryDirectory() as tmp:
    a, b = Path(tmp) / "a.jsonl", Path(tmp) / "b.jsonl"
    s.escribir(ordenados, a)
    s.escribir(sorted(list(reversed(registros)), key=s._orden), b)
    comprobar("dos corridas sobre los mismos datos dan bytes idénticos",
              a.read_bytes() == b.read_bytes())
    comprobar("una línea por criterio",
              len(a.read_text(encoding="utf-8").strip().split("\n")) == len(ordenados))
    comprobar("cada línea es JSON válido y con claves ordenadas",
              all(json.loads(l) and l.index('"anio"') < l.index('"clave"')
                  for l in a.read_text(encoding="utf-8").strip().split("\n")))
    comprobar("los acentos se escriben tal cual, no como \\u",
              "\\u00e9" not in a.read_text(encoding="utf-8"))
    comprobar("leer lo escrito devuelve lo mismo",
              [x["clave"] for x in s.leer(a)] == [x["clave"] for x in ordenados])
    comprobar("leer un archivo inexistente devuelve lista vacía",
              s.leer(Path(tmp) / "no_existe.jsonl") == [])


print("\n== Guardia de merma y resumen ==")


class Args:
    salida = ""
    meta = ""
    resumen = ""
    cache = ""
    sin_detalle = True
    limite = 0
    concurrencia = 1
    intentos = 1
    intervalo = 0.0
    solo_cache = False


async def con_volcado_recortado(tmp):
    """Simula la corrida semanal con un volcado que perdió la mitad del corpus."""
    args = Args()
    args.salida = str(Path(tmp) / "tepjf.jsonl")
    args.meta = str(Path(tmp) / "tepjf.meta.json")
    s.escribir(ordenados * 4, Path(args.salida))     # "snapshot anterior" grande
    original = s.volcado

    async def volcado_falso(fuente, tipo):
        return tipo1 if tipo == "1" else tipo2

    s.volcado = volcado_falso
    try:
        return await s.sincronizar(args)
    finally:
        s.volcado = original


with tempfile.TemporaryDirectory() as tmp:
    codigo = asyncio.run(con_volcado_recortado(tmp))
    comprobar("el guardia del 95 % aborta con código 2", codigo == 2, f"código {codigo}")
    comprobar("y no toca el snapshot anterior",
              len(s.leer(Path(tmp) / "tepjf.jsonl")) == len(ordenados) * 4)

nuevos = [dict(r) for r in ordenados]
nuevos[0] = dict(nuevos[0], vigente=False, motivo_no_vigente="Sentencia",
                 nota_vigencia="Se declaró no vigente por la sentencia SUP-REC-1/2026.")
texto = s.resumen(ordenados, nuevos[1:] + [nuevos[0]])
comprobar("el resumen destaca los cambios de vigencia", "Cambios de vigencia" in texto)
comprobar("el resumen incluye la nota del cambio", "SUP-REC-1/2026" in texto)
comprobar("el resumen cuenta los criterios", f"**{len(nuevos)}** criterios" in texto)
sin_nada = s.resumen(ordenados, ordenados)
comprobar("sin cambios, el resumen no inventa secciones",
          "Cambios de vigencia" not in sin_nada and "Criterios nuevos" not in sin_nada)
comprobar("el resumen del primer snapshot no truena",
          "criterios" in s.resumen([], ordenados))


print("\n== Los temas no se borran si el carrusel falla ==")


async def temas_desde(mapa, anteriores, tmp):
    """Corre la sincronización completa con volcado y temas simulados."""
    args = Args()
    args.salida = str(Path(tmp) / "tepjf.jsonl")
    args.meta = str(Path(tmp) / "tepjf.meta.json")
    if anteriores:
        s.escribir(anteriores, Path(args.salida))
    vol, tem = s.volcado, s.temas

    async def volcado_falso(fuente, tipo):
        return tipo1 if tipo == "1" else tipo2

    async def temas_falsos(fuente):
        return mapa

    s.volcado, s.temas = volcado_falso, temas_falsos
    try:
        await s.sincronizar(args)
        return {f"{r['tipo']}:{r['clave']}": r["temas"] for r in s.leer(Path(args.salida))}
    finally:
        s.volcado, s.temas = vol, tem


primera = registros[0]
llave = f"{primera['tipo']}:{primera['clave']}"
con_temas = [dict(r, temas=["Género"]) for r in registros]

with tempfile.TemporaryDirectory() as tmp:
    salida = asyncio.run(temas_desde({llave: ["Propaganda"]}, con_temas, tmp))
    comprobar("con carrusel bueno, los temas se rearman",
              salida[llave] == ["Propaganda"], str(salida[llave]))
    otra = next(k for k in salida if k != llave)
    comprobar("y el que salió del tema se queda sin tema", salida[otra] == [],
              str(salida[otra]))

with tempfile.TemporaryDirectory() as tmp:
    salida = asyncio.run(temas_desde({}, con_temas, tmp))
    comprobar("si el carrusel falla, se conservan los temas anteriores",
              salida[llave] == ["Género"], str(salida[llave]))

with tempfile.TemporaryDirectory() as tmp:
    salida = asyncio.run(temas_desde({}, [], tmp))
    comprobar("y sin snapshot anterior, simplemente quedan vacíos",
              salida[llave] == [])


print("\n== Detección del bot manager ==")


class RespuestaFalsa:
    def __init__(self, status, texto):
        self.status_code = status
        self.text = texto


comprobar("un 302 es bloqueo", s._bloqueado(RespuestaFalsa(302, "")))
comprobar("HTML es bloqueo", s._bloqueado(RespuestaFalsa(200, "<html>reto</html>")))
comprobar("el JSON con _event_transid es bloqueo",
          s._bloqueado(RespuestaFalsa(200, '{"_event_transid":"abc"}')))
comprobar("un JSON de verdad no es bloqueo",
          not s._bloqueado(RespuestaFalsa(200, '[{"claveNueva":"1/2020"}]')))
comprobar("un JSON de objeto legítimo tampoco",
          not s._bloqueado(RespuestaFalsa(200, '{"sentencia":{"clave":"1/2020"}}')))


print(f"\n{'TODO BIEN' if not fallos else f'{fallos} FALLA(S)'}")
sys.exit(1 if fallos else 0)
