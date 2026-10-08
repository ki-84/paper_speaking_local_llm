import { useRef, useState } from "react";
import { RotateCcw, Volume2 } from "lucide-react";
import { fileUrl, post, Row } from "./api";

export function reviewInterval(seconds:number){
  if(seconds<3600)return `${Math.max(1,Math.round(seconds/60))}分後`;
  if(seconds<86400)return `${Math.round(seconds/3600)}時間後`;
  return `${Math.round(seconds/86400)}日後`;
}
const labels:Record<string,string>={again:'忘れた',hard:'難しい',good:'思い出せた',easy:'簡単'};

export function ReviewSession({card,onError,onRated,disabled=false,practice}:{card:Row;onError:(s:string)=>void;onRated:(result:any)=>void;disabled?:boolean;practice?:()=>void}){
  const [revealed,setRevealed]=useState(false),[busy,setBusy]=useState(false),[result,setResult]=useState<any>(null);
  const events=useRef<Record<string,string>>({});
  const sound=useRef<HTMLAudioElement|null>(null);
  const rate=async(rating:string)=>{
    if(busy||result||disabled)return;setBusy(true);
    try{
      events.current[rating] ||= crypto.randomUUID();
      const value=await post(`/reviews/${encodeURIComponent(card.id)}/complete`,{rating,event_id:events.current[rating],revision:card.revision});
      setResult(value);onRated(value);
    }catch(e){onError((e as Error).message);}finally{setBusy(false);}
  };
  return <article className="review-session" aria-label="思い出す練習">
    <div className="eyebrow">RECALL BEFORE YOU LOOK · 思い出す練習</div>
    <h3>{card.chapter_title||'Review'}</h3><p>{card.prompt_en||'Try to explain this idea before looking at the answer.'}</p>
    {card.cue_ja&&<p className="japanese-aid" lang="ja">{card.cue_ja}</p>}
    <div className="actions">{!revealed&&<button className="primary" disabled={disabled} onClick={()=>setRevealed(true)}>答えを確認する</button>}{practice&&<button className="secondary" onClick={practice}>録音して練習</button>}</div>
    {revealed&&<div className="review-answer"><p>{card.answer_en}</p>{card.answer_ja&&card.answer_ja!==card.cue_ja&&<p className="japanese-aid" lang="ja">{card.answer_ja}</p>}{card.audio&&<><button className="text-button" disabled={disabled} onClick={()=>sound.current?.play().catch(e=>onError(e.message))}><Volume2 size={16}/> お手本を聞く</button><audio ref={sound} src={fileUrl(card.audio)} preload="none"/></>}</div>}
    {result?<p className="review-scheduled" role="status">記録しました。次の復習：{new Date(result.due*1000).toLocaleString('ja-JP',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Tokyo'})}（{reviewInterval(result.seconds)}）</p>:<><p className="subtle">答えを見る前に、どのくらい思い出せましたか？</p><div className="review-ratings">{(card.options||[]).map((o:any)=><button className={`secondary rating-${o.rating}`} key={o.rating} disabled={!revealed||busy||disabled} onClick={()=>rate(o.rating)}><strong>{labels[o.rating]}</strong><small>{reviewInterval(o.seconds)}</small></button>)}</div></>}
  </article>;
}

export function ReviewQueue({cards,onError,refresh,openLesson}:{cards:Row[];onError:(s:string)=>void;refresh:()=>void;openLesson:(id:string,target?:Row|null)=>void}){
  const [notice,setNotice]=useState<any>(null);
  return <section aria-label="今日の復習"><div className="page-heading"><div className="eyebrow">RECALL · CHECK · RETURN</div><h1>今日の復習</h1><p>先に声に出すか、頭の中で答えてから、回答例を確認してください。</p></div>
    {notice&&<p className="review-scheduled" role="status">記録しました。次の復習は{reviewInterval(notice.seconds)}です。</p>}
    {cards.length?<div className="review-queue">{cards.map(r=><div key={r.id}><p className="review-course-title">{r.lesson_data?JSON.parse(r.lesson_data).title:''}</p><ReviewSession card={r} onError={onError} onRated={r=>{setNotice(r);refresh();}} practice={()=>openLesson(r.lesson_id,r)}/></div>)}</div>:<div className="home-empty"><RotateCcw size={28}/><h2>今の復習は完了です。</h2><p>学習した章や追加した表現は、次の予定に合わせてここに戻ってきます。</p></div>}
  </section>;
}
