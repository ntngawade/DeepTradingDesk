'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
async function test(){
 const elements=new Map(),calls=[],timers=[];let clickHandler,enabled=false;const records=[];
 const el=id=>{if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:'',disabled:false,className:'',classList:{add(){},remove(){}}});return elements.get(id);};
 const plans=[{id:'P1',symbol:'JIOFIN',status:'Watching',enabled:true},{id:'P2',symbol:'BSOFT',status:'Watching',enabled:true}];
 const context={Intl,Date,String,AbortSignal,Error,document:{getElementById:el,querySelector:()=>({click(){}}),body:{addEventListener(type,fn){clickHandler=fn;}}},setInterval(fn,ms){timers.push(ms);},TradeDeskData:{watchingPlans:()=>plans,plan:id=>plans.find(p=>p.id===id),settings:()=>({capital:500000,risk:1}),setPlanFlags(){},setLiveRecords(){}},fetch:async(path,options)=>{
  calls.push({path,options});let data;
  if(path.endsWith('/preview'))data={previewId:'REVIEW-'+JSON.parse(options.body).plan.id,tradeId:JSON.parse(options.body).plan.id,symbol:JSON.parse(options.body).plan.symbol,entry:1000,sl:980,target:1040,quantity:250,cash:250000,risk:5000,tickSize:0.05,triggerPercent:1};
  else{if(path.endsWith('/enable'))enabled=true;if(path.endsWith('/arm')){const id=JSON.parse(options.body).previewId.replace('REVIEW-','');records.push({tradeId:id,symbol:id==='P1'?'JIOFIN':'BSOFT',quantity:250,filledQty:0,entry:1000,sl:980,target:1040,state:'ARMED',message:'Waiting',updatedAt:String(records.length+1)});}if(path.endsWith('/pause'))enabled=false;data={enabled,records:records.slice(),marketSession:true};}
  return {ok:true,headers:{get:()=> 'application/json'},json:async()=>data};
 }};
 vm.createContext(context);vm.runInContext(fs.readFileSync(__dirname+'/live-controls.js','utf8'),context);await new Promise(setImmediate);
 assert(calls.every(c=>c.path.endsWith('/status')),'Opening the page must never enable or queue live trading');
 await el('liveEnable').onclick();assert(JSON.parse(calls.find(c=>c.path.endsWith('/enable')).options.body).acknowledgeRealOrders);
 assert.equal(calls.filter(c=>c.path.endsWith('/preview')).length,2,'Global live enable automatically prechecks every enabled Watching plan');
 assert.equal(calls.filter(c=>c.path.endsWith('/arm')).length,2,'Passing plans are queued without a separate Arm click');
 assert(el('liveState').textContent.includes('2 enabled plan(s) queued'));
 const third={id:'P3',symbol:'CIPLA',status:'Watching',enabled:true};const result=await context.LiveDesk.activatePlan(third);assert(result.armed,'Enabling one plan while global live mode is active auto-queues it');
 assert.equal(calls.filter(c=>c.path.endsWith('/arm')).length,3);
 await el('livePause').onclick();assert.equal(enabled,false);assert.equal(records.length,3,'Pause keeps queued broker monitoring records intact');
 assert(timers.includes(5000));assert(!calls.some(c=>c.path.includes('/super/orders')));
 console.log('Passed: no live activation on page load; global enable prechecks and queues enabled plans; enabling one plan while live is on auto-queues it; pause blocks new entries without cancelling queued records.');
}
test().catch(error=>{console.error(error);process.exitCode=1;});
