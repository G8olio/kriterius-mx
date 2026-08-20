#!/usr/bin/env python3
"""
Pruebas del módulo de ejecutorias del SJF. Sin red.

Los fixtures reproducen respuestas reales del API (registros 34143, 34151 y 201074),
incluidas sus rarezas: la localización separada por puntos en vez de punto y coma, la
época escrita con ordinal ("Duodécima") en vez de número, y el hecho de que búsqueda y
detalle nombran los campos distinto.

Uso: python3 test_ejecutorias.py
"""

import sys

import kriterius_mx as k

fallos = 0


def comprobar(nombre, condicion, detalle=""):
    global fallos
    if condicion:
        print(f"  [OK]    {nombre}")
    else:
        print(f"  [FALLA] {nombre}" + (f" — {detalle}" if detalle else ""))
        fallos += 1


# Documentos tal como los devuelve la BÚSQUEDA (no el detalle)
PLENO = {
    "ius": 34143,
    "rubro": "...&#x0D;\n&#x0D;\nPor otra parte, si bien este Tribunal Pleno ha establecido "
             "que los municipios carecen de interés legítimo para alegar una violación al "
             "artículo 2° de la Constitución Federal.",
    "localizacion": "Duodécima Época. Pleno. Semanario Judicial de la Federación, "
                    "Libro 11, Julio de 2026.",
    "tipoAsunto": "ACCIÓN DE INCONSTITUCIONALIDAD",
    "tipoAsuntoE": "ACCIÓN DE INCONSTITUCIONALIDAD 129/2024.",
    "promovente": "Poder Ejecutivo Federal.",
    "sala": "Suprema Corte de Justicia de la Nación",
    "instancia": None,
}

REGIONAL = {
    "ius": 34160,
    "rubro": "el interés legítimo del quejoso quedó acreditado",
    "localizacion": "Duodécima Época. Plenos Regionales. Semanario Judicial de la "
                    "Federación, Libro 11, Julio de 2026.",
    "tipoAsunto": "CONTRADICCIÓN DE CRITERIOS (ANTES CONTRADICCIÓN DE TESIS)",
    "tipoAsuntoE": "CONTRADICCIÓN DE CRITERIOS 15/2026.",
    "promovente": None,
}

PRIMERA_VIEJA = {
    "ius": 201074,
    "rubro": "interés legítimo de la Fiscalía",
    "localizacion": "Décima Época. Primera Sala. Sistema de Precedentes en Controversias "
                    "Constitucionales y en Acciones de Inconstitucionalidad",
    "tipoAsunto": "CONTROVERSIA CONSTITUCIONAL",
    "tipoAsuntoE": "CONTROVERSIA CONSTITUCIONAL 267/2024.",
    "promovente": "FISCALÍA GENERAL DE LA REPÚBLICA",
}

TCC = {
    "ius": 33900,
    "rubro": "interés legítimo en el amparo indirecto",
    "localizacion": "Duodécima Época. Tribunales Colegiados de Circuito. Gaceta del "
                    "Semanario Judicial de la Federación, Libro 5, Enero de 2026, "
                    "Tomo II, Volumen 1, Pág.679.",
    "tipoAsunto": "AMPARO EN REVISIÓN",
    "tipoAsuntoE": "AMPARO EN REVISIÓN 123/2025.",
    "promovente": "Particular.",
}


print("\nLOCALIZACIÓN (separada por puntos, no por punto y coma como en las tesis)")
epoca, organo, fuente = k._ejec_loc(PLENO["localizacion"])
comprobar("extrae la época", epoca == "Duodécima Época", epoca)
comprobar("extrae el órgano", organo == "Pleno", organo)
comprobar("extrae la fuente", "Libro 11, Julio de 2026" in fuente, fuente)

e2, o2, f2 = k._ejec_loc(PRIMERA_VIEJA["localizacion"])
comprobar("órgano de una sala", o2 == "Primera Sala", o2)
comprobar("época de una colección histórica", e2 == "Décima Época", e2)
comprobar("fuente del sistema de precedentes", "Sistema de Precedentes" in f2, f2)

e3, o3, f3 = k._ejec_loc(TCC["localizacion"])
comprobar("órgano de un colegiado", o3 == "Tribunales Colegiados de Circuito", o3)

e4, o4, f4 = k._ejec_loc("")
comprobar("no truena con localización vacía", o4 == "Otros órganos" and e4 == "", f"{e4}|{o4}")


print("\nÉPOCA Y ÓRGANO (las ejecutorias escriben el ordinal, las tesis usan número)")
comprobar("lee 'Duodécima Época' como 12",
          k._numero_epoca(PLENO["localizacion"]) == 12,
          str(k._numero_epoca(PLENO["localizacion"])))
