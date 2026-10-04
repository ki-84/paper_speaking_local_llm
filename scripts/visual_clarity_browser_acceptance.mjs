// Read-only check of repaired copy/posters and LAN downloads; no generation.
import fs from "node:fs/promises";
import {chromium, expect} from "../frontend/node_modules/@playwright/test/index.mjs";
const projectId=process.argv[2];
if(!projectId)throw new Error("A completed project ID is required");
const base=process.env.PAPERSPEAK_TEST_URL||"https://192.168.10.112:8443";
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1050}});
const report={project_id:projectId,time:new Date().toISOString(),checks:{},exports:[]};
const get=async route=>{const response=await context.request.get(base+"/api"+route);if(!response.ok())throw new Error(route+": "+response.status());return response.json();};
try {
  const before=await get("/jobs"),project=await get("/video-projects/"+projectId),videos=await get("/videos");
  const publication=project.data.publication;
  expect(publication.status).toBe("verified");
  const films=videos.filter(v=>v.project_id===projectId);
  expect(films.length).toBe(2);
  const page=await context.newPage(),errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  await page.goto(base);
  await page.getByRole("searchbox").fill(project.data.paper_title);
  await expect(page.locator(".video-library-item")).toHaveCount(2);
  for(const film of films){
    const card=page.locator(`[data-video-id="${film.id}"]`);
    await expect(card.locator(".video-library-meta")).toContainText(publication.label);
    expect(film.data.title).not.toContain("学会未確認");
    expect(film.data.description).toContain("発表学会："+publication.label);
    expect(film.data.description).not.toMatch(/https?:\/\//);
    const image=card.locator("img");await image.scrollIntoViewIfNeeded();
    await expect.poll(()=>image.evaluate(i=>i.complete&&i.naturalWidth===1280)).toBeTruthy();
    const thumb=await context.request.get(base+"/api/files/"+film.data.thumbnail);
    expect(thumb.status()).toBe(200);
    const mp4=await context.request.get(base+"/api/files/"+film.data.mp4,{headers:{Range:"bytes=0-127"}});
    expect(mp4.status()).toBe(206);expect((await mp4.body()).length).toBe(128);
    await card.getByRole("button",{name:"タイトル・説明",exact:true}).click();
    await expect(page.getByLabel("YouTube用タイトル")).toHaveValue(film.data.title);
    await expect(page.getByLabel("YouTube用説明文")).toHaveValue(film.data.description);
    await page.getByRole("button",{name:"動画を閉じる",exact:true}).click();
    report.exports.push({id:film.id,kind:film.kind,title:film.data.title,conference:publication.label,awards:film.data.awards,thumbnail:film.data.thumbnail,thumbnail_download:200,mp4_range_download:206});
  }
  await page.screenshot({path:"data/evaluation/visual-clarity-library.png",fullPage:true});
  const after=await get("/jobs");
  expect(after.map(j=>[j.id,j.state])).toEqual(before.map(j=>[j.id,j.state]));
  expect(errors).toEqual([]);
  report.checks={official_publication_displayed:true,thumbnail_downloads:true,mp4_range_downloads:true,copy_matches_download_metadata:true,url_free_descriptions:true,no_jobs_started_or_resumed:true,no_browser_exceptions:true};
  report.status="passed";
} finally {
  await fs.writeFile("data/evaluation/visual-clarity-browser.json",JSON.stringify(report,null,2)+"\n");
  await browser.close();
}
console.log(JSON.stringify(report));
