#!/usr/bin/env python3
"""
Prueba unitaria del parser de la Corte IDH en la versión remota, sin red.

Gemela de sjf-mcpb/test-corteidh.js: usa los MISMOS fixtures y las mismas
aserciones, para garantizar que la versión Python y la versión Node se comportan
igual. Si una de las dos cambia, esta prueba lo delata.

Uso: python3 test_corteidh.py
"""

import re
import sys

import kriterius_mx as k


def tarjeta_caso(cita, pais, anio, doc, ficha, parrafo, texto):
    return f"""
  <div class="row" id="hash{anio}" style="">
    <div class="col-md-12">
      <div class="resultados_listado_iconos row"><a href="#" onclick='historial("h");return false;'><img></a></div>
      <div class="row"><div class="title">
        {cita}
      </div></div>
      <div class="row"><span><a href="#" onclick='javascript:doBusqueda("{pais}","pais");'>{pais}</a>
        | <a href="#" onclick='javascript:doBusqueda("{anio}","anio");'>{anio}</a></span></div>
      <div class="row"><div class="col-md-12">
        <div class="resumenContenido row">[ extracto con <em class="highlight">resaltado</em> ]</div>
        <div class="resumenContenido row"><br><p>{parrafo}. {texto}<font>66</font><br></p></div>
        <div class="row"><div class="col-xs-12"><div class="row">
          <a class="mas" href="/interamericano/doc?ficha={ficha}"></a>
          <a class="mas" href="#" onclick='toogleContenido("h");return false;'>Mostrar p&aacute;rrafo</a>
          <a class="mas" href="/interamericano/doc?doc={doc}#">Ver documento |</a>
        </div></div></div>
      </div></div>
    </div>
  </div>
  <hr id="separatorResponsive">"""


def tarjeta_anidada(cita, pais, anio, doc, parrafo, texto):
    """Caso real (Tzompaxtle): <p> vacíos y texto en divs anidados, con el
    extracto entre corchetes delante, notas al pie y espacios en el nombre del PDF."""
    return f"""
  <div class="row" id="hashan" style="">
    <div class="col-md-12">
      <div class="row"><div class="title">
        {cita}
      </div></div>
      <div class="row"><span><a href="#" onclick='javascript:doBusqueda("{pais}","pais");'>{pais}</a>
        | <a href="#" onclick='javascript:doBusqueda("{anio}","anio");'>{anio}</a></span></div>
      <div class="row"><div class="col-md-12">
        <div class="resumenContenido row">[ test de proporcionalidad de la pena ]<p></p></div>
        <div class="resumenContenido row">
          <div class="interior"><b>b)Test de proporcionalidad</b>
            <div class="mas"><p></p>{parrafo}. {texto}<font>160</font></div>
          </div>
        </div>
        <div class="row"><div class="col-xs-12"><div class="row">
          <a class="mas" href="#" onclick='toogleContenido("h");return false;'>Mostrar p&aacute;rrafo</a>
          <a class="mas" href="/interamericano/doc?doc={doc}#">Ver documento |</a>
        </div></div></div>
      </div></div>
    </div>
  </div>
  <hr id="separatorResponsive">"""


def tarjeta_oc(cita, doc, texto):
    return f"""
  <div class="row" id="hashoc" style="">
    <div class="col-md-12">
      <div class="row"><div class="title">
        {cita}
      </div></div>
      <div class="row"><div class="col-md-12">
        <div class="resumenContenido row">[ extracto de la opini&oacute;n ]</div>
        <div class="resumenContenido row">{texto}</div>
        <div class="row"><div class="col-xs-12"><div class="row">
          <a class="mas" href="/interamericano/doc?doc={doc}#">Ver documento |</a>
        </div></div></div>
      </div></div>
    </div>
  </div>
  <hr id="separatorResponsive">"""


