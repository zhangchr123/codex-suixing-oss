"""Browser regression fixture: uses the real image encoder and production CSP; loopback only."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import re
root=Path(__file__).resolve().parent
def current_policy():return re.search(r'self.send_header\("Content-Security-Policy", "([^"]+)"\)',(root/'viewer.py').read_text(encoding='utf-8')).group(1)
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  if self.path=='/probe.js':
   app=(root/'app.js').read_text(encoding='utf-8')
   compression=app[app.index('async function compressImage(file){'):app.index('async function chooseImages(')]
   script=compression+'''\n(async()=>{
const results=[];
for(const type of ['image/png','image/jpeg']){
 const canvas=document.createElement('canvas');canvas.width=40;canvas.height=20;const ctx=canvas.getContext('2d');ctx.fillStyle='#e13232';ctx.fillRect(0,0,40,20);
 const blob=await new Promise(resolve=>canvas.toBlob(resolve,type));
 try{const output=await compressImage(new File([blob],'probe.'+(type==='image/png'?'png':'jpg'),{type}));const preview=new Image();preview.src=URL.createObjectURL(output.blob);await preview.decode();document.body.append(preview);results.push(type+': PASS '+preview.naturalWidth+'x'+preview.naturalHeight)}catch(e){results.push(type+': FAIL '+e.name+' '+e.message)}
}
try{await compressImage(new File(['invalid image'],'broken.png',{type:'image/png'}));results.push('invalid: FAIL accepted')}catch(e){results.push('invalid: rejected '+e.message)}
document.getElementById('result').textContent=results.join('\\n');
})();'''
   content=script.encode();mime='application/javascript; charset=utf-8'
  else:
   content=b'<!doctype html><html><meta charset="utf-8"><title>Image decode regression</title><body><h1>Image decode regression</h1><pre id="result">Running...</pre><script src="/probe.js"></script></body></html>';mime='text/html; charset=utf-8'
  self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Security-Policy',current_policy());self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
if __name__ == '__main__':
 print('Image decoding fixture at http://127.0.0.1:18768 (Ctrl+C to stop)',flush=True)
 ThreadingHTTPServer(('127.0.0.1',18768),Handler).serve_forever()
