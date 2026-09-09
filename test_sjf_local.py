#!/usr/bin/env python3
"""
Pruebas del acervo local de la Gaceta del SJF. Sin red, contra el JSONL real.

Lo que vigilan es lo que se puede romper en silencio y saldría caro en un escrito:

  - que el acervo cargue completo y el índice en disco sea intercambiable con el
    de memoria (mismos resultados, misma prelación);
  - que la muestra dorada salga íntegra —es la tesis verificada a ojo contra el
    PDF— y que las tesis que solo aparecen CITADAS en una ejecutoria no entren;
  - que la cita traiga los datos de identificación, la página, el PDF oficial y
    diga que el registro digital está pendiente, en vez de inventarlo;
  - que las dos series de la Décima Época se citen con la publicación correcta:
    hasta noviembre de 2013 "Semanario Judicial de la Federación y su Gaceta",
    desde diciembre de 2013 "Gaceta del Semanario Judicial de la Federación";
  - que la prelación ponga al Pleno antes que a un Colegiado y a la jurisprudencia
    antes que a la tesis aislada;
  - que TODA salida lleve el aviso de acervo local: si un día se pierde ese aviso,
    el usuario cita derecho de respaldo creyendo que consultó el SJF;
  - que una consulta con caracteres raros no rompa FTS5;
  - que con el archivo ausente el módulo se apague en vez de tronar.

Uso: python3 test_sjf_local.py
"""

import os
import sys
import tempfile

import sjf_local as S

fallos = 0


def comprobar(nombre, condicion, detalle=""):
    global fallos
    if condicion:
        print(f"  [OK]    {nombre}")
    else:
        print(f"  [FALLA] {nombre}" + (f" — {detalle}" if detalle else ""))
        fallos += 1


CLAVE_DORADA = "(IV Región)1o. J/1 A (12a.)"

print("\n== Carga ==")
# En memoria a propósito: es el modo que no depende de que haya caché escribible,
# y así la prueba no deja basura en la máquina de nadie.
n = S.cargar(ruta_indice=None)
comprobar("carga sin lanzar", n > 0, f"cargó {n}")
comprobar("acervo completo (≥ 30 000 tesis)", n >= 30000, str(n))
comprobar("meta con fuente y cobertura",
          bool(S.meta().get("fuente")) and bool(S.meta().get("hasta")), str(S.meta())[:120])
comprobar("disponible() dice que sí", S.disponible())


print("\n== Muestra dorada ==")
dorada = S.obtener(CLAVE_DORADA)
comprobar("la clave dorada existe y es única", len(dorada) == 1, f"{len(dorada)}")
if dorada:
    d = dorada[0]
    comprobar("es jurisprudencia", d["tipo"] == "J")
    comprobar("nivel TCC", d["nivel"] == "TCC")
    comprobar("Duodécima Época", d["epoca"] == "12a")
    comprobar("órgano completo, no inferido",
              d["organo"].startswith("PRIMER TRIBUNAL COLEGIADO DE CIRCUITO DEL CENTRO")
              and not d["organo_inferido"])
    comprobar("rubro completo y con punto final",
              d["rubro"].startswith("DERECHOS POR REVALIDACIÓN ANUAL")
              and d["rubro"].endswith("."), d["rubro"][:60])
    comprobar("cinco precedentes", len(d["precedentes"]) == 5, str(len(d["precedentes"])))
    comprobar("publicada el 14 de agosto de 2026", d["publicacion_sjf"] == "2026-08-14",
              str(d["publicacion_sjf"]))
    comprobar("obligatoria desde el 17 de agosto de 2026",
              d["obligatoria_desde"] == "2026-08-17", str(d["obligatoria_desde"]))
    comprobar("Libro 12, página 682",
              d["gaceta"]["libro"] == 12 and d["gaceta"]["pagina"] == 682, str(d["gaceta"]))
    comprobar("la página coincide con la del índice del libro",
              d["gaceta"]["pagina"] == d["gaceta"]["pagina_indice"], str(d["gaceta"]))
    comprobar("sin marcas de revisión", not d.get("revisar"), str(d.get("revisar")))
    comprobar("el PDF de la fuente es el del libro, no la portada de la Gaceta",
              d["fuente_pdf"].endswith(".pdf") and "12_ago" in d["fuente_pdf"],
              d["fuente_pdf"])


