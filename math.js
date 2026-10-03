'use strict';
function installMath(md,katex){
  md.inline.ruler.before('escape','suixing_math',(state,silent)=>{
    const s=state.src,p=state.pos;
    let open,close,display=false;
    if(s.startsWith('\\[',p)){open='\\[';close='\\]';display=true}
    else if(s.startsWith('\\(',p)){open='\\(';close='\\)'}
    else if(s.startsWith('$$',p)){open=close='$$';display=true}
    else if(s[p]==='$'&&!/\s/.test(s[p+1]||' ')){open=close='$'}
    else return false;
    let end=s.indexOf(close,p+open.length);
    while(end>=0&&s[end-1]==='\\')end=s.indexOf(close,end+close.length);
    if(end<0||end===p+open.length)return false;
    const content=s.slice(p+open.length,end);
    if(!display&&(/\n/.test(content)||/^\d+(?:\.\d+)?$/.test(content)||open==='$'&&(/\s$/.test(content)||/\d/.test(s[end+1]||''))))return false;
    if(!silent){const token=state.push('suixing_math','',0);token.content=content;token.meta={display}}
    state.pos=end+close.length;return true;
  });
  md.renderer.rules.suixing_math=(tokens,i)=>{
    try{return katex.renderToString(tokens[i].content,{displayMode:tokens[i].meta.display,throwOnError:false,trust:false,strict:'ignore',maxExpand:1000,maxSize:20})}
    catch{return md.utils.escapeHtml(tokens[i].content)}
  };
}
if(typeof module!=='undefined'&&module.exports)module.exports=installMath;
