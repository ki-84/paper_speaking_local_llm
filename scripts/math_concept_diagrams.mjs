// Fixed geometric drawings only. Model output supplies meanings, never executable code.
const colors = ["#257f9e", "#c27b24", "#7751a8"];
const esc = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const line = (x1,y1,x2,y2,color=colors[0],extra="") => `<path d="M${x1} ${y1}L${x2} ${y2}" fill="none" stroke="${color}" stroke-width="6" ${extra}/>`;
const arrow = (x1,y1,x2,y2,color=colors[0]) => line(x1,y1,x2,y2,color,`marker-end="url(#head-${colors.indexOf(color)})"`);
const dots = (x,y,count,color,r=15) => Array.from({length:count},(_,i)=>`<circle cx="${x}" cy="${y+i*37}" r="${r}" fill="${color}"/>`).join("");
const weightGrid = (x,y,small=false,transpose=false,color=small?colors[1]:colors[0]) => Array.from({length:small?8:16},(_,i)=>`<rect x="${x+(i%(small&&!transpose?2:4))*25}" y="${y+Math.floor(i/(small&&!transpose?2:4))*25}" width="21" height="21" rx="4" fill="${color}" opacity="${small?.85:.35+(i%4)*.12}"/>`).join("");

export function constraintExample() {
  // Actual least squares for a schematic TWO-dimensional example, not paper measurements.
  const s=Math.SQRT1_2, constraints=[{n:[1,0],p:[130,150]},{n:[0,1],p:[230,100]},{n:[s,s],p:[180,170]}];
  const m=[[0,0],[0,0]], b=[0,0];
  for(const {n,p} of constraints)for(let i=0;i<2;i++){
    b[i]+=n[i]*(n[0]*p[0]+n[1]*p[1]);
    for(let j=0;j<2;j++)m[i][j]+=n[i]*n[j];
  }
  const det=m[0][0]*m[1][1]-m[0][1]*m[1][0];
  const fitted=[(b[0]*m[1][1]-b[1]*m[0][1])/det,(m[0][0]*b[1]-m[1][0]*b[0])/det];
  const residuals=q=>constraints.map(({n,p})=>n[0]*(q[0]-p[0])+n[1]*(q[1]-p[1]));
  return {constraints,initial:[290,200],fitted,residuals,loss:q=>residuals(q).reduce((a,v)=>a+v*v,0)};
}

function fitPicture() {
  const example=constraintExample();
  return [null,example.initial,example.fitted].map((q,index)=>{
    const dx=60+index*550;
    let paths=example.constraints.map(({n,p})=>line(dx+p[0]-n[1]*100,p[1]+n[0]*100,dx+p[0]+n[1]*100,p[1]-n[0]*100,colors[0])+`<circle cx="${dx+p[0]}" cy="${p[1]}" r="6" fill="${colors[0]}"/>`+(index===0?arrow(dx+p[0],p[1],dx+p[0]+n[0]*40,p[1]+n[1]*40):'')).join("");
    if(q){
      for(const {n,p} of example.constraints){
        const d=n[0]*(q[0]-p[0])+n[1]*(q[1]-p[1]);
        paths+=line(dx+q[0],q[1],dx+q[0]-d*n[0],q[1]-d*n[1],"#a94949",'stroke-dasharray="8 7"');
      }
      paths+=`<circle cx="${dx+q[0]}" cy="${q[1]}" r="14" fill="${colors[1]}" stroke="white" stroke-width="4"/>`;
    }
    if(index<2)paths+=arrow(dx+420,145,dx+490,145,colors[index]);
    return paths;
  }).join("");
}

