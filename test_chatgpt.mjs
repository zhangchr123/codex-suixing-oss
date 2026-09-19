import assert from 'node:assert/strict';
import {chatSummary,chatTranscript} from './chatgpt_format.mjs';
const summary=chatSummary({id:'chat',title:'聊天标题',updatedAt:100,status:'idle'});
assert.equal(summary.kind,'chatgpt');assert.equal(summary.loaded,false);
const transcript=chatTranscript({thread:{updatedAt:200,status:{type:'active'}},page:{hasMore:true},turns:[
  {startedAt:180,completedAt:190,items:[{type:'userMessage',content:[{type:'text',text:'第二轮'}]},{type:'agentMessage',text:'回复 :chatgpt-content-reference{index="0"}',truncated:true}]},
  {startedAt:100,completedAt:120,items:[{type:'userMessage',content:[{type:'text',text:'第一轮'}]},{type:'reasoning',text:'不要导出'},{type:'toolOutput',text:'秘密'},{type:'agentMessage',text:'# 标题'}]}
]},summary);
assert.deepEqual(transcript.messages.map(m=>m.role),['user','assistant','user','assistant']);
assert.equal(transcript.messages[0].text,'第一轮');assert.equal(transcript.messages[1].text,'# 标题');
assert.match(transcript.messages[3].text,/请在电脑/);assert.doesNotMatch(JSON.stringify(transcript),/秘密|不要导出/);
assert.equal(transcript.loaded,true);assert.equal(transcript.truncated,true);assert.equal(transcript.status,'running');
console.log('ChatGPT classification, ordering, formatting and private-item filtering passed');
