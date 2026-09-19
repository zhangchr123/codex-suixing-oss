// Local-only adapter for the installed Codex App Tools named-pipe protocol.
// This process accepts only conversation reads, Codex task creation and messages.
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { chatSummary, chatTranscript } from './chatgpt_format.mjs';

const root = path.dirname(fileURLToPath(import.meta.url));
const stateDir = path.resolve(process.env.CODEX_SUIXING_STATE_DIR || path.join(root, '.state'));
const statePath = path.join(stateDir, 'desktop.json');
const context = process.env.CODEX_SUIXING_CONTEXT_THREAD_ID || '';
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
let currentPipe = process.env.CODEX_APP_TOOLS_PIPE_PATH;
let allowed = new Set();
let knownThreadIds = [];
let projects = [];
let chats=[], chatCatalogAt=0;
let chatPipeCheckedAt=0;
const chatCache=new Map();
try { currentPipe ||= JSON.parse(fs.readFileSync(statePath, 'utf8')).pipe; } catch {}

function rpc(pipe, method, params, timeout = 12000) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(pipe);
    let buffer = Buffer.alloc(0), settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true; socket.destroy(); clearTimeout(timer);
      error ? reject(error) : resolve(value);
    };
    const timer = setTimeout(() => finish(new Error('桌面端响应超时')), timeout);
    socket.on('error', error => finish(error));
    socket.on('close', () => finish(new Error('桌面端连接中断')));
    socket.on('connect', () => {
      const payload = Buffer.from(JSON.stringify({jsonrpc:'2.0', id:1, method, params}));
      const frame = Buffer.alloc(payload.length + 4);
      frame.writeUInt32LE(payload.length); payload.copy(frame, 4); socket.write(frame);
    });
    socket.on('data', chunk => {
      buffer = Buffer.concat([buffer, chunk]);
      if (buffer.length < 4) return;
      const length = buffer.readUInt32LE();
      if (length > 8 * 1024 * 1024) return finish(new Error('桌面响应超过限制'));
      if (buffer.length < length + 4) return;
      try {
        const response = JSON.parse(buffer.subarray(4, length + 4).toString('utf8'));
        if (response.error) finish(new Error(response.error.message));
        else finish(null, response.result);
      } catch (error) { finish(error); }
    });
  });
}

async function tool(pipe, name, args, id='status') {
  const result = await rpc(pipe, 'tools/call', {
    namespace:'codex_app', tool:name, arguments:args, threadId:context,
    callId:'codex-viewer-'+id+'-'+randomUUID(), turnId:'codex-viewer-'+id
  }, 20000);
  const text = result.contentItems?.filter(x=>x.type==='inputText').map(x=>x.text).join('\n') || '';
  return {success:result.success === true, text};
}

async function status() {
  if (!uuid.test(context)) return {connected:false, threadIds:[], error:'请在电脑端配置一个自己的 Codex 上下文任务 ID'};
  const candidates = [currentPipe];
  if (process.platform === 'win32' && !process.env.CODEX_APP_TOOLS_PIPE_PATH) {
    for (const name of fs.readdirSync('\\\\.\\pipe\\')) {
      if (/^codex-browser-use-[0-9a-f-]+$/.test(name)) candidates.push('\\\\.\\pipe\\'+name);
    }
  }
  for (const pipe of [...new Set(candidates.filter(Boolean))]) {
    try {
      const catalog = await rpc(pipe, 'tools/list', {threadStartKind:'all'}, 1500);
      if (!catalog.tools?.some(t=>t.namespace==='codex_app'&&t.name==='send_message_to_thread')) continue;
      // The sidebar inventory omits some projectless tasks. Only the local log
      // exporter supplies this allowlist; a phone request cannot add IDs to it.
      allowed = new Set(knownThreadIds);
      currentPipe = pipe;
      const projectResult = await tool(pipe, 'list_projects', {});
      projects = projectResult.success ? JSON.parse(projectResult.text).projects.filter(p=>p.projectKind==='local'&&(!p.hostId||p.hostId==='local')) : [];
      fs.mkdirSync(path.dirname(statePath), {recursive:true, mode:0o700});
      fs.writeFileSync(statePath, JSON.stringify({pipe}), {mode:0o600});
      return {connected:true, threadIds:[...allowed], projects:projects.map(p=>({id:p.projectId,label:p.label}))};
    } catch (error) { if(process.env.CODEX_VIEWER_DEBUG) console.error(error.message); }
  }
  currentPipe = null; allowed.clear();
  return {connected:false, threadIds:[], error:process.platform==='win32'?'请在电脑上打开 Codex':'请打开 Codex，并配置当前用户的 App Tools Unix socket 路径'};
}

