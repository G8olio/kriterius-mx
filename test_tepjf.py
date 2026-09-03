#!/usr/bin/env python3
"""
Pruebas de la fuente TEPJF. Sin red, contra el snapshot real del repo.

Corren en cada push y son baratas: cargar el JSONL e indexarlo toma menos de un
segundo. Lo que vigilan es lo que se puede romper en silencio:

  - que el snapshot esté completo y sin claves duplicadas;
  - que la vigencia esté bien leída (un criterio derogado mostrado como vivo es el
    daño más caro que puede hacer esta fuente);
  - que el índice ignore acentos, porque "genero" y "género" tienen que dar lo mismo;
  - que la prelación no ponga un criterio muerto antes que uno vivo;
  - que una consulta con caracteres raros no rompa FTS5 ni devuelva el corpus entero;
  - que con el archivo ausente el módulo se apague en vez de tronar.

Uso: python3 test_tepjf.py
"""

import sys

import tepjf

fallos = 0


def comprobar(nombre, condicion, detalle=""):
    global fallos
    if condicion:
        print(f"  [OK]    {nombre}")
    else:
        print(f"  [FALLA] {nombre}" + (f" — {detalle}" if detalle else ""))
        fallos += 1


print("\n== Carga del snapshot ==")
n = tepjf.cargar()
comprobar("carga sin lanzar", n > 0, f"cargó {n}")
comprobar("corpus completo (≥ 2 000 criterios)", n >= 2000, f"{n}")
comprobar("meta con fecha de snapshot", bool(tepjf.meta().get("fecha_snapshot")),
          str(tepjf.meta()))

registros = list(tepjf._por_clave.values())
claves = [f"{r['tipo']}:{r['clave']}" for r in registros]
comprobar("sin claves duplicadas por tipo", len(claves) == len(set(claves)))
comprobar("todos con rubro", all(r["rubro"] for r in registros))
comprobar("todos con texto", all(r["texto"] for r in registros))
comprobar("todos con año de cuatro dígitos",
          all(1990 <= int(r["anio"]) <= 2100 for r in registros),
          str(sorted({r["anio"] for r in registros})[:3]))
comprobar("hay jurisprudencias y tesis",
          any(r["tipo"] == "J" for r in registros) and any(r["tipo"] == "T" for r in registros))


print("\n== Vigencia ==")
sucias = [r["clave"] for r in registros if "--" in r["clave"]]
comprobar("ninguna clave limpia arrastra el sufijo", not sucias, str(sucias[:3]))
sin_motivo = [r["clave"] for r in registros if not r["vigente"] and not r["motivo_no_vigente"]]
comprobar("todo no vigente trae motivo", not sin_motivo, str(sin_motivo[:3]))
con_motivo_vivo = [r["clave"] for r in registros if r["vigente"] and r["motivo_no_vigente"]]
comprobar("ningún vigente trae motivo de baja", not con_motivo_vivo, str(con_motivo_vivo[:3]))
no_vigentes = [r for r in registros if not r["vigente"]]
# Rango amplio a propósito: lo que detecta esta prueba es una regex que dejó de
# reconocer el sufijo (caerían a ~0) o que empezó a reconocerlo de más.
comprobar("conteo de no vigentes en un rango sano (300–900)",
          300 <= len(no_vigentes) <= 900, f"{len(no_vigentes)}")
motivos = {r["motivo_no_vigente"] for r in no_vigentes}
comprobar("los tres motivos conocidos aparecen",
          {"Sentencia", "Acuerdo General", "Reiteración"} <= motivos, str(motivos))
comprobar("la mayoría de los no vigentes trae nota explicativa",
          sum(1 for r in no_vigentes if r["nota_vigencia"]) > len(no_vigentes) * 0.8,
          f"{sum(1 for r in no_vigentes if r['nota_vigencia'])} de {len(no_vigentes)}")
comprobar("las claves crudas de los no vigentes conservan el sufijo",
          all("--" in r["clave_cruda"] for r in no_vigentes))


print("\n== Búsqueda ==")
paridad = tepjf.buscar("paridad")
comprobar("'paridad' devuelve resultados", len(paridad) > 5, f"{len(paridad)}")
comprobar("el primero es vigente", paridad and paridad[0]["vigente"],
          paridad[0]["clave"] if paridad else "sin resultados")
comprobar("'zzzqqq' no devuelve nada", tepjf.buscar("zzzqqq") == [])
comprobar("acentos: 'genero' == 'género'",
          [r["clave"] for r in tepjf.buscar("genero")] ==
          [r["clave"] for r in tepjf.buscar("género")])
comprobar("mayúsculas: 'PARIDAD' == 'paridad'",
          [r["clave"] for r in tepjf.buscar("PARIDAD")] == [r["clave"] for r in paridad])

solo_j = tepjf.buscar("paridad", solo_jurisprudencia=True)
comprobar("solo_jurisprudencia filtra las tesis", all(r["tipo"] == "J" for r in solo_j))
solo_vivos = tepjf.buscar("paridad", incluir_no_vigentes=False)
comprobar("incluir_no_vigentes=False deja solo vigentes",
          all(r["vigente"] for r in solo_vivos))
recientes = tepjf.buscar("elección", anio_desde=2020)
comprobar("anio_desde filtra", all(r["anio"] >= 2020 for r in recientes))
viejos = tepjf.buscar("elección", anio_hasta=2005)
comprobar("anio_hasta filtra", all(r["anio"] <= 2005 for r in viejos))

