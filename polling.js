'use strict';
class PollingCadence {
  constructor(clock=()=>performance.now()) {
    this.clock=clock;this.activeUntil=0;this.serverActiveUntil=0;
  }
  activate() { this.activeUntil=this.clock()+180000; }
  observeServer(interval, online) {
    this.serverActiveUntil=online&&interval===2?this.clock()+60000:0;
  }
  delay() {
    return this.clock()<Math.max(this.activeUntil,this.serverActiveUntil)?2000:5000;
  }
}
if(typeof module!=='undefined'&&module.exports)module.exports=PollingCadence;
