// Read-only checks of the deployed conference catalogue. No selection buttons.
import fs from 'node:fs/promises';
import {chromium, expect} from '../frontend/node_modules/@playwright/test/index.mjs';

const base=process.env.PAPERSPEAK_TEST_URL||'https://192.168.10.112:8443';
const browser=await chromium.launch({headless:true,args:['--disable-gpu']});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1050}});
const page=await context.newPage();
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
const report={checked_at:new Date().toISOString(),base,checks:{},sources:[]};
try {
  const response=await context.request.get(base+'/api/conference-awards');
  expect(response.ok()).toBeTruthy();
  const catalogue=await response.json();
  await page.goto(base);
  const panel=page.getByRole('region',{name:'学会別の受賞論文'});
  await expect(panel.getByRole('table')).toBeVisible();
  await expect(panel.locator('.award-total')).toContainText(`${catalogue.winner_count}件の受賞情報 / ${catalogue.paper_count}本`);
  await panel.screenshot({path:'data/evaluation/conference-awards-overview.png'});
  await panel.getByText('受賞論文の一覧と取得状況',{exact:true}).click();
  await expect(panel.getByRole('article')).toHaveCount(catalogue.sources.length);
  for(const receipt of catalogue.sources){
    const {venue,year}=receipt.source;
    const card=panel.getByRole('article',{name:`${venue} ${year}の受賞情報`});
    await expect(card.locator('.award-coverage-status')).toContainText(`受賞情報 ${receipt.winner_count}件 / 論文 ${receipt.paper_count}本`);
    if(receipt.coverage?.missing_categories?.length){
      await card.getByText(`受賞者を未取得の賞（${receipt.coverage.missing_categories.length}件）`,{exact:true}).click();
      for(const prize of receipt.coverage.missing_categories){
        await expect(card.getByRole('link',{name:prize.name,exact:true})).toHaveAttribute('href',prize.official_url);
      }
    }
    if(receipt.local_ai){
      const trace=card.locator('.award-ai-research');
      await trace.locator(':scope > summary').click();
      await expect(trace.locator('p')).toHaveCount(receipt.local_ai.records?.length||0);
      for(const record of receipt.local_ai.records||[]){
        if(record.reason_ja)await expect(trace.locator('p').filter({hasText:record.reason_ja}).first()).toBeVisible();
      }
    }
    if(receipt.papers.length){
      await card.getByText(`受賞論文を表示（${receipt.paper_count}本）`,{exact:true}).click();
      for(const paper of receipt.papers){
        const row=card.locator('li').filter({has:page.getByText(paper.title,{exact:true})}).filter({has:page.getByRole('link',{name:paper.name,exact:true})});
        await expect(row).toHaveCount(1);
        if(paper.evidence_type==='institution')await expect(row).toContainText('所属機関の受賞発表');
        await expect(row.getByRole('link',{name:paper.name,exact:true})).toHaveAttribute('href',paper.official_url);
      }
    }
    report.sources.push({venue,year,status:receipt.status,winner_count:receipt.winner_count,paper_count:receipt.paper_count,official_links_checked:receipt.papers.length,missing_prizes_checked:receipt.coverage?.missing_categories?.length||0,institutional_winners_checked:receipt.papers.filter(p=>p.evidence_type==='institution').length,local_ai_units_checked:receipt.local_ai?.units||0});
  }
  // Keep one expanded list on the screenshot and collapse the rest.
  while(await panel.locator('article details[open]').count())await panel.locator('article details[open]').first().locator(':scope > summary').click();
  const selected=catalogue.sources.find(r=>r.source.venue==='ICRA'&&r.papers.length);
  await panel.getByRole('button',{name:'ICRAの受賞情報を見る'}).click();
  const icra=panel.getByRole('article',{name:`ICRA ${selected.source.year}の受賞情報`});
  await icra.getByText(`受賞論文を表示（${selected.paper_count}本）`,{exact:true}).click();
  await icra.screenshot({path:'data/evaluation/conference-awards-icra.png'});
  await panel.screenshot({path:'data/evaluation/conference-awards-lan.png'});
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>panel.evaluate(element=>element.scrollWidth<=element.clientWidth)).toBeTruthy();
  await panel.screenshot({path:'data/evaluation/conference-awards-mobile.png'});
  expect(errors).toEqual([]);
  report.winner_count=catalogue.winner_count;
  report.paper_count=catalogue.paper_count;
  report.checks={coverage_table_visible_without_expanding:true,all_enabled_editions_visible:true,counts_match_saved_receipts:true,all_winner_titles_and_official_links_visible:true,small_screen_has_no_horizontal_overflow:true,known_missing_prizes_are_visible:true,institutional_evidence_is_labeled:true,local_ai_research_records_visible:true,no_browser_errors:true,read_only:true};
  report.status='passed';
} catch(error) {
  report.status='failed';
  report.error=error.message;
  process.exitCode=1;
} finally {
  await fs.writeFile('data/evaluation/award-collection-browser.json',JSON.stringify(report,null,2)+'\n');
  await browser.close();
}
console.log(JSON.stringify({status:report.status,winner_count:report.winner_count,paper_count:report.paper_count,checks:report.checks,error:report.error}));
