// Fixed composition and local fonts. Never execute model-produced markup/code.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {chromium} from '../frontend/node_modules/playwright-core/index.mjs';
const [input,output]=process.argv.slice(2),spec=JSON.parse(await fs.readFile(input,'utf8'));
const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
async function data(file){return `data:image/${file.endsWith('.svg')?'svg+xml':'png'};base64,${(await fs.readFile(file)).toString('base64')}`;}
// Keep fixed 2D rendering off the inference GPU. Screenshot capture can fail
// transiently during a compositor frame; retry at most three captures locally.
const browser=await chromium.launch({headless:true,args:['--disable-gpu']});
try{
 const page=await browser.newPage({viewport:{width:spec.portrait?512:1280,height:spec.portrait?512:720},deviceScaleFactor:1});
 await page.route('**/*',r=>r.abort());
 async function screenshot(options){
  await page.evaluate(async()=>{await document.fonts.ready;await Promise.all([...document.images].map(i=>i.decode()));});
  for(let attempt=0;attempt<3;attempt++){
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   try{return await page.screenshot(options);}
   catch(error){if(attempt===2||!String(error).includes('Unable to capture screenshot'))throw error;}
  }
 }
 if(spec.portrait){
  const original=await data(path.resolve('assets/video',spec.role==='guide'?'maya.svg':'aiden.svg'));
  await page.setContent(`<body style="margin:0;background:${spec.reference?'white':'transparent'}"><canvas width="64" height="64" style="width:512px;height:512px;image-rendering:pixelated"></canvas></body>`);
  const generated=spec.generated?await data(spec.generated):null;
  await page.evaluate(async({original,generated,surprise})=>{
   const load=async src=>{const im=new Image();im.src=src;await im.decode();return im;};
   const ctx=document.querySelector('canvas').getContext('2d');ctx.imageSmoothingEnabled=false;ctx.drawImage(await load(original),0,0,64,64);
   const source=ctx.getImageData(0,0,64,64);
   if(generated){
    const c=document.createElement('canvas');c.width=c.height=64;const g=c.getContext('2d');g.imageSmoothingEnabled=false;g.drawImage(await load(generated),0,0,64,64);const pixels=g.getImageData(0,0,64,64);
    const palette=new Map();for(let i=0;i<source.data.length;i+=4)if(source.data[i+3]){const rgb=[...source.data.slice(i,i+3)];palette.set(rgb.join(','),rgb);}
    for(const [x,y,w,h] of [[23,25,19,9],[27,35,13,9]])for(let yy=y;yy<y+h;yy++)for(let xx=x;xx<x+w;xx++){
     const i=(yy*64+xx)*4;if(!source.data[i+3])continue;
     const rgb=[...palette.values()].sort((a,b)=>a.reduce((s,v,k)=>s+(v-pixels.data[i+k])**2,0)-b.reduce((s,v,k)=>s+(v-pixels.data[i+k])**2,0))[0];source.data.set(rgb,i);
    }
    ctx.putImageData(source,0,0);
   }else if(surprise){
    ctx.fillStyle='#f6c49b';ctx.fillRect(28,35,11,9);ctx.fillStyle='#743b43';ctx.fillRect(30,36,7,8);ctx.fillStyle='#f6c49b';ctx.fillRect(32,38,3,4);
   }
  },{original,generated,surprise:spec.surprise});
  await screenshot({path:output,omitBackground:!spec.reference});
 }else{
  const identity=spec.identity;
  if(!identity?.paper_title||!identity?.conference||!['概要解説','詳細解説'].includes(identity?.edition))throw new Error('Missing paper/conference/edition identity');
  const award=identity.awards?.[0];
  const bg=spec.background?await data(spec.background):null;
  const maya=await data(spec.maya),aiden=await data(spec.aiden);
  const rays=Array.from({length:24},(_,i)=>`<i style="transform:translate(-50%,-50%) rotate(${i*15}deg)"></i>`).join('');
  await page.setContent(`<!doctype html><html><head><meta charset="utf-8"><style>
   *{box-sizing:border-box}body{margin:0;width:1280px;height:720px;overflow:hidden;background:${spec.palette==='violet'?'#301456':spec.palette==='orange'?'#713619':'#073b47'};font-family:'Noto Sans CJK JP',sans-serif}
   .rays{position:absolute;inset:0;overflow:hidden}.rays i{position:absolute;left:50%;top:60%;width:2300px;height:28px;background:#ffffff12}
   .idea{position:absolute;left:260px;top:185px;width:760px;height:445px;object-fit:cover;border:6px solid #ffdc57;border-radius:35px;box-shadow:0 0 60px #ffe56566}
   .portrait{position:absolute;top:225px;width:480px;height:480px;image-rendering:pixelated;filter:drop-shadow(0 8px 0 #081222) drop-shadow(0 0 8px white)}
   .maya{left:-55px}.aiden{right:-55px}.bang{position:absolute;top:280px;font-size:120px;color:#ffe549;font-weight:900;-webkit-text-stroke:5px #101a33;text-shadow:5px 7px #111c2b}.bang.left{left:300px}.bang.right{right:285px}
   .paper-name{position:absolute;left:24px;right:24px;top:8px;height:120px;padding:10px 18px;background:#08162ff2;border:2px solid #94d4dd;border-radius:16px;display:flex;align-items:center;justify-content:center;text-align:center;color:white}
   .paper-name span{display:block;width:100%;font-size:44px;font-weight:900;line-height:1.12;overflow-wrap:anywhere;text-wrap:balance}
   .identity{position:absolute;left:36px;right:36px;top:138px;height:54px;display:flex;justify-content:space-between;gap:24px;align-items:center}
   .edition,.conference{font-size:40px;line-height:1.15;font-weight:900;padding:3px 18px;border:3px solid #0b1930;border-radius:12px;background:#fff;color:#10213c;white-space:nowrap}
   .edition{font-size:44px;background:${identity.edition==='概要解説'?'#65efe1':'#ffcd6a'}}.conference{color:white;background:#142748}
   .title{position:absolute;left:36px;right:36px;top:198px;text-align:center;line-height:1.08;font-size:88px;font-weight:1000;color:#ffec58;-webkit-text-stroke:7px #10172c;paint-order:stroke fill;text-shadow:0 8px #10172c,0 11px 15px #0009}
   .title span{display:block}.title span:last-child{color:white}.tag{position:absolute;bottom:18px;left:440px;right:440px;text-align:center;font-size:30px;font-weight:900;color:#142340;background:#ffe962;border:4px solid #142340;border-radius:12px;padding:8px;white-space:nowrap}
   .name{position:absolute;bottom:20px;color:white;font-size:25px;font-weight:900;background:#101c39cc;padding:4px 18px;border-radius:10px}.name.m{left:88px}.name.a{right:88px}
   .award{position:absolute;left:350px;right:350px;top:497px;height:128px;padding:7px 12px;background:#ffec80f7;color:#10213c;border:4px solid #15213d;border-radius:16px;text-align:center;box-shadow:0 4px 0 #15213d;display:flex;flex-direction:column;justify-content:center}
   .award-label{font-size:32px;font-weight:900;line-height:1.15;text-wrap:balance}.award-name{font-size:26px;font-weight:800;line-height:1.15;text-wrap:balance;overflow-wrap:anywhere}
  </style></head><body><div class="rays">${rays}</div>${bg?`<img class="idea" src="${bg}">`:`<div class="idea" style="background:linear-gradient(145deg,#f7d75a,#74d8d1);display:flex;align-items:center;justify-content:center;font-size:64px;font-weight:900;color:#122f41">${esc(spec.topic||'AI × NEW IDEA')}</div>`}
  <img class="portrait maya" src="${maya}"><img class="portrait aiden" src="${aiden}"><div class="bang left">!?</div><div class="bang right">!!</div>
  <div class="paper-name"><span>${esc(identity.paper_title)}</span></div><div class="identity"><div class="edition">${esc(identity.edition)}</div><div class="conference">${esc(identity.conference)}</div></div>
  <div class="title">${spec.lines.map(s=>`<span>${esc(s)}</span>`).join('')}</div>${award?`<div class="award"><div class="award-label">${esc(award.label)}</div><div class="award-name">${esc(award.name)}</div></div>`:''}<div class="tag">${String(spec.identity?.edition||'').includes('日本語')?'日本語で学ぶAI':'英語で学ぶAI'}</div><div class="name m">MAYA</div><div class="name a">AIDEN</div></body></html>`);
  await page.evaluate(()=>document.fonts.ready);
  await page.evaluate(()=>{
   const paper=document.querySelector('.paper-name'),name=paper.firstElementChild;
   for(let size=44;size>=24;size-=1){name.style.fontSize=size+'px';if(name.scrollWidth<=name.clientWidth&&name.offsetHeight<=paper.clientHeight-20)break;}
   const box=document.querySelector('.title');for(let i=0;i<25;i++){if([...box.children].every(e=>e.scrollWidth<=e.clientWidth)&&box.offsetHeight<=190)break;box.style.fontSize=parseFloat(getComputedStyle(box).fontSize)-2+'px';}
   const award=document.querySelector('.award');if(award){for(let i=0;i<14;i++){if(award.scrollHeight<=award.clientHeight&&[...award.children].every(e=>e.scrollWidth<=e.clientWidth))break;for(const e of award.children)e.style.fontSize=(parseFloat(getComputedStyle(e).fontSize)-1)+'px';}}
  });
  const overflow=await page.evaluate(()=>{
   const name=document.querySelector('.paper-name'),text=name.firstElementChild,title=document.querySelector('.title'),row=document.querySelector('.identity');
   const award=document.querySelector('.award');
   return (award&&(award.scrollHeight>award.clientHeight||[...award.children].some(e=>e.getBoundingClientRect().top<award.getBoundingClientRect().top+4||e.getBoundingClientRect().bottom>award.getBoundingClientRect().bottom-4)))||text.offsetHeight>name.clientHeight-20||title.offsetHeight>190||row.children[0].getBoundingClientRect().right>row.children[1].getBoundingClientRect().left-16||[...document.querySelectorAll('.paper-name span,.title span,.edition,.conference,.tag,.award-label,.award-name')].some(e=>e.scrollWidth>e.clientWidth||e.getBoundingClientRect().bottom>720);
  });
  if(overflow)throw new Error('Thumbnail title overflows');
  await screenshot({path:output});
  const rendered=await page.evaluate(()=>({
   paper_title:document.querySelector('.paper-name span').textContent,
   conference:document.querySelector('.conference').textContent,
   edition:document.querySelector('.edition').textContent,
   award_name:document.querySelector('.award-name')?.textContent||null,
   award_label:document.querySelector('.award-label')?.textContent||null,
  }));
  const sha=async file=>createHash('sha256').update(await fs.readFile(file)).digest('hex');
  await fs.writeFile(output.replace(/\.png$/,'.verification.json'),JSON.stringify({
   version:'thumbnail-dom-check-1',rendered,resolution:[1280,720],
   no_text_overflow:true,external_requests_blocked:true,
   png_sha256:await sha(output),input_sha256:await sha(input),
   renderer_sha256:await sha(fileURLToPath(import.meta.url)),
  }));
 }
}finally{await browser.close();}