frase = tepjf.buscar("violencia", frase="violencia política")
comprobar("la frase exacta filtra de más a menos",
          0 < len(frase) <= len(tepjf.buscar("violencia")), f"{len(frase)}")
excluido = tepjf.buscar("candidatura", excluir="indígena")
comprobar("excluir quita los que traen la palabra",
          all("indígena" not in (r["texto"] + r["rubro"]).lower() for r in excluido))


print("\n== Prelación ==")
res = tepjf.buscar("representación proporcional")
primeros_no_vigentes = [i for i, r in enumerate(res) if not r["vigente"]]
ultimos_vigentes = [i for i, r in enumerate(res) if r["vigente"]]
comprobar("ninguna no vigente aparece antes que una vigente",
          not (primeros_no_vigentes and ultimos_vigentes)
          or min(primeros_no_vigentes) > max(ultimos_vigentes))
vivos = [r for r in res if r["vigente"]]
tipos = [r["tipo"] for r in vivos]
comprobar("entre los vigentes, jurisprudencia antes que tesis",
          "J" not in tipos[tipos.index("T"):] if "T" in tipos else True, str(tipos[:12]))


print("\n== Inyección y consultas raras ==")
total = len(tepjf._por_clave)
for consulta in ['paridad" OR rubro:*', "paridad*", 'AND OR NOT', '"', "-paridad",
                 "paridad AND", "(((", "rubro:paridad", "género^2"]:
    try:
        r = tepjf.buscar(consulta)
        ok = len(r) < total
    except Exception as e:
        ok, r = False, str(e)
    comprobar(f"no truena ni devuelve el corpus con {consulta!r}", ok,
              f"{len(r) if isinstance(r, list) else r}")
comprobar("búsqueda vacía devuelve vacío", tepjf.buscar("") == [])
comprobar("solo exclusiones devuelve vacío", tepjf.buscar("", excluir="paridad") == [])


print("\n== Detalle y formato ==")
r = tepjf.obtener("11/2018", "J")
comprobar("11/2018 existe", r is not None)
if r:
    texto = tepjf.formatear_criterio(r)
    for esperado in ["PARIDAD", "Jurisprudencia 11/2018", "Época", "TEXTO", "CITA",
                     "te.gob.mx/ius2021"]:
        comprobar(f"la ficha incluye {esperado!r}", esperado in texto)
comprobar("obtener encuentra aunque el tipo venga equivocado",
          tepjf.obtener("11/2018", "T") is not None)
comprobar("clave inexistente devuelve None", tepjf.obtener("99999/1900", "J") is None)

romana = next((x for x in registros if x["tipo"] == "T"), None)
comprobar("las tesis se numeran con romanos",
          romana is not None and any(c in romana["clave"] for c in "IVXLC"),
          romana["clave"] if romana else "")

muerto = next((x for x in registros if not x["vigente"] and x["nota_vigencia"]), None)
if muerto:
    ficha = tepjf.formatear_criterio(muerto)
    comprobar("la ficha de un no vigente lo dice", "NO VIGENTE" in ficha)
    comprobar("la ficha de un no vigente trae la nota", muerto["nota_vigencia"][:40] in ficha)
    comprobar("la ficha de un no vigente muestra la clave cruda",
              muerto["clave_cruda"] in ficha)

pagina = tepjf.formatear_busqueda(paridad, "TEPJF — paridad", pagina=1)
comprobar("la página dice cuántos resultados hay", "resultado(s)" in pagina)
comprobar("la página avisa del bloque de no vigentes cuando los hay",
          ("NO VIGENTES" in pagina) == any(not x["vigente"] for x in paridad[:10]))
comprobar("una página fuera de rango no truena",
          "página" in tepjf.formatear_busqueda(paridad, "x", pagina=999))
comprobar("sin resultados se sugiere otra ruta",
          "temas_tepjf" in tepjf.formatear_busqueda([], "x"))


print("\n== Temas ==")
temas = tepjf.lista_temas()
comprobar("hay 15 temas", len(temas) == 15, f"{len(temas)}")
comprobar("los temas traen conteo", all(n > 0 for _, n in temas))
genero = tepjf.por_tema("Género")
comprobar("el tema 'Género' trae criterios", len(genero) > 10, f"{len(genero)}")
comprobar("el tema se busca sin acentos ni mayúsculas",
          [r["clave"] for r in tepjf.por_tema("genero")] == [r["clave"] for r in genero])
comprobar("un tema inexistente devuelve vacío", tepjf.por_tema("zzzqqq") == [])
comprobar("dentro del tema, los vigentes van primero",
          [r["vigente"] for r in genero] == sorted((r["vigente"] for r in genero),
                                                   reverse=True))


print("\n== Degradación sin snapshot ==")
comprobar("con archivo inexistente cargar() devuelve 0 y no lanza",
          tepjf.cargar("no_existe_este_archivo.jsonl", "tampoco.json") == 0)
comprobar("la fuente se declara no disponible", not tepjf.disponible())
comprobar("buscar devuelve vacío en vez de tronar", tepjf.buscar("paridad") == [])
comprobar("obtener devuelve None", tepjf.obtener("11/2018") is None)
comprobar("el mensaje de no disponible menciona a las demás fuentes",
          "SJF" in tepjf.NO_DISPONIBLE)
# Y se vuelve a levantar: el estado apagado no es permanente.
comprobar("recargar la deja lista otra vez", tepjf.cargar() > 0 and tepjf.disponible())


print(f"\n{'TODO BIEN' if not fallos else f'{fallos} FALLA(S)'}")
sys.exit(1 if fallos else 0)