comprobar("lee 'Décima Época' como 10",
          k._numero_epoca(PRIMERA_VIEJA["localizacion"]) == 10,
          str(k._numero_epoca(PRIMERA_VIEJA["localizacion"])))
comprobar("no confunde undécima con décima",
          k._numero_epoca("Undécima Época. Pleno.") == 11,
          str(k._numero_epoca("Undécima Época. Pleno.")))
comprobar("Pleno manda sobre Plenos Regionales",
          k._rango_organo(PLENO["localizacion"]) < k._rango_organo(REGIONAL["localizacion"]))
comprobar("Plenos Regionales antes que colegiados",
          k._rango_organo(REGIONAL["localizacion"]) < k._rango_organo(TCC["localizacion"]))


print("\nORDEN POR OBLIGATORIEDAD")
ordenadas = k._ejec_ordenar([TCC, REGIONAL, PRIMERA_VIEJA, PLENO])
comprobar("el Pleno queda primero", ordenadas[0]["ius"] == 34143, str(ordenadas[0]["ius"]))
comprobar("el colegiado queda al final", ordenadas[-1]["ius"] == 33900, str(ordenadas[-1]["ius"]))
comprobar("la Primera Sala va antes que el Pleno Regional",
          [d["ius"] for d in ordenadas].index(201074) < [d["ius"] for d in ordenadas].index(34160))

mismo_organo = k._ejec_ordenar([
    {"ius": 1, "localizacion": "Décima Época. Pleno. X."},
    {"ius": 2, "localizacion": "Duodécima Época. Pleno. X."},
])
comprobar("a igual órgano gana la época más reciente",
          mismo_organo[0]["ius"] == 2, str(mismo_organo[0]["ius"]))


print("\nFORMATO DE CITA")
linea = k._ejec_linea(PLENO)
comprobar("usa el número del asunto, no el tipo genérico",
          "[ACCIÓN DE INCONSTITUCIONALIDAD 129/2024]" in linea[0], linea[0][:80])
comprobar("incluye el órgano", "[Pleno]" in linea[0], linea[0][:120])
comprobar("incluye el promovente",
          "[Promovente: Poder Ejecutivo Federal]" in linea[0], linea[0][:160])
comprobar("incluye el registro", "[34143]" in linea[0], linea[0][-40:])
comprobar("el link apunta a /detalle/ejecutoria/",
          linea[1] == "https://sjf2.scjn.gob.mx/detalle/ejecutoria/34143", linea[1])
comprobar("marca el fragmento como coincidencia, no como rubro",
          any(x.startswith("Coincidencia:") for x in linea), " | ".join(linea)[:120])
comprobar("decodifica las entidades del fragmento",
          not any("&#x0D;" in x for x in linea))

sin_promovente = k._ejec_linea(REGIONAL)
comprobar("avisa cuando no hay promovente",
          "[Promovente no indicado]" in sin_promovente[0], sin_promovente[0][:140])

vacio = k._ejec_linea({"ius": 9, "localizacion": "", "rubro": ""})
comprobar("no truena con un documento incompleto",
          "Asunto sin identificar" in vacio[0] and "/detalle/ejecutoria/9" in vacio[1])


print("\nBÚSQUEDA DENTRO DEL TEXTO (mapa de acentos)")
TEXTO = ("El Pleno resolvió que el interés legítimo exige una afectación real. "
         "Más adelante, la sentencia precisó que el INTERÉS LEGÍTIMO no se confunde "
         "con el interés simple. Por último, reiteró el criterio.")

plano, mapa = k._sin_acentos_con_mapa(TEXTO)
comprobar("el mapa tiene un índice por carácter del texto plano", len(plano) == len(mapa))
comprobar("los índices apuntan al carácter correcto",
          all(k._sin_acentos_con_mapa(TEXTO[mapa[j]])[0] == plano[j] for j in range(len(plano))))
comprobar("quita los acentos", "interes legitimo" in plano)

hits = k._ejec_coincidencias(TEXTO, "interes legitimo")
# Las dos apariciones están a 86 caracteres una de otra y el contexto es de 600, así
# que caen en el MISMO extracto: devolver dos recortes casi idénticos de un texto de
# 198 caracteres era el bug que se arregló en 2.9.2.
comprobar("encuentra sin importar acentos ni mayúsculas",
          len(hits) == 1 and "interés legítimo" in hits[0] and "INTERÉS LEGÍTIMO" in hits[0],
          str(len(hits)))
