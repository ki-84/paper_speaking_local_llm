// Fixed slide template. All text is escaped and all images are local data URLs.
import fs from "node:fs/promises";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { chromium } from "../frontend/node_modules/playwright-core/index.mjs";

const [input, output] = process.argv.slice(2);
const scene = JSON.parse(await fs.readFile(input, "utf8"));
const charactersRoot = new URL("../assets/video/", import.meta.url);
const characters = JSON.parse(await fs.readFile(new URL("characters.json", charactersRoot), "utf8"));
const font = execFileSync("fc-match", ["Noto Sans CJK JP", "--format=%{family}"], {encoding:"utf8"});
if (!font.includes("Noto Sans CJK JP")) throw new Error("Install local fonts-noto-cjk for video slides.");
const esc = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
const asset = scene.asset;
let image = "";
if (asset?.image_path) {
  if (!scene.data_root) throw new Error("Video data directory is missing.");
  const bytes = await fs.readFile(path.join(scene.data_root, asset.image_path));
  const mime = asset.image_path.endsWith(".svg") ? "image/svg+xml" : "image/png";
  const src = `data:${mime};base64,${bytes.toString("base64")}`;
  const focus = (asset.regions || []).filter(region => (scene.focus || []).includes(region.id));
  image = `<div class="artwork"><img src="${src}" alt="" />${focus.map(region => region.points
    ? `<svg class="focus-path" viewBox="0 0 1 1" preserveAspectRatio="none"><polyline points="${region.points.map(p => p.map(Number).join(",")).join(" ")}" /></svg>`
    : `<div class="focus-box" style="left:${Number(region.box[0])*100}%;top:${Number(region.box[1])*100}%;width:${Number(region.box[2])*100}%;height:${Number(region.box[3])*100}%"></div>`).join("")}</div>`;
}
const kind = !asset ? "Paper conversation · 論文の会話" : asset.kind === "original"
  ? `${esc(asset.label)} · Original paper figure · 論文の原図 · p.${esc(asset.page)}`
  : asset.kind === "example" ? "Hypothetical example · 仮の例" : "Teaching diagram · 説明用の補助図";