HTML = f"""
<div id="btnMapa" class="btnMapa"></div>
<div class="paginacion">Resultados de busqueda: tortura Total de resultados: 1,162 &lt;&lt; 1 de 117 &gt;&gt;</div>
<div id="listaResultados" class="row">
  <div class="col-md-12">
  {tarjeta_caso(
    "Corte IDH. Caso Maritza Urrutia Vs. Guatemala. Fondo, Reparaciones y Costas. "
    "Sentencia de 27 de noviembre de 2003. Serie C No. 103, P&aacute;rrafo 69",
    "Guatemala", 2003, "casos_sentencias/CasoMaritzaUrrutia_esp.pdf", "104.pdf", 69,
    "La Corte ha establecido que la tortura est&aacute; estrictamente prohibida.")}
  {tarjeta_caso(
    "Corte IDH. Caso Cabrera Garc&iacute;a y Montiel Flores Vs. M&eacute;xico. "
    "Excepci&oacute;n Preliminar, Fondo, Reparaciones y Costas. Sentencia de 26 de "
    "noviembre de 2010. Serie C No. 220, P&aacute;rrafo 173",
    "M&eacute;xico", 2010, "casos_sentencias/seriec_220_esp.pdf", "220.pdf", 173,
    "Para analizar la relaci&oacute;n entre las tres declaraciones, la Corte observa.")}
  {tarjeta_caso(
    "Corte IDH. Caso Xyz Vs. Honduras. Supervisi&oacute;n de Cumplimiento de Sentencia. "
    "Resoluci&oacute;n de 1 de marzo de 2021. Serie C No. 400, P&aacute;rrafo 12",
    "Honduras", 2021, "casos_sentencias/seriec_400_esp.pdf", "400.pdf", 12,
    "El Estado ha cumplido parcialmente con las medidas de reparaci&oacute;n.")}
  {tarjeta_oc(
    "OC-29/22 Enfoques diferenciados de determinados grupos de personas privadas de la libertad",
    "opinionesConsultivas/seriea_29_esp.pdf",
    "46. La Corte resalta tambi&eacute;n que determinados grupos y personas se "
    "encuentran m&aacute;s expuestas a la tortura.")}
  {tarjeta_anidada(
    "Corte IDH. Caso Tzompaxtle Tecpile y otros Vs. M&eacute;xico. Excepci&oacute;n "
    "Preliminar, Fondo, Reparaciones y Costas. Sentencia de 7 de noviembre de 2022. "
    "Serie C No. 470., P&aacute;rrafo 104",
    "M&eacute;xico", 2022, "casos_sentencias/Corte IDH. Caso Tzompaxtle Tecpile.htm", 104,
    "Respecto del segundo punto, la Corte ha afirmado que corresponde a la autoridad judicial motivar.")}
  {tarjeta_caso(
    "Caso Manuela y otros Vs. El Salvador. Excepciones preliminares, Fondo, Reparaciones "
    "y Costas. Sentencia de 2 de noviembre de 2021. Serie C No. 441. , P&aacute;rrafo 253",
    "El Salvador", 2021, "casos_sentencias/seriec_441_esp.pdf", "441.pdf", 253,
    "La Corte reitera que la proporcionalidad de la pena debe valorarse caso por caso.")}
  {tarjeta_caso(
    "Corte IDH. Caso Mendoza y otros Vs. Argentina. Excepciones Preliminares, Fondo y "
    "Reparaciones. Sentencia de 14 de mayo de 2013. Serie C No. 260, P&aacute;rrafo 151",
    "Argentina", 2013, "casos_sentencias/seriec_260_esp.pdf", "260.pdf", 151,
    "Para la determinaci&oacute;n de las consecuencias jur&iacute;dicas del delito opera "
    "el principio de proporcionalidad.")}
  {tarjeta_caso(
    "Corte IDH. Caso Mendoza y otros Vs. Argentina. Excepciones Preliminares, Fondo y "
    "Reparaciones. Sentencia de 14 de mayo de 2013. Serie C No. 260, P&aacute;rrafo 174",
    "Argentina", 2013, "casos_sentencias/seriec_260_esp.pdf", "260.pdf", 174,
    "Las penas radicalmente desproporcionadas se encuentran bajo el &aacute;mbito de la "
    "prohibici&oacute;n de la tortura.")}
  </div>
</div>"""


fallos = 0


def comprobar(nombre, condicion, detalle=""):
    global fallos
    if condicion:
        print(f"  [OK]    {nombre}")
    else:
        print(f"  [FALLA] {nombre}" + (f" — {detalle}" if detalle else ""))
        fallos += 1