print("\n== Falsos positivos conocidos ==")
# Estas dos aparecen CITADAS dentro de ejecutorias del Libro 12 de la Duodécima.
# Si el parser las tomara por tesis de ese libro, el acervo estaría inventando
# criterios; por eso están en la prueba y no solo en el informe de cobertura.
for clave in ("2a./J. 65/2000", "2a./J. 221/2007"):
    coladas = [r for r in S.obtener(clave)
               if r["epoca"] == "12a" and (r["gaceta"] or {}).get("libro") == 12]
    comprobar(f"{clave} no entró como tesis del Libro 12 de la 12a", not coladas)


print("\n== Cita ==")
if dorada:
    c = S.cita(dorada[0])
    comprobar("la cita trae el rubro", "DERECHOS POR REVALIDACIÓN" in c)
    comprobar("la cita trae el órgano", "PRIMER TRIBUNAL COLEGIADO" in c)
    comprobar("la cita trae tipo y clave", "Jurisprudencia (IV Región)1o. J/1 A (12a.)" in c)
    comprobar("la cita trae la época", "Duodécima Época" in c)
    comprobar("la cita trae libro, mes, año y página",
              "Libro 12" in c and "agosto de 2026" in c and "página 682" in c)
    comprobar("la cita trae el PDF oficial con la página", ".pdf" in c and "(p. 682)" in c)
    comprobar("la fecha va en español, no en ISO",
              "14 de agosto de 2026" in c and "2026-08-14" not in c)
    comprobar("declara el registro digital como pendiente, no lo inventa",
              "Registro digital: pendiente" in c and "sjf2.scjn.gob.mx" in c)
    # Y en cuanto resolver_registros.py lo averigüe, la cita lleva el link canónico.
    S._registros[dorada[0]["id"]] = 2032415
    c2 = S.cita(dorada[0])
    comprobar("con el registro resuelto, la cita trae el link canónico",
              "Registro digital: 2032415" in c2
              and "sjf2.scjn.gob.mx/detalle/tesis/2032415" in c2, c2.split(chr(10))[-1])
    S._registros.clear()
    comprobar("no duplica puntuación al unir rubro y órgano", "., " not in c.split("\n")[0])

print("\n== Las dos series de la Décima Época ==")
antigua = [r for r in S.buscar("amparo") if (r["gaceta"] or {}).get("serie") == "SJFyG"]
moderna = [r for r in S.buscar("amparo") if (r["gaceta"] or {}).get("serie") == "GSJF"]
comprobar("hay tesis de las dos series", bool(antigua) and bool(moderna))
if antigua:
    comprobar("la serie de 2011-2013 se cita como 'Semanario Judicial de la "
              "Federación y su Gaceta'",
              S._publicacion(antigua[0]) == "Semanario Judicial de la Federación y su Gaceta")
    comprobar("y con libro en número romano",
              str(antigua[0]["gaceta"]["libro_cita"]).strip("IVXLC") == "",
              str(antigua[0]["gaceta"]["libro_cita"]))
if moderna:
    comprobar("la serie desde diciembre de 2013 se cita como 'Gaceta del Semanario "
              "Judicial de la Federación'",
              S._publicacion(moderna[0]) == "Gaceta del Semanario Judicial de la Federación")
    comprobar("y con libro en número arábigo", str(moderna[0]["gaceta"]["libro_cita"]).isdigit(),
              str(moderna[0]["gaceta"]["libro_cita"]))


print("\n== Búsqueda y prelación ==")
res = S.buscar("interés legítimo")
comprobar("la búsqueda devuelve resultados", len(res) > 5, str(len(res)))
rangos = [S.rango_organo(r["clave"], r["nivel"]) for r in res]
comprobar("los órganos vienen de mayor a menor jerarquía",
          rangos == sorted(rangos), str(rangos[:12]))
