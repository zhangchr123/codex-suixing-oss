'use strict';
const $=id=>document.getElementById(id);
let csrf='';
const challenge=new URLSearchParams(location.hash.slice(1)).get('challenge')||'';
async function request(path,data){
  const response=await fetch(path,{cache:'no-store',...(data?{method:'POST',headers:{'Content-Type':'application/json','X-Viewer-CSRF':csrf},body:JSON.stringify(data)}:{})});
  const result=await response.json();
  if(response.status===401){$('login-note').hidden=false;throw Error('网页尚未登录，登录后刷新此页即可。')}
  if(!response.ok)throw Error(result.error||'连接失败，请重试');
  return result;
}
async function load(){
  try{
    const result=await request('/api/android/devices');csrf=result.csrfToken;$('login-note').hidden=true;
    $('pair').hidden=!/^[A-Za-z0-9_-]{43}$/.test(challenge);
    $('devices').replaceChildren();
    if(!result.devices.length)$('devices').textContent='还没有绑定手机。';
    for(const item of result.devices){
      const row=document.createElement('div');row.className='device';
      const label=document.createElement('span');label.textContent=item.name;
      const button=document.createElement('button');button.textContent='解除绑定';
      button.onclick=async()=>{if(!confirm('解除 '+item.name+' 的免密登录？'))return;button.disabled=true;try{await request('/api/android/revoke',{deviceId:item.id});await load()}catch(e){$('message').textContent=e.message;button.disabled=false}};
      row.append(label,button);$('devices').append(row);
    }
  }catch(e){$('devices').textContent=e.message}
}
$('pair').onclick=async()=>{
  $('pair').disabled=true;
  try{
    const result=await request('/api/android/pair',{challenge});
    // The code is bound to a verifier held only by the initiating installation.
    const target='intent://activate?code='+encodeURIComponent(result.code)+'#Intent;scheme=codexsuixing;package=org.codexsuixing.app;end';
    $('activate').href=target;$('activate').hidden=false;$('message').textContent='授权已准备好，点“返回 App 完成绑定”。';
  }catch(e){$('message').textContent=e.message;$('pair').disabled=false}
};
load();