print("\nPARSER")
items = k._bjdh_parsear(HTML)
comprobar("extrae las 8 tarjetas", len(items) == 8, f"obtuvo {len(items)}")

mx = next((r for r in items if "Cabrera" in r["cita"]), None)
comprobar("decodifica entidades HTML", mx and "México" in mx["cita"], mx and mx["cita"][:60])
comprobar("extrae serie", mx and mx["serie"] == "Serie C No. 220", mx and mx["serie"])
comprobar("extrae párrafo", mx and mx["parrafo"] == "173", mx and mx["parrafo"])
comprobar("extrae año", mx and mx["anio"] == 2010, mx and str(mx["anio"]))
comprobar("extrae texto del párrafo", mx and len(mx["texto"]) > 50, mx and str(len(mx["texto"])))
comprobar("arma el link a la sentencia",
          mx and mx["url_doc"].endswith("doc?doc=casos_sentencias/seriec_220_esp.pdf"), mx and mx["url_doc"])
comprobar("arma el link a la ficha", mx and mx["url_ficha"].endswith("doc?ficha=220.pdf"), mx and mx["url_ficha"])

print("\nOPINIÓN CONSULTIVA (formato sin prefijo ni serie)")
oc = next((r for r in items if "OC-29" in r["cita"]), None)
comprobar("la detecta pese a no empezar con 'Corte IDH'", oc is not None)
comprobar("antepone 'Corte IDH.'", oc and oc["cita"].startswith("Corte IDH. OC-29/22"), oc and oc["cita"][:40])
comprobar("deriva la serie de la ruta del PDF", oc and oc["serie"] == "Serie A No. 29", oc and oc["serie"])
comprobar("deriva el año de la clave OC-29/22", oc and oc["anio"] == 2022, oc and str(oc["anio"]))
comprobar("toma el párrafo del inicio del texto", oc and oc["parrafo"] == "46", oc and oc["parrafo"])
comprobar("recupera el texto sin <p>", oc and len(oc["texto"]) > 50, oc and str(len(oc["texto"])))
comprobar("la cita queda citable", oc and oc["cita"].endswith("Serie A No. 29, párr. 46."), oc and oc["cita"])

print("\nREGRESIONES DETECTADAS CONTRA EL SITIO REAL")
tz = next((r for r in items if "Tzompaxtle" in r["cita"]), None)
comprobar("texto en divs anidados con <p> vacíos: lo recupera",
          tz and tz["texto"].startswith("104. Respecto del segundo punto"), tz and tz["texto"][:60])
comprobar("no arrastra el extracto entre corchetes",
          tz and "[" not in tz["texto"] and "Test de proporcionalidad" not in tz["texto"],
          tz and tz["texto"][:45])
comprobar("descarta las notas al pie en <font>", tz and "160" not in tz["texto"])
comprobar("URL con espacios: no la trunca",
          tz and tz["url_doc"].endswith("Corte IDH. Caso Tzompaxtle Tecpile.htm"), tz and tz["url_doc"])
comprobar("normaliza 'No. 470., Párrafo 104'",
          tz and tz["cita"].endswith("Serie C No. 470, párr. 104."), tz and tz["cita"][-40:])

man = next((r for r in items if "Manuela" in r["cita"]), None)
comprobar("acepta citas sin el prefijo 'Corte IDH.'", man is not None)
comprobar("le antepone el prefijo", man and man["cita"].startswith("Corte IDH. Caso Manuela"),
          man and man["cita"][:35])
comprobar("normaliza 'No. 441. , Párrafo 253'",
          man and man["cita"].endswith("Serie C No. 441, párr. 253."), man and man["cita"][-40:])
comprobar("normalizador directo",
          k._bjdh_normalizar_cita("Corte IDH. Caso X. Serie C No. 279, Párrafo 374")
          == "Corte IDH. Caso X. Serie C No. 279, párr. 374.")

print("\nPRELACIÓN (México → tipo de resolución → año)")
ord_ = k._bjdh_ordenar(items)
comprobar("México va primero", "México" in ord_[0]["cita"], ord_[0]["cita"][:50])
comprobar("entre los mexicanos, el más reciente encabeza", "Tzompaxtle" in ord_[0]["cita"], ord_[0]["cita"][:45])
comprobar("la opinión consultiva va antes que las sentencias no mexicanas",
          "OC-29" in ord_[2]["cita"], ord_[2]["cita"][:40])
