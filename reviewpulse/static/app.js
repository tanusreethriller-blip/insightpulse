'use strict';
const $ = (id) => document.getElementById(id);
const state = {cfg:null,jobs:[],job:null,sources:[],page:'overview',reviewPage:0,busy:false,approvalPreview:null,reconciliation:null};
const pageText = {
  overview:['THE CUSTOMER PERSPECTIVE','Your feedback, in focus.','Turn what customers are saying into what your team does next.','Overview'],
  reviews:['LISTEN CLOSELY','Every review. The full context.','Find the customer voices behind your themes and trends.','Review explorer'],
  report:['FROM EVIDENCE TO ACTION','A pulse worth sharing.','Check the evidence, review the exact payload, and make the final call.','Reports & approval'],
  knowledge:['GROUNDED IN YOUR PRODUCT','Give your answers a source.','Build a focused library of policies and product documentation.','Knowledge base'],
  activity:['YOUR WORKSPACE, CONNECTED','A clear trail. A connected workflow.','Configure n8n, inspect publishing outcomes, and follow every decision.','Activity & setup']
};
function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function fmt(value) { return Number(value || 0).toLocaleString(); }
function dateLabel(value) { if(!value)return ''; return new Date(value.slice(0,10)+'T12:00:00').toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'}); }
function pct(n,d) { return d ? Math.round(100*n/d) : 0; }
function safeLink(url) { try { const u=new URL(url); return ['http:','https:'].includes(u.protocol) ? esc(u.href) : ''; } catch { return ''; } }
function pill(status) { const c=['ready','published','done'].includes(status)?'mint':['failed','needs_attention','uncertain','rejected'].includes(status)?'danger':['queued','running','publishing'].includes(status)?'lavender':'';return `<span class="pill ${c}">${esc(status.replaceAll('_',' '))}</span>`; }
let toastTimer;
function toast(message) { $('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,7000); }
async function api(path, options={}) {
  const headers={...(options.body ? {'Content-Type':'application/json'}:{}),...(options.headers||{})};
  const r=await fetch('/api'+path,{credentials:'same-origin',...options,headers});
  let data;try {data=await r.json();}catch {throw new Error('The server returned an unreadable response.');}
  if(!r.ok)throw new Error(data.detail || 'The request failed.');
  return data;
}
async function guarded(fn) { try {await fn();}catch(e){toast(e.message||'Something went wrong.');} }
async function submitForm(form, fn) {
  const button=form.querySelector('button[type="submit"]');const previous=button.textContent;button.disabled=true;button.textContent='Working...';
  try {await fn();}catch(e){toast(e.message);}finally{button.disabled=false;button.textContent=previous;}
}
function showWorkspace(){ $('sidebar-product').textContent=state.cfg.product_name;$('product-initial').textContent=state.cfg.product_name[0].toUpperCase();$('mode-label').textContent=state.cfg.analysis_mode==='local'?'Local rules \u00b7 no API key':'LLM-assisted analysis'; }
function showPage(page,updateHash=true) {
  if(!pageText[page])page='overview';state.page=page;
  for(const name of Object.keys(pageText))$('page-'+name).hidden=name!==page;
  document.querySelectorAll('.nav-item[data-page]').forEach(e=>e.classList.toggle('active',e.dataset.page===page));
  const [eyebrow,title,description,breadcrumb]=pageText[page];$('page-eyebrow').textContent=eyebrow;$('page-title').textContent=title;$('page-description').textContent=description;$('breadcrumb-page').textContent=breadcrumb;
  if(updateHash)history.replaceState(null,'','#'+page+(state.job?'/'+state.job.id:''));
  if(page==='knowledge')guarded(loadSources);
  if(page==='activity')guarded(loadActivity);
  if(page==='reviews')renderReviews();
}
function renderJobOptions(){
  $('job-select').innerHTML=state.jobs.length?state.jobs.map(j=>`<option value="${esc(j.id)}">${esc(j.name)} \u00b7 ${esc(j.status)}</option>`).join(''):'<option value="">No analysis runs yet</option>';
  $('job-select').value=state.job?.id||'';
}
async function loadJobs(preferred){
  state.jobs=(await api('/jobs')).jobs;
  const id=preferred||state.job?.id||state.jobs[0]?.id;
  if(id && state.jobs.some(j=>j.id===id))await loadJob(id);else{state.job=null;renderAll();}
  renderJobOptions();
}
async function loadJob(id){
  state.job=await api('/jobs/'+encodeURIComponent(id));
  state.reviewPage=0;renderAll();
  history.replaceState(null,'','#'+state.page+'/'+state.job.id);
}
function renderAll(){
  const job=state.job, result=job?.result;
  $('run-status').outerHTML=`<span id="run-status">${pill(job?.approval?.status||job?.status||'no data')}</span>`;
  const working=job&&['queued','running'].includes(job.status);
  $('processing-banner').hidden=!working;
  if(working){$('processing-title').textContent=job.status==='queued'?'Your analysis is queued':'Finding the signal';$('processing-detail').textContent=job.progress;}
  $('empty-overview').hidden=!!result||!!working;
  $('overview-content').hidden=!result;
  if(result)renderOverview(result);
  if(job?.status==='failed')toast(job.error||'The analysis failed.');
  $('theme-filter').innerHTML='<option value="all">All themes</option>'+(result?.metrics.themes||[]).map(t=>`<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('');
  renderReviews();renderReport();
}
function trendSVG(weekly){
  const left=33,right=586,top=12,bottom=143;
  const x=i=>weekly.length===1?310:left+i*(right-left)/(weekly.length-1);
  const y=r=>bottom-(r-1)*(bottom-top)/4;
  const maximum=Math.max(1,...weekly.map(w=>w.count));
  const grids=[1,2,3,4,5].map(r=>`<line x1="${left}" y1="${y(r)}" x2="600" y2="${y(r)}" stroke="#eef0f5" stroke-dasharray="3 4"/><text x="9" y="${y(r)+3}">${r}</text>`).join('');
  const barWidth=Math.min(24,(right-left)/Math.max(weekly.length,1)*.45);
  const bars=weekly.map((w,i)=>`<rect x="${x(i)-barWidth/2}" y="${bottom-w.count/maximum*48}" width="${barWidth}" height="${w.count/maximum*48}" rx="3" fill="#eae9f2"><title>${esc(w.week)}: ${w.count} reviews</title></rect>`).join('');
  let segments=[],segment=[];
  weekly.forEach((w,i)=>{if(w.average_rating===null){if(segment.length)segments.push(segment);segment=[];}else segment.push(`${x(i)},${y(w.average_rating)}`);});
  if(segment.length)segments.push(segment);
  const paths=segments.map(s=>`<polyline points="${s.join(' ')}" fill="none" stroke="#ac9acb" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>`).join('');
  const points=weekly.filter(w=>w.average_rating!==null).map(w=>{const i=weekly.indexOf(w);return `<circle cx="${x(i)}" cy="${y(w.average_rating)}" r="3" fill="#fff" stroke="#ac9acb" stroke-width="2"><title>${esc(w.week)}: ${w.average_rating}/5, ${w.count} reviews</title></circle>`;}).join('');
  const stride=Math.ceil(weekly.length/6);
  const labels=weekly.map((w,i)=>i%stride===0||i===weekly.length-1?`<text x="${x(i)}" y="166" text-anchor="middle">${esc(new Date(w.week+'T12:00:00').toLocaleDateString(undefined,{day:'numeric',month:'short'}))}</text>`:'').join('');
  return `<svg class="trend-svg" viewBox="0 0 615 178" role="img" aria-label="Weekly average star rating from one to five, with relative review-volume bars">${grids}${bars}${paths}${points}${labels}</svg>`;
}
function renderOverview(r){
  const m=r.metrics,s=m.sentiment;const issues=m.themes.filter(t=>t.negative_count>0).slice(0,3);
  const positive=pct(s.positive,m.review_count),neutral=pct(s.neutral,m.review_count);
  const demo=r.is_demo?'<span class="pill sand">SYNTHETIC DEMO</span>':'';
  const coverage=m.weekly.filter(w=>w.count>0).length;
  const metric=(label,value,foot,icon)=>`<article class="metric"><div class="metric-label">${label}<span class="metric-icon">${icon}</span></div><div class="metric-value">${value}</div><div class="metric-foot">${foot}</div></article>`;
  $('overview-content').innerHTML=`
    <div class="compact-banner"><span>${demo} <span>${esc(dateLabel(m.date_from))} &ndash; ${esc(dateLabel(m.date_to))}</span></span><span>${r.mode==='local'?'Keyword-based classification':'LLM-assisted classification'} &middot; one primary theme</span></div>
    <div class="metric-grid">
      ${metric('Reviews analyzed',fmt(m.review_count),'<span class="pill mint">Validated</span> '+fmt(r.validation.input_rows)+' rows submitted','&#9776;')}
      ${metric('Average rating',m.average_rating.toFixed(2)+' <small>/ 5</small>','Across the full retained review period','&#9734;')}
      ${metric('Negative feedback',m.negative_share+'<small>%</small>',fmt(s.negative)+' reviews rated 1&ndash;2 stars','&#8600;')}
      ${metric('Themes identified',m.themes.length,'Across '+coverage+' represented weeks','&#9638;')}
    </div>
    <div class="chart-grid"><section class="panel"><div class="panel-heading"><div><h2>How the experience is trending</h2><p>Weekly average rating, grounded in your review data.</p></div><div class="chart-legend"><span><i class="legend-dot l-purple"></i>Avg. rating</span><span><i class="legend-dot l-gray"></i>Relative volume</span></div></div>${trendSVG(m.weekly)}<div class="chart-footer">Weeks begin Monday. Empty weeks are not assigned a rating. Volume bars are relative, not on the rating scale.</div></section>
    <section class="panel"><div class="panel-heading"><div><h2>The sentiment mix</h2><p>How customers rate their experience.</p></div></div><div class="sentiment-hero"><div class="donut" style="background:conic-gradient(#a8c5b5 0 ${positive}%,#e7c4a5 ${positive}% ${positive+neutral}%,#b7a9ce ${positive+neutral}% 100%)" role="img" aria-label="${s.positive} positive, ${s.neutral} neutral, ${s.negative} negative reviews"><div class="donut-inner">${fmt(m.review_count)}<small>TOTAL REVIEWS</small></div></div><div class="sentiment-legend"><div class="sentiment-row"><span><i class="legend-dot l-green"></i>Positive</span><strong>${positive}%</strong></div><div class="sentiment-row"><span><i class="legend-dot l-orange"></i>Neutral</span><strong>${neutral}%</strong></div><div class="sentiment-row"><span><i class="legend-dot l-purple"></i>Negative</span><strong>${pct(s.negative,m.review_count)}%</strong></div></div></div><div class="chart-footer">4&ndash;5 stars positive &middot; 3 neutral &middot; 1&ndash;2 negative.<br>These labels come from ratings, not inferred emotion.</div></section></div>
    <div class="section-heading"><div><h2>What needs your attention</h2><p>Leading themes ranked by 1&ndash;2 star review count.</p></div><button class="text-link" data-page="reviews">Explore all reviews &rarr;</button></div>
    <div class="theme-grid">${issues.map((t,i)=>`<article class="theme-card"><div class="theme-card-head"><span class="theme-rank">SIGNAL 0${i+1}</span><span class="pill ${t.change_pp>0?'sand':'mint'}" title="Change in negative-theme reviews as a share of all reviews, latest four weeks versus previous four weeks">${t.change_pp===null?'No baseline':(t.change_pp>0?'+':'')+t.change_pp.toFixed(1)+' pp'}</span></div><h3>${esc(t.name)}</h3><div class="theme-desc">${t.negative_count} negative reviews &middot; ${t.count} total in this theme</div><div class="theme-bar"><i style="width:${t.share}%"></i></div><p class="quote">&ldquo;${esc(t.quotes[0]?.text||'')}&rdquo;</p><div class="theme-foot"><span>${esc(t.quotes[0]?.review_id||'')}</span><span>${t.share}% of all reviews</span></div></article>`).join('')||'<div class="panel">No 1-2 star reviews in this run. Review the broader feedback before drawing conclusions.</div>'}</div>
    <section class="panel"><div class="panel-heading"><div><h2>A report with receipts</h2><p>Exact review quotes, reproducible metrics, and support evidence from your source library.</p></div><button class="button secondary small-button" data-page="report">Review your pulse &rarr;</button></div><div class="checks"><span class="pill mint">${r.validation.accepted_rows} valid reviews</span><span class="pill">${r.validation.dropped_rows} dropped</span><span class="pill">${r.validation.redacted_rows} rows masked</span><span class="pill lavender">Human approval required</span></div><p class="small subtle">${esc(r.validation.warnings.join(' ')||'Descriptive results only. Theme assignment and source relevance still need human review.')} Basic PII masking is not complete anonymization.</p></section>`;
}
function renderReviews(){
  const r=state.job?.result;
  if(!r){$('review-table').innerHTML='<div class="small-empty">Select a completed analysis to explore its reviews.</div>';$('review-count').textContent='No data';$('review-page-label').textContent='';$('reviews-prev').disabled=true;$('reviews-next').disabled=true;return;}
  const query=$('review-search').value.toLowerCase(),sentiment=$('sentiment-filter').value,theme=$('theme-filter').value;
  const filtered=r.reviews.filter(v=>(!query||v.text.toLowerCase().includes(query))&&(theme==='all'||v.theme_id===theme)&&(sentiment==='all'||sentiment==='negative'&&v.rating<=2||sentiment==='neutral'&&v.rating===3||sentiment==='positive'&&v.rating>=4));
  const maxPage=Math.max(0,Math.ceil(filtered.length/30)-1);state.reviewPage=Math.min(state.reviewPage,maxPage);
  const begin=state.reviewPage*30,shown=filtered.slice(begin,begin+30),themes=Object.fromEntries(r.metrics.themes.map(t=>[t.id,t.name]));
  $('review-count').textContent=fmt(filtered.length)+' reviews';
  $('review-table').innerHTML=shown.length?`<table><thead><tr><th>REVIEW</th><th>RATING</th><th>PRIMARY THEME</th><th>DATE</th></tr></thead><tbody>${shown.map(v=>`<tr><td class="text-cell">${esc(v.text)}<span class="review-id">${esc(v.review_id)}</span></td><td><span class="stars" aria-label="${v.rating} out of five stars">${'&#9733;'.repeat(v.rating)}${'&#9734;'.repeat(5-v.rating)}</span></td><td><span class="pill">${esc(themes[v.theme_id])}</span></td><td>${esc(dateLabel(v.date))}</td></tr>`).join('')}</tbody></table>`:'<div class="small-empty">No reviews match these filters.</div>';
  $('review-page-label').textContent=filtered.length?`${begin+1}-${Math.min(begin+30,filtered.length)} of ${fmt(filtered.length)}`:'0 reviews';
  $('reviews-prev').disabled=state.reviewPage===0;$('reviews-next').disabled=state.reviewPage>=maxPage;
}
function renderReport(){
  const job=state.job,r=job?.result;
  if(!r){$('report-content').innerHTML='<div class="empty-state"><h2>Start with an analysis.</h2><p>Your report and exact publishing preview will appear here once the job is ready.</p><button class="button primary" data-action="upload">New analysis</button></div>';return;}
  const a=job.approval,target=r.payload.target;
  const flow=(label,done,n)=>`<div class="flow-step ${done?'done':''}"><span class="flow-number">${done?'&#10003;':n}</span>${label}</div>`;
  // Object key order is not semantically meaningful; compare individual destination fields.
  const targetMatch=Object.keys(target).every(k=>target[k]===state.cfg.target[k]);
  const canApprove=job.status==='ready'&&!a&&target.mode!=='disabled'&&targetMatch;
  $('report-content').innerHTML=`
    ${r.is_demo?'<div class="notice lavender"><p>Synthetic demo report. Its publishing destination is always local, never your live Google account.</p></div>':''}
    ${a?.error?`<div class="notice"><div><strong>Publishing needs a check</strong><p>${esc(a.error)}</p></div></div>`:''}
    ${!targetMatch?'<div class="notice"><p>The saved publishing target differs from this deployment. Create a new analysis for your current configuration. To publish a demo locally, run in local publishing mode.</p></div>':''}
    <div class="report-layout"><section class="panel"><div class="panel-heading"><div><h2>${esc(r.product.name)} &middot; Customer feedback pulse</h2><p>${esc(dateLabel(r.metrics.date_from))} &ndash; ${esc(dateLabel(r.metrics.date_to))}</p></div>${pill(a?.status||job.status)}</div><pre class="report-paper" id="report-text"></pre></section>
    <aside class="panel report-actions"><div class="eyebrow">THE PUBLISHING CHECKPOINT</div><h2>Review. Then release.</h2><p class="small">Only the exact saved payload can be approved.</p>${flow('Report prepared',true,1)}${flow('Human approved',!!a,2)}${flow('Document appended',a?.doc_state==='done',3)}${flow('Email draft created',a?.draft_state==='done',4)}<div class="target-box"><p><strong>Mode:</strong> ${esc(target.mode)} ${target.mode==='local'?'(no external writes)':''}</p><p><strong>Document:</strong> ${esc(target.document_id||'not configured')}</p><p><strong>Recipient:</strong> ${esc(target.recipient||'not configured')}</p></div><button id="approve-button" class="button primary" ${canApprove?'':'disabled'}>${target.mode==='local'?'Approve & publish locally':'Approve & create draft'}</button><button id="reject-button" class="button secondary" ${job.status==='ready'&&!a?'':'disabled'}>Reject this report</button><a class="button secondary" href="/api/jobs/${job.id}/report.md">Download Markdown</a>${a?.status==='needs_attention'?'<button id="reconcile-button" class="button secondary">Reconcile external writes</button>':''}<p class="small subtle" style="margin:18px 0 4px">Payload SHA-256</p><code class="hash">${esc(r.payload_hash)}</code><p class="small subtle">No email report is sent automatically. A draft stays in Gmail until you send it.</p></aside></div>
    <section class="panel"><div class="panel-heading"><div><h2>Support answer &amp; evidence</h2><p>${esc(r.support.question||'No support question supplied.')}</p></div>${pill(r.support.status)}</div><p class="small subtle">${esc(r.support.note)}</p>${r.support.statements.map(s=>`<div class="source-evidence"><p>${esc(s.text)}</p><small>${esc(s.chunk_id)}</small><details><summary class="small">Exact supporting excerpt</summary><pre class="small">${esc(s.quote)}</pre></details></div>`).join('')}${r.support.sources.map(s=>`<p class="small">${safeLink(s.url)?`<a class="source-url" href="${safeLink(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)} &nearr;</a>`:esc(s.title)}<code>${esc(s.chunk_id)}</code></p>`).join('')}</section>
    <section class="report-previews"><div class="section-heading"><h2>The exact payload you approve</h2></div><details><summary>Document entry &middot; plain text</summary><pre id="doc-preview"></pre></details><details><summary>Email draft &middot; ${esc(r.payload.subject)}</summary><iframe id="email-preview" class="email-preview" title="Exact email draft preview" sandbox=""></iframe></details><details><summary>Validation and source checks</summary><pre id="validation-preview"></pre></details></section>`;
  $('report-text').textContent=r.report_markdown;$('doc-preview').textContent=r.payload.doc_entry;$('email-preview').srcdoc=r.payload.body;$('validation-preview').textContent=JSON.stringify({validation:r.validation,checks:r.checks},null,2);
  $('approve-button')?.addEventListener('click',openApproval);
  $('reject-button')?.addEventListener('click',()=>guarded(async()=>{if(!confirm('Reject this report? This does not delete the evidence. A new analysis is needed to prepare a different report.'))return;await api('/jobs/'+job.id+'/reject',{method:'POST'});await loadJobs(job.id);toast('Report rejected. Nothing was published.');}));
  $('reconcile-button')?.addEventListener('click',()=>{state.reconciliation={job_id:job.id,approval_id:job.approval.id};$('reconcile-form').reset();$('reconcile-dialog').showModal();});
}
function openApproval(){
  const r=state.job.result;
  state.approvalPreview={id:state.job.id,payload_hash:r.payload_hash};
  $('approval-form').reset();$('approval-summary').innerHTML=`<div class="notice ${r.payload.target.mode==='local'?'lavender':'mint'}"><div><strong>${r.payload.target.mode==='local'?'Local demonstration only':'Google Docs + Gmail draft'}</strong><p>${esc(r.payload.subject)}</p><p>Recipient: ${esc(r.payload.target.recipient)}</p></div></div><code class="hash">${esc(r.payload_hash)}</code>`;
  $('approval-dialog').showModal();$('approval-word').focus();
}
async function loadSources(){
  state.sources=(await api('/sources')).sources;
  $('source-list').innerHTML=state.sources.length?state.sources.map(s=>`<article class="source-card"><div class="meta"><span class="pill ${s.is_demo?'sand':'lavender'}">${s.is_demo?'SYNTHETIC DEMO':'DOCUMENT SNAPSHOT'}</span><span>${fmt(s.content.length)} characters</span></div><h3>${esc(s.title)}</h3>${safeLink(s.url)?`<a class="source-url" href="${safeLink(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.url)} &nearr;</a>`:'<span class="source-url">Owner-uploaded text &middot; no external URL</span>'}<div class="source-excerpt">${esc(s.content)}</div><div class="source-actions"><code>${esc(s.sha256.slice(0,16))}</code><button class="button quiet small-button delete-source" data-source-id="${esc(s.id)}">Delete source</button></div>${s.is_demo?'<p class="small subtle">Excluded from all non-demo analysis runs.</p>':''}</article>`).join(''):'<div class="empty-state"><h2>Context makes better answers.</h2><p>Add your product documentation to ground support explanations. Reviews themselves are never treated as policy.</p></div>';
}
async function loadActivity(){
  const c=state.cfg;
  $('setup-content').innerHTML=`<div class="setup-grid"><section class="panel"><div class="eyebrow">01 / ANALYSIS</div><h3>${c.analysis_mode==='local'?'Local rules':'LLM-assisted analysis'}</h3><p>${c.analysis_mode==='local'?'Keyword themes and exact metrics. No model-provider network calls.':'Model: '+esc(c.model)+'. Cleaned review batches and retrieved documentation are sent to the configured provider.'}</p><span class="pill lavender">ANALYSIS_MODE=${esc(c.analysis_mode)}</span></section><section class="panel"><div class="eyebrow">02 / PUBLISHING</div><h3>${c.publish_mode==='mcp'?'n8n MCP server':c.publish_mode==='local'?'Local-only output':'Publishing disabled'}</h3><p>${c.publish_mode==='mcp'?'Authenticated Streamable HTTP with exactly three approved publishing tools.':c.publish_mode==='local'?'Document entries and drafts are stored in SQLite for testing. Nothing goes to Google.':'Reports can be reviewed and downloaded, but not published.'}</p><button id="mcp-check" class="button secondary small-button">Check connection</button></section><section class="panel"><div class="eyebrow">03 / INGESTION</div><h3>Ready for n8n</h3><p>POST reviews to <code>/api/jobs</code> using the ingestion Bearer token from your environment. This credential cannot approve or publish.</p><span class="pill mint">Durable SQLite queue</span></section></div><div class="notice lavender"><div><strong>Connect your own data and accounts</strong><p>See <code>README.md</code> and <code>docs/N8N_SETUP.md</code>. Import the two workflows in <code>n8n/</code>, configure credentials, and update your <code>.env</code>. Restart the app after configuration changes.</p></div></div><section class="panel" id="local-effects-panel" ${c.publish_mode==='local'?'':'hidden'}><div class="panel-heading"><div><h2>Local publishing output</h2><p>Demonstration receipts only. No Google Docs or Gmail calls were made.</p></div></div><div id="local-effects" class="local-effects"></div></section>`;
  $('mcp-check').addEventListener('click',()=>guarded(async()=>{const b=$('mcp-check');b.disabled=true;try{const r=await api('/integrations/check',{method:'POST'});toast(r.message);}finally{b.disabled=false;}}));
  const events=(await api('/audit')).events;
  $('audit-table').innerHTML=events.length?`<table><thead><tr><th>EVENT</th><th>ENTITY</th><th>TIME</th><th>DETAIL</th></tr></thead><tbody>${events.map(e=>`<tr><td class="audit-event"><strong>${esc(e.event.replaceAll('_',' '))}</strong></td><td><code>${esc(e.entity_id.slice(0,12))}</code></td><td>${esc(new Date(e.at).toLocaleString())}</td><td class="text-cell"><span class="small">${esc(e.detail_json)}</span></td></tr>`).join('')}</tbody></table>`:'<div class="small-empty">Your activity trail will appear here.</div>';
  if(c.publish_mode==='local'){
    const effects=(await api('/local-effects')).effects;
    $('local-effects').innerHTML=effects.length?effects.map(e=>`<details><summary><span class="pill mint">${esc(e.kind)}</span> ${esc(e.marker)}</summary><pre>${esc(e.kind==='document'?e.payload.entry:JSON.stringify({recipient:e.payload.recipient,subject:e.payload.subject,note:'HTML body is saved in SQLite and visible in the report preview.'},null,2))}</pre></details>`).join(''):'<p class="small subtle">Approve a report in local mode to create demonstration output.</p>';
  }
}
function openUpload(){ $('upload-dialog').showModal(); }
async function boot(){
  try{state.cfg=await api('/me');showWorkspace();const route=location.hash.slice(1).split('/');showPage(route[0]||'overview',false);await loadJobs(/^[a-f0-9]{32}$/.test(route[1]||'')?route[1]:undefined);}catch(e){toast(e.message||'Could not load the dashboard.');}
}
document.addEventListener('click',e=>{const nav=e.target.closest('[data-page]');if(nav)showPage(nav.dataset.page);const close=e.target.closest('[data-close]');if(close)$(close.dataset.close).close();if(e.target.closest('[data-action="upload"]'))openUpload();const del=e.target.closest('[data-source-id]');if(del)guarded(async()=>{if(!confirm('Delete this source from future analyses? Existing reports retain their saved evidence.'))return;await api('/sources/'+encodeURIComponent(del.dataset.sourceId),{method:'DELETE'});await loadSources();toast('Source deleted from the active library.');});});
$('upload-button').addEventListener('click',openUpload);
$('refresh-button').addEventListener('click',()=>guarded(async()=>{await loadJobs();toast('Workspace refreshed.');}));
$('audit-refresh').addEventListener('click',()=>guarded(loadActivity));
$('job-select').addEventListener('change',e=>guarded(async()=>{if(e.target.value)await loadJob(e.target.value);}));
$('demo-button').addEventListener('click',()=>guarded(async()=>{const b=$('demo-button');b.disabled=true;try{const j=await api('/demo',{method:'POST'});await loadJobs(j.id);toast('Synthetic demo queued. It uses local analysis and demo documentation only.');}finally{b.disabled=false;}}));
$('csv-file').addEventListener('change',()=>{$('file-label').textContent=$('csv-file').files[0]?.name||'Choose a CSV file';});
$('upload-form').addEventListener('submit',e=>{e.preventDefault();submitForm(e.currentTarget,async()=>{const file=$('csv-file').files[0];if(!file)throw new Error('Choose a CSV file.');if(file.size>state.cfg.max_upload_bytes)throw new Error('CSV is too large. Reduce its size or split reporting periods.');const content=await file.text();const body=JSON.stringify({name:$('dataset-name').value.trim(),csv:content,question:$('support-question').value.trim()});if(new Blob([body]).size>state.cfg.max_upload_bytes)throw new Error('The encoded request exceeds 5 MB. Reduce the CSV size.');const j=await api('/jobs',{method:'POST',body});$('upload-dialog').close();showPage('overview');await loadJobs(j.id);toast('Your review analysis has been queued.');});});
$('add-source-button').addEventListener('click',()=>{$('source-form').reset();$('source-dialog').showModal();});
$('source-file').addEventListener('change',()=>guarded(async()=>{const file=$('source-file').files[0];if(file){if(file.size>200000)throw new Error('Use a smaller document, up to 100,000 characters.');$('source-content').value=await file.text();}}));
$('source-form').addEventListener('submit',e=>{e.preventDefault();submitForm(e.currentTarget,async()=>{await api('/sources',{method:'POST',body:JSON.stringify({title:$('source-title').value.trim(),url:$('source-url').value.trim(),content:$('source-content').value.trim()})});$('source-dialog').close();await loadSources();toast('Source saved. New analyses will snapshot this document.');});});
$('approval-form').addEventListener('submit',e=>{e.preventDefault();submitForm(e.currentTarget,async()=>{const preview=state.approvalPreview;if(!preview)throw new Error('Open a saved report preview first.');const id=preview.id;await api('/jobs/'+id+'/approve',{method:'POST',body:JSON.stringify({confirmation:$('approval-word').value,payload_hash:preview.payload_hash})});$('approval-dialog').close();await loadJobs(id);toast('Approval saved. Publishing is queued with the exact payload.');});});
$('reconcile-form').addEventListener('submit',e=>{e.preventDefault();submitForm(e.currentTarget,async()=>{const verified=state.reconciliation;if(!verified)throw new Error('Open the reconciliation dialog first.');await api('/approvals/'+verified.approval_id+'/reconcile',{method:'POST',body:JSON.stringify({confirmation:$('reconcile-word').value,doc_state:$('reconcile-doc').value,draft_state:$('reconcile-draft').value,note:$('reconcile-note').value})});$('reconcile-dialog').close();await loadJobs(verified.job_id);toast('Verification saved. Only pending steps are eligible to run.');});});
for(const id of ['review-search','sentiment-filter','theme-filter'])$(id).addEventListener(id==='review-search'?'input':'change',()=>{state.reviewPage=0;renderReviews();});
$('reviews-prev').addEventListener('click',()=>{state.reviewPage=Math.max(0,state.reviewPage-1);renderReviews();});
$('reviews-next').addEventListener('click',()=>{state.reviewPage++;renderReviews();});
window.addEventListener('hashchange',()=>{const route=location.hash.slice(1).split('/');showPage(route[0],false);if(route[1]&&route[1]!==state.job?.id)guarded(()=>loadJob(route[1]));});
setInterval(()=>{
  if(!state.cfg||state.busy||!state.job)return;
  if(['queued','running'].includes(state.job.status)||['queued','publishing'].includes(state.job.approval?.status)){
    state.busy=true;loadJobs(state.job.id).catch(e=>toast(e.message)).finally(()=>state.busy=false);
  }
},2500);
boot();