comprobar("el primer extracto trae la frase con sus acentos",
          "interés legítimo" in hits[0], hits[0][:60])
comprobar("el extracto fusionado trae también la versión en mayúsculas",
          "INTERÉS LEGÍTIMO" in hits[0], hits[0][:60])
comprobar("no encuentra lo que no está", k._ejec_coincidencias(TEXTO, "usucapión") == [])
comprobar("término vacío no devuelve nada", k._ejec_coincidencias(TEXTO, "") == [])

largo = ("relleno " * 500) + "punto buscado" + (" relleno" * 500)
uno = k._ejec_coincidencias(largo, "punto buscado")
comprobar("recorta el contexto alrededor de la coincidencia",
          len(uno) == 1 and len(uno[0]) <= 2 * k.EJEC_CONTEXTO + 40, str(len(uno[0])))
comprobar("y la coincidencia queda dentro del extracto", "punto buscado" in uno[0])

# El tope limita cuántas APARICIONES se consideran. Con la fusión, apariciones
# vecinas se juntan, así que para probar el tope hay que separarlas más que el
# contexto; si se pegan, el resultado correcto es un solo extracto.
separadas = ("aguja" + ("x" * (2 * k.EJEC_CONTEXTO + 100))) * 10
comprobar("tope de apariciones consideradas",
          len(k._ejec_coincidencias(separadas, "aguja", maximo=4)) == 4,
          str(len(k._ejec_coincidencias(separadas, "aguja", maximo=4))))
comprobar("pegadas entre sí, el tope no multiplica extractos",
          len(k._ejec_coincidencias("aguja " * 50, "aguja", maximo=4)) == 1)


print("\nENTREGA POR PARTES")
cuerpo = "\n".join(f"Párrafo {i} con su contenido correspondiente." for i in range(1, 2001))
trozos = k._ejec_partir(cuerpo)
comprobar("parte un texto largo en varias piezas", len(trozos) > 5, str(len(trozos)))
comprobar("ninguna pieza excede el tope",
          all(len(t) <= k.EJEC_TROZO for t in trozos), str(max(len(t) for t in trozos)))
comprobar("no se pierde contenido",
          "Párrafo 1 " in trozos[0] and "Párrafo 2000 " in trozos[-1])
comprobar("corta en fin de párrafo, no a media palabra",
          all(not t.endswith("Párra") for t in trozos))
comprobar("un texto corto queda en una sola parte", len(k._ejec_partir("corto")) == 1)
comprobar("texto vacío no truena", k._ejec_partir("") == [""])

junto = "".join(trozos)
comprobar("las partes reconstruyen el texto",
          junto.replace("\n", "") == cuerpo.replace("\n", ""))


print("\nCONFIGURACIÓN")
comprobar("el endpoint de ejecutorias es otro microservicio",
          "sjfejecutoriamicroservice" in k.BASE_EJEC and k.BASE_EJEC != k.BASE)
comprobar("el Referer apunta al buscador de ejecutorias",
          "busqueda-principal-ejecutorias" in k.HEADERS_EJEC["Referer"])
comprobar("las ejecutorias comparten el freno de concurrencia del SJF",
          "sjf" in k.CONCURRENCIA)
comprobar("el id de sala 6 es el Pleno", k.SALAS_EJEC["6"] == "Pleno")

print("\nver_ejecutoria (con el API simulado)")

# Detalle tal como responde el API: campos con OTROS nombres que en la búsqueda.
# tipoAsunto aquí sí trae el número, promovente no viene y sala es un id numérico.
DETALLE = {
    "ius": 201074,
    "epoca": "Duodécima Época",
    "volumen": "Libro 7, Marzo de 2026",
    "localizacion": None,
    "tipoAsunto": "CONTROVERSIA CONSTITUCIONAL 267/2024.",
    "tipoAsuntoE": None,
    "promovente": None,
    "sala": "1",
    "instancia": "Primera Sala",
    "fuente": "Sistema de Precedentes en Controversias Constitucionales y en Acciones "
              "de Inconstitucionalidad",
    "textoPublicacion": "",
    "rubro": "<p>I. CONTROVERSIA CONSTITUCIONAL. LA PERSONA TITULAR DE LA UNIDAD "
             "ESPECIALIZADA EN ASUNTOS JURÍDICOS DE LA FISCALÍA GENERAL DE LA REPÚBLICA "
             "TIENE LEGITIMACIÓN PARA PROMOVERLA.</p><br><p>II. INTERÉS LEGÍTIMO.</p>",
    "tesis": [],
    "texto": "<p> CONTROVERSIA CONSTITUCIONAL 267/2024. FISCALÍA GENERAL DE LA REPÚBLICA. "
             "30 DE ABRIL DE 2025.</p><br>"
             + "".join(f"<p>Considerando {i}. La Primera Sala estima que el interés "
                       f"legítimo de la Fiscalía quedó acreditado en autos.</p><br>"
                       for i in range(1, 400)),
}