primer_rango = rangos[0] if rangos else 0
mismo = [r for r, g in zip(res, rangos) if g == primer_rango]
comprobar("dentro del mismo órgano, la jurisprudencia va antes que la aislada",
          [r["tipo"] for r in mismo] == sorted([r["tipo"] for r in mismo]),
          str([r["tipo"] for r in mismo][:10]))
comprobar("ignora acentos: 'interes legitimo' da lo mismo que 'interés legítimo'",
          len(S.buscar("interes legitimo")) == len(res))
comprobar("respeta la frase exacta entre comillas",
          0 < len(S.buscar('"interés legítimo"')) <= len(res))
comprobar("filtra por época", all(r["epoca"] == "12a" for r in S.buscar("amparo", epocas=["12a"])))
comprobar("filtra por tipo", all(r["tipo"] == "J" for r in S.buscar("amparo", tipo="jurisprudencia")))
comprobar("una consulta con caracteres raros no rompe FTS5",
          isinstance(S.buscar('amparo AND* "(" NEAR ^ ñ'), list))
comprobar("una consulta vacía no devuelve el acervo entero", S.buscar("   ") == [])


print("\n== El aviso de acervo local ==")
salida = S.formatear_busqueda(res, "interés legítimo", "HTTP 302")
comprobar("la búsqueda empieza con el aviso", salida.startswith("⚠ ACERVO LOCAL"))
comprobar("el aviso dice el motivo", "HTTP 302" in salida)
comprobar("el aviso advierte de la vigencia", "vigencia" in salida)
comprobar("el aviso dice que no hay registro digital", "registro digital" in salida)
vacia = S.formatear_busqueda([], "xyzzy", "HTTP 403")
comprobar("sin resultados también avisa", vacia.startswith("⚠ ACERVO LOCAL"))
if dorada:
    ficha = S.formatear_tesis(dorada[0], "HTTP 403")
    comprobar("la ficha de una tesis también empieza con el aviso",
              ficha.startswith("⚠ ACERVO LOCAL"))
    comprobar("la ficha trae texto, precedentes y cita",
              "TEXTO" in ficha and "PRECEDENTES" in ficha and "CITA" in ficha)


print("\n== Índice en disco ==")
with tempfile.TemporaryDirectory() as tmp:
    ruta = os.path.join(tmp, "idx.sqlite")
    n2 = S.cargar(ruta_indice=ruta)
    comprobar("construye el índice en disco y carga lo mismo", n2 == n, f"{n2} vs {n}")
    comprobar("el archivo del índice quedó escrito", os.path.exists(ruta))
    en_disco = S.buscar("interés legítimo")
    comprobar("da exactamente los mismos resultados que en memoria",
              [r["clave"] for r in en_disco] == [r["clave"] for r in res])
    # Segunda carga: debe reutilizar el índice, no reconstruirlo.
    comprobar("la segunda carga reutiliza el índice", S.cargar(ruta_indice=ruta) == n)
    comprobar("y un índice de OTRO origen se descarta",
              not S._indice_sirve(__import__("pathlib").Path(ruta),
                                  __import__("pathlib").Path(__file__)))


print("\n== Degradación sin acervo ==")
comprobar("con archivo inexistente cargar() devuelve 0 y no lanza",
          S.cargar("no_existe.jsonl.gz", "tampoco.json", None) == 0)
comprobar("la fuente se declara no disponible", not S.disponible())
comprobar("buscar devuelve vacío en vez de tronar", S.buscar("amparo") == [])
comprobar("obtener devuelve vacío", S.obtener(CLAVE_DORADA) == [])
comprobar("el mensaje de no disponible dice qué archivo falta",
          "sjf_gaceta.jsonl.gz" in S.NO_DISPONIBLE)
comprobar("recargar la deja lista otra vez",
          S.cargar(ruta_indice=None) > 0 and S.disponible())

print(f"\n{'TODO BIEN' if not fallos else f'{fallos} FALLA(S)'}")
sys.exit(1 if fallos else 0)
