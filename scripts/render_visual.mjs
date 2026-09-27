// Model output never becomes executable HTML: Python supplies a fixed SVG template.
import fs from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { chromium } from "../frontend/node_modules/playwright-core/index.mjs";

const [input, output] = process.argv.slice(2);
const svg = await fs.readFile(input, "utf8");
const font = execFileSync('fc-match', ['Noto Sans CJK JP', '--format=%{family}'], {encoding:'utf8'});
if (!font.includes('Noto Sans CJK JP')) throw new Error('Install the local fonts-noto-cjk package before rendering diagrams.');
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ deviceScaleFactor: 1.5 });
  await page.route("**/*", route => route.abort());
  await page.setContent(`<style>body{margin:0}svg{display:block}</style>${svg}`);
  await page.evaluate(() => document.fonts.ready);
  const errors = await page.evaluate(() => {
    const errors = [];
    for (const group of document.querySelectorAll("[data-node]")) {
      const r = group.querySelector("rect").getBBox();
      for (const text of group.querySelectorAll("text")) {
        const t = text.getBBox();
        if (t.x < r.x || t.y < r.y || t.x+t.width > r.x+r.width || t.y+t.height > r.y+r.height)
          errors.push(`Label exceeds node ${group.id}`);
      }
    }
    const canvas = document.querySelector('svg').viewBox.baseVal;
    const texts = [...document.querySelectorAll('svg text')].map(t => t.getBBox());
    for (let i = 0; i < texts.length; i++) {
      const a = texts[i];
      if (a.x < 0 || a.y < 0 || a.x+a.width > canvas.width || a.y+a.height > canvas.height)
        errors.push('A label exceeds the image boundary');
      for (const b of texts.slice(i+1)) {
        if (Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x) > 1 && Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y) > 1)
          errors.push('Diagram labels overlap; shorten or rearrange the labels');
      }
    }
    return errors;
  });
  if (errors.length) throw new Error(errors.join("; "));
  await page.locator("svg").screenshot({ path: output });
} finally {
  await browser.close();
}
