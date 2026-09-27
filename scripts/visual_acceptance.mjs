// Check a real generated visual lesson over LAN HTTPS; restore the learner's position.
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'frontend/package.json'));
const {chromium, expect} = require('@playwright/test');
const id = process.argv[2];
if (!id) throw new Error('Supply a visual lesson ID with a ready chapter.');
const base = process.env.PAPERSPEAK_URL || 'https://192.168.10.112:8443';
const api = `${base}/api/lessons/${id}`;
const report = {started: new Date().toISOString(), lesson_id: id,
  scope: 'Real local-model assets over LAN HTTPS in Linux Chromium. External browser requests blocked. Does not test a physical Mac or OS-level backend network isolation.', checks: {}};
const browser = await chromium.launch();
const context = await browser.newContext({ignoreHTTPSErrors: true, viewport: {width: 1440, height: 1100}});
const page = await context.newPage();
const errors = [], external = [];
page.on('pageerror', e => errors.push(e.message));
await page.route('**/*', route => {
  const url = route.request().url();
  if (new URL(url).origin === new URL(base).origin) return route.continue();
  external.push(url); return route.abort();
});
let original, detail;
try {
  await page.goto(base);
  if (await page.getByLabel('Studio password').isVisible()) {
    const credentials = JSON.parse(fs.readFileSync(path.join(root, 'data/credentials.json')));
    await page.getByLabel('Studio password').fill(credentials.password);
    await page.getByRole('button', {name: 'Come on in'}).click();
  }
  detail = await (await context.request.get(api)).json();
  original = detail.progress;
  const c = detail.chapters.find(c => c.state === 'ready' && c.data.visuals?.length);
  if (!c) throw new Error('No ready visual chapter.');
  const assets = new Map(detail.visuals.map(a => [a.id, a]));
  const refs = new Map(c.data.visuals.map(r => [r.key, assets.get(r.asset_id)]));
  if (![...refs.values()].some(a => a.kind === 'original') || ![...refs.values()].some(a => a.kind !== 'original'))
    throw new Error('Acceptance requires both a paper figure and a teaching diagram.');
  const turns = c.data.turns;
  let prior = -1, start = -1, i = -1;
  for (let n = 0; n < turns.length; n++) {
    if (!turns[n].visual) continue;
    if (prior >= 0 && turns[n].visual.key !== turns[prior].visual.key) {start=prior; i=n; break;}
    prior = n;
  }
  if (i < 0) throw new Error('Need spoken turns that change the displayed visual.');
  const progress = {chapter_id:c.id, turn_index:start, role:'both', speed:1, subtitles:true, visual_mode:'auto', visual_key:null};
  await context.request.put(api+'/progress', {data:progress});
  await page.locator(`[data-lesson-id="${id}"]`).click();
  const panel = page.getByRole('region', {name:'Lesson visuals'});
  const before = refs.get(turns[start].visual.key), after = refs.get(turns[i].visual.key);
  await expect(panel.getByRole('heading', {name:before.data.title_en, exact:true})).toBeVisible();
  await page.getByRole('button', {name:'Listen to chapter', exact:true}).click();
  await expect.poll(() => page.locator('.sentence-card audio').evaluate(a => a.currentTime)).toBeGreaterThan(0);
  // Wait for the real audio ended event, not a synthetic event.
  await expect(panel.getByRole('heading', {name:after.data.title_en, exact:true})).toBeVisible({timeout:90000});
  await page.getByRole('button', {name:'Pause', exact:true}).click();
  report.checks.real_audio_auto_visual_switch = true;
  await expect(panel.getByText(after.data.description_ja, {exact:true})).toBeVisible();
  await expect(page.locator('.sentence-translation')).not.toBeEmpty();
  report.checks.japanese_ready_from_start = true;
  await panel.locator('.visual-choices button').filter({hasText:before.data.title_en}).click();
  await expect.poll(async () => (await (await context.request.get(api)).json()).progress.visual_key).toBe(turns[start].visual.key);
  await page.reload();
  await page.locator(`[data-lesson-id="${id}"]`).click();
  await expect(panel.getByRole('heading', {name:before.data.title_en, exact:true})).toBeVisible();
  await panel.getByRole('button', {name:'Auto · 自動表示', exact:true}).click();
  await expect(panel.getByRole('heading', {name:after.data.title_en, exact:true})).toBeVisible();
  report.checks.pin_reload_and_return_to_auto = true;
  const focused = turns.findIndex((t,n) => n >= i && t.visual?.key === turns[i].visual.key && t.visual?.focus?.length);
  if (focused < i) throw new Error('Need a reviewed original region to test highlighting.');
  for (let n=i; n<focused; n++) await page.getByRole('button', {name:'Next sentence',exact:true}).click();
  await expect.poll(() => panel.locator('.visual-highlight,.visual-focus-path').count()).toBeGreaterThan(0);
  report.checks.real_original_region_highlight = true;
  for (const a of refs.values()) {
    await panel.locator('.visual-choices button').filter({hasText:a.data.title_en}).click();
    await expect.poll(() => panel.locator('.visual-picture img').evaluate(i => i.complete && i.naturalWidth > 0)).toBeTruthy();
    if (a.kind === 'original') {
      await panel.getByRole('button', {name:'Original page · 原ページ'}).click();
      await expect(panel.locator('.visual-picture img')).toHaveAttribute('src', '/api/files/'+a.data.full_page_path);
      await expect.poll(() => panel.locator('.visual-picture img').evaluate(i => i.complete && i.naturalWidth > 0)).toBeTruthy();
      await panel.getByRole('button', {name:'Figure · 図に戻る'}).click();
    }
    await panel.getByRole('button', {name:'Enlarge · 拡大'}).click();
    await expect(page.getByRole('dialog', {name:'Enlarged lesson figure'})).toBeVisible();
    if (a.id === refs.get(turns[focused].visual.key).id) {
      await page.getByLabel('Figure zoom',{exact:true}).selectOption('2');
      await page.getByRole('button',{name:'Focus here · 注目箇所へ',exact:true}).click();
      await expect.poll(() => page.locator('.visual-zoom-view').evaluate(e => e.scrollTop+e.scrollLeft)).toBeGreaterThan(0);
      report.checks.real_zoom_and_focus = true;
      await page.getByLabel('Figure zoom',{exact:true}).selectOption('1');
      await page.locator('.visual-zoom-view').evaluate(e => e.scrollTo(0,0));
    }
    await page.screenshot({path:path.join(root, `data/evaluation/visual-${a.kind}-${a.id}.png`)});
    await page.keyboard.press('Escape');
  }
  report.checks.all_images_original_pages_and_zoom = true;
  await panel.getByRole('button', {name:'Auto · 自動表示', exact:true}).click();
  await expect(panel.getByRole('heading', {name:refs.get(turns[focused].visual.key).data.title_en, exact:true})).toBeVisible();
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(root,'data/evaluation/visual-study-viewport.png')});
  await page.screenshot({path:path.join(root,'data/evaluation/visual-study.png'), fullPage:true});
  if (errors.length || external.length) throw new Error(JSON.stringify({errors, external}));
  report.checks.no_external_browser_requests_or_exceptions = true;
  report.chapter_id = c.id;
  report.visual_ids = c.data.visuals;
  report.status = 'passed';
} catch (e) {
  report.status = 'failed'; report.error = e.message; process.exitCode = 1;
} finally {
  await page.goto('about:blank');
  if (detail) {
    const progress = original?.chapter_id ? original : {chapter_id:detail.chapters[0].id,turn_index:0};
    report.progress_restored = (await context.request.put(api+'/progress',{data:progress})).ok();
  }
  await browser.close();
  report.finished = new Date().toISOString();
  fs.writeFileSync(path.join(root,'data/evaluation/visual-browser.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
}
