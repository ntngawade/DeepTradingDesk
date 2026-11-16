'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
async function test(){
 const elements=new Map(),calls=[];
 const el=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',disabled:false,classList:{add(){},remove(){}}});return elements.get(id);};
 let missing=true, reject=false;const stored=new Map();
 const context={console,Date,Intl,AbortSignal,Error,localStorage:{getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value)},setInterval(){},document:{getElementById:el,hidden:false,addEventListener(){}},fetch:async(path,options)=>{
  calls.push({path,options});
  if(missing)return {ok:false,status:404,headers:{get:()=> 'text/html'}};
  const connected=path.endsWith('/connect');
  if(connected&&reject)return {ok:false,status:401,headers:{get:()=> 'application/json'},json:async()=>({error:'Dhan rejected the token.'})};
  return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>({connected,hasSession:connected,profile:connected?{dhanClientId:'123'}:null})};
 }};
 vm.createContext(context);vm.runInContext(fs.readFileSync(__dirname+'/dhan-connection.js','utf8'),context);
 await new Promise(setImmediate);
 assert(el('dhanStatus').textContent.includes('does not provide the Dhan connection API'));
 el('dhanToken').value='fake-token-for-test-only';el('dhanClientId').value='123';
 await el('dhanForm').onsubmit({preventDefault(){}});
 assert(calls.every(c=>c.options.method==='GET'),'Never send credentials without a healthy backend');
 assert.equal(el('dhanToken').value,'fake-token-for-test-only');
 missing=false;reject=true;el('dhanRemember').checked=true;
 await el('dhanForm').onsubmit({preventDefault(){}});
 assert.equal(el('dhanToken').value,'fake-token-for-test-only','Failed connection must retain the input for retry');
 reject=false;
 await el('dhanForm').onsubmit({preventDefault(){}});
 assert.equal(calls.at(-1).options.method,'POST');
 assert.equal(JSON.parse(calls.at(-1).options.body).remember,true);
 assert.equal(el('dhanToken').value,'');
 assert.equal(el('dhanClientId').value,'123');assert.equal(stored.get('deepDesk.dhanClientId'),'123');
 assert(![...stored.values()].includes('fake-token-for-test-only'),'Token must never enter browser storage');
 assert.equal(el('dhanBadge').textContent,'Dhan / Connected');
 console.log('Passed: missing-backend diagnosis, credential submission guard, recovery and connected status.');
}
test().catch(error=>{console.error(error);process.exitCode=1;});