pos = [i for i, r in enumerate(ord_) if "Mendoza" in r["cita"]]
comprobar("los párrafos de una misma resolución quedan adyacentes",
          len(pos) == 2 and pos[1] - pos[0] == 1, str(pos))
comprobar("supervisión de cumplimiento queda al final",
          "Supervisi" in (ord_[-1].get("tipo") or ""), ord_[-1].get("tipo"))
comprobar("rango: opinión consultiva = 0", k._bjdh_rango_resolucion("Opinión Consultiva") == 0)
comprobar("rango: fondo = 1", k._bjdh_rango_resolucion("Fondo, Reparaciones y Costas") == 1)
comprobar("rango: excepciones con fondo pesa como fondo",
          k._bjdh_rango_resolucion("Excepción Preliminar, Fondo, Reparaciones y Costas") == 1)
comprobar("rango: excepciones solas = 2", k._bjdh_rango_resolucion("Excepciones Preliminares") == 2)
comprobar("rango: supervisión = 4", k._bjdh_rango_resolucion("Supervisión de Cumplimiento de Sentencia") == 4)
comprobar("detecta caso mexicano por el país", k._bjdh_es_mexico({"pais": "México", "cita": ""}))
comprobar("detecta caso mexicano por la cita", k._bjdh_es_mexico({"pais": "", "cita": "Caso X Vs. México. Fondo"}))

print("\nTOTALES Y FORMATO DE SALIDA")
t = k._bjdh_totales(HTML)
comprobar("lee el total de resultados", t["total"] == "1,162", str(t["total"]))
comprobar("lee la paginación", t["total_paginas"] == "117", f"{t['pagina']} de {t['total_paginas']}")

salida = k._bjdh_formatear(ord_, "ENCABEZADO", True)
comprobar("el texto va entre comillas", '"46. La Corte resalta' in salida)
comprobar("el link se rotula 'Sentencia:'", "\nSentencia: https" in salida)
comprobar("el link de ficha se rotula 'Ficha técnica:'", "\nFicha técnica: https" in salida)
comprobar("marca los casos contra México", "CASO CONTRA MÉXICO — vinculante directo" in salida)
comprobar("agrupa el segundo párrafo del mismo caso", "Párr. 174 (mismo caso)." in salida)
comprobar("el caso agrupado imprime su link UNA vez",
          len(re.findall(r"seriec_260_esp\.pdf", salida)) == 1,
          str(len(re.findall(r"seriec_260_esp\.pdf", salida))))
comprobar("no quedan rótulos del formato anterior", "Sentencia (PDF)" not in salida)

print("\nJERARQUÍA DEL SJF (portada desde la versión Node)")
comprobar("Pleno pesa más que TCC",
          k._rango_organo("11a. Época; Pleno") < k._rango_organo("11a. Época; T.C.C."))
comprobar("Primera Sala antes que Segunda Sala",
          k._rango_organo("Primera Sala") < k._rango_organo("Segunda Sala"))
comprobar("lee la época en el formato real del SJF ('12a. Época')",
          k._numero_epoca("12a. Época") == 12 and k._numero_epoca("9a. Época") == 9)
comprobar("no confunde undécima con décima",
          k._numero_epoca("Undécima Época") == 11 and k._numero_epoca("Décima Época") == 10)
docs = [
    {"ius": 1, "localizacion": "10a. Época; T.C.C. [TA]", "rubro": "AMPARO"},
    {"ius": 2, "localizacion": "11a. Época; Pleno [J]", "rubro": "AMPARO"},
    {"ius": 3, "localizacion": "11a. Época; Primera Sala [J]", "rubro": "OTRO"},
]
orden_sjf = [d["ius"] for d in k._ordenar_por_jerarquia(docs, "amparo")]
comprobar("ordena Pleno → Primera Sala → TCC", orden_sjf == [2, 3, 1], str(orden_sjf))

print(f"\n{'TODO OK' if fallos == 0 else str(fallos) + ' FALLA(S)'} — {len(items)} resultados parseados\n")
sys.exit(0 if fallos == 0 else 1)
