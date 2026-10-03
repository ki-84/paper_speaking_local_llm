// Real LAN studio/assets, with Chromium's recorded-file microphone for repeatable QA.
// This checks the browser recording path, not a physical Mac microphone or learner accuracy.
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require=createRequire(path.join(root,'frontend/package.json'));
const {chromium,expect}=require('@playwright/test');
// Completed projects include both scripts and thumbnail sets; allow LAN loading
// and rendering to settle before checking their practice controls.
expect.configure({timeout:15000});
const ident=process.argv[2];
if(!ident)throw new Error('Supply a video project ID');
const base=process.env.PAPERSPEAK_URL||'https://192.168.10.112:8443';
const report={project_id:ident,started:new Date().toISOString(),scope:'Linux Chromium over LAN HTTPS; real movies and practice audio. Optional file-fed microphone tests capture/upload/local evaluation, not human pronunciation accuracy or physical Mac hardware.',checks:{}};
const microphone=process.argv.includes('--microphone');
const microphoneFile=process.env.PAPERSPEAK_MICROPHONE_FILE||path.join(root,'data/evaluation/story-microphone.wav');
const captureMs=Number(process.env.PAPERSPEAK_CAPTURE_MS||4400);
const browser=await chromium.launch({headless:true,args:microphone?['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream',`--use-file-for-fake-audio-capture=${microphoneFile}`]:[]});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1000},permissions:microphone?['microphone']:[]});
const page=await context.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));
let lesson,deepLesson,attemptId,reviewSnapshot;
const reviews=(action,payload)=>{
  const code=`import json,sys
from paperspeak import db
a,v=sys.argv[1],json.loads(sys.argv[2])
if a=='read': print(json.dumps(db.one('SELECT * FROM reviews WHERE id=?',(v['id'],))))
else:
 with db.connection() as c:
  busy=c.execute('SELECT id FROM attempts WHERE chapter_id=? AND turn_id=? AND created>? LIMIT 1',(v['chapter_id'],v['turn_id'],v['started'])).fetchone()
  if not busy:
   old=v['snapshot']
   if old: c.execute('INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?,?,?)',(old['id'],old['lesson_id'],old['chapter_id'],old['turn_id'],old['due'],old['step'],db.dumps(old['data'])))
   else: c.execute('DELETE FROM reviews WHERE id=?',(v['id'],))
  print(json.dumps({'restored':not bool(busy)}))`;
  const r=spawnSync(path.join(root,'.venv/bin/python'),['-c',code,action,JSON.stringify(payload)],{env:{...process.env,PYTHONPATH:path.join(root,'backend')},encoding:'utf8'});
  if(r.status!==0)throw new Error(r.stderr);return JSON.parse(r.stdout);
};
const api=async route=>{const response=await context.request.get(base+'/api'+route);if(!response.ok())throw new Error(`${route}: ${response.status()}`);return response.json();};
try {
  const project=await api('/video-projects/'+ident);
  const track=project.data.modes.overview;
  lesson=await api('/lessons/'+track.lesson_id);
  const chapter=lesson.chapters.find(c=>c.state==='ready');
  await context.request.put(base+`/api/lessons/${lesson.id}/progress`,{data:{chapter_id:chapter.id,turn_index:0,role:'both',speed:1,subtitles:true}});
  await page.goto(base);
  const studio=page.locator('.story-studio');
  await studio.locator('select').selectOption(project.paper_id);
  const panel=studio.getByRole('region',{name:'解説・詳解動画'});
  await expect(panel).toBeVisible();
  const movies=panel.locator('.story-film');
  await expect(movies).toHaveCount(2);
  report.checks.two_film_cards=true;
  if(process.argv.includes('--require-complete')){
    for(let i=0;i<2;i++){
      const card=movies.nth(i),v=card.locator('video');
      await expect(card.getByRole('link',{name:'Download MP4 · 動画'})).toBeVisible();
      await v.evaluate(video=>video.play());
      await expect.poll(()=>v.evaluate(video=>video.currentTime)).toBeGreaterThan(0);
      await v.evaluate(video=>video.pause());
      report.checks[i?'deep_dive_movie_plays':'overview_movie_plays']=true;
    }
  }
  const overview=movies.first();
  const film=overview.locator('video');
  await expect(film).toBeVisible();
  await film.evaluate(v=>v.play());
  await expect.poll(()=>film.evaluate(v=>v.currentTime)).toBeGreaterThan(0);
  report.checks.finished_movie_plays=true;
  await film.evaluate(v=>v.pause());
  const href=await overview.getByRole('link',{name:'Download MP4 · 動画'}).getAttribute('href');
  const range=await context.request.get(new URL(href,base).href,{headers:{Range:'bytes=0-1023'}});
  report.checks.lan_mp4_range_download=range.status()===206&&(await range.body()).length===1024;
  await overview.locator('summary').filter({hasText:'Titles & description'}).click();
  await expect(overview.locator('.story-title')).toHaveCount(3);
  await expect(overview.locator('textarea')).toHaveValue(/0:00/);
  report.checks.packaging_and_timestamps=true;
  await page.screenshot({path:path.join(root,'data/evaluation/story-library.png'),fullPage:true});
  await overview.getByRole('button',{name:'Practice English · 英語練習'}).click();
  await expect(page.locator('.spoken-sentence')).toHaveText(chapter.data.turns[0].text);
  await expect(page.locator('.sentence-translation')).not.toBeEmpty();
  report.checks.same_script_and_initial_japanese=true;
  await page.getByRole('button',{name:'Hear it',exact:true}).click();
  await expect.poll(()=>page.locator('.sentence-card audio').evaluate(a=>a.currentTime)).toBeGreaterThan(0);
  await page.getByRole('button',{name:'Next sentence'}).click();
  await expect(page.locator('.spoken-sentence')).toHaveText(chapter.data.turns[1].text);
  report.checks.sentence_audio_and_navigation=true;
  const expressions=page.locator('.story-expressions button');
  await expect(expressions.first()).toBeVisible();
  await expressions.first().click();
  const phrase=chapter.data.expressions[0].phrase;
  await expect(page.locator('.spoken-sentence')).toContainText(phrase);
  report.checks.actual_expression_practice=true;
  if(microphone){
    reviewSnapshot=reviews('read',{id:chapter.id+':'+chapter.data.turns[0].id});
    // Reset to the exact line supplied to the file-fed microphone.
    await context.request.put(base+`/api/lessons/${lesson.id}/progress`,{data:{chapter_id:chapter.id,turn_index:0,role:'both',speed:1,subtitles:true}});
    await page.reload();
    await page.locator('.lesson-card').filter({hasText:lesson.data.title}).first().click();
    await expect(page.locator('.spoken-sentence')).toHaveText(chapter.data.turns[0].text);
    const posted=page.waitForResponse(r=>r.url().endsWith('/api/attempts')&&r.request().method()==='POST',{timeout:60000});
    await page.locator('.record-button').click();
    await expect(page.locator('.record-button')).toContainText('Done');
    await page.waitForTimeout(captureMs);
    await page.locator('.record-button').click();
    const response=await posted;if(!response.ok())throw new Error('Recording upload failed');
    attemptId=(await response.json()).attempt_id;
    report.attempt_id=attemptId;report.checks.media_recorder_upload=true;
    await expect.poll(async()=> (await api('/attempts/'+attemptId)).state,{timeout:1200000,intervals:[3000]}).toBe('ready');
    const attempt=await api('/attempts/'+attemptId);
    report.checks.local_recording_evaluation=true;
    report.recording_result={state:attempt.state,stages:Object.keys(attempt.data),transcript:attempt.data.transcript,quality:attempt.data.quality};
  }
  await page.screenshot({path:path.join(root,'data/evaluation/story-practice.png'),fullPage:true});
  if(process.argv.includes('--require-complete')){
    deepLesson=await api('/lessons/'+project.data.modes.deep_dive.lesson_id);
    const worked=project.data.modes.deep_dive.scenes.find(s=>s.beat==='worked_example') || project.data.modes.deep_dive.scenes.find(s=>/worked example|hypothetical/i.test(s.title));
    const workedIndex=project.data.modes.deep_dive.scenes.indexOf(worked);
    const c=deepLesson.chapters.find(c=>workedIndex>=0 && c.data.story_scene===workedIndex) || deepLesson.chapters.find(c=>c.data.turns.some(t=>/hypothetical|for example|imagine|suppose/i.test(t.text)));
    if(!c)throw new Error('The spoken worked example is missing');
    const cue=c.data.turns.findIndex(t=>t.visual && /hypothetical|example|imagine|suppose/i.test(t.text));
    const i=cue>=0?cue:c.data.turns.findIndex(t=>t.visual),turn=c.data.turns[i];
    if(!turn)throw new Error('The worked example has no figure cue');
    await context.request.put(base+`/api/lessons/${deepLesson.id}/progress`,{data:{chapter_id:c.id,turn_index:i,role:'both',speed:1,subtitles:true}});
    await page.goto(base);
    await page.locator('.story-studio select').selectOption(project.paper_id);
    await page.locator('.story-studio .story-film').nth(1).getByRole('button',{name:'Practice English · 英語練習'}).click();
    await expect(page.locator('.spoken-sentence')).toHaveText(turn.text);
    await expect(page.locator('.sentence-translation')).not.toBeEmpty();
    const img=page.locator('.visual-picture img');
    await expect.poll(()=>img.evaluate(img=>img.complete&&img.naturalWidth>0)).toBeTruthy();
    const ref=c.data.visuals.find(r=>r.key===turn.visual.key),asset=deepLesson.visuals.find(a=>a.id===ref.asset_id);
    if(!decodeURIComponent(new URL(await img.getAttribute('src'),base).pathname).endsWith(asset.data.image_path))throw new Error('The practice figure does not match the spoken cue');
    await page.getByRole('button',{name:'Hear it',exact:true}).click();
    await expect.poll(()=>page.locator('.sentence-card audio').evaluate(a=>a.currentTime)).toBeGreaterThan(0);
    report.checks.deep_worked_example_practice_and_figure=true;
    await page.screenshot({path:path.join(root,'data/evaluation/story-deep-practice.png'),fullPage:true});
  }
  if(errors.length)throw new Error(errors.join('\n'));
  report.checks.no_browser_exceptions=true;
  report.status=Object.values(report.checks).every(Boolean)?'passed':'failed';
  if(report.status==='failed')process.exitCode=1;
}catch(e){report.status='failed';report.error=e.message;process.exitCode=1;}
finally{
  await page.goto('about:blank');
  if(lesson){const restored=await context.request.put(base+`/api/lessons/${lesson.id}/progress`,{data:lesson.progress?.chapter_id?lesson.progress:{chapter_id:lesson.chapters[0].id,turn_index:0}});report.progress_restored=restored.ok();}
  if(deepLesson){const restored=await context.request.put(base+`/api/lessons/${deepLesson.id}/progress`,{data:deepLesson.progress?.chapter_id?deepLesson.progress:{chapter_id:deepLesson.chapters[0].id,turn_index:0}});report.deep_progress_restored=restored.ok();}
  if(attemptId){
    const removed=await context.request.delete(base+'/api/attempts/'+attemptId);report.qa_recording_removed=removed.ok();
    if(removed.ok()){
      const chapter=lesson.chapters.find(c=>c.state==='ready'),turn=chapter.data.turns[0];
      report.qa_review_restored=reviews('restore',{id:chapter.id+':'+turn.id,chapter_id:chapter.id,turn_id:turn.id,started:Date.parse(report.started)/1000,snapshot:reviewSnapshot}).restored;
    }
  }
  await browser.close();report.finished=new Date().toISOString();
  const suffix=microphone?'-microphone':process.argv.includes('--require-complete')?'-complete':'';
  fs.writeFileSync(path.join(root,`data/evaluation/story-browser${suffix}.json`),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
}
