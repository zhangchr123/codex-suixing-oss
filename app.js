'use strict';
const $ = id => document.getElementById(id);
const md = window.markdownit({html:false, breaks:true, linkify:true, typographer:false});
md.renderer.rules.image = (tokens, i) => md.utils.escapeHtml('[图片：'+(tokens[i].content||'请在电脑查看')+']');
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
const oldApp=!!appVersion&&parseFloat(appVersion)<1.2;
let refreshTimer;
let channel='codex',preparingImages=false,newPreparingImages=false;
const imageDrafts=new Map();let newImages=[];
const drafts = new Map();
const expandedDeliveries=new Set(), messageExpansion=new Map();
const names={running:'处理中',idle:'已回复',stopped:'已停止',unknown:'对话'};
const deliveryNames={queued:'等待电脑接收',delivering:'正在发送到电脑',sent:'已发送',failed:'发送失败',uncertain:'结果待确认'};
function date(s){return s?new Date(s).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):''}
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}
async function api(url,options={}) {
  const r=await fetch(url,{cache:'no-store',...options});
  const data=await r.json();
  if(r.status===401){$('shell').hidden=true;$('auth').hidden=false;throw new Error(data.error||'请先登录')}
  if(!r.ok)throw new Error(data.error||'连接暂时不可用');
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
function canSend(){return online()&&(isChat()?(bridge.chatgptConnected&&(bridge.chatgptThreadIds||[]).includes(selected)):(bridge.threadIds||[]).includes(selected))}
function currentImages(){return imageDrafts.get(selected)||[]}
function status(){
  const stale=syncAge()>90000;
  $('dot').classList.toggle('off',stale);
  $('sync').textContent=!syncedAt?'等待电脑首次同步':stale?'同步暂停 · 最后同步 '+date(syncedAt):'同步正常 · '+date(syncedAt);
  $('read-status').textContent=stale?'当前显示上次同步的内容':polling.delay()===2000?'交流中 · 每 2 秒刷新':'空闲 · 每 5 秒刷新';
  $('new-thread').disabled=!online()||channel==='chatgpt';
  $('new-thread').textContent=channel==='chatgpt'?'请先在电脑新建 ChatGPT 聊天':'＋ 新建 Codex 任务';
  $('channel-note').textContent=channel==='chatgpt'?'使用桌面已登录的 ChatGPT 账号，可查看和文字续聊。新建聊天和添加图片请在电脑完成。':'本机项目与独立 Codex 任务，可发送文字和图片。';
  $('codex-tab').setAttribute('aria-pressed',String(channel==='codex'));
  $('chatgpt-tab').setAttribute('aria-pressed',String(channel==='chatgpt'));
  $('connection-pill').textContent=online()?'电脑在线':'电脑离线';
  $('composer').hidden=!selected;
  $('send').disabled=sending||preparingImages||!canSend()||(!$('message').value.trim()&&!currentImages().length);
  $('attach').hidden=isChat();$('attach').disabled=sending||preparingImages||!canSend();$('attach').textContent=oldApp?'更新后选图':'＋ 图片';
  $('send').textContent=sending?'发送中…':'发送 ↑';
  $('composer-hint').textContent=preparingImages?'正在处理图片…':!online()?'电脑未连接，暂时不能发送':!canSend()?'桌面暂未连接此对话':isChat()?'通过电脑上的 ChatGPT 继续聊天':'消息会发到电脑，沿用当前任务的模型与设置';
  renderMetrics();
}
function list(){
  const q=$('search').value.toLowerCase();
  const group=threads.filter(t=>(t.kind==='chatgpt'?'chatgpt':'codex')===channel);
  const filtered=group.filter(t=>(t.title+' '+t.workspace).toLowerCase().includes(q));
  const signature=JSON.stringify([filtered,selected]);
  if(signature===lastListing)return;lastListing=signature;
  const nodes=filtered.map(t=>{
    const button=el('button','thread'+(selected===t.id?' active':''));button.type='button';
    button.append(el('div','thread-title',t.title));
    const meta=el('div','thread-meta');
    meta.append(el('span','',(t.workspace||'').split(/[\\/]/).filter(Boolean).pop()||'Codex'),el('span','',date(t.updatedAt)));
    button.append(meta);button.onclick=()=>select(t.id).catch(showError);return button;
  });
  $('list').replaceChildren(...nodes);
  if(!nodes.length)$('list').append(el('div','small',group.length?'没有匹配的对话':channel==='chatgpt'?'等待桌面同步 ChatGPT 聊天列表…':'等待电脑同步任务…'));
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
  return div;
}
function showError(error){$('send-error').textContent=error.message}
async function select(id){
  if(selected)drafts.set(selected,$('message').value);
  selected=id;channel=isChat(id)?'chatgpt':'codex';rendered='';$('message').value=drafts.get(id)||'';$('send-error').textContent='';renderImageDrafts();
  history.replaceState(null,'','#'+id);$('shell').classList.add('open');list();status();await refreshThread(true);
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
  $('title').textContent=data.title;$('workspace').textContent=(data.workspace||'')+' · '+(names[data.status]||'对话');
  deliveries(data.deliveries||[]);
  const signature=JSON.stringify([data.messages,data.truncated,data.loaded]);if(signature===rendered&&!force)return;rendered=signature;
  const box=$('timeline'),old=box.scrollTop,atEnd=box.scrollHeight-box.clientHeight-old<100,nodes=[];
  if(data.kind==='chatgpt')nodes.push(el('div','notice',data.loaded?'当前显示最近最多 10 轮聊天。更早的历史和原有附件请在电脑查看。':'正在从电脑读取聊天内容…首次打开可能需要几秒。'));
  else if(data.truncated)nodes.push(el('div','notice','这是一段较长的对话，当前展示最近的内容。完整历史请在电脑查看。'));
  for(const m of data.messages){
    const article=el('article','message '+(m.role==='user'?'user':m.phase==='commentary'?'progress':'assistant'));
    const label=el('div','message-label');label.append(el('span','',m.role==='user'?'你':data.kind==='chatgpt'?'ChatGPT':m.phase==='commentary'?'Codex · 进度':'Codex'),el('time','',date(m.time)));
    article.append(label);
    if(m.role==='user'){
      const key=id+':'+m.time+':'+m.text,details=el('details','user-message'),summary=el('summary');
      details.open=messageExpansion.has(key)?messageExpansion.get(key):m.text.length<=400;
      const update=()=>{summary.textContent=details.open?'收起内容':m.text.replace(/\s+/g,' ').slice(0,70)+(m.text.length>70?'…':'')+' · 展开';};
      details.append(summary,body(m.text));if(m.images?.length)details.append(imageGallery(m.images));update();
      details.ontoggle=()=>{messageExpansion.set(key,details.open);update()};article.append(details);
    }else article.append(body(m.text));
    nodes.push(article);
  }
  box.replaceChildren(...nodes);box.scrollTop=force||atEnd?box.scrollHeight:old;
}
async function refresh(){
  if(busy)return;busy=true;
  try{
    const data=await api('/api/threads');threads=data.threads;syncedAt=data.syncedAt;bridge=data.bridge||{};csrfToken=data.csrfToken;
    polling.observeServer(data.pollInterval, online());
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
  const key='send:'+id,payload={text,images:images.map(i=>({data:i.data}))};payload.requestId=requestId(key,payload);sending=true;status();$('send-error').textContent='';
  try{await post('/api/threads/'+id+'/messages',payload);delete pending[key];drafts.delete(id);images.forEach(releaseImage);imageDrafts.delete(id);if(selected===id){$('message').value='';renderImageDrafts()}await refreshThread()}
  catch(error){showError(error)}finally{sending=false;status()}
};
$('message').oninput=()=>{drafts.set(selected,$('message').value);status()};
$('message').onkeydown=event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();$('composer').requestSubmit()}};
$('new-thread').onclick=()=>{
  if(channel==='chatgpt')return;
  const options=[new Option('独立对话（不绑定项目）',''),...(bridge.projects||[]).map(p=>new Option(p.label,p.id))];
  $('new-project').replaceChildren(...options);$('create-error').textContent='';$('new-dialog').showModal();
};
$('cancel-create').onclick=()=>$('new-dialog').close();
$('new-form').onsubmit=async event=>{
  event.preventDefault();if(creating||newPreparingImages||!online())return;
  const payload={text:$('new-message').value.trim(),title:$('new-title').value.trim(),projectId:$('new-project').value,images:newImages.map(i=>({data:i.data}))};
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
  $('title').textContent=value==='chatgpt'?'选择一个 ChatGPT 聊天':'选择一个 Codex 任务';$('workspace').textContent='';
  $('timeline').replaceChildren(el('div','empty',value==='chatgpt'?'电脑上的 ChatGPT 账号与历史聊天':'电脑上的项目与独立 Codex 任务'));
  $('shell').classList.remove('open');status();list();
}
$('codex-tab').onclick=()=>switchChannel('codex');$('chatgpt-tab').onclick=()=>switchChannel('chatgpt');
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
function scheduleRefresh(){
  clearTimeout(refreshTimer);
  refreshTimer=setTimeout(async()=>{
    try{if(!document.hidden){await refresh();measureLatency()}}
    finally{scheduleRefresh()}
  },polling.delay());
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden){refresh();measureLatency();scheduleRefresh()}});
setInterval(()=>{if(!document.hidden)renderMetrics()},1000);
refresh();measureLatency();scheduleRefresh();
