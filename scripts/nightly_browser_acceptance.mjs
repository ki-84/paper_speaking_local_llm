// Actual LAN UI and thumbnail downloads. No fabricated model or paper results.
import fs from 'node:fs/promises';
import path from 'node:path';
import {chromium,expect} from '../frontend/node_modules/@playwright/test/index.mjs';
const root=process.cwd(),base=process.env.PAPERSPEAK_TEST_URL||'https://192.168.10.112:8443';
const projectId=process.argv[2]||'8ab4bf9ac3274635925c609fc9860165';
const report={project_id:projectId,checks:{},downloads:[],time:new Date().toISOString()};
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1000}});
const page=await context.newPage();
const exceptions=[];page.on('pageerror',e=>exceptions.push(e.message));
const api=async route=>{const r=await context.request.get(base+'/api'+route);if(!r.ok())throw new Error(route+': '+r.status());return r.json();};
let restore;
try{
 const project=await api('/video-projects/'+projectId);
 await page.goto(base);
 const panel=page.getByRole('region',{name:'昨夜の動画'});
 await expect(panel).toBeVisible();
 report.checks.nightly_panel=true;
 const studio=page.locator('.story-studio');
 await studio.locator('select').selectOption(project.paper_id);
 await expect(studio.locator('.thumbnail-grid img')).toHaveCount(6,{timeout:30000});
 for(const mode of ['overview','deep_dive']){
  const row=project.data.modes[mode].thumbnails;
  if(row.state!=='ready'||row.data.candidates.length!==3)throw new Error('Three ready thumbnail candidates are required');
  for(const c of row.data.candidates){
   for(const format of ['png','jpg']){
    const file=await context.request.get(base+'/api/files/'+c[format]);
    if(!file.ok())throw new Error('LAN thumbnail download failed');
    const bytes=(await file.body()).length;
    report.downloads.push({mode,id:c.id,format,bytes});
    if(bytes>2000000)throw new Error('Thumbnail exceeds 2 MB');
   }
  }
 }
 report.checks.six_png_and_six_jpeg_downloads=true;
 await expect.poll(()=>studio.locator('.thumbnail-grid img').evaluateAll(imgs=>imgs.every(i=>i.complete&&i.naturalWidth===1280&&i.naturalHeight===720))).toBeTruthy();
 report.checks.thumbnail_dimensions_1280_720=true;
 await studio.screenshot({path:path.join(root,'data/evaluation/nightly-thumbnails-desktop.png')});
 const row=project.data.modes.overview.thumbnails;
 restore={id:row.id,candidate_id:row.data.selected_id};
 const index=row.data.candidates.findIndex(c=>c.id!==restore.candidate_id);
 await studio.locator('.story-film').first().locator('.thumbnail-grid article').nth(index).getByRole('button',{name:'この案を使う'}).click();
 await expect.poll(async()=> (await api('/video-projects/'+projectId)).data.modes.overview.thumbnails.data.selected_id).toBe(row.data.candidates[index].id);
 await page.reload();
 await expect.poll(async()=> (await api('/video-projects/'+projectId)).data.modes.overview.thumbnails.data.selected_id).toBe(row.data.candidates[index].id);
 report.checks.manual_selection_survives_reload=true;
 // A phone-sized contact sheet, preserving pixels and typography from real PNGs.
 const thumbs=Object.entries(project.data.modes).map(([mode,track])=>({mode,c:track.thumbnails.data.candidates.find(c=>c.id===track.thumbnails.data.recommended_id)}));
 const phone=await context.newPage();
 await phone.setViewportSize({width:360,height:470});
 await phone.route('**/*',r=>r.abort());
 const cards=[];
 for(const {mode,c} of thumbs){
  const bytes=await fs.readFile(path.join(root,'data',c.png));
  cards.push(`<div>${mode}</div><img src="data:image/png;base64,${bytes.toString('base64')}">`);
 }
 await phone.setContent(`<style>body{margin:0;padding:20px;background:#f4f5f0;font:14px sans-serif}img{display:block;width:320px;height:180px;margin:8px 0 14px}</style>${cards.join('')}`);
 await phone.locator('img').last().evaluate(i=>i.decode());
 await phone.screenshot({path:path.join(root,'data/evaluation/nightly-thumbnails-phone.png')});
 report.checks.phone_320px_preview=true;
 report.checks.no_browser_exceptions=exceptions.length===0;
 if(exceptions.length)throw new Error(exceptions.join('; '));
 report.status='passed';
}catch(e){report.status='failed';report.error=e.message;process.exitCode=1;}
finally{
 if(restore)await context.request.put(base+`/api/thumbnail-sets/${restore.id}/selection`,{data:{candidate_id:restore.candidate_id}});
 await fs.writeFile(path.join(root,'data/evaluation/nightly-browser-acceptance.json'),JSON.stringify(report,null,2)+'\n');
 await browser.close();
}
console.log(JSON.stringify(report,null,2));