DETALLE_SEMANAL = dict(DETALLE, ius=34143, sala="6", instancia="Pleno",
                       tipoAsunto="ACCIÓN DE INCONSTITUCIONALIDAD 129/2024.",
                       tesis=[2032439, 2032440], texto="<p>Texto breve.</p>")

_llamadas: list = []


def _simular(respuestas):
    """Reemplaza la salida a la red por respuestas fijas, según isSemanal."""
    async def falso(path, method="GET", json_body=None):
        _llamadas.append(path)
        for marca, valor in respuestas.items():
            if marca in path:
                if isinstance(valor, Exception):
                    raise valor
                return valor
        raise RuntimeError("404 no encontrado")
    k._ejec_fetch = falso


import asyncio

_original = k._ejec_fetch

# Caso histórico: isSemanal=true revienta con 500 y solo funciona con false
_llamadas.clear()
_simular({"isSemanal=true": RuntimeError("respondió 500"), "isSemanal=false": DETALLE})
salida = asyncio.run(k.ver_ejecutoria(201074))
comprobar("prueba isSemanal=true antes que false", "isSemanal=true" in _llamadas[0], str(_llamadas[:2]))
comprobar("un 500 no lo da por perdido, prueba el otro valor", len(_llamadas) == 2, str(len(_llamadas)))
comprobar("identifica el asunto con su número",
          "Asunto: CONTROVERSIA CONSTITUCIONAL 267/2024" in salida)
comprobar("traduce la instancia", "Órgano: Primera Sala" in salida)
comprobar("muestra la fuente y el volumen",
          "Sistema de Precedentes" in salida and "Libro 7, Marzo de 2026" in salida)
comprobar("separa los criterios del cuerpo", "CRITERIOS QUE DEJÓ ESTA SENTENCIA:" in salida)
comprobar("limpia el HTML de los criterios", "<p>" not in salida and "<br>" not in salida)
comprobar("entrega el texto por partes", "TEXTO — parte 1 de" in salida, salida[:200])
comprobar("dice cómo pedir la siguiente parte",
          "ver_ejecutoria(201074, parte=2)" in salida)
comprobar("cierra con el link oficial",
          salida.rstrip().endswith("https://sjf2.scjn.gob.mx/detalle/ejecutoria/201074"))
comprobar("no vuelca la sentencia entera", len(salida) < 25000, f"{len(salida)} caracteres")

# Caso del Semanario en curso: funciona a la primera y trae tesis derivadas
_llamadas.clear()
_simular({"isSemanal=true": DETALLE_SEMANAL})
salida2 = asyncio.run(k.ver_ejecutoria(34143))
comprobar("si el primer intento sirve, no hace el segundo", len(_llamadas) == 1, str(_llamadas))
comprobar("traduce el id de sala cuando no hay instancia", "Órgano: Pleno" in salida2)
comprobar("enlaza las tesis derivadas",
          "TESIS DERIVADAS" in salida2 and "2032439, 2032440" in salida2)

# Búsqueda de un término dentro de la sentencia
_simular({"isSemanal=false": DETALLE, "isSemanal=true": RuntimeError("respondió 500")})
salida3 = asyncio.run(k.ver_ejecutoria(201074, buscar_en_texto="interes legitimo"))
comprobar("devuelve coincidencias en vez de una parte",
          "COINCIDENCIAS DE 'interes legitimo'" in salida3 and "TEXTO — parte" not in salida3)
comprobar("las coincidencias conservan los acentos del original",
          "interés legítimo" in salida3)

salida4 = asyncio.run(k.ver_ejecutoria(201074, buscar_en_texto="usucapión"))
comprobar("avisa cuando el término no aparece",
          "no aparece en el cuerpo" in salida4 and "parte=1" in salida4)

# Registro que no existe en ninguna de las dos colecciones
_simular({})
salida5 = asyncio.run(k.ver_ejecutoria(999999999))
comprobar("explica el fallo con los dos intentos",
          "No se encontró la ejecutoria" in salida5 and "isSemanal=true" in salida5
          and "isSemanal=false" in salida5, salida5[:200])

k._ejec_fetch = _original

print(f"\n{'TODO OK' if fallos == 0 else str(fallos) + ' FALLA(S)'}\n")
sys.exit(0 if fallos == 0 else 1)
