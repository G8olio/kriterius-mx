/** Operaciones mediante el Chrome controlado por CUA en la Mac.
 * Se importa desde cua_repl y recibe un Tab documentado. No inicia Chrome,
 * no conecta por CDP, no exporta cookies ni usa peticiones fuera de la página.
 * El llamador debe comprobar VPN + IP de Chrome antes de iniciar, y vigilar la
 * VPN entre fichas. Un bloqueo o cambio de plantilla detiene la ejecución.
 */
import { readFile, writeFile, mkdir, unlink } from 'node:fs/promises';
import { join } from 'node:path';

const BASE = 'https://sjfsemanal.scjn.gob.mx';

export async function consumirPermiso(path,paso) {
  const permiso=JSON.parse(await readFile(path,'utf8'));
  await unlink(path);
  const now=Date.now(), emitido=Date.parse(permiso.emitido_utc), vence=Date.parse(permiso.vence_utc);
  if(permiso.verificado!==true || permiso.paso!==paso || !Number.isFinite(emitido) ||
     !Number.isFinite(vence) || now<emitido || now>vence || vence-emitido>30000)
    throw new Error('Falta una comprobación VPN nueva para este paso');
}

export async function descubrir(tab,carpeta,permisoVpn) {
  await consumirPermiso(permisoVpn,'catalogo');
  await tab.goto(BASE+'/presentacion');
  await tab.playwright.getByRole('link',{name:'TESIS',exact:true}).waitFor({state:'visible',timeoutMs:20000});
  await tab.playwright.domSnapshot();
  await tab.playwright.getByRole('link',{name:'TESIS',exact:true}).click();
  await tab.playwright.waitForURL(BASE+'/busqueda-principal-tesis');
  const row=tab.playwright.getByRole('row').filter({hasText:'Ambas'});
  await row.waitFor({state:'visible',timeoutMs:20000});
  await tab.playwright.domSnapshot();
  const total=Number(await row.getByRole('button').last().innerText());
  if(!Number.isInteger(total) || total<1 || total>100) throw new Error('Total semanal no soportado');
  await row.getByRole('button').last().click();
  await tab.playwright.waitForURL(BASE+'/listado-resultado-tesis');
  const cantidad=tab.playwright.getByRole('combobox',{name:'Número de elementos',exact:true});
  await cantidad.waitFor({state:'visible',timeoutMs:20000});
  await tab.playwright.domSnapshot();
  await cantidad.selectOption({label:'50'});
  await tab.playwright.getByText(`Registro(s): 1 al ${Math.min(total,50)} de ${total}`,{exact:true})
    .waitFor({state:'visible',timeoutMs:20000});
  await tab.playwright.domSnapshot();
  const catalogo={url:await tab.url(),capturado_utc:new Date().toISOString(),texto_visible:'',enlaces:[]};
  for(let page=0;page<2;page++) {
    const text=await tab.playwright.locator('body').innerText();
    const count=/Registro\(s\):\s*(\d+)\s+al\s+(\d+)\s+de\s+(\d+)/.exec(text);
    if(!count || Number(count[3])>100) throw new Error('Total del catálogo no soportado');
    catalogo.texto_visible+='\n'+text;
    const links=await tab.playwright.getByRole('link').filter({hasText:'Registro digital:'})
      .evaluateAll(els=>els.map(el=>({texto:el.innerText,href:el.getAttribute('href')})));
    catalogo.enlaces.push(...links);
    if(Number(count[2])===Number(count[3])) break;
    if(page===1) throw new Error('Catálogo incompleto');
    await tab.playwright.getByRole('navigation',{name:'Paginación de resultados',exact:true})
      .getByRole('link',{name:'Next',exact:true}).click();
    await tab.playwright.getByText(new RegExp(`Registro\\(s\\):\\s*${Number(count[2])+1} al`))
      .waitFor({state:'visible',timeoutMs:20000});
    await tab.playwright.domSnapshot();
  }
  await mkdir(carpeta,{recursive:true});
  await writeFile(join(carpeta,'catalogo.json'),JSON.stringify(catalogo,null,2)+'\n');
  return {total:catalogo.enlaces.length,url:catalogo.url};
}

