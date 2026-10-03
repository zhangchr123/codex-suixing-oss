'use strict';
const $ = id => document.getElementById(id);
const md = window.markdownit({html:false, breaks:true, linkify:true, typographer:false});
installMath(md,window.katex);
md.renderer.rules.image = (tokens, i) => {
  const src=tokens[i].attrGet('src')||'',alt=tokens[i].content||'对话图片';
  if(!/^\/api\/(?:media|images)\/[0-9a-f]{64}\.(?:png|jpg|gif|webp)$/.test(src)&&!/^https:\/\//i.test(src))
    return '<span class="image-unavailable">'+md.utils.escapeHtml('[图片暂不可预览：'+alt+']')+'</span>';
  return '<a class="preview-image" href="'+md.utils.escapeHtml(src)+'" target="_blank" rel="noopener noreferrer"><img src="'+md.utils.escapeHtml(src)+'" alt="'+md.utils.escapeHtml(alt)+'" loading="lazy" referrerpolicy="no-referrer"></a>';
};
const defaultLink = md.renderer.rules.link_open || ((tokens,i,options,env,self)=>self.renderToken(tokens,i,options));
md.renderer.rules.link_open = (tokens,i,options,env,self) => {
  const href = tokens[i].attrGet('href') || '';
  if (!/^(https?:|mailto:|#)/i.test(href)) tokens[i].attrSet('href','#');
  tokens[i].attrSet('target','_blank'); tokens[i].attrSet('rel','noopener noreferrer');
  return defaultLink(tokens,i,options,env,self);
};
let threads=[], selected='', rendered='', busy=false, syncedAt=null, lastListing='', bridge={}, csrfToken='';
let sending=false, creating=false, creationId='', pending={};
let syncClock=null, pingBusy=false, networkMs=null, networkState='measuring';
const polling=new PollingCadence();
const appVersion=/CodexSuixing\/([\d.]+)/.exec(navigator.userAgent)?.[1];
const appClient=!!appVersion;
document.documentElement.classList.toggle('app-client',appClient);
if(appClient){$('message').rows=1;$('message').placeholder='发送消息…';}
const oldApp=!!appVersion&&parseFloat(appVersion)<1.2;
let refreshTimer;
let channel='codex',preparingImages=false,newPreparingImages=false;
let cloudState={};
const optimistic=new Map();
const apiCache=new Map(),threadCache=new Map(),progressExpansion=new Set();
const imageDrafts=new Map();let newImages=[];
const drafts = new Map();
const generationSettings = new Map();
const resolvedPrompts = new Set();let activePrompt=null;
const effortNames={none:'关闭',minimal:'最低',low:'低',medium:'中',high:'高',xhigh:'很高',max:'最高',ultra:'极高'};
function selectedSettings(id=selected){return generationSettings.get(id)||{}}
function fillEfforts(prefix,thinking=''){
  const model=(bridge.models||[]).find(m=>m.id===$(prefix+'-model').value);
  const input=$(prefix+'-thinking');
  input.replaceChildren(new Option(model?'由电脑决定':'沿用当前设置',''),...(model?.efforts||[]).map(e=>new Option(effortNames[e]+' · '+e,e)));
  if(model?.efforts.includes(thinking))input.value=thinking;
  input.disabled=!model;
}
function fillModels(prefix,settings={}){
  $(prefix+'-model').replaceChildren(new Option('沿用当前设置',''),...(bridge.models||[]).map(m=>new Option(m.id,m.id)));
  $(prefix+'-model').value=(bridge.models||[]).some(m=>m.id===settings.model)?settings.model:'';
  fillEfforts(prefix,settings.thinking);
}
function readSettings(prefix){
  const model=$(prefix+'-model').value,thinking=$(prefix+'-thinking').value;
  return model?{model,...(thinking?{thinking}:{})}:{};
}
const expandedDeliveries=new Set(), messageExpansion=new Map();
const names={running:'处理中',idle:'已回复',stopped:'已停止',unknown:'对话'};
const deliveryNames={queued:'等待电脑接收',delivering:'正在发送到电脑',sent:'已发送',failed:'发送失败',uncertain:'结果待确认'};
function date(s){return s?new Date(s).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):''}
function preview(text){return text.replace(/!\[[^\]]*\]\([^)]*\)/g,'[图片]').replace(/(?:^|\s)#{1,6}\s+/g,' ').replace(/[*_`>|]/g,'').trim()}
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}
async function api(url,options={}) {
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);
  let r,data;
  try{
    const previous=apiCache.get(url),headers={...options.headers};
    if(!options.method&&previous?.etag)headers['If-None-Match']=previous.etag;
    r=await fetch(url,{cache:'no-store',signal:controller.signal,...options,headers});
    if(r.status===304&&previous)data=previous.data;else data=await r.json();
    if(!options.method&&r.ok)apiCache.set(url,{etag:r.headers.get('ETag'),data});
  }finally{clearTimeout(timeout)}
  if(r.status===401){$('shell').hidden=true;$('auth').hidden=false;throw new Error(data.error||'请先登录')}
  if(!r.ok&&r.status!==304){const error=new Error(data.error||'连接暂时不可用');error.httpStatus=r.status;throw error;}
  if(url==='/api/threads'){
    const serverAt=Date.parse(r.headers.get('Date'));
    syncClock={serverAt:Number.isFinite(serverAt)?serverAt:Date.now(),receivedAt:performance.now()};
  }
  return data;
}
function syncAge(){
  if(!syncedAt||!Number.isFinite(Date.parse(syncedAt)))return Infinity;
  const current=syncClock?syncClock.serverAt+performance.now()-syncClock.receivedAt:Date.now();
  return Math.max(0,current-Date.parse(syncedAt));
}
function renderMetrics(){
  const age=syncAge(),seconds=Math.floor(age/1000);
  const fresh=!Number.isFinite(age)?'尚未同步':seconds<60?seconds+' 秒前':seconds<3600?Math.floor(seconds/60)+' 分 '+seconds%60+' 秒前':Math.floor(seconds/3600)+' 小时前';
  const label=networkState==='measuring'?'测量中':networkState==='timeout'?'超时':networkState==='offline'?'未连接':networkMs>=1000?(networkMs/1000).toFixed(2)+' 秒':Math.round(networkMs)+' ms';
  for(const node of document.querySelectorAll('[data-network-latency]')){
    node.textContent=label;node.dataset.quality=networkState==='ok'?(networkMs<200?'good':networkMs<800?'fair':'poor'):networkState==='measuring'?'pending':'poor';
  }
  for(const node of document.querySelectorAll('[data-sync-age]')){node.textContent=fresh;node.dataset.quality=age<60000?'good':'poor'}
}
async function measureLatency(){
  if(pingBusy||document.hidden)return;pingBusy=true;
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),5000),started=performance.now();
  try{
    const response=await fetch('/health',{cache:'no-store',signal:controller.signal});
    const result=await response.json();
    if(!response.ok||result.ok!==true)throw new Error('unavailable');
    if(result.migrationTarget&&!$('migration-notice')){
      const notice=el('div','','服务已迁移。请覆盖安装新版 APK，原手机绑定会保留。');notice.id='migration-notice';
      notice.style.cssText='padding:14px;background:#fff1c9;color:#4a3920;line-height:1.7';
      const download=el('a','',' 用浏览器下载新版 APK');download.href=result.migrationTarget.replace(/\/$/,'')+'/downloads/codex-suixing.apk';download.target='_blank';download.rel='noopener';
      const open=el('a','',' 打开新入口');open.href=result.migrationTarget;open.target='_blank';open.rel='noopener';
      notice.append(download,open);document.body.prepend(notice);
    }
    networkMs=performance.now()-started;networkState='ok';
  }catch(error){networkMs=null;networkState=controller.signal.aborted?'timeout':'offline'}
  finally{clearTimeout(timeout);pingBusy=false;renderMetrics()}
}
function online(){return bridge.connected && syncAge()<60000}
function isChat(id=selected){return threads.find(t=>t.id===id)?.kind==='chatgpt'}
function isCloud(id=selected){return threads.find(t=>t.id===id)?.kind==='cloud'}
function canSend(){return !isCloud()&&online()&&(isChat()?(bridge.chatgptConnected&&(bridge.chatgptThreadIds||[]).includes(selected)):(bridge.threadIds||[]).includes(selected))}
function currentImages(){return imageDrafts.get(selected)||[]}
function status(){
  const stale=syncAge()>90000;
  $('dot').classList.toggle('off',stale);
  $('sync').textContent=!syncedAt?'等待电脑首次同步':stale?'同步暂停 · 最后同步 '+date(syncedAt):'同步正常 · '+date(syncedAt);
  const current=threadCache.get(selected),stamp=current?.kind==='chatgpt'?current.bodyCheckedAt:current?.contentSyncedAt;
  $('read-status').textContent=stale?'缓存内容 · 最后同步 '+date(syncedAt):(stamp?'正文 '+date(stamp)+' · ':'')+'后台更新';
  $('new-thread').disabled=!online()||channel!=='codex';
  $('new-thread').textContent=channel==='cloud'?'云端任务 · 只读同步':channel==='chatgpt'?'请先在电脑新建 ChatGPT 聊天':'＋ 新建 Codex 任务';
  $('channel-note').textContent=channel==='cloud'?(cloudState.connected?'使用本机 CLI 登录同步云任务列表与状态。完整对话请打开原云任务。最近检查：'+date(cloudState.checkedAt):cloudState.error||'正在连接云端任务…'):channel==='chatgpt'?'使用桌面已登录的 ChatGPT 账号，可查看和文字续聊。新建聊天和添加图片请在电脑完成。':'本机项目与独立 Codex 任务，可发送文字和图片。';
  $('codex-tab').setAttribute('aria-pressed',String(channel==='codex'));
  $('chatgpt-tab').setAttribute('aria-pressed',String(channel==='chatgpt'));
  $('cloud-tab').setAttribute('aria-pressed',String(channel==='cloud'));
  $('connection-pill').textContent=channel==='cloud'?(cloudState.connected?'云任务已同步':'云任务未连接'):online()?'可续聊':syncAge()<60000?'同步正常 · 续聊未连接':'同步未连接';
  $('composer').hidden=!selected||isCloud();
  const prompt=(bridge.prompts||[]).find(p=>p.threadId===selected&&!resolvedPrompts.has(p.id));
  $('request-banner').hidden=!prompt;
  $('request-summary').textContent=prompt?(prompt.type==='question'?'Codex 需要你补充信息':'Codex 等待你的确认'):'';
  $('model-options').hidden=!selected||isChat()||isCloud();
  $('model-options').disabled=sending||!canSend();
  const settings=selectedSettings();
  $('model-options').textContent=settings.model?'模型 · '+(settings.thinking?effortNames[settings.thinking]:'已选'):'模型';
  $('model-options').title=settings.model||'选择模型和思考强度';
  $('send').disabled=sending||preparingImages||!canSend()||(!$('message').value.trim()&&!currentImages().length);
  $('attach').hidden=isChat();$('attach').disabled=sending||preparingImages||!canSend();$('attach').textContent=oldApp?'更新后选图':'＋ 图片';
  $('send').textContent=sending?'发送中…':'发送 ↑';
  $('composer-hint').textContent=preparingImages?'正在处理图片…':!online()?'电脑未连接，暂时不能发送':!canSend()?'桌面暂未连接此对话':isChat()?'通过电脑上的 ChatGPT 继续聊天':settings.model?settings.model+' · '+(settings.thinking?effortNames[settings.thinking]+'思考':'思考强度由电脑决定'):'消息会发到电脑，沿用当前任务的模型与设置';
  if(appClient){
    $('new-thread').hidden=channel!=='codex';$('new-thread').textContent='＋ 新建';
    const count=threads.filter(t=>(t.kind==='cloud'?'cloud':t.kind==='chatgpt'?'chatgpt':'codex')===channel).length;
    $('app-status').textContent=count+' 个'+(channel==='chatgpt'?'聊天':'任务')+' · '+(!syncedAt?'等待同步':stale?'同步暂停':'同步正常');
    $('app-status').dataset.stale=String(stale||!syncedAt);
    $('app-sync-info').textContent=$('sync').textContent+' · '+$('connection-pill').textContent;
    $('app-channel-info').textContent=channel==='cloud'?'展示云任务标题与状态，完整对话请打开原任务。最近检查：'+date(cloudState.checkedAt):channel==='chatgpt'?'可以文字续聊。新建聊天与原有附件请在电脑查看。':'查看本机任务，发送文字和图片。电脑需保持连接。';
    $('composer-hint').hidden=!!online()&&canSend()&&!preparingImages;
    if(oldApp){$('attach').textContent='更新';$('attach').title='更新 App 后选图';}
    resizeComposer();
  }
  renderMetrics();
}
function resizeComposer(){
  if(!appClient)return;
  const input=$('message');input.style.height='auto';input.style.height=Math.max(38,Math.min(112,input.scrollHeight))+'px';
}
function list(){
  const q=$('search').value.toLowerCase();
  const group=threads.filter(t=>(t.kind==='cloud'?'cloud':t.kind==='chatgpt'?'chatgpt':'codex')===channel);
  const filtered=group.filter(t=>(t.title+' '+t.workspace).toLowerCase().includes(q));
  const signature=JSON.stringify([filtered,selected]);
  if(signature===lastListing)return;lastListing=signature;
  const nodes=filtered.map(t=>{
    const button=el('button','thread'+(selected===t.id?' active':''));button.type='button';
    button.append(el('div','thread-title',t.title));
    const meta=el('div','thread-meta');
    meta.append(el('span','',(t.workspace||'').split(/[\\/]/).filter(Boolean).pop()||'Codex'),el('span','',date(t.updatedAt)));
    button.append(meta,el('span','thread-state '+t.status,t.kind==='cloud'?({running:'处理中',idle:'已完成',stopped:'已结束'}[t.status]||'云任务'):names[t.status]||'对话'));
    if(t.previewText)button.append(el('div','thread-preview',preview(t.previewText)));
    button.onclick=()=>select(t.id).catch(showError);return button;
  });
  $('list').replaceChildren(...nodes);
  if(!nodes.length)$('list').append(el('div','small',group.length?'没有匹配的对话':channel==='cloud'?(cloudState.connected?'当前账号没有 Codex 云任务':cloudState.error||'正在检查云端任务…'):channel==='chatgpt'?'等待桌面同步 ChatGPT 聊天列表…':'等待电脑同步任务…'));
  $('count').textContent=group.length+' 个最近'+(channel==='chatgpt'?'聊天':'任务')+' · 自动同步';
}
function body(text){
  const div=el('div','bubble markdown');
  // markdown-it escapes source HTML and rejects unsafe link protocols.
  div.innerHTML=md.render(text);
  for(const table of [...div.querySelectorAll('table')]){
    const wrap=el('div','table-wrap');table.replaceWith(wrap);wrap.append(table);
  }
  for(const pre of div.querySelectorAll('pre')){
    const copy=el('button','code-copy','复制代码');copy.type='button';
    copy.onclick=async()=>{try{await navigator.clipboard.writeText(pre.querySelector('code').textContent);copy.textContent='已复制'}catch{copy.textContent='请长按代码复制'}};
    pre.prepend(copy);
  }
  for(const img of div.querySelectorAll('img')){
    img.onerror=()=>img.closest('a').replaceChildren(el('span','image-unavailable','图片暂不可用，请稍后刷新'));
  }
  return div;
}
function showError(error){$('send-error').textContent=error.message}
async function select(id){
  polling.activate();scheduleRefresh();
  if(selected)drafts.set(selected,$('message').value);
  selected=id;channel=isCloud(id)?'cloud':isChat(id)?'chatgpt':'codex';rendered='';$('message').value=drafts.get(id)||'';$('send-error').textContent='';renderImageDrafts();
  history.replaceState(null,'','#'+id);$('shell').classList.add('open');list();status();
  const summary=threads.find(t=>t.id===id);if(summary)$('title').textContent=summary.title;
  const cached=threadCache.get(id);if(cached)paintThread(cached,true);
  else $('timeline').replaceChildren(el('div','empty','正在读取对话…'));
  await refreshThread(!cached);
}
function deliveries(rows){
  const id=selected;
  const render=row=>{
    const node=el('div','delivery '+row.status);
    node.append(el('span','',deliveryNames[row.status]||row.status),el('span','delivery-preview',row.text.slice(0,70)));
    if(row.status==='failed'||row.status==='uncertain')node.append(el('div','small',row.detail));
    if(row.args?.images?.length)node.append(imageGallery(row.args.images.map(i=>i.id)));
    return node;
  };
  const nodes=rows.filter(row=>row.status!=='sent').map(render),sent=rows.filter(row=>row.status==='sent');
  if(sent.length){
    const details=el('details','sent-deliveries');details.open=expandedDeliveries.has(id);
    details.append(el('summary','',`已发送 ${sent.length} 条 · 展开查看`),...sent.map(render));
    details.ontoggle=()=>details.open?expandedDeliveries.add(id):expandedDeliveries.delete(id);
    nodes.push(details);
  }
  $('deliveries').replaceChildren(...nodes);
}
async function refreshThread(force=false){
  if(!selected)return;const id=selected;const data=await api('/api/threads/'+id);if(id!==selected)return;
  threadCache.set(id,data);paintThread(data,force);status();
}
function paintThread(data,force=false){
  const id=selected;
  $('title').textContent=data.title;$('workspace').textContent=((data.workspace||'').split(/[\\/]/).filter(Boolean).pop()||'独立任务')+' · '+(names[data.status]||'对话');$('workspace').title=data.workspace||'';
  const receipts=new Map((data.deliveries||[]).map(r=>[r.id,r]));
  for(const [key,row] of optimistic){if(receipts.has(key))optimistic.delete(key);else if(row.threadId===id)receipts.set(key,row)}
  const messages=(data.messages||[]).map(m=>({...m,text:cleanDisplayText(m.text,m.role)})).filter(m=>m.text||m.images?.length);
  const timeline=mergeTimeline(messages,[...receipts.values()].filter(r=>r.kind!=='decision'));
  $('deliveries').replaceChildren();
  const signature=JSON.stringify([timeline,data.truncated,data.loaded]);if(signature===rendered&&!force)return;rendered=signature;
  const box=$('timeline'),old=box.scrollTop,atEnd=box.scrollHeight-box.clientHeight-old<100,nodes=[];
  let progress=[];
  const flushProgress=()=>{
    if(!progress.length)return;
    const group=el('details','progress-group'),key=id+':'+progress[0].key;
    group.open=progressExpansion.has(key);group.append(el('summary','',progress.length+' 条工作进展 · '+progress.at(-1).text.replace(/\s+/g,' ').slice(0,44)),...progress.map(p=>p.article));
    group.ontoggle=()=>group.open?progressExpansion.add(key):progressExpansion.delete(key);nodes.push(group);progress=[];
  };
  if(data.kind==='chatgpt')nodes.push(el('div','notice',data.loaded?'当前显示最近最多 10 轮聊天。更早的历史和原有附件请在电脑查看。':'正在从电脑读取聊天内容…首次打开可能需要几秒。'));
  else if(data.truncated)nodes.push(el('div','notice','这是一段较长的对话，当前展示最近的内容。完整历史请在电脑查看。'));
  if(data.kind==='cloud'){
    const notice=el('div','notice','云任务状态：'+(data.cloudStatus||'未知')+'。当前同步任务列表与状态，CLI 尚未提供完整聊天正文。');
    if(data.cloudUrl){const link=el('a','',' 打开原云任务');link.href=data.cloudUrl;link.target='_blank';link.rel='noopener noreferrer';notice.append(link)}nodes.push(notice);
  }
  for(const m of timeline){
    const article=el('article','message '+(m.role==='user'?'user':m.phase==='commentary'?'progress':'assistant'));
    const label=el('div','message-label');label.append(el('span','',m.role==='user'?'你':data.kind==='chatgpt'?'ChatGPT':m.phase==='commentary'?'Codex · 进度':'Codex'),el('time','',date(m.time)));
    article.append(label);
    if(m.delivery){
      const state=el('div','delivery '+m.delivery.status,deliveryNames[m.delivery.status]||m.delivery.status);
      if(['failed','uncertain'].includes(m.delivery.status))state.append(el('div','small',m.delivery.detail));article.append(state);
    }
    if(m.role==='user'){
      if(m.text.length<=400){article.append(body(m.text));if(m.images?.length)article.append(imageGallery(m.images))}
      else{
      const key=id+':'+m.time+':'+m.text,details=el('details','user-message'),summary=el('summary');
      details.open=messageExpansion.has(key)?messageExpansion.get(key):m.text.length<=400;
      const update=()=>{summary.textContent=details.open?'收起内容':m.text.replace(/\s+/g,' ').slice(0,70)+(m.text.length>70?'…':'')+' · 展开';};
      details.append(summary,body(m.text));if(m.images?.length)details.append(imageGallery(m.images));update();
      details.ontoggle=()=>{messageExpansion.set(key,details.open);update()};article.append(details);
      }
    }else article.append(body(m.text));
    if(m.role==='assistant'&&m.phase==='commentary')progress.push({article,text:m.text,key:m.time});
    else{flushProgress();nodes.push(article)}
  }
  flushProgress();
  box.replaceChildren(...nodes);box.scrollTop=force||atEnd?box.scrollHeight:old;
}
async function refresh(){
  if(busy)return;busy=true;
  try{
    const data=await api('/api/threads');threads=data.threads;syncedAt=data.syncedAt;bridge=data.bridge||{};cloudState=data.cloud||{};csrfToken=data.csrfToken;
    polling.observeServer(data.pollInterval, syncAge()<60000);
    $('auth').hidden=true;$('shell').hidden=false;status();list();
    const hash=location.hash.slice(1);
    if(!selected&&threads.some(t=>t.id===hash))await select(hash);
    else if(selected&&threads.some(t=>t.id===selected))await refreshThread();
    else if(selected){selected='';rendered='';$('shell').classList.remove('open');$('title').textContent='选择一个对话';$('timeline').replaceChildren(el('div','empty','该对话已不在最近同步的列表中。'));status();list()}
    if(creationId){
      const result=(data.creations||[]).find(c=>c.id===creationId);
      if(result){$('creation-status').textContent=result.detail||deliveryNames[result.status];$('creation-status').hidden=false;
        if(result.resultThreadId&&threads.some(t=>t.id===result.resultThreadId)){creationId='';await select(result.resultThreadId)}
      }
    }
  }catch(error){
    if($('auth').hidden){bridge={};status();$('sync').textContent='连接中断，正在重试…';$('dot').classList.add('off')}
  }finally{busy=false}
}
function requestId(key,payload){const text=JSON.stringify(payload);if(!pending[key]||pending[key].text!==text)pending[key]={text,id:crypto.randomUUID()};return pending[key].id}
async function post(path,payload){
  const result=await api(path,{method:'POST',headers:{'Content-Type':'application/json','X-Viewer-CSRF':csrfToken},body:JSON.stringify(payload)});
  polling.activate();scheduleRefresh();return result;
}
$('composer').onsubmit=async event=>{
  event.preventDefault();if(sending||preparingImages||!canSend())return;const id=selected,text=$('message').value.trim(),images=currentImages();if(!text&&!images.length)return;
  const key='send:'+id,payload={text,images:images.map(i=>({data:i.data})),...(!isChat()?selectedSettings(id):{})};payload.requestId=requestId(key,payload);sending=true;status();$('send-error').textContent='';
  optimistic.set(payload.requestId,{id:payload.requestId,threadId:id,text:text||'请查看附图。',created:Date.now()/1000,status:'queued',args:{images:[]}});
  refreshThread().catch(()=>{});
  try{const receipt=await post('/api/threads/'+id+'/messages',payload);optimistic.set(payload.requestId,receipt);delete pending[key];drafts.delete(id);images.forEach(releaseImage);imageDrafts.delete(id);if(selected===id){$('message').value='';renderImageDrafts()}await refreshThread()}
  catch(error){const row=optimistic.get(payload.requestId);if(row){row.status=error.httpStatus&&error.httpStatus<500?'failed':'uncertain';row.detail=row.status==='failed'?error.message:'结果未确认，请刷新后查看，避免重复发送'}showError(error);refreshThread().catch(()=>{})}finally{sending=false;status()}
};
$('message').oninput=()=>{drafts.set(selected,$('message').value);status()};
$('message').onkeydown=event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();$('composer').requestSubmit()}};
$('new-thread').onclick=()=>{
  if(channel==='chatgpt')return;
  const options=[new Option('独立对话（不绑定项目）',''),...(bridge.projects||[]).map(p=>new Option(p.label,p.id))];
  $('new-project').replaceChildren(...options);fillModels('new',readSettings('new'));$('create-error').textContent='';$('new-dialog').showModal();
};
$('cancel-create').onclick=()=>$('new-dialog').close();
$('new-form').onsubmit=async event=>{
  event.preventDefault();if(creating||newPreparingImages||!online())return;
  const payload={text:$('new-message').value.trim(),title:$('new-title').value.trim(),projectId:$('new-project').value,images:newImages.map(i=>({data:i.data})),...readSettings('new')};
  if(!payload.text&&!newImages.length)return;payload.requestId=requestId('create',payload);creating=true;$('create').disabled=true;$('create-error').textContent='';
  try{await post('/api/threads/new',payload);creationId=payload.requestId;delete pending.create;$('new-dialog').close();$('new-message').value='';$('new-title').value='';newImages.forEach(releaseImage);newImages=[];renderImageDrafts(true);$('creation-status').hidden=false;$('creation-status').textContent='等待电脑创建新对话…';await refresh()}
  catch(error){$('create-error').textContent=error.message}finally{creating=false;$('create').disabled=false}
};
$('login').onsubmit=async event=>{event.preventDefault();$('error').textContent='';try{await api('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:$('password').value})});$('password').value='';await refresh()}catch(error){$('error').textContent=error.message}};
$('search').oninput=list;$('back').onclick=()=>$('shell').classList.remove('open');$('latest').onclick=()=>$('timeline').scrollTop=$('timeline').scrollHeight;
function switchChannel(value){
  if(selected)drafts.set(selected,$('message').value);
  channel=value;selected='';rendered='';lastListing='';history.replaceState(null,'',location.pathname);
  $('message').value='';$('search').value='';$('deliveries').replaceChildren();$('image-preview').replaceChildren();
  $('title').textContent=value==='cloud'?'选择一个 Codex 云任务':value==='chatgpt'?'选择一个 ChatGPT 聊天':'选择一个 Codex 任务';$('workspace').textContent='';
  $('timeline').replaceChildren(el('div','empty',value==='cloud'?'使用本机登录状态同步 Codex 云任务':value==='chatgpt'?'电脑上的 ChatGPT 账号与历史聊天':'电脑上的项目与独立 Codex 任务'));
  $('shell').classList.remove('open');status();list();
}
$('codex-tab').onclick=()=>switchChannel('codex');$('chatgpt-tab').onclick=()=>switchChannel('chatgpt');
$('cloud-tab').onclick=()=>switchChannel('cloud');
try{document.documentElement.dataset.theme=localStorage.getItem('suixing.theme')||'light'}catch{}
function toggleTheme(){const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;try{localStorage.setItem('suixing.theme',theme)}catch{}}
$('theme-toggle').onclick=toggleTheme;$('app-theme-toggle').onclick=toggleTheme;
$('app-options-button').onclick=()=>$('app-options').showModal();$('app-options-close').onclick=()=>$('app-options').close();
$('model-options').onclick=()=>{fillModels('setting',selectedSettings());$('model-availability').textContent=bridge.models?.length?'选择后从下一条消息生效，也会更新桌面对话的设置。':'电脑暂未提供可选模型，仍可沿用当前设置。';$('model-dialog').showModal()};
$('setting-model').onchange=()=>fillEfforts('setting');$('new-model').onchange=()=>fillEfforts('new');
$('model-close').onclick=()=>$('model-dialog').close();
$('model-form').onsubmit=event=>{event.preventDefault();generationSettings.set(selected,readSettings('setting'));$('model-dialog').close();status()};
$('request-open').onclick=()=>{
  activePrompt=(bridge.prompts||[]).find(p=>p.threadId===selected&&!resolvedPrompts.has(p.id));if(!activePrompt)return;
  $('request-title').textContent=activePrompt.type==='question'?'补充信息':activePrompt.title;
  $('request-detail').textContent=activePrompt.detail||'';$('request-error').textContent='';$('request-fields').replaceChildren();
  $('request-decline').hidden=activePrompt.type==='question';$('request-submit').textContent=activePrompt.type==='question'?'提交回答':'允许这一次';
  for(const q of activePrompt.questions||[]){
    const group=el('div','request-question'),label=el('label','',q.question||q.header||q.id),input=el('textarea');input.dataset.questionId=q.id;input.required=true;input.maxLength=2000;input.rows=2;group.append(label);
    for(const option of q.options||[]){const button=el('button','plain',option.label);button.type='button';button.onclick=()=>input.value=option.label;group.append(button)}
    group.append(input);$('request-fields').append(group);
  }
  $('request-dialog').showModal();
};
$('request-close').onclick=()=>$('request-dialog').close();
async function submitPrompt(decision){
  if(!activePrompt)return;const p=activePrompt,payload={promptId:p.id};
  if(p.type==='question')payload.answers=Object.fromEntries([...$('request-fields').querySelectorAll('textarea')].map(input=>[input.dataset.questionId,input.value.trim()]));else payload.decision=decision;
  payload.requestId=requestId('prompt:'+p.id,payload);$('request-submit').disabled=true;$('request-decline').disabled=true;
  try{await post('/api/threads/'+p.threadId+'/actions',payload);resolvedPrompts.add(p.id);$('request-dialog').close();status()}
  catch(error){$('request-error').textContent=error.message}
  finally{$('request-submit').disabled=false;$('request-decline').disabled=false}
}
$('request-form').onsubmit=event=>{event.preventDefault();submitPrompt('accept')};$('request-decline').onclick=()=>submitPrompt('decline');
$('timeline').addEventListener('click',event=>{
  const link=event.target.closest('.preview-image,.image-gallery a');if(!link)return;
  event.preventDefault();$('image-full').src=link.href;$('image-full').alt=link.querySelector('img')?.alt||'对话图片';$('image-caption').textContent=$('image-full').alt;$('image-original').href=link.href;$('image-dialog').showModal();
});
$('image-close').onclick=()=>$('image-dialog').close();
$('image-dialog').addEventListener('click',event=>{if(event.target===$('image-dialog')){const r=$('image-dialog').getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)$('image-dialog').close()}});
function imageGallery(ids){
  const gallery=el('div','image-gallery');
  for(const id of ids){
    if(!/^[0-9a-f]{64}\.(jpg|png)$/.test(id))continue;
    const a=el('a');a.href='/api/images/'+id;a.target='_blank';a.rel='noopener';
    const img=el('img');img.src=a.href;img.alt='手机上传的图片';img.loading='lazy';
    img.onerror=()=>a.replaceChildren(el('span','small','图片暂不可用'));a.append(img);gallery.append(a);
  }
  return gallery;
}
function renderImageDrafts(fresh=false){
  const rows=fresh?newImages:currentImages(),nodes=rows.map((row,index)=>{
    const tile=el('div','image-draft'),img=el('img');row.previewURL ||= URL.createObjectURL(row.blob);img.src=row.previewURL;img.alt=row.name;
    const remove=el('button','','×');remove.type='button';remove.setAttribute('aria-label','移除 '+row.name);
    remove.onclick=()=>{if(sending||creating||preparingImages||newPreparingImages)return;releaseImage(row);rows.splice(index,1);renderImageDrafts(fresh);status()};
    tile.append(img,remove);return tile;
  });
  $(fresh?'new-image-preview':'image-preview').replaceChildren(...nodes);
}
function releaseImage(row){if(row.previewURL){URL.revokeObjectURL(row.previewURL);delete row.previewURL}}
async function compressImage(file){
  if(!file.type.startsWith('image/')||file.size>25*1024*1024)throw Error('请选择 25 MiB 以内的图片');
  // Both decoding and previews use blob: images, which the CSP explicitly permits.
  // Android's request interceptor also handles data: URLs, so avoid them for images.
  const url=URL.createObjectURL(file),img=new Image();
  try{
    img.src=url;
    try{await img.decode()}catch{throw Error('无法解码这张图片。请改用 JPG/PNG，或将图片截图后重试。')}
    const ratio=Math.min(1,2048/Math.max(img.naturalWidth,img.naturalHeight));
    const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(img.naturalWidth*ratio));canvas.height=Math.max(1,Math.round(img.naturalHeight*ratio));
    const ctx=canvas.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(img,0,0,canvas.width,canvas.height);
    let blob;for(const quality of [.88,.72,.55]){
      blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',quality));
      if(!blob)throw Error('图片处理失败，请换一张图片重试');
      if(blob.size<=2*1024*1024)break;
    }
    if(blob.size>2*1024*1024)throw Error('图片压缩后仍过大，请裁剪后再试');
    const data=await new Promise((resolve,reject)=>{
      const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);
      reader.onerror=()=>reject(Error('图片读取失败，请重新选择'));reader.onabort=()=>reject(Error('图片读取已取消，请重新选择'));
      reader.readAsDataURL(blob);
    });
    return {data,name:file.name||'图片',blob};
  }finally{URL.revokeObjectURL(url)}
}
async function chooseImages(input,fresh=false){
  const id=selected,rows=fresh?newImages:currentImages(),files=[...input.files];input.value='';
  if(!files.length)return;
  if(rows.length+files.length>4){$(fresh?'create-error':'send-error').textContent='每条消息最多 4 张图片';return;}
  if(fresh)newPreparingImages=true;else preparingImages=true;status();$('create').disabled=creating||newPreparingImages;
  try{
    const prepared=[];for(const file of files)prepared.push(await compressImage(file));rows.push(...prepared);
    if(!fresh)imageDrafts.set(id,rows);if(fresh||selected===id)renderImageDrafts(fresh);
    $(fresh?'create-error':'send-error').textContent='';
  }catch(error){$(fresh?'create-error':'send-error').textContent=error.message}
  finally{if(fresh)newPreparingImages=false;else preparingImages=false;status();$('create').disabled=creating||newPreparingImages}
}
$('attach').onclick=()=>oldApp?location.assign('/android'):$('image-file').click();$('image-file').onchange=()=>chooseImages($('image-file'));
$('new-attach').onclick=()=>oldApp?location.assign('/android'):$('new-image-file').click();$('new-image-file').onchange=()=>chooseImages($('new-image-file'),true);
if(oldApp)$('new-attach').textContent='更新 App 后选图';
function scheduleRefresh(elapsed=0){
  clearTimeout(refreshTimer);
  refreshTimer=setTimeout(async()=>{
    const started=performance.now();
    try{if(!document.hidden){await refresh();measureLatency()}}
    finally{scheduleRefresh(performance.now()-started)}
  },Math.max(100,polling.delay()-elapsed));
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden){refresh();measureLatency();scheduleRefresh()}});
setInterval(()=>{if(!document.hidden)renderMetrics()},1000);
refresh();measureLatency();scheduleRefresh();
