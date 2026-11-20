'use strict';
(function(root){
 const fields={id:['tradeid','id'],setup:['tradesetup','setup'],symbol:['symbol','stock','stockname','scrip','stockscript'],sectorIndex:['sectorindex','sector','index','sectoralindex'],side:['buysell','side'],timeframe:['timeframe','executiontimeframe','executiontf','tf'],entry:['entry','entryprice'],sl:['sl','stoploss','stoplossprice'],target:['target','targetprice'],cmp:['cmp','currentprice','ltp'],qty:['qty','quantity'],sale:['saleprice','exitprice','sellprice','saleamount','sellamount'],brokerage:['brokerage','charges'],expiry:['expiry','expirydate'],notes:['notes','remark','remarks','noteremark'],status:['status'],targetMode:['targetmode'],qtyMode:['qtymode','quantitymode'],brokerageMode:['brokeragemode'],manualBrokerage:['manualbrokerage'],fill:['entryfill','actualbuyprice','buyfill'],enabled:['enabled','tradeenabled'],actualTotalCharges:['actualtotalcharges','contractnotecharges'],holdingBroker:['holdingbroker','broker']};
 const labels={id:'Trade ID',setup:'TRADE SETUP',symbol:'STOCK/SCRIPT',sectorIndex:'SECTOR / INDEX',side:'BUY/SELL',timeframe:'EXECUTION TF',entry:'ENTRY',sl:'STOPLOSS',target:'TARGET',cmp:'CMP',qty:'QTY',sale:'SALE PRICE',brokerage:'Brokerage',expiry:'Expiry',notes:'Note/Remark',status:'Status',targetMode:'Target Mode',qtyMode:'Quantity Mode',brokerageMode:'Brokerage Mode',manualBrokerage:'Manual Brokerage',fill:'Entry Fill',enabled:'Enabled',actualTotalCharges:'Actual Total Charges',holdingBroker:'Holding Broker'};
 const normalize=s=>String(s??'').toLowerCase().replace(/[^a-z0-9]/g,'');
 const num=value=>{const text=String(value??'').replace(/[,₹\s]/g,'');return !text||text.startsWith('#')?null:Number.isFinite(Number(text))?Number(text):null;};
 const timeframeKey=value=>({w:'weekly',d:'daily',m:'monthly',q:'quarterly',y:'yearly'}[String(value).toLowerCase()]||String(value).toLowerCase().replace(/\s*min(utes?)?$/,''));
 const key=p=>[p.symbol,normalize(p.setup),timeframeKey(p.timeframe),p.entry,p.sl].join('|');
 function detect(rows){let best={row:0,map:{},score:0};rows.slice(0,30).forEach((row,i)=>{const map=Object.fromEntries(Object.entries(fields).map(([k,aliases])=>[k,row.findIndex(h=>aliases.includes(normalize(h)))]));const score=['symbol','entry','sl'].filter(k=>map[k]>=0).length;if(score>best.score)best={row:i,map,score};});return best;}
 function review(rows,header,map,state){const staged=[],seen=new Set();for(let i=header+1;i<rows.length;i++){
  const raw=Object.fromEntries(Object.keys(fields).map(k=>[k,map[k]>=0?String(rows[i][map[k]]??'').trim():'']));
  if(!raw.symbol&&!raw.entry&&!raw.sl)continue;
  const p={id:raw.id||'',setup:raw.setup,symbol:raw.symbol.toUpperCase(),side:(raw.side||'BUY').toUpperCase(),timeframe:raw.timeframe.replace(/\.0$/,'')||'Daily',entry:num(raw.entry),sl:num(raw.sl),target:num(raw.target),cmp:null,spreadsheetCmp:num(raw.cmp),cmpSource:'Awaiting Dhan',expiry:raw.expiry,notes:raw.notes,status:'Watching',brokerage:0,sourceBrokerage:num(raw.brokerage)||0,sourceQty:num(raw.qty),sourceSale:num(raw.sale),source:'Excel'};
  if(p.target===null&&p.entry>p.sl)p.target=+(p.entry+(p.entry-p.sl)*state.settings.rr).toFixed(2);
  p.qty=p.entry>p.sl&&p.sl>0?Math.max(0,Math.min(Math.floor(state.settings.capital*state.settings.risk/100/(p.entry-p.sl)),Math.floor(state.settings.capital/p.entry))):0;
  p.targetMode=raw.targetMode==='manual'?'manual':raw.targetMode==='auto'?'auto':raw.target&&Math.abs(p.target-(p.entry+(p.entry-p.sl)*state.settings.rr))>0.011?'manual':'auto';
  p.qtyMode=raw.qtyMode==='manual'?'manual':'auto';p.brokerageMode=raw.brokerageMode==='manual'?'manual':'auto';p.manualBrokerage=num(raw.manualBrokerage)||0;if(p.qtyMode==='manual'&&p.sourceQty!==null)p.qty=p.sourceQty;
  if(Number.isInteger(p.sourceQty)&&p.sourceQty>0&&(raw.status?!['Watching','Armed'].includes(raw.status):p.qtyMode!=='manual')){p.qty=p.sourceQty;p.fill=num(raw.fill)??p.entry;if(p.sourceSale>0){p.status='Closed';p.exit=p.sourceSale;p.exitSource='Excel';}else p.status='Imported holding';}
  if(p.sourceSale>0){p.fill=num(raw.fill)??p.entry;p.exit=p.sourceSale;p.exitSource='Excel';p.qty=Number.isInteger(p.sourceQty)&&p.sourceQty>0?p.sourceQty:0;p.status=p.qty>0?'Closed':'Completed - details pending';p.qtyMode='manual';}
  p.actualTotalCharges=num(raw.actualTotalCharges);if(raw.holdingBroker)p.holdingBroker=raw.holdingBroker;
  p.brokerage=p.status!=='Watching'&&p.sourceBrokerage>0?p.sourceBrokerage:p.brokerageMode==='manual'?p.manualBrokerage:0;
  const match=state.plans.filter(x=>raw.id?x.id===raw.id:key(x)===key(p));const old=match[0];p.sectorIndex=raw.sectorIndex||old?.sectorIndex||'';
  const completingEdited=old?.journalEdited&&old.source!=='Dhan live'&&['Imported holding','Completed - details pending','Watching'].includes(old.status)&&p.sourceSale>0&&(!old.liveState||old.liveState==='DISARMED')&&old.exitSource!=='Dhan execution';
  if(completingEdited){p.qty=p.qty>0?p.qty:old.sourceQty>0?old.sourceQty:old.status==='Imported holding'?old.qty:0;p.fill=num(raw.fill)??old.fill??p.entry;p.status=p.qty>0?'Closed':'Completed - details pending';p.journalEdited=true;p.holdingBroker=old.holdingBroker;if(old.targetMode==='manual'){p.target=old.target;p.targetMode='manual';}}
  p.id=raw.id||old?.id||'excel-'+encodeURIComponent([p.setup,key(p)].join('|'));
  if(old?.cmpSource==='Dhan'){p.cmp=old.cmp;p.cmpSource=old.cmpSource;p.cmpFetchedAt=old.cmpFetchedAt;p.securityId=old.securityId;p.cmpError=old.cmpError||'';}
  p.enabled=raw.enabled?!['no','false','0','disabled'].includes(raw.enabled.toLowerCase()):old?.enabled!==false;
  let error='';if(p.actualTotalCharges!==null&&p.actualTotalCharges<0)error='Actual charges must be non-negative.';if(p.manualBrokerage<0)error='Manual brokerage must not be negative.';if(!/^[A-Z0-9&._ -]{1,30}$/.test(p.symbol))error='Invalid stock symbol.';
  else if(p.side!=='BUY')error='Only delivery BUY records are supported.';
  else if(![p.entry,p.sl,p.target].every(n=>Number.isFinite(n)&&n>0)||!(p.sl<p.entry&&p.target>p.entry))error='Invalid entry / stop-loss / target.';
  else if(p.fill!==undefined&&(!Number.isFinite(p.fill)||p.fill<=0))error='Actual entry fill must be a positive number.';
  else if(p.status!=='Completed - details pending'&&(!Number.isInteger(p.qty)||p.qty<1))error='Quantity must be a positive whole number.';
  else if(p.expiry&&!/^\d{4}-\d{2}-\d{2}$/.test(p.expiry))error='Expiry must use YYYY-MM-DD.';
  else if(raw.sale&&p.sourceSale===null)error='Invalid sale price.';
  if(/^Live /i.test(raw.status))error='Open live records must be reconciled from the server ledger, not recreated from CSV.';
  const recordKey=raw.id?'id:'+raw.id:'plan:'+key(p);if(seen.has(recordKey))error='Duplicate within this upload.';seen.add(recordKey);
  if(match.length>1)error='Ambiguous match. Supply a Trade ID.';
  let action='Add';if(old){action='Update';if(old.journalEdited&&!completingEdited||old.liveState&&old.liveState!=='DISARMED'||old.exitSource==='Dhan execution'||old.source!=='Excel'&&old.status!=='Watching'&&!completingEdited||['Armed','Holding'].includes(old.status))error='Active or broker-verified application trade is protected.';else if(Object.keys(p).every(k=>(old[k]??'')===(p[k]??'')))action='Unchanged';}
  staged.push({row:i+1,p,action:error?'Skip':action,detail:error||(p.status==='Completed - details pending'?'Sale recorded: completed trade; actual executed quantity still required.':p.status==='Closed'?'Recorded closed trade; original quantity preserved.':p.status==='Imported holding'?'Recorded holding; not connected to broker.':p.cmp===null?'Valid plan; CMP unavailable.':'Valid plan; quantity calculated from app risk settings.')});
 }return staged;}
 root.TradeImport={fields,labels,detect,review,key,num};
 if(typeof module!=='undefined')module.exports=root.TradeImport;
})(globalThis);
