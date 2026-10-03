'use strict';
function cleanDisplayText(value,role='assistant'){
  let text=String(value||'');
  if(role==='user'&&text.trimStart().startsWith('# Files mentioned by the user:')){
    const request=/^#{1,6} My request:\s*\n/m.exec(text);if(request)text=text.slice(request.index+request[0].length);
  }
  const protectedCode=[];
  // Keep literal HTML/XML in code examples; remove only app metadata envelopes.
  text=text.replace(/(^|\n)(`{3,}|~{3,})[^\n]*\n[\s\S]*?\n\2[^\n]*(?=\n|$)|(`+)[^\n]*?\3/g,(code)=>{const key='\uE000'+protectedCode.length+'\uE001';protectedCode.push(code);return key;});
  const tags=['oai-mem-citation','codex_suixing_message_id'];
  if(role==='user')tags.push('codex_internal_context','heartbeat','in-app-browser-context','external_codex_apps_open_page','app-context','environment_context','skills_instructions','recommended_plugins','collaboration_mode','subagent_notification');
  for(const tag of tags)text=text.replace(new RegExp('<'+tag+'(?:\\s[^>]*)?>[\\s\\S]*?<\\/'+tag+'>','g'),'');
  if(role==='user')text=text.replace(/<\/?image\b[^>]*>/g,'').replace(/^\s*#{1,6} My request:\s*/,'');
  text=text.replace(/\uE000(\d+)\uE001/g,(_,index)=>protectedCode[Number(index)]);
  return text.trim();
}
if(typeof module!=='undefined')module.exports={cleanDisplayText};
