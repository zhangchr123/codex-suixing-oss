const iso=value=>new Date((Number(value)||0)*1000).toISOString();
const clean=text=>String(text||'').replace(/:chatgpt-content-reference\{[^}]*\}/g,'[图片或附件：请在电脑上的 ChatGPT 查看]');
export function chatSummary(row, cached) {
  return {id:row.id,kind:'chatgpt',title:row.title||'ChatGPT 对话',workspace:'ChatGPT',updatedAt:iso(row.updatedAt),status:row.status==='active'?'running':'idle',messages:cached?.messages||[],loaded:!!cached?.loaded,truncated:!!cached?.truncated};
}
export function chatTranscript(data, summary) {
  const messages=[];
  for(const turn of [...(data.turns||[])].reverse()) {
    for(const item of turn.items||[]) {
      if(item.type==='userMessage') {
        const text=(item.content||[]).map(c=>c.type==='text'?c.text:'[附件：请在电脑上的 ChatGPT 查看]').join('\n');
        if(text)messages.push({role:'user',text:clean(text),phase:'',time:iso(turn.startedAt)});
      } else if(item.type==='agentMessage' && item.text) {
        messages.push({role:'assistant',text:clean(item.text),phase:'final',time:iso(turn.completedAt||turn.startedAt)});
      }
    }
  }
  let characters=messages.reduce((n,m)=>n+m.text.length,0),trimmed=false;
  while(messages.length>1 && (characters>250000 || messages.length>300)){characters-=messages.shift().text.length;trimmed=true;}
  return {...summary,messages,loaded:true,truncated:trimmed||!!data.page?.hasMore||(data.turns||[]).some(t=>(t.items||[]).some(i=>i.truncated)),status:data.thread?.status?.type==='active'?'running':'idle',updatedAt:iso(data.thread?.updatedAt)};
}
