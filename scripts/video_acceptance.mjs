// Exercise the real LAN video download and browser decoding without changing learning progress.
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'frontend/package.json'));
const {chromium, expect} = require('@playwright/test');
const id = process.argv[2];
if (!id) throw new Error('Supply a visual lesson ID.');
const base = process.env.PAPERSPEAK_URL || 'https://192.168.10.112:8443';
const fileUrl = relative => '/api/files/' + relative.split('/').map(encodeURIComponent).join('/');
const report = {lesson_id:id, started:new Date().toISOString(), checks:{}};
const browser = await chromium.launch();
const context = await browser.newContext({ignoreHTTPSErrors:true, viewport:{width:1440,height:1100}});
const page = await context.newPage();
const external = [], errors = [];
page.on('pageerror', error => errors.push(error.message));
await page.route('**/*', route => {
  if (new URL(route.request().url()).origin === new URL(base).origin) return route.continue();
  external.push(route.request().url()); return route.abort();
});
try {
  await page.goto(base);
  if (await page.getByLabel('Studio password').isVisible()) {
    const credentials = JSON.parse(fs.readFileSync(path.join(root, 'data/credentials.json')));
    await page.getByLabel('Studio password').fill(credentials.password);
    await page.getByRole('button',{name:'Come on in'}).click();
  }
  const lesson = await (await context.request.get(`${base}/api/lessons/${id}`)).json();
  const video = lesson.videos.find(v => v.kind === 'chapter' && v.state === 'ready');
  if (!video) throw new Error('A completed chapter video is needed.');
  const openingEn = await (await context.request.get(base+fileUrl(video.data.en_srt))).text();
  const openingJa = await (await context.request.get(base+fileUrl(video.data.ja_srt))).text();
  if (!openingEn.split('\n\n')[0].includes('paper titled') || !openingJa.split('\n\n')[0].includes('論文'))
    throw new Error('The spoken opening title needs matching English and Japanese captions.');
  report.checks.spoken_title_has_bilingual_captions = true;
  await page.locator(`[data-lesson-id="${id}"]`).click();
  const panel = page.getByRole('region',{name:'YouTube video export'});
  await expect(panel).toBeVisible();
  const chapter = lesson.chapters.find(c => c.id === video.chapter_id);
  const suggestedTitle = video.data.title || `${lesson.paper.title} — 第${chapter.ordinal + 1}章`;
  await expect(panel.getByLabel('推奨タイトル（YouTube用）')).toHaveValue(suggestedTitle);
  if (video.data.title && !video.data.mp4.endsWith('/' + suggestedTitle + '.mp4'))
    throw new Error('The stored MP4 filename does not match its suggested title.');
  await context.grantPermissions(['clipboard-read','clipboard-write'], {origin:new URL(base).origin});
  await panel.getByRole('button',{name:'タイトルをコピー'}).click();
  if (await page.evaluate(() => navigator.clipboard.readText()) !== suggestedTitle)
    throw new Error('The suggested YouTube title did not copy.');
  const firstDownload = panel.getByRole('link',{name:/Download first MP4/});
  await expect(firstDownload).toBeVisible();
  if (await firstDownload.getAttribute('href') !== fileUrl(video.data.mp4))
    throw new Error('The content video button does not lead to the completed MP4.');
  if (await firstDownload.getAttribute('download') !== suggestedTitle + '.mp4')
    throw new Error('The downloaded filename does not match its suggested title.');
  const download = page.getByRole('link',{name:/Download chapter MP4/});
  await expect(download).toBeVisible();
  const href = await download.getAttribute('href');
  if (href !== fileUrl(video.data.mp4)) throw new Error('The displayed download points to the wrong chapter video.');
  const ranged = await context.request.get(base+href,{headers:{Range:'bytes=0-1023'}});
  if (ranged.status() !== 206 || (await ranged.body()).length !== 1024) throw new Error('LAN video range download failed.');
  report.checks.ranged_https_download = true;
  const media = await page.evaluate(async src => {
    const player = document.createElement('video');
    player.preload = 'metadata'; player.src = src; document.body.appendChild(player);
    await new Promise((resolve,reject) => {player.onloadedmetadata=resolve;player.onerror=()=>reject(new Error('Video decoder rejected the MP4'));});
    const value = {duration:player.duration,width:player.videoWidth,height:player.videoHeight};
    player.remove(); return value;
  },href);
  if (Math.abs(media.duration - video.data.duration) > .2 || media.width !== 1920 || media.height !== 1080)
    throw new Error('Browser-decoded video metadata disagrees with the export.');
  report.checks.browser_decodes_1080p_mp4 = media;
  if (errors.length || external.length) throw new Error(JSON.stringify({errors,external}));
  report.checks.no_external_requests_or_page_errors = true;
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(root,'data/evaluation/video-lesson-browser.png')});
  report.status='passed';
} catch(error) {
  report.status='failed';report.error=error.message;process.exitCode=1;
} finally {
  await browser.close();
  report.finished=new Date().toISOString();
  fs.writeFileSync(path.join(root,'data/evaluation/video-browser.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
}