function picture(template) {
  switch(template){
    case "parallel_paths":return dots(85,88,4,colors[0])+arrow(120,100,290,80)+arrow(120,190,565,205,colors[1])+weightGrid(305,38)+arrow(425,92,1295,112)+weightGrid(600,174,true,true)+arrow(710,201,745,201,colors[1])+dots(775,181,2,colors[1])+arrow(800,201,835,201,colors[1])+weightGrid(860,150,true)+arrow(925,201,970,201,colors[1])+dots(1000,143,4,colors[1])+arrow(1035,203,1295,135,colors[1])+`<circle cx="1340" cy="124" r="34" fill="white" stroke="${colors[2]}" stroke-width="5"/><path d="M1320 124H1360M1340 104V144" stroke="${colors[2]}" stroke-width="7"/>`+arrow(1380,124,1540,124,colors[2])+dots(1590,68,4,colors[2])+`<path d="M345 28v-14a22 22 0 0144 0v14" fill="none" stroke="#355363" stroke-width="6"/><rect x="337" y="20" width="60" height="40" rx="8" fill="#355363"/>`;
    case "bottleneck":return dots(240,30,6,colors[0])+arrow(295,126,718,126)+`<path d="M475 30L600 92V163L475 230" fill="#257f9e18" stroke="${colors[0]}" stroke-width="4"/>`+dots(855,95,2,colors[1])+arrow(900,126,1318,126,colors[1])+`<path d="M1220 30L1095 92V163L1220 230" fill="#7751a818" stroke="${colors[2]}" stroke-width="4"/>`+dots(1450,30,6,colors[2]);
    case "weighted_sum":return dots(140,88,3,colors[0])+arrow(185,124,355,124)+`<circle cx="410" cy="124" r="37" fill="white" stroke="${colors[0]}" stroke-width="5"/><path d="M410 124l25-24" stroke="${colors[0]}" stroke-width="7"/><path d="M455 124L570 55L1225 115" fill="none" stroke="${colors[0]}" stroke-width="12" marker-end="url(#head-0)"/>`+dots(740,132,3,colors[1])+arrow(785,170,895,170,colors[1])+`<circle cx="950" cy="170" r="37" fill="white" stroke="${colors[1]}" stroke-width="5"/><path d="M950 170l-25-24" stroke="${colors[1]}" stroke-width="7"/>`+arrow(995,170,1230,145,colors[1])+`<circle cx="1280" cy="124" r="35" fill="white" stroke="${colors[2]}" stroke-width="6"/><path d="M1258 124H1302M1280 102V146" stroke="${colors[2]}" stroke-width="7"/>`+arrow(1325,124,1420,124,colors[2])+dots(1495,86,3,colors[2]);
    case "fit_constraints":return fitPicture();
    case "return_target":return `<circle cx="245" cy="120" r="65" fill="#d3e9ed" stroke="${colors[0]}" stroke-width="6"/><path d="M215 120H275M245 90V150" stroke="${colors[0]}" stroke-width="9"/>`+`<path d="M335 120L420 35H1375L1400 75" fill="none" stroke="${colors[0]}" stroke-width="7" marker-end="url(#head-0)"/>`+`<path d="M665 215H1075" stroke="${colors[1]}" stroke-width="5" stroke-dasharray="8 8"/><rect x="770" y="55" width="170" height="125" rx="18" fill="#fff1d6" stroke="${colors[1]}" stroke-width="5"/><path d="M800 145l30-30 30 15 45-40" fill="none" stroke="${colors[1]}" stroke-width="8"/>`+arrow(975,120,1340,120,colors[1])+`<circle cx="1460" cy="120" r="65" fill="#eee4f2" stroke="${colors[2]}" stroke-width="6"/><path d="M1430 120H1490M1460 90V150" stroke="${colors[2]}" stroke-width="9"/>`;
    case "policy_tradeoff":return dots(235,70,4,colors[0])+[-1,0,1].map(i=>arrow(260,125,360,125+i*65)).join('')+`<rect x="765" y="65" width="185" height="130" rx="18" fill="#fff1d6" stroke="${colors[1]}" stroke-width="5"/><path d="M790 165l30-30 30 15 55-55" fill="none" stroke="${colors[1]}" stroke-width="8"/><path d="M1455 80V215M1360 115H1550M1455 65l-18 25h36Z" stroke="${colors[2]}" stroke-width="7" fill="#eee4f2"/><path d="M1360 115l-45 70h90ZM1550 115l-45 70h90Z" fill="#eee4f2" stroke="${colors[2]}" stroke-width="5"/>`+arrow(385,125,720,125)+arrow(990,125,1300,125,colors[1]);
    case "moving_average":return weightGrid(205,75)+weightGrid(785,75,false,false,colors[1])+`<path d="M350 125L1210 80" fill="none" stroke="${colors[0]}" stroke-width="8"/><path d="M900 170L1210 155" fill="none" stroke="${colors[1]}" stroke-width="8"/><circle cx="1280" cy="120" r="55" fill="#eee4f2" stroke="${colors[2]}" stroke-width="6"/><path d="M1250 120H1310M1280 90V150" stroke="${colors[2]}" stroke-width="7"/>`+arrow(1345,120,1430,120,colors[2])+weightGrid(1470,70,false,false,colors[2]);
    case "relationship":return dots(235,50,5,colors[0])+line(290,124,710,124,colors[0])+`<path d="M790 55L950 55L1010 125L950 195L790 195L730 125Z" fill="#fff5dd" stroke="${colors[1]}" stroke-width="5"/><path d="M815 109H925M815 139H925" stroke="${colors[1]}" stroke-width="7"/>`+line(1015,124,1390,124,colors[2])+dots(1450,50,5,colors[2]);
    default:throw new Error("Unsupported mathematical concept template");
  }
}

function colorEquation(tex,symbols){
  const rules=symbols.flatMap(s=>[s.latex,s.latex.replace(/_\{([A-Za-z0-9])\}/g,'_$1'),s.latex.replace(/_([A-Za-z0-9])/g,'_{$1}')].map(tex=>({tex,color:colors[s.part]}))).sort((a,b)=>b.tex.length-a.tex.length);
  let result="";
  for(let i=0;i<tex.length;){
    const rule=rules.find(r=>tex.startsWith(r.tex,i));
    if(rule){result+=`\\textcolor{${rule.color}}{${rule.tex}}`;i+=rule.tex.length;continue;}
    if(tex[i]==="\\"){
      const command=tex.slice(i).match(/^\\[A-Za-z]+/);
      if(command){
        result+=command[0];i+=command[0].length;
        if(["\\text","\\mathrm","\\operatorname","\\mathbb"].includes(command[0])&&tex[i]==="{"){
          let depth=0;
          do{if(tex[i]==="{")depth++;if(tex[i]==="}")depth--;result+=tex[i++];}while(i<tex.length&&depth);
        }
        continue;
      }
    }
    result+=tex[i++];
  }
  return result;
}

