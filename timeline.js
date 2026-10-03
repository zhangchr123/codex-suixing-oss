'use strict';
// Reconcile phone receipts with canonical history, including repeated identical messages.
function mergeTimeline(messages, receipts) {
  const rows=messages.map(m=>({...m})), used=new Set();
  for(const receipt of receipts) {
    const created=Number(receipt.created)*1000;
    let index=rows.findIndex((m,i)=>!used.has(i)&&m.role==='user'&&m.requestId===receipt.id);
    if(index<0 && ['sent','uncertain'].includes(receipt.status)) {
      const comparable=m=>receipt.args?.images?.length?m.text.trim().replace(/(?:\n(?:!\[附件图片\]\(\/api\/media\/[0-9a-f]{64}\.(?:jpg|png|gif|webp)\)|\[附件：请在电脑上的 Codex 查看\]))+$/g,'').trim():m.text.trim();
      index=rows.findIndex((m,i)=>!used.has(i)&&m.role==='user'&&!m.requestId&&
        comparable(m)===receipt.text.trim()&&Date.parse(m.time)>=created-10000&&Date.parse(m.time)<=created+7200000);
    }
    const delivery={status:receipt.status,detail:receipt.detail||'',id:receipt.id};
    if(index>=0) {used.add(index);rows[index].delivery=delivery;rows[index].requestId=receipt.id;}
    else rows.push({role:'user',phase:'',text:receipt.text,time:new Date(created).toISOString(),
      requestId:receipt.id,images:(receipt.args?.images||[]).map(i=>i.id),delivery});
  }
  return rows.map((m,i)=>({m,i})).sort((a,b)=>(Date.parse(a.m.time)||0)-(Date.parse(b.m.time)||0)||a.i-b.i).map(r=>r.m);
}
if(typeof module!=='undefined'&&module.exports)module.exports=mergeTimeline;
