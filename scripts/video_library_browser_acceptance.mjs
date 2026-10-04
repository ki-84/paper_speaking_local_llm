// Read-only verification of the deployed chronological video library.
import fs from 'node:fs/promises';
import path from 'node:path';
import {chromium, expect} from '../frontend/node_modules/@playwright/test/index.mjs';

const root=process.cwd(), base=process.env.PAPERSPEAK_TEST_URL||'https://192.168.10.112:8443';
const report={time:new Date().toISOString(),checks:{},downloads:[]};
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1000},timezoneId:'America/Los_Angeles'});
const page=await context.newPage();
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
const api=async route=>{
  const response=await context.request.get(base+'/api'+route);
  expect(response.ok()).toBeTruthy();
  return response.json();
};
const jobs=async()=> (await api('/jobs')).map(job=>[job.id,job.kind,job.state]);
const fileUrl=value=>'/api/files/'+value.split('/').map(encodeURIComponent).join('/');
try{
  await page.goto(base);
  const credentials=JSON.parse(await fs.readFile(path.join(root,'data/credentials.json'),'utf8'));
  if(credentials.password_required!==false){
    await page.getByLabel('Studio password').fill(credentials.password);
    await page.getByRole('button',{name:'Come on in'}).click();
  }
  const before=await jobs(), rows=await api('/videos');
  const films=rows.filter(v=>v.kind!=='chapter');
  if(!films.length)throw new Error('A completed film is required');
  const panel=page.getByRole('region',{name:'作成した動画',exact:true});
  const displayed=()=>panel.locator('.video-library-item').evaluateAll(items=>items.map(item=>item.dataset.videoId));
  await expect.poll(displayed).toEqual(films.map(v=>v.id));
  expect(rows.every((v,i)=>!i||rows[i-1].completed_at>=v.completed_at)).toBeTruthy();
  expect(rows.some(v=>v.kind.includes('preview'))).toBeFalsy();
  await expect(panel.getByText('日時は日本時間')).toBeVisible();
  await expect(panel.locator('.video-library-day h2').first()).toHaveText(new Date(films[0].completed_at*1000).toLocaleDateString('ja-JP',{timeZone:'Asia/Tokyo',year:'numeric',month:'long',day:'numeric'}));
  report.films=films.map(v=>({id:v.id,label:v.label,title:v.paper_title,completed_at:v.completed_at,revisions:v.revisions.length}));
  report.checks.latest_completed_first_with_japan_dates=true;
  for(const row of films){
    const item=panel.locator(`[data-video-id="${row.id}"]`);
    await expect(item.getByText(row.label,{exact:true})).toBeVisible();
    await expect(item.getByRole('link',{name:'MP4をダウンロード',exact:true})).toHaveAttribute('download',row.data.title+'.mp4');
    if(row.data.thumbnail){
      await item.scrollIntoViewIfNeeded();
      await expect.poll(()=>item.locator('img').evaluate(img=>img.complete&&img.naturalWidth>0)).toBeTruthy();
      const image=await context.request.get(base+fileUrl(row.data.thumbnail));
      expect(image.ok()).toBeTruthy();
    }
    for(const revision of [row,...row.revisions]){
      const response=await context.request.get(base+fileUrl(revision.data.mp4),{headers:{Range:'bytes=0-1023'}});
      expect(response.status()).toBe(206);
      expect((await response.body()).length).toBe(1024);
      report.downloads.push({id:revision.id,range:response.headers()['content-range']});
    }
  }
  report.checks.actual_thumbnails_and_current_and_previous_downloads=true;
  const revised=films.find(v=>v.revisions.length);
  if(revised){
    const item=panel.locator(`[data-video-id="${revised.id}"]`);
    await item.locator('.video-library-revisions summary').click();
    await expect(item.locator('.video-library-revisions a')).toHaveCount(revised.revisions.length);
    report.checks.earlier_renders_folded_and_preserved=true;
  }
  await panel.getByLabel('並び順',{exact:true}).selectOption('oldest');
  await expect.poll(displayed).toEqual([...films].reverse().map(v=>v.id));
  await panel.getByLabel('並び順',{exact:true}).selectOption('newest');
  const title=films[0].paper_title;
  await panel.getByLabel('論文名・タイトル・学会で検索').fill(title);
  await expect.poll(displayed).toEqual(films.filter(v=>`${v.paper_title} ${v.data.title} ${v.data.conference||''}`.toLowerCase().includes(title.toLowerCase())).map(v=>v.id));
  await panel.getByLabel('論文名・タイトル・学会で検索').fill('');
  await panel.getByLabel('動画の種類',{exact:true}).selectOption('chapter');
  await expect.poll(displayed).toEqual(rows.filter(v=>v.kind==='chapter').map(v=>v.id));
  await panel.getByLabel('動画の種類',{exact:true}).selectOption('films');
  report.checks.search_sort_and_chapter_filter=true;
  await panel.locator(`[data-video-id="${films[0].id}"]`).getByRole('button',{name:'タイトル・説明',exact:true}).click();
  const dialog=page.getByRole('dialog');
  await expect(dialog.getByLabel('YouTube用タイトル')).toHaveValue(films[0].data.title);
  if(films[0].data.description)await expect(dialog.getByLabel('YouTube用説明文')).toHaveValue(films[0].data.description);
  await dialog.locator('video').evaluate(video=>video.play());
  await expect.poll(()=>dialog.locator('video').evaluate(video=>video.currentTime)).toBeGreaterThan(0);
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  report.checks.finished_video_plays_and_copy_matches=true;
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:path.join(root,'data/evaluation/video-library-desktop.png')});
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
  await page.screenshot({path:path.join(root,'data/evaluation/video-library-phone.png')});
  report.checks.phone_layout_without_horizontal_overflow=true;
  expect(await jobs()).toEqual(before);
  report.checks.no_jobs_started_or_resumed=true;
  expect(errors).toEqual([]);
  report.checks.no_browser_exceptions=true;
  report.status='passed';
}catch(error){report.status='failed';report.error=error.message;process.exitCode=1;}
finally{
  await browser.close();
  await fs.mkdir(path.join(root,'data/evaluation'),{recursive:true});
  await fs.writeFile(path.join(root,'data/evaluation/video-library-browser.json'),JSON.stringify(report,null,2)+'\n');
}
console.log(JSON.stringify(report,null,2));
