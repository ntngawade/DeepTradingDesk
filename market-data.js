'use strict';
(() => {
 const $=id=>document.getElementById(id);
 let connected=false,busy=false,generation=0;
 const message=text=>$('marketStatus').textContent=text;
 function feed(text,connected=false){$('feedHealth').textContent=text;$('feedHealth').className='feed-chip'+(connected?' connected':'');}
 async function refresh(){
  if(busy)return;
  if(!connected){message('Connect Dhan in Settings to load CMP. Excel CMP is not used as a live price.');return;}
  const symbols=TradeDeskData.symbols();if(!symbols.length){message('Create or import a plan to fetch CMP.');return;}
  const requestGeneration=generation;busy=true;$('refreshPrices').disabled=true;
  message('Fetching Dhan CMP...');feed('FETCHING DHAN PRICES');
  try{
   const all={quotes:{},unresolved:[],missing:[],quoteDiagnostics:null};
   for(let i=0;i<symbols.length;i+=100){
    const response=await fetch('/api/dhan/quotes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbols:symbols.slice(i,i+100)}),signal:AbortSignal.timeout(45000)});
    if(!(response.headers.get('Content-Type')||'').includes('application/json'))throw Error('Restart the updated application server to enable Dhan CMP.');
    const data=await response.json();if(!response.ok)throw Error(data.error||'Dhan CMP request failed.');
    Object.assign(all.quotes,data.quotes);all.unresolved.push(...data.unresolved);all.missing.push(...data.missing);all.quoteDiagnostics=data.quoteDiagnostics||all.quoteDiagnostics;all.fetchedAt=data.fetchedAt;
   }
   if(requestGeneration!==generation)return;
   TradeDeskData.applyQuotes(all);
   feed(`DHAN / ${Object.keys(all.quotes).length} PRICES / 30s`,Object.keys(all.quotes).length>0);
   const missing=[...all.unresolved,...all.missing];
   const diagnostics=all.quoteDiagnostics||{},emptyFeed=Object.keys(all.quotes).length===0&&diagnostics.requestedInstrumentCount>0&&diagnostics.returnedInstrumentCount===0;
   const detail=emptyFeed?` Dhan returned zero quote records for ${diagnostics.requestedInstrumentCount} requested instruments (API status: ${diagnostics.apiStatus}; segments returned: ${diagnostics.returnedSegmentNames?.join(', ')||'none'}). Check that the Data API subscription is active for this account/token, then retry; this response does not identify a stock-symbol problem.`:missing.length?' No price returned for: '+missing.join(', ')+'.':'';
   message(`${Object.keys(all.quotes).length}/${symbols.length} Dhan prices / retrieved ${new Date(all.fetchedAt).toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata'})} IST / refresh every 30s.${detail} LTP may be the last traded price outside market hours.`);
  }catch(error){if(requestGeneration===generation){const text=error.name==='TimeoutError'?'Dhan CMP request timed out. Retry Refresh Dhan CMP.':error.message;TradeDeskData.clearQuotes(text);message(text);feed('FEED UNAVAILABLE');}}
  finally{busy=false;$('refreshPrices').disabled=false;}
 }
 globalThis.TradeDeskMarket={setConnection(value){const changed=connected!==value;connected=value;if(changed){generation++;if(connected)refresh();else{TradeDeskData.clearQuotes('Dhan disconnected');message('Dhan disconnected. Connect in Settings to fetch CMP.');feed('DHAN DISCONNECTED');}}},refresh};
 $('refreshPrices').onclick=refresh;
 setInterval(()=>{if(!document.hidden)refresh();},30000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden&&connected)refresh();});
})();
