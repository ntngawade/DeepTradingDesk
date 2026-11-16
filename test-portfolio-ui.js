'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
async function test(){
 const elements=new Map(),calls=[],timers=[];let reject=false;
 const el=id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);};
 const context={Intl,Date,Number,String,Error,AbortSignal,document:{getElementById:el,hidden:false},setInterval(fn,ms){timers.push(ms);},
  fetch:async path=>{calls.push(path);return {ok:!reject,headers:{get:()=> 'application/json'},json:async()=>reject?{error:'Expired token'}:{clientId:'123',holdings:[{symbol:'BDL',quantity:40,availableQty:40,averagePrice:1004}],positions:[{symbol:'KNRCON',quantity:167,averagePrice:111.28,unrealizedProfit:100}],checkedAt:'2026-11-15T04:00:00Z'}};}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(__dirname+'/portfolio-view.js','utf8'),context);
 assert.equal(calls.length,1,'Read-only portfolio sync starts immediately on page load');
 context.TradeDeskPortfolio.setConnection(true);await new Promise(setImmediate);
 assert(el('portfolioHoldings').innerHTML.includes('BDL'));assert(el('portfolioPositions').innerHTML.includes('167'));
 reject=true;await context.TradeDeskPortfolio.refresh();assert(!el('portfolioHoldings').innerHTML.includes('BDL'),'A failed verification must not show holdings as current');
 assert(el('portfolioStatus').textContent.includes('Expired token'));assert(el('ongoingSyncStatus').textContent.includes('Expired token'),'Sync errors must be visible beside ongoing trades');assert(timers.includes(30000));assert(calls.every(path=>path==='/api/dhan/portfolio'));
 console.log('Passed: read-only portfolio, actual quantities, refresh cadence and clearing unverified account data.');
}
test().catch(error=>{console.error(error);process.exitCode=1;});
