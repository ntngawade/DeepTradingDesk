'use strict';
(() => {
 const $=id=>document.getElementById(id),escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const money=n=>Number.isFinite(n)?new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:2}).format(n):'Not provided';
 let connected=false,busy=false,generation=0,refreshQueued=false;
 function clear(message){$('ongoingSyncStatus').textContent=message;$('ongoingSyncStatus').className='hint red';$('plannerPortfolioStatus').textContent=message;globalThis.TradeDeskData?.markPortfolioUnavailable?.(message);$('portfolioStatus').textContent=message;for(const id of ['portfolioHoldings','portfolioPositions'])$(id).innerHTML='<tr><td colspan="4">Account data unavailable.</td></tr>';}
 async function refresh(){
  if(busy){refreshQueued=true;return;}
  busy=true;$('ongoingSyncStatus').textContent='Refreshing Dhan holdings...';$('ongoingSyncStatus').className='hint';const requested=generation;$('portfolioRefresh').disabled=true;
  try{
   const response=await fetch('/api/dhan/portfolio',{cache:'no-store',signal:AbortSignal.timeout(35000)});
   if(!(response.headers.get('Content-Type')||'').includes('application/json'))throw Error('Restart the updated backend to read Dhan holdings.');
   const data=await response.json();if(!response.ok)throw Error(data.error||'Cannot read Dhan portfolio.');if(requested!==generation)return;
   if(!Array.isArray(data.holdings)||!Array.isArray(data.positions)||!data.clientId||!data.checkedAt)throw Error('The account response is incomplete. Open http://127.0.0.1:8081 and sync again.');
   globalThis.TradeDeskData?.applyPortfolioSnapshot?.(data);
   $('ongoingSyncStatus').textContent=`Dhan returned ${data.holdings.length} holdings: ${data.holdings.map(row=>row.symbol+' ('+row.quantity+')').join(', ')}. Checked ${new Date(data.checkedAt).toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata'})} IST.`;
   $('plannerPortfolioStatus').textContent='Dhan holdings checked '+new Date(data.checkedAt).toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata'})+' IST. Single matching plans are synced; choose a link if multiple setups match.';
   $('portfolioHoldings').innerHTML=data.holdings.map(row=>`<tr><td>${escape(row.symbol)}</td><td>${escape(row.quantity)}</td><td>${escape(row.availableQty??'Not provided')}</td><td>${money(row.averagePrice)}</td></tr>`).join('')||'<tr><td colspan="4">No demat holdings returned by Dhan.</td></tr>';
   $('portfolioPositions').innerHTML=data.positions.map(row=>`<tr><td>${escape(row.symbol)}</td><td>${escape(row.quantity)}</td><td>${money(row.averagePrice)}</td><td>${money(row.unrealizedProfit)}</td></tr>`).join('')||'<tr><td colspan="4">No open delivery positions returned by Dhan.</td></tr>';
   $('portfolioStatus').textContent=`Dhan account snapshot / checked ${new Date(data.checkedAt).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'})} IST / refresh every 30 seconds. Matching single trade plans sync quantity and average buy price automatically. Multiple setups need a holding link. No CMP Data API subscription is needed.`;
  }catch(error){if(requested===generation)clear(error.message);}
  finally{busy=false;$('portfolioRefresh').disabled=false;if(refreshQueued){refreshQueued=false;refresh();}}
 }
 globalThis.TradeDeskPortfolio={refresh,setConnection(value){if(connected===value)return;connected=value;generation++;if(connected)refresh();else clear('Dhan disconnected. Reconnect to verify current holdings.');}};
 $('portfolioRefresh').onclick=refresh;
 $('plannerSyncHoldings').onclick=refresh;
 $('ongoingRefresh').onclick=refresh;
 setInterval(()=>{if(!document.hidden)refresh();},30000);
 refresh();
})();
