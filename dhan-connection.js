'use strict';
(() => {
 const $=id=>document.getElementById(id);
 let hasSession=false, remembered=false, needsToken=false, busy=false, backendReady=false;
 try{$('dhanClientId').value=localStorage.getItem('deepDesk.dhanClientId')||'';}catch{}
 function show(status){
  globalThis.TradeDeskMarket?.setConnection(!!status.connected);
  globalThis.TradeDeskPortfolio?.setConnection(!!status.connected);
  hasSession=!!status.hasSession;remembered=!!status.remembered;needsToken=!!status.needsToken;
  const clientId=status.clientId||status.profile?.dhanClientId;
  if(clientId){if(!$('dhanClientId').value)$('dhanClientId').value=clientId;try{localStorage.setItem('deepDesk.dhanClientId',clientId);}catch{}}
  const profile=status.profile||{};
  $('dhanBadge').textContent=status.connected?'Dhan / Connected':needsToken?'Dhan / Update token':hasSession?'Dhan / Retrying check':'Dhan / Not connected';
  $('dhanBadge').className='pill '+(status.connected?'':hasSession?'paper':'status');
  $('dhanStatus').textContent=(status.restoring?'Restoring securely saved Dhan connection...':status.error?(status.error+(needsToken?' Paste a fresh access token below; your client ID is retained.':hasSession?' Your token is retained; checks retry automatically.':'')):null)||(status.connected?'Connected. Live orders require explicit enabling and per-trade arming in Holdings.':'No token session. Connect once; enable Remember securely to restore after backend restarts.');
  $('dhanStatus').className='hint '+(status.error?'red':'');
  if(status.connected&&status.remembered)$('dhanStatus').textContent+=' Token saved encrypted for reconnecting after restarts.';
  for(const [id,value] of Object.entries({dhanAccount:clientId,dhanSaved:status.restoring?'Restoring encrypted credentials':remembered?'Saved encrypted on this Windows account':'Not saved - connect with Remember securely checked',dhanValidity:profile.tokenValidity,dhanDataPlan:profile.dataPlan,dhanChecked:status.checkedAt?new Date(status.checkedAt).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'})+' IST':''}))$(id).textContent=value||'—';
  $('dhanRefresh').disabled=!hasSession||busy;
  $('dhanDisconnect').disabled=!(hasSession||status.remembered)||busy;
 }
 async function request(path,body){
  let response;
  try{response=await fetch('/api/dhan/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,cache:'no-store',signal:AbortSignal.timeout(35000)});}
  catch(error){backendReady=false;throw Error(error.name==='TimeoutError'?'Connection check timed out. Please retry.':'Cannot reach the local backend. Start python NitinTradeDesk/server.py and open its printed URL.');}
  const json=(response.headers.get('Content-Type')||'').includes('application/json');
  if(!json||[404,405,501].includes(response.status)){backendReady=false;$('dhanBackendHelp').classList.remove('hidden');throw Error('This server does not provide the Dhan connection API. Run python NitinTradeDesk/server.py --port 8081, then open http://127.0.0.1:8081. Do not use Live Server or python -m http.server.');}
  const data=await response.json();if(!response.ok)throw Error(data.error||'Dhan connection request failed.');backendReady=true;$('dhanBackendHelp').classList.add('hidden');return data;
 }
 async function run(path,body){if(busy)return;busy=true;$('dhanConnect').disabled=true;$('dhanRefresh').disabled=true;$('dhanDisconnect').disabled=true;try{const result=await request(path,body);show(result);return result;}catch(error){$('dhanStatus').textContent=error.message;$('dhanStatus').className='hint red';$('dhanBadge').textContent='Dhan / Check connection';$('dhanBadge').className='pill paper';}finally{busy=false;$('dhanConnect').disabled=false;$('dhanRefresh').disabled=!hasSession;$('dhanDisconnect').disabled=!(hasSession||remembered);}}
 $('dhanForm').onsubmit=async event=>{event.preventDefault();if(busy)return;if(!backendReady){const status=await run('status');if(!status)return;}const accessToken=$('dhanToken').value.trim(),clientId=$('dhanClientId').value.trim();try{localStorage.setItem('deepDesk.dhanClientId',clientId);}catch{}$('dhanStatus').textContent='Verifying account with Dhan...';const result=await run('connect',{clientId,accessToken,remember:!!$('dhanRemember').checked});if(result?.connected)$('dhanToken').value='';};
 $('dhanRefresh').onclick=()=>run('refresh',{});
 $('dhanDisconnect').onclick=()=>run('disconnect',{});
 run('status');
 // Connection health only. Market prices are not requested by this module.
 setInterval(()=>{if(!document.hidden)run(hasSession&&!needsToken?'refresh':'status',hasSession&&!needsToken?{}:undefined);},30000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)run(hasSession&&!needsToken?'refresh':'status',hasSession&&!needsToken?{}:undefined);});
})();