const paperSize = Array.from(scene.paper_title || "").length > 90 ? 58 : 76;
const avatars = (await Promise.all(["guide", "host"].map(async role => {
  const person = characters[role];
  const bytes = await fs.readFile(new URL(person.sprite, charactersRoot));
  return `<div class="avatar ${esc(person.side)}"><img src="data:image/svg+xml;base64,${bytes.toString("base64")}" alt="" /><div class="nameplate">${esc(person.name)} · ${esc(person.role)}</div></div>`;
}))).join("");
const footer = `<footer class="captions">${avatars}</footer>`;
const titleCard = `<main class="title-page"><div class="intro-meta"><span>PaperSpeak</span><span>CHAPTER ${Number(scene.ordinal)+1} / 第${Number(scene.ordinal)+1}章</span></div>
<div class="intro-copy"><div class="intro-kicker">A PAPER, EXPLAINED IN CONVERSATION · 会話で論文を学ぶ</div>
<h1 style="font-size:${paperSize}px">${esc(scene.paper_title)}</h1><div class="intro-rule"></div>
<h2>${esc(scene.chapter_title_en)}</h2><p lang="ja">${esc(scene.chapter_title_ja)}</p></div></main>${footer}`;
const lessonSlide = `<header class="top"><div class="titles"><h1>${esc(scene.chapter_title_en)}</h1><p lang="ja">${esc(scene.chapter_title_ja)}</p></div><div class="chapter-number">Chapter ${Number(scene.ordinal)+1}</div></header>
<main class="figure-zone"><div class="kind">${kind}</div><div class="figure-content">${image || `<div class="empty">${esc(scene.paper_title)}</div>`}
${asset ? `<aside class="description"><h2>${esc(asset.title_en)}</h2><p class="ja-title" lang="ja">${esc(asset.title_ja)}</p><p>${esc(asset.description_en)}</p><p class="ja-desc" lang="ja">${esc(asset.description_ja)}</p>${(scene.focus || []).map(id => (asset.regions || []).find(r => r.id === id)).filter(Boolean).map(r => `<div class="focus-label">${esc(r.label_en)}<br /><span lang="ja">${esc(r.label_ja)}</span></div>`).join("")}</aside>` : ""}</div></main>${footer}`;
const html = `<!doctype html><html lang="en"><head><meta charset="utf-8" /><style>
*{box-sizing:border-box}html,body{width:1920px;height:1080px;margin:0;overflow:hidden}
body{background:#f1f3e9;color:#183630;font-family:"Noto Sans CJK JP","Noto Sans",sans-serif}
.top{height:144px;padding:26px 70px 15px;background:#143b37;color:#f7f7ee;display:flex;justify-content:space-between;gap:60px;align-items:center}
.titles{min-width:0;max-width:1440px}.titles h1{font-size:45px;line-height:1.22;margin:0;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.titles p{font-size:27px;line-height:1.3;margin:5px 0 0;color:#d7e6db;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chapter-number{flex:none;font-size:23px;letter-spacing:.1em;text-transform:uppercase;color:#d7e6db}
.figure-zone{height:666px;padding:25px 75px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px}
.kind{font-size:23px;font-weight:700;color:#476655;text-align:center;white-space:nowrap}
.figure-content{width:100%;display:flex;align-items:center;justify-content:center;gap:55px}
.artwork{position:relative;display:inline-block;max-width:1210px;max-height:580px;line-height:0;box-shadow:0 8px 30px #18363020;background:white;padding:10px;border:1px solid #b9cbbd;flex:none}
.artwork img{display:block;max-width:1188px;max-height:558px;width:auto;height:auto;object-fit:contain}
.focus-box{position:absolute;border:5px solid #ed9c40;background:#efa54b40;border-radius:10px;box-shadow:0 0 0 3px #fff9}
.focus-path{position:absolute;inset:10px;width:calc(100% - 20px);height:calc(100% - 20px);overflow:visible}
.focus-path polyline{fill:none;stroke:#e88328;stroke-width:.008;stroke-linejoin:round;stroke-linecap:round}
.description{width:440px;flex:none;color:#334f45;line-height:1.35}
.description h2{font-size:34px;line-height:1.2;margin:0 0 12px}
.description .ja-title{font-size:26px;font-weight:700;color:#527065;margin:0 0 24px}
.description p{font-size:22px;margin:0 0 15px}
.description .ja-desc{color:#527065}
.focus-label{padding:12px 15px;border-left:6px solid #e88328;background:#fff8e8;font-size:21px;color:#5a3921}
.empty{font-family:Georgia,serif;font-size:72px;font-style:italic;color:#527065}
.captions{height:270px;background:#122e2c;position:relative;border-top:${Number(characters.layout.footer_border)}px solid #d99550}
.avatar{position:absolute;top:10px;width:225px;height:245px;text-align:center;z-index:2}
.avatar.left{left:34px}.avatar.right{right:34px}
.avatar img{display:block;width:210px;height:210px;margin:0 auto;image-rendering:pixelated}
.nameplate{position:absolute;bottom:4px;left:17px;width:190px;border:2px solid #9bbaaa;background:#244843;border-radius:7px;color:#fff;font-size:17px;font-weight:700;line-height:22px}
.title-page{height:810px;padding:58px 100px 55px;background:radial-gradient(circle at 93% 9%,#64857477 0 0.5%,transparent 35%),linear-gradient(135deg,#e9f0df,#f8f7ee 58%,#e4eade);position:relative;overflow:hidden}
.title-page:after{content:"";position:absolute;width:640px;height:640px;border:3px solid #8aab8b66;border-radius:50%;right:-200px;top:230px;box-shadow:0 0 0 55px #8aab8b22,0 0 0 115px #8aab8b17;pointer-events:none}
.intro-meta{display:flex;justify-content:space-between;align-items:center;font-size:23px;font-weight:700;letter-spacing:.15em;color:#315c4d}
.intro-copy{position:relative;z-index:1;max-width:1570px;margin-top:100px}
.intro-kicker{font-size:22px;font-weight:700;letter-spacing:.1em;color:#547d66}
.intro-copy h1{max-width:1550px;margin:24px 0 18px;line-height:1.15;letter-spacing:-.03em;color:#123b34;overflow-wrap:anywhere}
.intro-rule{width:135px;height:7px;background:#d29148;border-radius:5px;margin:36px 0 28px}
.intro-copy h2{font-size:43px;line-height:1.25;margin:0 0 10px;color:#24483e}
.intro-copy p{font-size:30px;line-height:1.25;margin:0;color:#557361}
</style></head><body>${scene.title_card ? titleCard : lessonSlide}</body></html>`;
const browser = await chromium.launch({headless:true});
try {
  const page = await browser.newPage({viewport:{width:1920,height:1080},deviceScaleFactor:1});
  await page.route("**/*", route => route.abort());
  await page.setContent(html);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({path:output});
} finally {await browser.close();}