export function identidad(texto, registro) {
  const ids = [...texto.matchAll(/^Registro digital:\s*(\d+)\s*$/gm)].map(m => m[1]);
  if (ids.length !== 1 || ids[0] !== registro)
    throw new Error('La ficha visible no corresponde al registro esperado');
  if (!/^Esta tesis se publicó .+/m.test(texto))
    throw new Error('Falta la nota final de publicación');
  if (/access denied|request unsuccessful|captcha|acceso denegado/i.test(texto.slice(0,1500)))
    throw new Error('El portal mostró un rechazo o una verificación');
}

export async function capturarFicha(tab, enlace, carpeta, permisoVpn) {
  const match = /^\/detalle\/tesis\/([1-9][0-9]{5,7})$/.exec(enlace.href);
  if (!match || !new RegExp(`Registro digital:\\s*${match[1]}\\b`).test(enlace.texto))
    throw new Error('Solo se siguen enlaces previamente observados en el catálogo');
  const registro = match[1];
  const url = BASE + enlace.href;
  await mkdir(carpeta,{recursive:true});
  const ritmo=join(carpeta,'ultima-apertura.json');
  let ultima=0;
  try {ultima=JSON.parse(await readFile(ritmo,'utf8')).fecha;} catch(error) {
    if(error.code!=='ENOENT') throw error;
  }
  if(!Number.isFinite(ultima)) throw new Error('Estado de intervalo inválido');
  const espera=Math.max(0,15000-(Date.now()-ultima));
  if(espera>15000) throw new Error('El reloj retrocedió');
  if(espera) await new Promise(resolve=>setTimeout(resolve,espera));
  await consumirPermiso(permisoVpn,registro);
  await writeFile(ritmo,JSON.stringify({fecha:Date.now(),registro}));
  try {
    // Sigue exactamente el enlace observado. La misma pestaña conserva la
    // sesión normal de Chrome; no se cambian sus señales de identificación.
    await tab.goto(url);
    const estado = await tab.playwright.domSnapshot();
    if (/Access denied|Request unsuccessful|Verify you are human|captcha/i.test(estado.slice(0,3000)))
      throw new Error('Acceso rechazado; no reintentar');
    try {
      await tab.playwright.getByText(`Registro digital: ${registro}`,{exact:true})
        .filter({visible:true}).waitFor({state:'visible',timeoutMs:20000});
      await tab.playwright.getByText(/^Esta tesis se publicó/)
        .filter({visible:true}).waitFor({state:'visible',timeoutMs:20000});
    } catch(error) {
      // Chrome puede completar la carga justo después de vencer el selector.
      // Una lectura adicional no vuelve a navegar ni repite solicitudes al portal.
      // Las comprobaciones de URL, identidad y cierre siguen siendo obligatorias.
      if(!/selector deadline exceeded|waitFor\(visible\) timed out/.test(String(error))) throw error;
      await tab.playwright.domSnapshot();
    }
    if (await tab.url() !== url) throw new Error('La URL cambió fuera de la ficha esperada');
    const visible = await tab.playwright.locator('body').innerText();
    identidad(visible,registro);
    const meta = {url,capturado_utc:new Date().toISOString(),
      canal:'Chrome mediante CUA; flujo programable de enlaces visibles'};
    await writeFile(join(carpeta,`${registro}.txt`),visible,'utf8');
    await writeFile(join(carpeta,`${registro}.meta.json`),JSON.stringify(meta,null,2)+'\n','utf8');
    return {registro,caracteres:visible.length,url};
  } catch (error) {
    await writeFile(join(carpeta,'interrupcion.json'),JSON.stringify({registro,url,
      fecha:new Date().toISOString(),error:String(error)},null,2));
    throw error;
  }
}

export async function pendientes(carpeta) {
  const plan = JSON.parse(await readFile(join(carpeta,'plan.json'),'utf8'));
  const catalogo = JSON.parse(await readFile(join(carpeta,'catalogo.json'),'utf8'));
  return plan.pendientes.map(id => {
    const link=catalogo.enlaces.find(e=>e.href===`/detalle/tesis/${id}`);
    if(!link) throw new Error('Registro no observado en el catálogo');
    return link;
  });
}
