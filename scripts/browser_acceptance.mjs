// Exercise the installed studio with its real, verified lesson assets.
// No recording is created; physical microphone acceptance remains separate.
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'frontend/package.json'));
const {chromium, expect} = require('@playwright/test');
const lessonId = process.argv[2];
if (!lessonId) throw new Error('Supply a lesson ID with a ready chapter.');
const base = process.env.PAPERSPEAK_URL || 'https://192.168.10.112:8443';
const credentials = JSON.parse(fs.readFileSync(path.join(root, 'data/credentials.json')));
const report = {started: new Date().toISOString(), lesson_id: lessonId,
  scope: 'Real lesson, source image, audio playback, hints and saved position in local Chromium. No physical second PC or microphone. TLS trust is checked separately with the local CA.', checks: {}};
const browser = await chromium.launch({headless: true});
const context = await browser.newContext({ignoreHTTPSErrors: true, viewport: {width: 1440, height: 1000}});
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
let original;
let detail;
const api = `/api/lessons/${lessonId}`;
try {
  await page.goto(base);
  if (credentials.password_required !== false) {
    await page.getByLabel('Studio password').fill(credentials.password);
    await page.getByRole('button', {name: 'Come on in'}).click();
  }
  await expect(page.locator('.lesson-card').first()).toBeVisible();
  detail = await (await page.request.get(base + api)).json();
  original = detail.progress;
  const chapter = detail.chapters.find(c => c.state === 'ready');
  if (!chapter) throw new Error('No verified chapter is ready.');
  const index = chapter.data.turns.findIndex(t => t.kind === 'paper' && t.source_ids?.length);
  if (index < 0) throw new Error('No paper claim with original evidence.');
  const progress = {chapter_id: chapter.id, turn_index: index, role: 'both', speed: 0.9, subtitles: true};
  await page.request.put(base + api + '/progress', {data: progress});
  await page.locator('.lesson-card').filter({hasText: detail.data.title}).first().click();
  await expect(page.locator('.spoken-sentence')).toHaveText(chapter.data.turns[index].text);
  const translated = chapter.data.translation?.items?.['turn:' + chapter.data.turns[index].id];
  if (translated?.english === chapter.data.turns[index].text) {
    await expect(page.locator('.sentence-translation')).toHaveText(translated.japanese);
    await expect(page.locator('.spoken-sentence')).toHaveText(chapter.data.turns[index].text);
    report.checks.local_japanese_aid = true;
  }
  await page.getByRole('button', {name: 'Hear it', exact: true}).click();
  await expect.poll(() => page.locator('.sentence-card audio').evaluate(a => a.currentTime)).toBeGreaterThan(0);
  report.checks.real_audio_played = true;
  await page.getByRole('button', {name: 'Source 1', exact: true}).click();
  await expect(page.locator('.source-text')).not.toBeEmpty();
  await expect.poll(() => page.locator('.source-drawer img').evaluate(i => i.complete && i.naturalWidth > 0)).toBeTruthy();
  report.checks.original_page_and_text = true;
  await page.screenshot({path: path.join(root, 'data/evaluation/production-source.png')});
  await page.getByRole('button', {name: 'Close source'}).click();
  await page.getByRole('button', {name: 'Next sentence'}).click();
  const nextText = chapter.data.turns[index + 1].text;
  await expect(page.locator('.spoken-sentence')).toHaveText(nextText);
  await expect.poll(async () => (await (await page.request.get(base + api)).json()).progress.turn_index).toBe(index + 1);
  await page.reload();
  await page.locator('.lesson-card').filter({hasText: detail.data.title}).first().click();
  await expect(page.locator('.spoken-sentence')).toHaveText(nextText);
  report.checks.position_restored = true;
  await page.locator('.question').first().click();
  await page.getByRole('button', {name: 'A little help'}).click();
  await expect(page.locator('.hint').first()).toContainText(chapter.data.questions[0].hints[0]);
  report.checks.real_question_hint = true;
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({path: path.join(root, 'data/evaluation/production-study.png'), fullPage: true});
  if (errors.length) throw new Error(errors.join('\n'));
  report.checks.no_browser_exceptions = true;
  report.status = 'passed';
} catch (error) {
  report.status = 'failed';
  report.error = error.message;
  process.exitCode = 1;
} finally {
  if (detail) {
    // Leave the learner at the position they had before this check.
    const progress = original?.chapter_id ? original : {chapter_id: detail.chapters[0].id, turn_index: 0};
    await page.goto('about:blank');
    const restored = await context.request.put(base + api + '/progress', {data: progress});
    report.progress_restored = restored.ok();
  }
  await browser.close();
  report.finished = new Date().toISOString();
  fs.writeFileSync(path.join(root, 'data/evaluation/production-browser.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
}
