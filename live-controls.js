'use strict';
(() => {
 const $=id=>document.getElementById(id),escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const money=n=>new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:2}).format(n);
 let busy=false,queueing=false,lastError=null,pauseRequested=false,lastDisplay=null,initialStatusLoaded=false;
 async function api(path,body){const response=await fetch('/api/live/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,cache:'no-store',signal:AbortSignal.timeout(60000)});if(!(response.headers.get('Content-Type')||'').includes('application/json'))throw Error('Restart the updated application backend for live trading.');const data=await response.json();if(!response.ok)throw Error(data.error||'Live request failed.');return data;}
 function display(data){
  lastDisplay=data;
  globalThis.TradeDeskData.setExecutionStatus?.(data);
  const active=data.records.filter(r=>!['DISARMED','CLOSED','CANCELLED','REJECTED','EXPIRED'].includes(r.state));
  $('tradingModeBadge').textContent=data.enabled?'LIVE CNC ENABLED':data.unverifiedActiveCount?'RECONNECT TO CHECK LIVE ORDERS':active.length?'LIVE ENTRIES PAUSED':'PAPER MODE';
  $('tradingModeBadge').className='pill '+(data.enabled||active.length||data.unverifiedActiveCount?'liveBadge':'paper');
  $('liveState').textContent=data.error||lastError||(data.unverifiedActiveCount?`${data.unverifiedActiveCount} persisted live plans need a Dhan reconnection to verify broker state. Existing orders/exit legs may still be active.`:`${data.enabled?'Live entry monitoring enabled':'New live entries paused'} / ${data.marketSession?'Regular NSE entry window open':'Outside regular entry window'} / ${active.length} active live plans. Enabled Watching plans are prechecked and queued automatically. Broker exits are not cancelled by Pause.`);
  $('liveEnable').disabled=busy||queueing||data.enabled;
  $('liveRecords').innerHTML=($('liveRecordsFilter').value==='all'?data.records:active).map(r=>`<tr><td>${escape(r.symbol)}</td><td>${r.state==='ARMED'?'Watching entry':escape(r.state)}</td><td>${r.filledQty||0} / ${r.quantity}</td><td>${money(r.entry)}</td><td class="red">${money(r.sl)}</td><td class="green">${money(r.target)}</td><td>${escape(r.orderId||'Not submitted')}</td><td>${escape(r.message)}</td></tr>`).join('')||'<tr><td colspan="8">No ongoing app automation records in this view. Current account holdings appear above.</td></tr>';
  globalThis.TradeDeskData.setPlanFlags?.(data.planEnabled||{});globalThis.TradeDeskData.setLiveRecords(data.records);
 }
 async function refresh(){if(busy||queueing)return;try{const wasEnabled=!!lastDisplay?.enabled,data=await api('status');display(data);if(data.enabled&&(!initialStatusLoaded||!wasEnabled))await activatePlans(globalThis.TradeDeskData.watchingPlans?.()||[]);initialStatusLoaded=true;}catch(error){$('liveState').textContent=error.message;$('tradingModeBadge').textContent='LIVE STATUS UNAVAILABLE';globalThis.TradeDeskData.setExecutionStatus?.({error:error.message,records:[]});}}
 async function perform(action,body){if(busy)return false;busy=true;if(action==='enable')pauseRequested=false;$('liveEnable').disabled=true;let success=false;try{await api(action,body);if(action==='enable'&&pauseRequested)await api('pause',{});lastError=null;display(await api('status'));success=true;}catch(error){lastError=error.message;$('liveState').textContent=error.message;}finally{busy=false;}return success;}
 async function pause(){pauseRequested=true;try{await api('pause',{});lastError=null;display(await api('status'));return true;}catch(error){lastError='Live pause could not be confirmed: '+error.message;$('liveState').textContent=lastError;return false;}}
 async function activatePlans(plans){
  if(queueing)return {error:'Live prechecks are already running.'};
  if(!lastDisplay?.enabled)return {waiting:true};
  queueing=true;lastError=null;$('liveEnable').disabled=true;$('liveState').textContent='Prechecking enabled plans against Dhan, funds, available capital, holdings and per-trade sizing...';
  let queued=0;const errors=[];const records=lastDisplay.records||[];
  try{
   for(const plan of plans){
    if(!plan||plan.status!=='Watching'||plan.enabled===false)continue;
    const existing=records.find(r=>r.tradeId===plan.id&&!['DISARMED'].includes(r.state));if(existing)continue;
    try{const preview=await api('preview',{plan,settings:globalThis.TradeDeskData.settings(),triggerPercent:1});const result=await api('arm',{previewId:preview.previewId});records.push(result);queued++;}
    catch(error){errors.push(`${plan.symbol}: ${error.message}`);}
   }
   lastDisplay=await api('status');display(lastDisplay);
   if(errors.length){lastError=`${queued} plan(s) queued. Not queued: ${errors.join(' / ')}`;$('liveState').textContent=lastError;}
   else if(queued){$('liveState').textContent=`${queued} enabled plan(s) queued. The server will submit each only after its entry trigger and all final checks pass.`;}
   return {queued,errors};
  }catch(error){lastError=error.message;$('liveState').textContent=error.message;return {queued,errors:[error.message]};}
  finally{queueing=false;$('liveEnable').disabled=!!lastDisplay?.enabled;}
 }
 async function activatePlan(plan){
  if(!lastDisplay)try{lastDisplay=await api('status');display(lastDisplay);}catch(error){return {error:error.message};}
  if(!lastDisplay?.enabled)return {waiting:true};
  const result=await activatePlans([plan]);return result.errors?.length?{error:result.errors.join(' / ')}:{queued:result.queued>0,waiting:result.queued===0};
 }
 $('liveEnable').onclick=async()=>{const ok=await perform('enable',{acknowledgeRealOrders:true});if(ok){const plans=globalThis.TradeDeskData.watchingPlans?.()||[];const result=await activatePlans(plans);if(!result.queued&&!result.errors?.length)$('liveState').textContent='Live execution is enabled. No enabled Watching plans need queuing.';}};
 $('livePause').onclick=pause;
 $('liveRefresh').onclick=()=>{refresh();globalThis.TradeDeskPortfolio?.refresh();};
 $('liveRecordsFilter').onchange=()=>{if(lastDisplay)display(lastDisplay);};
 globalThis.LiveDesk={pause,refresh,activatePlan,isEnabled:()=>!!lastDisplay?.enabled};
 refresh();setInterval(refresh,5000);
})();
