// Read-only verification of deployed film identity, URL-free copy and real downloads.
import fs from 'node:fs/promises';
import path from 'node:path';
import {chromium, expect} from '../frontend/node_modules/@playwright/test/index.mjs';
const base=process.env.PAPERSPEAK_TEST_URL||'https://192.168.10.112:8443';
const ids=process.argv.slice(2);
if(!ids.length)throw new Error('Supply completed awarded project IDs');
const report={time:new Date().toISOString(),projects:[],checks:{}};
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1000}});
const page=await context.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(base);
 const studio=page.locator('.story-studio');
 for(const id of ids){
  const response=await context.request.get(base+'/api/video-projects/'+id);
  expect(response.ok()).toBeTruthy();
  const project=await response.json();
  await studio.locator('select').selectOption(project.paper_id);
  await expect(studio.locator('.thumbnail-grid img')).toHaveCount(6,{timeout:30000});
  const record={id,paper:project.data.paper_title,modes:[]};
  for(const [index,mode] of ['overview','deep_dive'].entries()){
   const track=project.data.modes[mode],set=track.thumbnails,identity=set.data.manifest.identity;
   const film=studio.locator('.story-film').nth(index);
   await expect(film.locator('h3').first()).toHaveText(track.packaging.title);
   expect(track.packaging.title).toContain(identity.conference);
   expect(identity.awards.length).toBeGreaterThan(0);
   const selected=set.data.candidates.find(c=>c.id===set.data.selected_id);
   const complete=track.videos.find(v=>v.kind===mode&&v.state==='ready');
   const seconds=complete.data.duration;
   const durationLabel=`${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')} · 完成`;
   await expect(film.locator(':scope > p').first()).toHaveText(durationLabel);
   expect(complete.data.thumbnail).toBe(selected.png);
   expect(complete.data.description).not.toMatch(/https?:\/\/|www\./i);
   for(const prize of identity.awards){
    expect(complete.data.description).toContain(prize.name);
    expect(complete.data.description).toContain(`${prize.venue} ${prize.year}`);
   }
   await film.getByText('Titles & description · タイトルと説明欄',{exact:true}).click();
   await expect(film.locator('textarea')).toHaveValue(complete.data.description);
   await expect(film.getByRole('link',{name:'Download MP4 · 動画',exact:true})).toHaveAttribute('download',complete.data.title+'.mp4');
   const downloads=[];
   for(const candidate of set.data.candidates){
    for(const format of ['png','jpg']){
     const image=await context.request.get(base+'/api/files/'+candidate[format]);
     expect(image.ok()).toBeTruthy();
     const bytes=(await image.body()).length;
     expect(bytes).toBeLessThan(2_000_000);
     downloads.push({id:candidate.id,format,bytes});
    }
   }
   await expect.poll(()=>film.locator('.thumbnail-grid img').evaluateAll(imgs=>imgs.every(i=>i.complete&&i.naturalWidth===1280&&i.naturalHeight===720))).toBeTruthy();
   record.modes.push({mode,identity,downloads,duration_seconds:seconds,actual_duration_displayed:true,description_url_free:true,selected_poster_matches:true});
  }
  report.projects.push(record);
  if(id===ids[0]){
   await studio.screenshot({path:'data/evaluation/award-packaging-desktop.png'});
   const phone=await context.newPage();
   await phone.setViewportSize({width:360,height:470});
   await phone.route('**/*',r=>r.abort());
   const cards=[];
   for(const mode of ['overview','deep_dive']){
    const set=project.data.modes[mode].thumbnails.data;
    const c=set.candidates.find(c=>c.id===set.selected_id);
    cards.push(`<img src="data:image/png;base64,${(await fs.readFile(path.join('data',c.png))).toString('base64')}">`);
   }
   await phone.setContent(`<style>body{margin:0;padding:20px;background:#f4f5f0}img{display:block;width:320px;height:180px;margin:8px 0 14px}</style>${cards.join('')}`);
   await phone.locator('img').last().evaluate(i=>i.decode());
   await phone.screenshot({path:'data/evaluation/award-packaging-phone.png'});
   await phone.close();
  }
 }
 expect(errors).toEqual([]);
 report.status='passed';
 report.checks={actual_lan_ui_and_downloads:true,description_url_free_with_verified_awards:true,selected_thumbnail_dimensions:true,no_browser_errors:true,read_only:true};
}catch(e){report.status='failed';report.error=e.message;process.exitCode=1;}
finally{await fs.writeFile('data/evaluation/award-packaging-browser.json',JSON.stringify(report,null,2)+'\n');await browser.close();}
console.log(JSON.stringify({status:report.status,projects:report.projects.length,error:report.error,checks:report.checks}));
