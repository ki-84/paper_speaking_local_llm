import { useEffect, useState } from "react";
import { ArrowRight, BookOpen, Clapperboard, RotateCcw } from "lucide-react";
import { api, Row } from "./api";

export function LearningHome({version,onError,openLesson,review,viewGeneration,practice}:{version:number;onError:(s:string)=>void;openLesson:(id:string,target?:Row|null)=>void;review:()=>void;viewGeneration:(paper:string)=>void;practice:()=>void}) {
  const [home,setHome]=useState<any>(null);
  useEffect(()=>{let live=true;api<any>("/learning-home").then(h=>{if(live)setHome(h);}).catch(e=>{if(live)onError(e.message);});return()=>{live=false;};},[version]);
  if(!home)return <section className="learning-home" aria-label="学習ホーム"><h1>今日の学習</h1><p>学習の続きと復習を読み込み中…</p></section>;
  return <section className="learning-home" aria-label="学習ホーム">
    <div className="page-heading"><div className="eyebrow">CONTINUE · RECALL · UNDERSTAND</div><h1>今日の学習</h1><p>コースの続きと、思い出す練習をここから。</p></div>
    <div className="home-review-summary"><div><RotateCcw size={28}/><h2>今日の復習 <strong>{home.reviews_due}件</strong></h2><p>答えを見る前に思い出してから、覚えていた程度を選びます。</p></div><button className="primary" onClick={review}>復習を始める <ArrowRight size={17}/></button></div>
    <div className="review-calendar" aria-label="今後7日の復習予定">{home.review_calendar.map((d:any,i:number)=><div key={d.date}><span>{i===0?"今日":new Date(`${d.date}T00:00:00+09:00`).toLocaleDateString("ja-JP",{month:"numeric",day:"numeric",timeZone:"Asia/Tokyo"})}</span><strong>{d.count}</strong><small>件</small></div>)}</div>
    <section aria-label="学習中のコース"><div className="home-section-heading"><h2><BookOpen size={22}/> 学習中のコース</h2><button className="text-button" onClick={practice}>教材一覧</button></div>
      {home.active_courses.length ? <div className="home-course-grid">{home.active_courses.map((c:any)=><article className="home-course" key={c.id}>
        <span className="eyebrow">{c.mode==='overview'?'概要解説':c.mode==='deep_dive'?'詳細解説':'英語コース'}</span><h3>{c.title}</h3>
        <p>続き：{c.resume_chapter} · {c.turn_index+1}文目</p><progress aria-label={`${c.title}の学習進捗`} value={c.chapters_completed} max={c.chapters_total||1}/><small>学習済み {c.chapters_completed} / {c.chapters_total}章{c.chapters_ready<c.chapters_total&&` · 利用できる章 ${c.chapters_ready}`}{c.due_reviews>0&&` · 復習 ${c.due_reviews}件`}</small>
        <div className="actions"><button className="primary" onClick={()=>openLesson(c.id)}>続きから学ぶ <ArrowRight size={16}/></button><button className="text-button" onClick={()=>api(`/lessons/${c.id}/study-state`,{method:'PUT',body:JSON.stringify({state:'paused'})}).then(()=>api('/learning-home').then(setHome)).catch(e=>onError(e.message))}>ホームから外す</button></div>
      </article>)}</div>:<div className="home-empty"><p>学習を始めると、コースと再開する位置がここに表示されます。</p><button className="secondary" onClick={practice}>コースを選ぶ</button></div>}
    </section>
    {home.making_courses.length>0&&<section aria-label="作成中のコース"><div className="home-section-heading"><h2><Clapperboard size={22}/> 作成中のコース</h2></div><div className="home-course-grid">{home.making_courses.map((p:any)=><article className="home-course making" key={p.id}><span className={`badge ${p.job.state}`}>{({running:'作成中',queued:'待機中',failed:'問題あり',paused:'一時停止'} as Record<string,string>)[p.job.state]||p.job.state}</span><h3>{p.title}</h3><p>{p.job.stage}</p><progress value={p.job.progress} max={1}/>
      <div className="home-ready-tracks">{p.tracks.map((t:any)=><div key={t.mode}><span>{t.mode==='overview'?'概要':'詳細'}：{t.ready_chapters} / {t.total_chapters||'—'}章が利用可能</span>{t.ready_chapters>0&&<button className="secondary" onClick={()=>openLesson(t.lesson_id)}>できた章から学ぶ</button>}</div>)}</div><button className="text-button" onClick={()=>viewGeneration(p.paper_id)}>作成状況を見る</button></article>)}</div></section>}
    {!home.active_courses.length&&home.available_courses.length>0&&<section aria-label="学習を始める"><div className="home-section-heading"><h2>学習を始める</h2></div><div className="home-course-grid">{home.available_courses.map((c:any)=><article className="home-course" key={c.id}><h3>{c.title}</h3><p>{c.chapters_ready}章から学習できます。</p><button className="secondary" onClick={()=>openLesson(c.id)}>このコースを始める</button></article>)}</div></section>}
    <p className="subtle">復習間隔は、思い出せた程度と経過時間から調整します。発音の点数とは別に記録します。</p>
  </section>;
}
