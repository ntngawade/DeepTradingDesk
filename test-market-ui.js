'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
async function test(){
 const elements=new Map(),timers=[],calls=[],applied=[],cleared=[];
 const el=id=>{if(!elements.has(id))elements.set(id,{textContent:'',disabled:false});return elements.get(id);};
 let fail=false,empty=false;
 const NativeDate=Date;class TestDate extends NativeDate{constructor(...args){super(...(args.length?args:['2026-11-17T04:00:00Z']));}}
 const context={Date:TestDate,Intl,AbortSignal,Error,Set,document:{getElementById:el,hidden:false,addEventListener(){}},setInterval(fn,delay){timers.push({fn,delay});},TradeDeskData:{symbols:()=>['JIOFIN','UNKNOWN'],applyQuotes:data=>applied.push(data),clearQuotes:text=>cleared.push(text)},fetch:async(path,options)=>{
  calls.push({path,options});return {ok:!fail,headers:{get:()=> 'application/json'},json:async()=>fail?{error:'Data subscription required'}:empty?{quotes:{},unresolved:[],missing:['JIOFIN'],quoteDiagnostics:{apiStatus:'success',requestedInstrumentCount:1,returnedInstrumentCount:0,returnedSegmentNames:['NSE_EQ']},fetchedAt:'2026-11-14T05:00:00Z'}:{quotes:{JIOFIN:{price:250,source:'Dhan',fetchedAt:'2026-11-14T05:00:00Z'}},unresolved:['UNKNOWN'],missing:[],fetchedAt:'2026-11-14T05:00:00Z'}};
 }};
 vm.createContext(context);vm.runInContext(fs.readFileSync(__dirname+'/market-data.js','utf8'),context);
 await el('refreshPrices').onclick();assert.equal(calls.length,0);
 context.TradeDeskMarket.setConnection(true);await new Promise(setImmediate);
 assert.equal(applied.length,1);assert.equal(applied[0].quotes.JIOFIN.price,250);assert(el('marketStatus').textContent.includes('UNKNOWN'));
 assert.equal(timers[0].delay,30000);assert.equal(calls[0].path,'/api/dhan/quotes');
 assert.deepEqual(JSON.parse(calls[0].options.body),{symbols:['JIOFIN','UNKNOWN']});
 empty=true;await context.TradeDeskMarket.refresh();assert(el('marketStatus').textContent.includes('zero quote records'));assert(el('marketStatus').textContent.includes('API status: success'));
 fail=true;await timers[0].fn();await new Promise(setImmediate);
 assert(cleared.includes('Data subscription required'));
 context.TradeDeskMarket.setConnection(false);assert.equal(cleared.at(-1),'Dhan disconnected');
 console.log('Passed: connection gating, 30-second quote requests, Dhan-only price application, missing symbols and API failure handling.');
}
test().catch(error=>{console.error(error);process.exitCode=1;});
