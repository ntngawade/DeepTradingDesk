'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
async function test(){
 const html=fs.readFileSync(__dirname+'/index.html','utf8'),ids=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1])),elements=new Map(),calls=[];
 function element(){return {value:'',textContent:'',innerHTML:'',options:[],dataset:{},disabled:false,setAttribute(){},addEventListener(){},classList:{hidden:false,toggle(name,value){if(name==='hidden')this.hidden=value;},contains(name){return name==='hidden'&&this.hidden;},add(){},remove(){}}};}
 const el=id=>{assert(ids.has(id),'Missing HTML control: '+id);if(!elements.has(id))elements.set(id,element());return elements.get(id);};
 const workbook=JSON.parse(fs.readFileSync(__dirname+'/tracker-data.json','utf8'));
 const plan=(symbol,entry,sl,target,qty,extra={})=>({id:symbol,symbol,entry,sl,target,qty,qtyMode:'manual',targetMode:'manual',status:'Watching',setup:'QIT',timeframe:'Weekly',sourceQty:0,...extra});
 const saved={settings:{capital:500000,risk:1,rr:2},plans:[plan('PAGEIND',33000,30000,39000,3),plan('BDL',1004,878,1256,40),plan('KNRCON',111.28,81.38,171.08,167),plan('TMPV',264.2,208.32,375.96,200,{sourceQty:200,holdingBroker:'Zerodha',fill:264.2}),plan('JKTYRE',333.49,301.8,396.87,346,{sourceQty:346,holdingBroker:'Zerodha',fill:333.49}),plan('AIAENG',3501.9,3206,4093.7,16),plan('CUMMINSIND',3999.94,3860,4401.12,10,{status:'closed',fill:3999.94,exit:4401.2,completedAt:new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Calcutta'}),closedAt:new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Calcutta'})})],importedVersions:[workbook.version],appliedPlanUpdates:workbook.planUpdates.map(u=>u.id)};
 const stored=new Map([['tradeDesk.v1',JSON.stringify(saved)]]),nav=element();
 class SkewedClock extends Date{static now(){return Date.now()+3600000;}}
 const context={Intl,Date:SkewedClock,Number,String,Math,Array,Object,Set,Map,JSON,AbortSignal,crypto:require('node:crypto').webcrypto,setTimeout(){},clearTimeout(){},setInterval(){},
  window:{DEEP_TRADE_SECTOR_MAP:{sectors:[],symbols:{}}},localStorage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value)},location:{hash:'#live'},history:{replaceState(){}},
  document:{hidden:false,getElementById:el,querySelector:selector=>selector==='nav'?nav:element(),querySelectorAll:()=>[],documentElement:{setAttribute(){}},body:{addEventListener(type,fn){if(type==='click')(context.clickHandlers||(context.clickHandlers=[])).push(fn);}},addEventListener(){}},
  fetch:async(path,options={})=>{
   calls.push({path,method:options.method||'GET'});let data,ok=true;
   if(path==='/api/dhan/portfolio')data={clientId:'123',checkedAt:new Date().toISOString(),holdings:[{symbol:'BDL',securityId:'1',quantity:40,averagePrice:1004},{symbol:'KNRCON',securityId:'2',quantity:167,averagePrice:111.28},{symbol:'PAGEIND',securityId:'14413',quantity:4,averagePrice:32125}],positions:[{symbol:'SOMEPOS',securityId:'9',quantity:2,averagePrice:100,unrealizedProfit:20}]};
   else if(path==='/api/dhan/status')data={connected:true,hasSession:true,remembered:true,profile:{dhanClientId:'123'}};
   else if(path==='/api/live/status')data={enabled:false,records:[],planEnabled:{}};
   else if(path==='/api/dhan/trade-history')data={orders:[{side:'BUY',orderId:'page-buy',quantity:4,averagePrice:32125,firstTradeAt:'2026-10-10 09:20:00',lastTradeAt:'2026-10-10 09:20:00'}],truncated:false};
   else if(path==='tracker-data.json')data=workbook;
   else if(path==='/api/dhan/quotes'){ok=false;data={error:'Data plan inactive'};}
   else throw Error('Unexpected startup request '+path);
   return {ok,headers:{get:()=> 'application/json'},json:async()=>data};
  }};
 el('initialDhanPortfolio').textContent=JSON.stringify({clientId:'123',checkedAt:new Date().toISOString(),holdings:[{symbol:'BDL',securityId:'1',quantity:40,averagePrice:1004},{symbol:'KNRCON',securityId:'2',quantity:167,averagePrice:111.28},{symbol:'PAGEIND',securityId:'14413',quantity:4,averagePrice:32125}],positions:[{symbol:'SOMEPOS',securityId:'9',quantity:2,averagePrice:100,unrealizedProfit:20}]});
 for(const id of ['filter','plansStatus','ongoingBroker','completedOutcome','completedSource','completedTimeframe'])el(id).value='all';
 vm.createContext(context);
 for(const name of ['trade-math.js','import-core.js','app.js'])vm.runInContext(fs.readFileSync(__dirname+'/'+name,'utf8'),context);
 assert(el('ongoingRows').innerHTML.includes('PAGEIND'),'Initial page snapshot must render PAGEIND before the portfolio module loads');assert.equal(calls.filter(c=>c.path==='/api/dhan/portfolio').length,0);
 for(const name of ['market-data.js','portfolio-view.js','dhan-connection.js','live-controls.js'])vm.runInContext(fs.readFileSync(__dirname+'/'+name,'utf8'),context);
 for(let i=0;i<24;i++)await new Promise(setImmediate);
 for(const symbol of ['PAGEIND','BDL','KNRCON','TMPV','JKTYRE']){assert(el('ongoingRows').innerHTML.includes(symbol),symbol+' must appear in Ongoing Trades');assert(!el('plans').innerHTML.includes(symbol),symbol+' must leave Trade Plans');}
 assert.equal(vm.runInContext("state.plans.find(p=>p.symbol==='PAGEIND').executedAt",context),'2026-10-10','Holdings should fetch the entry date automatically after Dhan sync');assert(el('ongoingRows').innerHTML.includes('2026-10-10'),'Holdings shows the automatically fetched Dhan execution date');assert(calls.some(c=>c.path==='/api/dhan/trade-history'),'Dhan history is fetched automatically after holdings sync');
 assert(el('holdingPnlSummary').innerHTML.includes('Overall P/L'));assert(el('holdingPnlSummary').innerHTML.includes('1 trade(s) completed today'));
 assert(el('plans').innerHTML.includes('AIAENG'));assert(el('journal').innerHTML.includes('CUMMINSIND'));assert(!el('ongoingRows').innerHTML.includes('CUMMINSIND'));assert.equal(el('plansTabCount').textContent,'1');assert.equal(el('ongoingTabCount').textContent,'6');
 const recovered=JSON.parse(stored.get('tradeDesk.v1'));assert.equal(recovered.plans.find(p=>p.symbol==='PAGEIND').qty,4);assert.equal(recovered.plans.find(p=>p.symbol==='PAGEIND').fill,32125);
 vm.runInContext("state.plans.find(p=>p.symbol==='PAGEIND').symbol='BADLOCAL';render();",context);assert(el('ongoingRows').innerHTML.includes('PAGEIND'),'A mismatched stored security ID cannot suppress the authoritative PAGEIND account row');assert(el('ongoingInventory').innerHTML.includes('PAGEIND'));
 assert(el('ongoingRows').innerHTML.includes('Record Zerodha sale'));assert(!calls.some(c=>c.method==='POST'&&c.path.startsWith('/api/live/')));
 console.log('Passed: complete page startup with strict HTML controls, legacy saved holdings, immediate Dhan sync, skewed browser clock, five moved holdings and no order mutations.');
}
test().catch(error=>{console.error(error);process.exitCode=1;});
