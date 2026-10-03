// An isolated fake named pipe exercises routing without sending real chat messages.
import assert from 'node:assert/strict';
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import readline from 'node:readline';
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {fileURLToPath} from 'node:url';

const root=path.dirname(fileURLToPath(import.meta.url));
const fixture=fs.mkdtempSync(path.join(process.platform==='darwin'?'/tmp':os.tmpdir(),'suixing-test-'));
for(const name of ['desktop_bridge.mjs','chatgpt_format.mjs'])fs.copyFileSync(path.join(root,name),path.join(fixture,name));
fs.mkdirSync(path.join(fixture,'.state','incoming'),{recursive:true});
const imagePath=path.join(fixture,'.state','incoming','a'.repeat(64)+'.png');
fs.writeFileSync(imagePath,'test-only image path; decoder is tested separately');
const pipe=process.platform==='win32'?'\\\\.\\pipe\\codex-suixing-test-'+randomUUID():path.join(fixture,'ipc.sock');
const codex='11111111-1111-1111-1111-111111111111',chat='22222222-2222-2222-2222-222222222222';
const calls=[];
const server=net.createServer(socket=>{
  let input=Buffer.alloc(0);
  socket.on('error',()=>{});
  socket.on('data',chunk=>{
    input=Buffer.concat([input,chunk]);
    if(input.length<4 || input.length<input.readUInt32LE(0)+4)return;
    const request=JSON.parse(input.subarray(4)),params=request.params;
    let result;
    if(request.method==='tools/list')result={tools:[{namespace:'codex_app',name:'send_message_to_thread',inputSchema:{properties:{model:{description:'Models: gpt-5.5 (Legacy model.; supported reasoning efforts: low, medium, high, xhigh), gpt-6.1-sol (Latest.; supported reasoning efforts: low, medium, high, xhigh, max, ultra).'},thinking:{enum:['low','medium','high','xhigh','max','ultra']}}}}]};
    else {
      assert.equal(params.callerSource,'codex');
      calls.push(params);
      let data={};
      if(params.tool==='list_projects')data={projects:[]};
      if(params.tool==='read_thread')data={thread:{id:params.arguments.threadId,kind:params.arguments.threadId===chat?'chatgpt':'codex',hostId:'local'}};
      result={success:true,contentItems:[{type:'inputText',text:JSON.stringify(data)}]};
    }
    const raw=Buffer.from(JSON.stringify({jsonrpc:'2.0',id:request.id,result})),frame=Buffer.alloc(raw.length+4);
    frame.writeUInt32LE(raw.length);raw.copy(frame,4);socket.end(frame);
  });
});
await new Promise(resolve=>server.listen(pipe,resolve));
const child=spawn(process.execPath,[path.join(fixture,'desktop_bridge.mjs')],{cwd:fixture,env:{...process.env,CODEX_SUIXING_CONTEXT_THREAD_ID:codex,CODEX_APP_TOOLS_PIPE_PATH:pipe},stdio:['pipe','pipe','pipe']});
const lines=readline.createInterface({input:child.stdout})[Symbol.asyncIterator]();
async function request(data){child.stdin.write(JSON.stringify(data)+'\n');return JSON.parse((await lines.next()).value);}
try{
  const live=await request({mode:'status',threadIds:[codex]});
  assert.equal(live.connected,true);assert.equal(live.models.length,2);
  assert.equal((await request({mode:'send',id:randomUUID(),threadId:codex,text:'Codex text',channel:'codex'})).status,'sent');
  assert.equal((await request({mode:'send',id:randomUUID(),threadId:chat,text:'Chat text',channel:'chatgpt'})).status,'sent');
  assert.equal((await request({mode:'send',id:randomUUID(),threadId:codex,text:'wrong kind',channel:'chatgpt'})).status,'failed');
  assert.equal((await request({mode:'create',id:randomUUID(),text:'chat creation',channel:'chatgpt'})).status,'failed');
  assert.equal((await request({mode:'send',id:randomUUID(),threadId:codex,text:'bad path',imagePaths:['C:/not-an-image.txt']})).status,'failed');
  assert.equal((await request({mode:'send',id:randomUUID(),threadId:codex,text:'view image',imagePaths:[imagePath]})).status,'sent');
  const sends=calls.filter(c=>c.tool==='send_message_to_thread').map(c=>c.arguments);
  assert.equal(sends[0].threadId,codex);assert.equal(sends[0].hostId,'local');
  assert.equal(sends[0].prompt,'Codex text');assert.equal(sends[0].model,undefined);
  assert.equal(sends[1].threadId,chat);assert.equal(sends[1].hostId,undefined);
  assert.equal(sends[1].prompt,'Chat text');
  assert.equal(sends.length,3);assert.ok(sends[2].prompt.includes(imagePath));assert.match(sends[2].prompt,/view_image/);assert.ok(!sends[2].prompt.includes('<codex_suixing'));
  assert.equal(calls.filter(c=>c.tool==='create_thread').length,0);
  assert.equal((await request({mode:'send',id:randomUUID(),threadId:codex,text:'override',model:'gpt-6.1-sol',thinking:'ultra'})).status,'sent');
  const override=calls.at(-1).arguments;assert.equal(override.model,'gpt-6.1-sol');assert.equal(override.thinking,'ultra');
  const before=calls.filter(c=>c.tool==='send_message_to_thread').length;
  for(const params of [{model:'gpt-5.5',thinking:'ultra'},{thinking:'high'},{model:'fake'},{channel:'chatgpt',threadId:chat,model:'gpt-5.5'}])assert.equal((await request({mode:'send',id:randomUUID(),threadId:codex,text:'reject',...params})).status,'failed');
  assert.equal(calls.filter(c=>c.tool==='send_message_to_thread').length,before);
  assert.equal((await request({mode:'create',id:randomUUID(),text:'new task',model:'gpt-5.5',thinking:'high'})).status,'sent');
  assert.equal(calls.at(-1).arguments.model,'gpt-5.5');assert.equal(calls.at(-1).arguments.thinking,'high');
  console.log('Desktop named-pipe routing: Codex/ChatGPT separated, cross-kind sends and unsupported creation rejected');
}finally{
  child.stdin.end();child.kill();await new Promise(resolve=>server.close(resolve));
  for(const name of ['desktop_bridge.mjs','chatgpt_format.mjs','.state/desktop.json'])if(fs.existsSync(path.join(fixture,name)))fs.unlinkSync(path.join(fixture,name));
  fs.unlinkSync(imagePath);fs.rmdirSync(path.dirname(imagePath));fs.rmdirSync(path.join(fixture,'.state'));fs.rmdirSync(fixture);
}
