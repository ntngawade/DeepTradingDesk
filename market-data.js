'use strict';
(() => {
 const $=id=>document.getElementById(id);
 let connected=false,busy=false,generation=0;
 const message=text=>$('marketStatus').textContent=text;
 function feed(text,connected=false){$('feedHealth').textContent=text;$('feedHealth').className='feed-chip'+(connected?' connected':'');globalThis.TradeDeskData?.setQuoteStatus?.({label:text,connected});}
 function inAutoRefreshWindow(){
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kolkata',weekday:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date()).map(part=>[part.type,part.value]));
  const minutes=Number(parts.hour)*60+Number(parts.minute);
  return !['Sat','Sun'].includes(parts.weekday)&&minutes>=540&&minutes<960;
 }
 async function refresh(){
  if(busy)return;
  if(!connected){message('Connect Dhan in Settings to load CMP. Excel CMP is not used as a live price.');return;}
  const symbols=TradeDeskData.symbols(),indices=TradeDeskData.sectorIndices();if(!symbols.length&&!indices.length){message('Create stock or sector plans to fetch CMP.');return;}
  const requestGeneration=generation;busy=true;$('refreshPrices').disabled=true;
  message('Fetching Dhan CMP...');feed('FETCHING DHAN PRICES');
  try{
   const all={quotes:{},indexQuotes:{},unresolved:[],missing:[],unresolvedIndices:[],missingIndices:[],quoteDiagnostics:null,indexChangeApiMissing:false};
   const chunks=[];for(let i=0;i<symbols.length;i+=100)chunks.push(symbols.slice(i,i+100));if(!chunks.length)chunks.push([]);
   for(let i=0;i<chunks.length;i++){
    const response=await fetch('/api/dhan/quotes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbols:chunks[i],indices:i===0?indices:[]}),signal:AbortSignal.timeout(45000)});
    if(!(response.headers.get('Content-Type')||'').includes('application/json'))throw Error('Restart the updated application server to enable Dhan CMP.');
    const data=await response.json();if(!response.ok)throw Error(data.error||'Dhan CMP request failed.');
    Object.assign(all.quotes,data.quotes);if(indices.length&&data.indexChangeAvailable!==true)all.indexChangeApiMissing=true;Object.assign(all.indexQuotes,data.indexQuotes||{});all.unresolved.push(...data.unresolved);all.missing.push(...data.missing);all.unresolvedIndices.push(...(data.unresolvedIndices||[]));all.missingIndices.push(...(data.missingIndices||[]));all.quoteDiagnostics=data.quoteDiagnostics||all.quoteDiagnostics;all.fetchedAt=data.fetchedAt;
   }
   if(requestGeneration!==generation)return;
   TradeDeskData.applyQuotes(all);
   const totalPrices=Object.keys(all.quotes).length+Object.keys(all.indexQuotes).length;
   feed(`DHAN / ${totalPrices} PRICES / 30s`,totalPrices>0);
   const missing=[...all.unresolved,...all.missing];
   const diagnostics=all.quoteDiagnostics||{},emptyFeed=Object.keys(all.quotes).length===0&&diagnostics.requestedInstrumentCount>0&&diagnostics.returnedInstrumentCount===0;
   const detail=emptyFeed?` Dhan returned zero quote records for ${diagnostics.requestedInstrumentCount} requested instruments (API status: ${diagnostics.apiStatus}; segments returned: ${diagnostics.returnedSegmentNames?.join(', ')||'none'}). Check that the Data API subscription is active for this account/token, then retry; this response does not identify a stock-symbol problem.`:missing.length?' No price returned for: '+missing.join(', ')+'.':'';
   const missingSectors=[...all.unresolvedIndices,...all.missingIndices];
   message(`${Object.keys(all.quotes).length}/${symbols.length} stocks + ${Object.keys(all.indexQuotes).length}/${indices.length} indices / retrieved ${new Date(all.fetchedAt).toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata'})} IST / refresh every 30 seconds.${all.indexChangeApiMissing?' Index CMP and daily change need the updated local backend; restart Deep Trading Desk once to enable the full market strip.':''}${detail}${missingSectors.length?' Sector index prices unavailable: '+missingSectors.join(', ')+'.':''} New ticks depend on Dhan data; LTP may be the last traded price outside market hours.`);
  }catch(error){if(requestGeneration===generation){const text=error.name==='TimeoutError'?'Dhan CMP request timed out. Retry Refresh Dhan CMP.':error.message;TradeDeskData.clearQuotes(text);message(text);feed('FEED UNAVAILABLE');}}
  finally{busy=false;$('refreshPrices').disabled=false;}
 }
 globalThis.TradeDeskMarket={setConnection(value){const changed=connected!==value;connected=value;if(changed){generation++;if(connected){if(inAutoRefreshWindow())refresh();else message('Automatic CMP refresh runs weekdays, 9:00 AM–4:00 PM IST, every 30 seconds.');}else{TradeDeskData.clearQuotes('Dhan disconnected');message('Dhan disconnected. Connect in Settings to fetch CMP.');feed('DHAN DISCONNECTED');}}},refresh};
 $('refreshPrices').onclick=refresh;
 setInterval(()=>{if(!document.hidden&&connected&&inAutoRefreshWindow())refresh();},30000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden&&connected&&inAutoRefreshWindow())refresh();});
 message('Connect Dhan; automatic CMP refresh runs weekdays, 9:00 AM–4:00 PM IST, every 30 seconds.');
})();
