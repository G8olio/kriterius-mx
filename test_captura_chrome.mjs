import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {identidad,consumirPermiso,capturarFicha} from './captura_chrome.mjs';

test('rechaza contenido de otra ficha y fichas parciales',async()=>{
  const fixture=JSON.parse(await readFile(new URL('./kriterius_datos/sjf_semanal/2032910.json',import.meta.url)));
  identidad(fixture.texto_visible,'2032910');
  assert.throws(()=>identidad(fixture.texto_visible,'2032921'));
  assert.throws(()=>identidad(fixture.texto_visible.split('Esta tesis se publicó')[0],'2032910'));
});

test('permiso VPN caducado, de otra ficha o reutilizado nunca autoriza navegar',async()=>{
  const dir=await mkdtemp(join(tmpdir(),'chrome-vpn-'));
  try{
    const file=join(dir,'permiso.json');
    for(const tipo of ['caducado','distinto','valido']){
      const now=Date.now(),inicio=now-(tipo==='caducado'?60000:100);
      await writeFile(file,JSON.stringify({verificado:true,paso:tipo==='distinto'?'2032921':'2032910',
        emitido_utc:new Date(inicio).toISOString(),vence_utc:new Date(inicio+30000).toISOString()}));
      if(tipo==='valido')await consumirPermiso(file,'2032910');
      else await assert.rejects(consumirPermiso(file,'2032910'));
      await assert.rejects(consumirPermiso(file,'2032910'));
    }
  }finally{await rm(dir,{recursive:true,force:true});}
});

test('sin permiso VPN no llega a goto',async()=>{
  let navegaciones=0;
  const tab={goto:async()=>{navegaciones++;}};
  await assert.rejects(capturarFicha(tab,{href:'/detalle/tesis/2032910',texto:'Registro digital: 2032910'},tmpdir(),'/permiso-inexistente'));
  assert.equal(navegaciones,0);
});

test('carga tardía se lee sin volver a navegar y conserva controles de integridad',async()=>{
  const fixture=JSON.parse(await readFile(new URL('./kriterius_datos/sjf_semanal/2032910.json',import.meta.url)));
  for(const visible of [fixture.texto_visible,'Request unsuccessful',fixture.texto_visible.replaceAll('2032910','2032921'),fixture.texto_visible.split('Esta tesis se publicó')[0]]) {
    const dir=await mkdtemp(join(tmpdir(),'chrome-carga-'));
    let navegaciones=0;
    try {
      const now=Date.now(),file=join(dir,'permiso.json');
      await writeFile(file,JSON.stringify({verificado:true,paso:'2032910',emitido_utc:new Date(now).toISOString(),vence_utc:new Date(now+30000).toISOString()}));
      const tab={goto:async()=>{navegaciones++;},url:async()=>fixture.procedencia.url,playwright:{
        domSnapshot:async()=>'',
        getByText:()=>({filter:()=>({waitFor:async()=>{throw new Error('Playwright selector deadline exceeded');}})}),
        locator:()=>({innerText:async()=>visible})}};
      const run=capturarFicha(tab,{href:'/detalle/tesis/2032910',texto:'Registro digital: 2032910'},dir,file);
      if(visible===fixture.texto_visible) {
        await run;
        assert.equal(await readFile(join(dir,'2032910.txt'),'utf8'),visible);
      } else await assert.rejects(run);
      assert.equal(navegaciones,1);
    } finally {await rm(dir,{recursive:true,force:true});}
  }
});
