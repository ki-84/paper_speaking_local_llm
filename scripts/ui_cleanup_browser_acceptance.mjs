// Read-only LAN UI checks; preserves the selected lesson's learning position.
import fs from 'node:fs/promises';
import path from 'node:path';
import {chromium,expect} from '../frontend/node_modules/@playwright/test/index.mjs';
const root=process.cwd(),base=process.env.PAPERSPEAK_TEST_URL||'https://192.168.10.112:8443';
const report={time:new Date().toISOString(),checks:{},pages:[],status:'running'};
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1050}});
const page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const api=async route=>{const r=await context.request.get(base+'/api'+route);if(!r.ok())throw new Error(`${route}: ${r.status()}`);return r.json();};
let lesson,previous;
const before=await api('/jobs');
const oldPaused=before.filter(j=>j.kind==='lesson'&&j.state==='paused').map(j=>j.id);
const shot=async(name)=>page.screenshot({path:path.join(root,`data/evaluation/ui-cleanup-${name}.png`),fullPage:true});
try {
 await page.goto(base);
 await expect(page.getByRole('region',{name:'作成した動画',exact:true})).toBeVisible();
 const videos=await api('/videos');
 report.video_count=videos.filter(v=>['overview','deep_dive'].includes(v.kind)).length;
 await expect(page.locator('.video-library-item')).toHaveCount(report.video_count);
 await expect(page.locator('.nightly-panel,.story-project,.lesson-card')).toHaveCount(0);
 report.checks.library_is_only_chronological_videos=true;
 await shot('videos');
 await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
 await expect(page.getByRole('region',{name:'自動選定と動画作成'})).toBeVisible();
 await expect(page.getByText('作成状況を読み込み中…',{exact:true})).toHaveCount(0,{timeout:30000});
 await expect(page.locator('.nightly-panel img')).toHaveCount(0);
 await expect(page.getByRole('region',{name:'学会別の受賞論文'})).toHaveCount(0);
 report.checks.creation_has_compact_progress=true;
 await shot('create');
 await page.getByText('学会の受賞情報・取得状況',{exact:true}).click();
 await expect(page.getByRole('region',{name:'学会別の受賞論文'}).getByRole('table')).toBeVisible();
 report.checks.awards_are_available_on_demand=true;
 await page.getByText('学会の受賞情報・取得状況',{exact:true}).click();
 await page.locator('nav').getByRole('button',{name:'英語練習',exact:true}).click();
 const lessons=await api('/lessons');report.lesson_count=lessons.length;
 await expect(page.locator('.lesson-card')).toHaveCount(lessons.length);
 await shot('practice');
 const id=lessons.find(l=>l.data.format==='paper-story-1'&&l.state==='ready')?.id;
 if(!id)throw new Error('No ready story lesson');
 lesson=await api('/lessons/'+id);previous=lesson.progress;
 const chapter=lesson.chapters.find(c=>c.state==='ready');
 await context.request.put(base+`/api/lessons/${id}/progress`,{data:{chapter_id:chapter.id,turn_index:0,role:'both',speed:1,subtitles:true}});
 await page.locator(`[data-lesson-id="${id}"]`).click();
 await expect(page.locator('.spoken-sentence')).toHaveText(chapter.data.turns[0].text);
 await expect(page.locator('.story-project,.video-export,.thumbnail-grid')).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Your turn',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Hear it',exact:true}).click();
 await expect.poll(()=>page.locator('.sentence-card audio').evaluate(a=>a.currentTime)).toBeGreaterThan(0);
 await page.getByRole('button',{name:'Pause',exact:true}).click();
 report.checks.ready_lesson_audio_and_recording_controls=true;
 await shot('learn');
 for(const [label,name] of [['動画一覧','videos'],['動画を作る','create'],['英語練習','practice'],['論文を探す','discover'],['設定','settings']]) {
  await page.locator('nav').getByRole('button',{name:label,exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await shot(name+'-phone');report.pages.push({name,mobile_overflow:false});
 }
 await expect(page.getByText('従来の論文探索・章教材')).toHaveCount(0);
 report.checks.old_automation_controls_removed=true;
 const after=await api('/jobs');
 expect(after.filter(j=>j.kind==='lesson'&&j.state==='paused').map(j=>j.id).sort()).toEqual(oldPaused.sort());
 const initiations=['nightly_video','video_project','import','lesson','discover','award_refresh'];
 expect(after.filter(j=>initiations.includes(j.kind)&&!before.some(b=>b.id===j.id))).toEqual([]);
 report.checks.no_jobs_started_or_resumed=true;
 report.checks.no_browser_exceptions=errors.length===0;
 if(errors.length)throw new Error(errors.join('; '));
 report.status='passed';
} catch(e) { report.status='failed';report.error=e.message;process.exitCode=1; }
finally {
 if(lesson) await context.request.put(base+`/api/lessons/${lesson.id}/progress`,{data:previous||{}});
 await fs.writeFile(path.join(root,'data/evaluation/ui-cleanup-browser.json'),JSON.stringify(report,null,2)+'\n');
 await browser.close();
}
console.log(JSON.stringify(report,null,2));