async function handle(input) {
  if(input.mode==='chat_snapshot') {
    if(!currentPipe || Date.now()-chatPipeCheckedAt>30000) {
      const live=await status();
      if(!live.connected)return {connected:false,threads:[]};
      chatPipeCheckedAt=Date.now();
    }
    if(Date.now()-chatCatalogAt>30000 || !chats.length) {
      const result=await tool(currentPipe,'list_threads',{limit:50});
      if(!result.success)throw Error('无法读取桌面 ChatGPT 列表');
      const listing=JSON.parse(result.text);
      chats=[...new Map([...(listing.pinnedThreads||[]),...(listing.threads||[])].filter(t=>t.kind==='chatgpt'&&uuid.test(t.id)).map(t=>[t.id,t])).values()].slice(0,30);
      for(const id of chatCache.keys())if(!chats.some(t=>t.id===id))chatCache.delete(id);
      chatCatalogAt=Date.now();
    }
    const wanted=new Set(Array.isArray(input.threadIds)?input.threadIds.slice(0,3):[]);
    for(const row of chats.filter(t=>wanted.has(t.id))) {
      const result=await tool(currentPipe,'read_thread',{threadId:row.id,turnLimit:10,includeOutputs:false,maxOutputCharsPerItem:20000});
      if(!result.success)continue;
      const data=JSON.parse(result.text);
      if(data.thread?.kind==='chatgpt'&&data.thread.id===row.id)chatCache.set(row.id,chatTranscript(data,chatSummary(row)));
    }
    return {connected:true,threads:chats.map(row=>{
      const cached=chatCache.get(row.id),summary=chatSummary(row,cached);
      return cached?{...cached,title:summary.title,updatedAt:cached.updatedAt>summary.updatedAt?cached.updatedAt:summary.updatedAt}:summary;
    })};
  }
  if (input.mode === 'status') {
    knownThreadIds = Array.isArray(input.threadIds) ? input.threadIds.filter(id=>typeof id==='string'&&uuid.test(id)).slice(0,100) : [];
    return status();
  }
  if (!['send','create'].includes(input.mode) || input.mode==='send'&&!uuid.test(input.threadId||'') || !uuid.test(input.id||'') || typeof input.text !== 'string' || !input.text.trim() || input.text.length > 8000 || ![undefined,'codex','chatgpt'].includes(input.channel)) {
    return {status:'failed', detail:'无效的消息请求'};
  }
  const live = await status();
  const chat=input.channel==='chatgpt';
  if (!live.connected || input.mode==='send'&&!chat&&!allowed.has(input.threadId)) return {status:'failed', detail:'该对话当前无法在桌面应用中发送'};
  if(chat && input.mode==='create')return {status:'failed',detail:'请先在电脑 ChatGPT Chat 模式新建聊天，再从手机继续'};
  let prompt=input.text;
  if(input.imagePaths?.length) {
    if(chat || !Array.isArray(input.imagePaths) || input.imagePaths.length>4)return {status:'failed',detail:'此聊天暂不支持手机图片传入'};
    const folder=path.join(stateDir,'incoming');
    for(const file of input.imagePaths) {
      if(typeof file!=='string' || path.dirname(path.resolve(file))!==folder || !/^[0-9a-f]{64}\.(jpg|png)$/.test(path.basename(file)) || !fs.existsSync(file))return {status:'failed',detail:'图片文件无效'};
    }
    prompt+='\n\n<codex_suixing_images>\n这些图片由用户从手机上传。请用 view_image 查看以下本机文件，再回答用户的问题：\n'+input.imagePaths.map(file=>JSON.stringify(file)).join('\n')+'\n</codex_suixing_images>';
  }
  // Never retry a send after an ambiguous failure: it may already be accepted.
  try {
    let result;
    if (input.mode==='create') {
      let target = {type:'projectless'};
      if (input.projectId) {
        const project = projects.find(p=>p.projectId===input.projectId);
        if (!project) return {status:'failed',detail:'所选项目当前不可用，请重新选择'};
        target = {type:'project', projectId:project.projectId, environment:{type:project.isGitRepository?'worktree':'local'}};
      }
      result = await tool(currentPipe, 'create_thread', {prompt, target, ...(input.title?{title:input.title.slice(0,100)}:{})}, input.id);
    } else {
      const check = await tool(currentPipe, 'read_thread', {threadId:input.threadId,...(!chat?{hostId:'local'}:{}),turnLimit:1,includeOutputs:false}, input.id);
      const info = check.success ? JSON.parse(check.text) : {};
      if (info.thread?.kind!==(chat?'chatgpt':'codex') || info.thread?.id!==input.threadId || (!chat&&info.thread?.hostId!=='local')) {
        return {status:'failed',detail:'对话类型或归属不匹配，请在电脑上检查'};
      }
      result = await tool(currentPipe, 'send_message_to_thread', {threadId:input.threadId, ...(!chat?{hostId:'local'}:{}), prompt}, input.id);
    }
    if (!result.success) return {status:'failed', detail:result.text.slice(0,500)||'桌面端未接受消息'};
    if (input.mode==='create') {
      let info={}; try { info=JSON.parse(result.text); } catch {}
      return {status:'sent',detail:info.threadId?'新对话已创建':'新对话正在准备，完成后会出现在列表中',resultThreadId:info.threadId||''};
    }
    return {status:'sent', detail:chat?'已发送到桌面 ChatGPT 聊天':'已发送到电脑上的 Codex'};
  } catch {
    return {status:'uncertain', detail:'发送结果未确认，请先查看对话；为避免重复，没有自动重发。'};
  }
}

const lines = readline.createInterface({input:process.stdin, crlfDelay:Infinity});
for await (const line of lines) {
  try { process.stdout.write(JSON.stringify(await handle(JSON.parse(line)))+'\n'); }
  catch { process.stdout.write(JSON.stringify({connected:false,status:'failed',detail:'桌面连接暂时不可用'})+'\n'); }
}