function symbolAnchors(concept){
  const placements=concept.template==='parallel_paths'?
    [['W_0','W₀',355,165,0],['x','x',85,55,0],['A','A',650,260,1],['B','B',885,278,1],['h','h',1590,235,2]]:
    concept.template==='fit_constraints'?
    [['n_i','nᵢ',265,135,0],['q_i','qᵢ',160,190,0],['v','v',905,237,1],['v','v',1320,168,1]]:[];
  return placements.filter(([latex,,,,part])=>(concept.symbols||[]).some(s=>s.latex.replaceAll('{','').replaceAll('}','')===latex&&s.part===part))
    .map(([,label,x,y,part])=>`<text x="${x}" y="${y}" text-anchor="middle" font-family="sans-serif" font-size="34" font-weight="700" fill="${colors[part]}">${label}</text>`).join('');
}

export function renderMathConcept(concept,equation,phase,katex){
  if(!Array.isArray(concept.parts)||concept.parts.length!==3)throw new Error("Concept needs three bilingual parts");
  const render=tex=>katex.renderToString(tex,{displayMode:false,throwOnError:true,trust:false,strict:"warn",maxExpand:1000});
  const symbols=phase==="symbols";
  const defs=colors.map((c,i)=>`<marker id="head-${i}" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto"><path d="M0 0L0 6L8 3Z" fill="${c}"/></marker>`).join("");
  const labels=concept.parts.map((p,i)=>`<section style="--part-color:${colors[i]}"><h2>${esc(p.en)}</h2><p lang="ja">${esc(p.ja)}</p></section>`).join("");
  const legend=(concept.symbols||[]).map(s=>`<div class="symbol-key" style="--part-color:${colors[s.part]}"><strong>${render(s.latex)}</strong><div><span>${esc(s.en)}</span><span lang="ja">${esc(s.ja)}</span></div></div>`).join("");
  const fitting=concept.template==="fit_constraints";
  return `<div class="math-concept ${symbols?'with-symbols':'intuition'}" data-phase="${phase}" data-template="${esc(concept.template)}">
    <div class="concept-picture"><svg viewBox="0 0 1720 280" role="img" aria-label="${esc(concept.parts.map(p=>p.en).join('; '))}"><defs>${defs}</defs>${picture(concept.template)}${symbols?symbolAnchors(concept):''}</svg><div class="concept-parts">${labels}</div></div>
    ${symbols?`<div class="symbol-keys">${legend}</div><div class="mapped-equation">${render(colorEquation(equation.latex,concept.symbols||[]))}</div>`:''}
    <div class="concept-note">${fitting?'2D conceptual example · 2次元の概念例':concept.template==='weighted_sum'?'Positive-weight example · 正の重みを使った概念例':'Schematic dimensions · 次元数は説明用の模式例'}${symbols?' · Match colors to the equation · 色で記号と絵を対応':''}</div></div>`;
}

export const mathConceptCSS = `
.math-concept{position:absolute;top:58px;left:100px;width:1720px;height:510px}
.concept-picture{height:440px;display:flex;flex-direction:column}.concept-picture svg{width:100%;height:330px;flex:none}
.concept-parts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:38px}.concept-parts section{text-align:center;border-top:5px solid var(--part-color);padding:8px 12px 0;min-height:92px;min-width:0}
.concept-parts h2{font-size:26px;line-height:1.5;color:var(--part-color);margin:0 0 5px;overflow-wrap:anywhere}.concept-parts p{font-size:24px;line-height:1.5;margin:0;color:#3c5e55;overflow-wrap:anywhere}
.with-symbols .concept-picture{height:245px}.with-symbols .concept-picture svg{height:155px}.with-symbols .concept-parts h2{font-size:24px}.with-symbols .concept-parts p{font-size:22px}
.symbol-keys{position:absolute;top:252px;width:100%;display:grid;grid-template-columns:repeat(4,1fr);gap:6px 15px}
.symbol-key{display:flex;align-items:center;gap:14px;border-left:5px solid var(--part-color);border-radius:7px;padding:5px 12px;background:#fffef8;height:75px}
.symbol-key strong{font-size:25px;color:var(--part-color);flex:none}.symbol-key div{display:flex;flex-direction:column;font-size:19px;line-height:1.5}.symbol-key span[lang]{font-size:18px;color:#527362}
.mapped-equation{position:absolute;top:418px;left:0;width:100%;height:72px;display:flex;align-items:center;justify-content:center;text-align:center;background:#fff8e6;border-radius:12px;padding:6px 16px;font-size:28px}.mapped-equation>.katex{display:inline-block}
.concept-note{position:absolute;bottom:-13px;width:100%;text-align:center;font-size:19px;color:#547260}
`;
