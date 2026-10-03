'use strict';
const assert=require('node:assert/strict');
const PollingCadence=require('./polling.js');
let now=1000;
const cadence=new PollingCadence(()=>now);
assert.equal(cadence.delay(),10000);
cadence.observeServer(5,true);
assert.equal(cadence.delay(),10000);
cadence.activate();
now+=179000;
cadence.observeServer(5,true); // An idle snapshot cannot cancel a just-sent message.
assert.equal(cadence.delay(),1000);
cadence.activate(); // Continued exchange renews the full three minutes.
now+=179999;
assert.equal(cadence.delay(),1000);
now+=1;
assert.equal(cadence.delay(),10000);
cadence.observeServer(1,true); // Another client or PC activity speeds this client up.
assert.equal(cadence.delay(),1000);
cadence.observeServer(5,true);
assert.equal(cadence.delay(),10000);
cadence.observeServer(1,true);
now+=60000; // A broken connection must not leave the browser fast forever.
assert.equal(cadence.delay(),10000);
cadence.observeServer(1,false);
assert.equal(cadence.delay(),10000);
console.log('Adaptive polling: activation, renewal, expiry and offline recovery passed');
