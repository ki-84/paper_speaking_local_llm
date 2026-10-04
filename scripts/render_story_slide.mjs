// Structured data only: no model-generated HTML/JS, no network requests.
import fs from "node:fs/promises";
import path from "node:path";
import { chromium } from "../frontend/node_modules/playwright-core/index.mjs";
import katex from "../frontend/node_modules/katex/dist/katex.mjs";
import {renderMathConcept, mathConceptCSS} from "./math_concept_diagrams.mjs";

const [input, output] = process.argv.slice(2);
const scene = JSON.parse(await fs.readFile(input, "utf8"));
const esc = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const root = new URL("../assets/video/", import.meta.url);
const characters = JSON.parse(await fs.readFile(new URL("characters.json", root), "utf8"));
let css = await fs.readFile(new URL("../frontend/node_modules/katex/dist/katex.min.css", import.meta.url), "utf8");
// Embed font bytes so equation rendering works with all network routes blocked.
const fonts = [...new Set([...css.matchAll(/url\((fonts\/[^)]+)\)/g)].map(m => m[1]))];
for (const name of fonts) {
  const data = await fs.readFile(new URL(`../frontend/node_modules/katex/dist/${name}`, import.meta.url));
  const mime = name.endsWith("woff2") ? "font/woff2" : name.endsWith("woff") ? "font/woff" : "font/ttf";
  css = css.replaceAll(`url(${name})`, `url(data:${mime};base64,${data.toString("base64")})`);
}
const avatars = (await Promise.all(["guide", "host"].map(async role => {
  const person = characters[role];
  const bytes = await fs.readFile(new URL(person.sprite, root));
  return `<div class="avatar ${esc(person.side)}"><img src="data:image/svg+xml;base64,${bytes.toString("base64")}"><div class="nameplate">${esc(person.name)} · ${esc(person.role)}</div></div>`;
}))).join("");
const spec = scene.visual;
const nodes = spec.nodes || [];
const mathematical = (spec.equations || []).length > 0;
const zoom = (spec.zoom_regions||[])[Number(scene.focus||0)-Number(spec.zoom_start||999)];
const showOriginal = !!spec.image_path && (!!zoom || !mathematical || Number(scene.focus||0)===0);
const relativeMathFocus = Math.max(0, Number(scene.focus||0)-(spec.image_path?1:0));
const pairedMath = Array.isArray(spec.concepts) && spec.concepts.length > 0;
const mathFocus = pairedMath ? Math.floor(relativeMathFocus/2) : relativeMathFocus;
const mathPhase = relativeMathFocus % 2 ? 'symbols' : 'intuition';
if(pairedMath && spec.concepts.length !== spec.equations.length)throw new Error('Each equation needs its conceptual picture');
const conceptMath = pairedMath && !showOriginal ? renderMathConcept(spec.concepts[mathFocus],spec.equations[mathFocus],mathPhase,katex) : '';
const columns = Math.min(Math.max(1, nodes.length), 3);
const nodeWidth = (1720 - (columns - 1) * 40) / columns;
const compact = mathematical;
const nodesHTML = nodes.map((node, i) => {
  const top = compact ? 425 : 100 + Math.floor(i / columns) * 240;
  const left = 100 + (i % columns) * (nodeWidth + 40);
  const active = i === Math.min(nodes.length-1, Number(scene.focus || 0));
  return `<div class="node ${active ? "active" : ""} ${compact ? "compact" : ""}" style="left:${left}px;top:${top}px;width:${nodeWidth}px">
    ${node.year ? `<span class="year">${esc(node.year)}</span>` : ""}<h2>${esc(node.en)}</h2><p lang="ja">${esc(node.ja)}</p>
    ${node.matrix ? `<div class="matrix">${esc(JSON.stringify(node.matrix))}</div>` : ""}</div>`;
}).join("");
const arrows = !compact && ["flow", "timeline", "matrix"].includes(spec.type) ? nodes.slice(0, -1).map((_, i) => {
  if ((i+1) % columns === 0) return "";
  return `<span class="arrow" style="left:${100+(i%columns)*(nodeWidth+40)+nodeWidth}px;top:${180+Math.floor(i/columns)*240}px">→</span>`;
}).join("") : "";
const equations = (spec.equations || []).slice(0, 3).map((eq,i) => {
  const rendered = katex.renderToString(eq.latex, {displayMode:true,throwOnError:true,trust:false,strict:"warn",maxExpand:1000});
  return `<div class="equation ${i===mathFocus?'active':''}">${rendered}<p>${esc(eq.en)} <span lang="ja">${esc(eq.ja)}</span></p></div>`;
}).join("");
let pictorial="";
if(spec.template==='surface_cells'){
  if(nodes.length!==3)throw new Error('The surface-cell sketch needs three panels');
  const grid=(sparse)=>{
    let cells='';
    for(let y=0;y<8;y++)for(let x=0;x<8;x++){
      const r=Math.hypot(x-3.5,y-3.5),surface=r>1.7&&r<3.2;
      cells+=`<rect x="${x*43+70}" y="${y*43+20}" width="40" height="40" rx="4" fill="${surface?'#5daebd':sparse?'none':'#c7d3d2'}" stroke="${surface?'#266d80':'#b5c4c2'}" opacity="${surface||!sparse?1:.18}"/>`;
    }
    return `<svg viewBox="0 0 500 390"><g>${cells}</g><circle cx="240" cy="190" r="123" fill="none" stroke="#253e57" stroke-width="5"/><circle cx="240" cy="190" r="75" fill="none" stroke="#253e57" stroke-width="5"/></svg>`;
  };
  const attributes=`<svg viewBox="0 0 500 390"><rect x="120" y="40" width="240" height="240" rx="14" fill="#e2eced" stroke="#376a72" stroke-width="4"/><path d="M130 230 Q245 40 350 130" fill="none" stroke="#276f86" stroke-width="9"/><circle cx="240" cy="134" r="10" fill="#276f86"/><path d="M245 160 L245 305" stroke="#647785" stroke-width="4"/><circle cx="190" cy="327" r="23" fill="#e99c3b"/><circle cx="245" cy="327" r="23" fill="#6088ad"/><circle cx="300" cy="327" r="23" fill="#9a85b5"/></svg>`;
  pictorial=nodes.map((node,i)=>`<section class="picture-panel ${Number(scene.focus||0)===i?'active':''}" style="left:${100+i*584}px"><div>${i===2?attributes:grid(i===1)}</div><h2>${esc(node.en)}</h2><p lang="ja">${esc(node.ja)}</p></section>`).join('');
}
let original = "";
if (spec.image_path) {
  const filename = path.resolve(scene.data_root, spec.image_path);
  if (!filename.startsWith(path.resolve(scene.data_root) + path.sep)) throw new Error("Unsafe source image");
  const bytes = await fs.readFile(filename);
  original = `<img class="original" src="data:image/png;base64,${bytes.toString("base64")}">`;
  if(zoom){
    if(!Array.isArray(zoom.box)||zoom.box.length!==4||zoom.box.some(n=>!Number.isFinite(n)||n<0||n>1)||zoom.box[2]<=0||zoom.box[3]<=0||zoom.box[0]+zoom.box[2]>1.001||zoom.box[1]+zoom.box[3]>1.001)throw new Error('Invalid verified region');
    const source=spec.original_source||{};
    const labelEn=zoom.verified_region_ids?zoom.label_en:String(zoom.label_en||'').split(/\swith\s|[（(]/)[0];
    const labelJa=String(zoom.label_ja||'').split(/[（(]/)[0];
    // Keep the whole source in view so a zoom never loses its location/context.
    original=`<div class="original-window" data-box="${esc(JSON.stringify(zoom.box))}">${original}</div>
      <aside class="source-overview"><div class="source-image">${original}<span class="source-highlight"></span></div><p>${esc(source.label||'Original figure')} ${source.page?`· p.${esc(source.page)}`:''}<br>Zoom location · 拡大位置</p></aside>
      <div class="region-label"><div>${esc(labelEn)}</div><div lang="ja">${esc(labelJa)}</div></div>`;
  }
}
const badge = scene.mode === "overview" ? "THE IDEA · 解説編" : "UNDER THE HOOD · 詳解編";
const content = scene.thumbnail
  ? `<div class="thumbnail"><p>AI PAPERS × REAL ENGLISH</p><h1 lang="ja">${esc(scene.title_ja)}</h1><h2>${esc(scene.title_en)}</h2><span>図解・英日字幕 / ${badge}</span></div>`
  : `<header><div><h1>${esc(scene.title_en)}</h1><p lang="ja">${esc(scene.title_ja)}</p></div><span>${badge}</span></header>
    <main id="diagram"><div class="badge">${spec.type === "example" ? "Hypothetical example · 仮の例" : showOriginal ? "Original paper figure · 論文の原図" : "Teaching diagram · 説明用の補助図"}</div>
    ${showOriginal ? original : conceptMath || pictorial || `${mathematical ? `<div class="equations">${equations}</div>` : ""}${nodesHTML}${arrows}`}
    <div class="caption"><div>${esc(spec.caption_en)}</div><div lang="ja">${esc(spec.caption_ja)}</div></div></main>`;
const html = `<!doctype html><html><head><meta charset="utf-8"><style>${css}${mathConceptCSS}
*{box-sizing:border-box}html,body{width:1920px;height:1080px;margin:0;overflow:hidden}
body{font-family:"Noto Sans CJK JP","Noto Sans",sans-serif;color:#edf4ef;background:#102d2c}
header{height:144px;padding:22px 70px;background:#143b37;display:flex;align-items:center;justify-content:space-between;gap:30px}
header h1{font-size:40px;margin:0;line-height:1.5;max-width:1450px}header p{font-size:25px;line-height:1.5;margin:6px 0 0;color:#bdd5c3}
header span{font-size:20px;color:#f4b950;flex:none}main{position:relative;height:666px;background:#eef3e9;color:#183b35}
.badge{position:absolute;top:23px;left:100px;font-size:20px;font-weight:700;color:#466f5b}
.node{position:absolute;height:190px;padding:22px 26px;border:3px solid #aac2ae;border-radius:18px;background:#fffef8;box-shadow:0 7px 15px #143b3717}
.node.active{border-color:#df9a32;box-shadow:0 0 0 6px #efba5c44,0 10px 25px #143b3720;background:#fff9e9}
.node h2{font-size:30px;line-height:1.5;margin:0 0 12px;overflow-wrap:anywhere}.node p{font-size:24px;line-height:1.5;color:#527362;margin:0}
.node.compact{height:125px;padding:12px 22px}.node.compact h2{font-size:26px;margin-bottom:4px}.node.compact p{font-size:21px}
.arrow{position:absolute;font-size:38px;width:40px;color:#527c68;text-align:center}.year{font-size:24px;color:#bd7c21;font-weight:bold}
.caption{position:absolute;bottom:17px;left:100px;right:100px;text-align:center;font-size:23px;line-height:1.4;color:#3b6654}.caption div[lang]{font-size:22px;color:#5b7768}
.equations{position:absolute;top:65px;left:120px;width:1680px;display:flex;flex-direction:column;gap:10px}
.equation{background:#fffef9;border-left:6px solid #dfa044;border-radius:10px;padding:6px 25px}.equation .katex-display{font-size:32px;margin:5px 0}
.equation.active{background:#fff3d5;box-shadow:0 0 0 3px #efba5c66}
.equation p{font-size:20px;text-align:center;margin:5px 0}.equation p span{margin-left:18px;color:#557966}
.original{position:absolute;top:60px;left:100px;width:1720px;height:490px;object-fit:contain}
.original-window{position:absolute;top:60px;left:100px;width:1720px;height:455px;overflow:hidden;background:white;border-radius:12px}
.source-overview{position:absolute;top:70px;right:100px;width:270px;background:white;border:2px solid #aac2ae;border-radius:10px;padding:8px}
.source-image{position:relative;width:250px}.source-image .original{position:static;display:block;width:250px;height:auto}.source-highlight{position:absolute;border:3px solid #d78418;background:#f3b34822}
.source-overview p{font-size:18px;line-height:1.4;text-align:center;margin:8px 0 0;color:#466f5b}
.region-label{position:absolute;top:521px;left:100px;right:100px;text-align:center;font-size:20px;line-height:1.3;color:#466f5b}
.matrix{font-size:27px;font-family:monospace;margin-top:8px}
.picture-panel{position:absolute;top:80px;width:550px;height:470px;padding:12px 20px;border-radius:18px;background:#ffffff9c;border:3px solid #b9cbbe}
.picture-panel.active{border-color:#df9a32;background:#fff8e8}.picture-panel svg{display:block;width:100%;height:350px}.picture-panel h2{font-size:28px;text-align:center;margin:8px 0 5px;line-height:1.5}.picture-panel p{font-size:23px;text-align:center;color:#527362;margin:0;line-height:1.5}
.captions{height:270px;background:#122e2c;position:relative;border-top:${Number(characters.layout.footer_border)}px solid #d99550}
.avatar{position:absolute;top:10px;width:225px;height:245px;text-align:center;z-index:2}.avatar.left{left:34px}.avatar.right{right:34px}
.avatar img{display:block;width:210px;height:210px;margin:0 auto;image-rendering:pixelated}
.nameplate{position:absolute;bottom:4px;left:17px;width:190px;border:2px solid #9bbaaa;background:#244843;border-radius:7px;color:#fff;font-size:17px;font-weight:700;line-height:22px}
.thumbnail{height:810px;background:linear-gradient(125deg,#143b37,#234d42);padding:95px 110px}.thumbnail p{color:#f4b950;font-size:32px;letter-spacing:.1em}
.thumbnail h1{font-size:90px;margin:35px 0;line-height:1.5;max-width:1650px}.thumbnail h2{font-size:45px;color:#c6dfcd;max-width:1600px}.thumbnail span{font-size:28px;color:#e6b864}
</style></head><body>${content}<footer class="captions">${avatars}</footer></body></html>`;
const browser = await chromium.launch({headless:true});
try {
  const page = await browser.newPage({viewport:{width:1920,height:1080},deviceScaleFactor:1});
  await page.route("**/*", route => route.abort());
  await page.setContent(html);
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(async()=>{
    const box=document.querySelector('.original-window');if(!box)return;
    const img=box.querySelector('img');await img.decode();
    const [x,y,w,h]=JSON.parse(box.dataset.box),pad=.0075;
    const left=Math.max(0,x-pad),top=Math.max(0,y-pad),right=Math.min(1,x+w+pad),bottom=Math.min(1,y+h+pad);
    const availableWidth=1400;
    const scale=Math.min(availableWidth/(img.naturalWidth*(right-left)),box.clientHeight/(img.naturalHeight*(bottom-top)));
    const width=img.naturalWidth*scale,height=img.naturalHeight*scale;
    const croppedWidth=width*(right-left),croppedHeight=height*(bottom-top);
    Object.assign(box.style,{width:croppedWidth+'px',height:croppedHeight+'px',left:100+(availableWidth-croppedWidth)/2+'px',top:60+(455-croppedHeight)/2+'px'});
    Object.assign(img.style,{width:width+'px',height:height+'px',left:-width*left+'px',top:-height*top+'px',objectFit:'fill'});
    const overview=document.querySelector('.source-image img');await overview.decode();
    const highlight=document.querySelector('.source-highlight');
    Object.assign(highlight.style,{left:x*overview.clientWidth+'px',top:y*overview.clientHeight+'px',width:w*overview.clientWidth+'px',height:h*overview.clientHeight+'px'});
  });
  await page.evaluate(() => {
    // Preserve all notation and meanings; reduce type only within a readable bound.
    const formula=document.querySelector('.mapped-equation');
    if(formula)for(let i=0;i<8;i++){
      const child=formula.querySelector('.katex');
      if(child.getBoundingClientRect().width<formula.clientWidth-34&&child.getBoundingClientRect().height<formula.clientHeight-12)break;
      formula.style.fontSize=Math.max(20,parseFloat(getComputedStyle(formula).fontSize)-1)+'px';
    }
    const equations=document.querySelector('.equations');
    if(equations && equations.getBoundingClientRect().height>335) {
      // Large worked matrices are revealed one at a time, keeping readable type.
      for(const eq of equations.children) if(!eq.classList.contains('active'))eq.style.display='none';
    }
    for (const node of document.querySelectorAll(".node")) {
      for (let i=0;i<8;i++) {
        const children=[...node.querySelectorAll("h2,p")];
        if(children.every(e=>e.getBoundingClientRect().bottom<=node.getBoundingClientRect().bottom-10)) break;
        for (const e of children) {const size=parseFloat(getComputedStyle(e).fontSize);e.style.fontSize=Math.max(e.tagName==="H2"?20:18,size-2)+"px";}
      }
    }
  });
  const errors = await page.evaluate(() => {
    const problems=[...document.querySelectorAll(".node h2,.node p,.caption,.equation,.thumbnail h1,header h1,.region-label,.source-overview p,.picture-panel h2,.picture-panel p,.concept-parts h2,.concept-parts p,.symbol-key,.mapped-equation,.concept-note")]
      .filter(e => e.scrollHeight > e.clientHeight + 1 || e.scrollWidth > e.clientWidth + 1).map(e => e.textContent);
    const caption=document.querySelector('.caption')?.getBoundingClientRect();
    for(const node of document.querySelectorAll('.node')) {
      const box=node.getBoundingClientRect();
      if([...node.children].some(e=>e.getBoundingClientRect().bottom>box.bottom-5))problems.push('Node text outside its box');
      if(caption&&box.bottom>caption.top-10)problems.push('Node overlaps caption');
    }
    const equations=document.querySelector('.equations')?.getBoundingClientRect();
    const firstNode=document.querySelector('.node')?.getBoundingClientRect();
    if(equations&&firstNode&&equations.bottom>firstNode.top-10)problems.push('Equations overlap meaning labels');
    const region=document.querySelector('.region-label')?.getBoundingClientRect();
    if(region&&caption&&region.bottom>caption.top-10)problems.push('Region label overlaps caption');
    for(const panel of document.querySelectorAll('.picture-panel'))if(panel.querySelector('p').getBoundingClientRect().bottom>panel.getBoundingClientRect().bottom-8)problems.push('Picture label outside its panel');
    const concept=document.querySelector('.math-concept');
    if(concept){
      const keys=document.querySelector('.symbol-keys')?.getBoundingClientRect(),formula=document.querySelector('.mapped-equation')?.getBoundingClientRect(),note=document.querySelector('.concept-note').getBoundingClientRect();
      const parts=document.querySelector('.concept-parts').getBoundingClientRect();
      if(keys&&parts.bottom>keys.top-4)problems.push('Concept labels overlap symbol meanings');
      if(keys&&formula&&keys.bottom>formula.top-4)problems.push('Symbol meanings overlap equation');
      if(formula&&formula.bottom>note.top-4)problems.push('Equation overlaps concept note');
      if(caption&&note.bottom>caption.top-4)problems.push('Concept note overlaps caption');
      for(const key of document.querySelectorAll('.symbol-key'))if([...key.children].some(c=>c.getBoundingClientRect().bottom>key.getBoundingClientRect().bottom-2))problems.push('Symbol meaning outside its key');
    }
    return problems;
  });
  if (errors.length) throw new Error("Visual text overflow: " + errors.join(" | "));
  await page.screenshot({path:output});
  if(pairedMath){
    const layout=await page.evaluate(()=>{
      const concept=document.querySelector('.math-concept'),formula=document.querySelector('.mapped-equation');
      return {phase:concept?.dataset.phase||'original',template:concept?.dataset.template||null,visible_equations:formula?1:0,visible_symbol_keys:document.querySelectorAll('.symbol-key').length,
        colored_formula_terms:[...document.querySelectorAll('.mapped-equation .katex-html [style*="color"]')].map(e=>({text:e.textContent,color:getComputedStyle(e).color})),
        rendered_formula_text:formula?.querySelector('.katex-html')?.textContent||'',layout_errors:[]};
    });
    await fs.writeFile(output+'.layout.json',JSON.stringify(layout,null,2));
  }
  if (scene.study_output) await page.locator("#diagram").screenshot({path:scene.study_output});
} finally {await browser.close();}
