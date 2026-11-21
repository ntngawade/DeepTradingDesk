"use strict";
(() => {
 const $=id=>document.getElementById(id),escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const money=n=>Number.isFinite(n)?new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:2}).format(n):'Not read';
 let queue=[],selected=null,processing=false,readerReady=null,nextId=1,readerError='',runtimeInfo=null,runtimeUsed=false;
 try{runtimeInfo=JSON.parse(document.getElementById('initialAppRuntime')?.textContent||'{}');}catch{}
 const active=()=>queue.find(item=>item.id===selected);
 const valid=item=>{const a=item?.analysis;return !!a&&!!a.symbol&&!!a.timeframe&&!!a.setup&&[a.entry,a.sl,a.target].every(n=>Number.isFinite(n)&&n>0)&&a.sl<a.entry&&a.target>a.entry;};
 const state=item=>item.saved?'Added to Trade Plans':item.editing?'Editing review values':item.status==='reading'?'Reading chart...':item.status==='queued'?'Queued':item.status==='blocked'?'Reader unavailable':item.error?(item.status==='error'?'Reader error':'Needs attention'):valid(item)?'Ready for review':item.analysis?.canCalculateTarget?'Target not marked':'Needs clearer chart';
 const copy=value=>JSON.parse(JSON.stringify(value));
 function analysisMarkup(item){
  const a=item.analysis,o=item.originalAnalysis||a,changed=item.correctedFields||[];
  const rows=[['Stock','symbol'],['Setup / higher timeframe','setup'],['Execution timeframe','timeframe'],['Entry','entry'],['Stop-loss','sl'],['Target','target'],['Notes','notes']];
  const display=(object,key)=>key==='notes'?(object.notes||[]).join('\n')||'None read':['entry','sl','target'].includes(key)?money(object[key]):object[key]||'Not read';
  return `<h2>Chart analysis and interpretation</h2><p class="hint">This analysis explains the marked text read from your image and the calculations from the reviewed values. It does not infer an unmarked trading setup from the candles.</p><ul class="ai-analysis-points"><li><b>Stock and execution:</b> ${escape(o.evidence?.symbol||o.evidence?.timeframe||'The chart title could not be read.')}</li><li><b>Setup interpretation:</b> ${escape(o.evidence?.setup||'No higher-timeframe zone label was read.')} ${o.higherTimeframe?escape('was interpreted as a '+o.higherTimeframe+' zone ('+o.setup+').'):''} The zone timeframe and chart execution timeframe are separate.</li><li><b>Marked prices:</b> ${escape(o.evidence?.entry||'Entry label unreadable')} / ${escape(o.evidence?.sl||'SL label unreadable')} / ${escape(o.evidence?.target||'No target label was read.')}</li></ul><div class="tablewrap"><table class="ai-analysis-table"><thead><tr><th>Field</th><th>Originally read</th><th>Current review value</th><th>Source</th></tr></thead><tbody>${rows.map(([label,key])=>`<tr><td>${label}</td><td>${escape(display(o,key))}</td><td>${escape(display(a,key))}</td><td>${changed.includes(key)?'Your correction':key==='target'&&item.defaultTarget?'Calculated default R:R':'Chart reading'}</td></tr>`).join('')}</tbody></table></div>`;
 }
 function correctionMarkup(item){
  if(!item.editing)return '';
  const a=item.draft||item.analysis,input=(label,id,value,type='text')=>`<label>${label}<input id="${id}" type="${type}" value="${escape(value??'')}" ${type==='number'?'min="0.01" step="0.01"':''}></label>`;
  return `<form id="aiCorrectionForm" data-ai-id="${item.id}" class="ai-correction-form"><h3>Correct the reviewed values</h3><p class="hint">The original chart reading stays visible above. Save corrections, then check the result again before adding.</p><div class="fields">${input('Stock / script','aiEditSymbol',a.symbol)}<label>Trade setup<select id="aiEditSetup"><option value="">Not read / choose setup</option>${['YIT','HYIT','QIT','MIT','WIT','DIT'].map(value=>`<option ${a.setup===value?'selected':''}>${value}</option>`).join('')}</select></label>${input('Execution timeframe (D, W, M, 125 min...)','aiEditTimeframe',a.timeframe)}${input('Entry','aiEditEntry',a.entry,'number')}${input('Stop-loss','aiEditSL',a.sl,'number')}${input('Target','aiEditTarget',a.target,'number')}</div><label class="topspace">Trade plan notes<textarea id="aiEditNotes" rows="4" maxlength="10000">${escape((a.notes||[]).join('\n'))}</textarea></label><p id="aiCorrectionError" class="red" role="alert"></p><div class="actions"><button type="submit" class="primary">Save corrections for review</button><button type="button" data-ai-action="cancelEdit" data-ai-id="${item.id}">Cancel corrections</button></div></form>`;
 }
 async function api(path,body){
  const response=await fetch('/api/chart-ai/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,cache:'no-store',signal:AbortSignal.timeout(body?65000:20000)});
  if(!(response.headers.get('Content-Type')||'').includes('application/json'))throw Error('Restart the updated Deep Trading Desk backend to use the built-in reader.');
  const result=await response.json();if(!response.ok)throw Error(result.error||'Could not read this chart.');return result;
 }
 async function checkReader(){
  $('aiCheck').disabled=true;
  try{
   let data;if(!runtimeUsed&&runtimeInfo?.chartReader?.independent===true&&Number(runtimeInfo.chartReader.apiVersion)>=9){data=runtimeInfo.chartReader;runtimeUsed=true;}else{runtimeUsed=true;data=await api('status?reader=9&check='+Date.now());}
   if(data.independent!==true)throw Error('This page reached an older chart-reader API. Open the updated application at http://127.0.0.1:8081.');
   if(Number(data.apiVersion)<9)throw Error('Restart Deep Trading Desk to load the updated RapidOCR chart reader. No chart was analyzed with the old reader.');
   readerReady=!!data.connected;readerError=readerReady?'':data.message||'Built-in reader is unavailable.';
   $('aiRuntime').textContent=(data.message||'Reader checked.')+(runtimeInfo?.backendVersion?' Backend '+runtimeInfo.backendVersion+'.':'');
  }catch(error){readerReady=false;readerError=error.message;$('aiRuntime').textContent=error.message;}
  finally{$('aiCheck').disabled=false;}
  return readerReady;
 }
 async function resumePending(){if(processing)return;for(const item of queue)if(item.status==='blocked'){item.status='queued';item.error=null;}readerReady=null;await pump();}

 function render(){
  $('aiQueueCount').textContent=String(queue.length);
  $('aiQueue').innerHTML=queue.map(item=>`<button type="button" role="listitem" class="ai-queue-item" data-ai-action="select" data-ai-id="${item.id}" aria-current="${item.id===selected}"><b>${escape(item.analysis?.symbol||item.name)}</b><small>${escape(item.name)} / ${escape(state(item))}${item.checked?' / Checked':''}</small></button>`).join('')||'<p class="hint">No charts in the queue.</p>';
  $('aiAnalyzeAll').disabled=processing||!queue.some(item=>['queued','blocked','error'].includes(item.status));
  $('aiAddAll').disabled=!queue.some(item=>item.checked&&!item.saved&&!item.editing&&valid(item));
  const item=active();$('aiChartEmpty').classList.toggle('hidden',!!item);$('aiChartStage').classList.toggle('hidden',!item);
  if(!item){$('aiSelectedLabel').textContent='No chart selected';$('aiChartImage').removeAttribute('src');$('aiViewFull').removeAttribute('href');$('aiOverlay').innerHTML='';$('aiReview').innerHTML='<p class="hint">Paste or upload a chart. Each result can be checked and added separately.</p>';return;}
  $('aiSelectedLabel').textContent=state(item);$('aiChartImage').src=item.url;$('aiViewFull').href=item.url;
  if(item.editing&&document.getElementById('aiCorrectionForm')?.dataset?.aiId===item.id){draw(item);return;}
  const a=item.analysis;
  if(!a){$('aiOverlay').innerHTML='';$('aiReview').innerHTML=`<h3>${escape(item.name)}</h3><p class="hint">${escape(item.error||state(item))}</p><button type="button" class="primary" data-ai-action="retry" data-ai-id="${item.id}" ${item.status==='reading'?'disabled':''}>${item.error?'Retry analysis':'Analyze this chart'}</button><button type="button" data-ai-action="remove" data-ai-id="${item.id}">Remove chart</button>`;return;}
  const settings=TradeDeskData.settings(),sizing=valid(item)?TradeMath.calculate(a.entry,a.sl,a.target,settings):null;
  const field=(label,value)=>`<div class="ai-read-value"><small>${label}</small><b>${escape(value??'Not read')}</b></div>`;
  $('aiReview').innerHTML=`${analysisMarkup(item)}<div class="actions"><button type="button" data-ai-action="edit" data-ai-id="${item.id}" ${item.saved||item.editing?'disabled':''}>Edit values and notes</button><button type="button" data-ai-action="resetEdits" data-ai-id="${item.id}" ${item.saved||item.editing||!(item.correctedFields?.length||item.defaultTarget)?'disabled':''}>Restore original reading</button></div>${correctionMarkup(item)}<h3>Values ready for Trade Plans</h3><div class="ai-review-grid">${field('Stock / script',a.symbol)}${field('Trade setup',a.setup)}${field('Execution timeframe',a.timeframe+(a.timeframeLabel?' / '+a.timeframeLabel:''))}${field('Entry',money(a.entry))}${field('Stop-loss',money(a.sl))}${field('Target',a.target?money(a.target):'Not marked / not read')}</div><p class="hint">Setup evidence: ${escape(a.evidence?.setup||'Not read')}. Target source: ${escape(a.targetSource||'missing')}.</p>${a.entry>a.sl&&a.sl>0?`<p class="hint">Reviewed price risk per share: ${money(a.entry-a.sl)}${a.target>a.entry?' / reward per share: '+money(a.target-a.entry):''}. Configured risk budget: ${money(settings.capital*settings.risk/100)} (${settings.risk}% of capital).</p>`:''}${a.canCalculateTarget&&!a.target&&!item.editing?`<p class="hint">A target was not marked. Default 1:${settings.rr} would give ${money(TradeMath.round(a.entry+(a.entry-a.sl)*settings.rr))}.</p><button type="button" data-ai-action="defaultTarget" data-ai-id="${item.id}">Use default 1:${settings.rr} target (calculated)</button>`:''}<h3>Notes captured from chart</h3><div class="ai-notes">${escape((a.notes||[]).join('\n')||'No chart note was read.')}</div>${sizing?`<p class="hint">Calculated quantity: ${sizing.qty} / R:R 1:${sizing.rr.toFixed(2)} / planned risk ${money(sizing.risk)} / potential profit ${money(sizing.profit)} before charges.</p>`:''}<h3>Original reader review notes</h3><ul class="ai-read-warnings">${(a.warnings||[]).map(text=>`<li>${escape(text)}</li>`).join('')}</ul>${item.error?`<p class="red">${escape(item.error)}</p>`:''}<label class="remember-option"><input type="checkbox" data-ai-action="check" data-ai-id="${item.id}" ${item.checked?'checked':''} ${!valid(item)||item.saved||item.editing?'disabled':''}> I checked this chart's stock, setup, timeframe, prices and notes.</label><div class="actions"><button class="primary" type="button" data-ai-action="add" data-ai-id="${item.id}" ${!valid(item)||!item.checked||item.saved||item.editing||item.saving?'disabled':''}>${item.saving?'Adding plan + sending...':item.telegramSent?'Plan added + Telegram sent':item.saved?'Plan added':'Add checked plan + send chart to Telegram'}</button>${item.saved&&!item.telegramSent&&!item.sendingTelegram&&item.reviewedPlan?`<button type="button" data-ai-action="retryTelegram" data-ai-id="${item.id}">Retry Telegram send</button>`:''}<button type="button" data-ai-action="retry" data-ai-id="${item.id}" ${item.saved||item.status==='reading'?'disabled':''}>Read again</button><button type="button" data-ai-action="remove" data-ai-id="${item.id}">Remove chart</button></div><p class="hint">Adding saves the reviewed setup to Trade Plans and sends this chart image plus its details to Telegram. It does not place or arm an order.</p><details><summary>Recognized chart text / extraction evidence</summary><pre class="ai-ocr-text">${escape(a.rawText||'')}</pre></details>`;
  draw(item);
 }
 function draw(item){
  const a=item.analysis;if(!a){$('aiOverlay').innerHTML='';return;}
  $('aiOverlay').innerHTML=['entry','sl','target'].map(key=>{
   const price=a[key];if(!(price>0))return '';
   let y=(item.correctedFields||[]).includes(key)?null:a.markers?.[key]?.y;
   if(!Number.isFinite(y)&&a.axis&&a.axis.slope<0)y=(price-a.axis.intercept)/a.axis.slope;
   if(!Number.isFinite(y)||y<0||y>1)return '';
   y*=1000;const color={entry:'#1255c7',sl:'#d62345',target:'#008360'}[key];
   return `<line x1="0" x2="1000" y1="${y}" y2="${y}" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/><rect x="5" y="${Math.max(0,y-25)}" width="250" height="25" fill="white" fill-opacity=".9"/><text x="12" y="${Math.max(20,y-6)}" fill="${color}" font-size="18" font-family="sans-serif">${key.toUpperCase()} ${price.toFixed(2)}</text>`;
  }).join('');
 }
 async function enqueue(files){
  let count=0;
  for(const file of Array.from(files||[])){
   if(!['image/png','image/jpeg'].includes(file.type)||file.size>5000000){$('aiProgress').textContent='Skipped an unsupported image. Use PNG/JPEG under 5 MB each.';continue;}
   if(queue.length>=20||queue.reduce((sum,item)=>sum+item.file.size,0)+file.size>50000000){$('aiProgress').textContent='Queue limit reached: 20 charts / 50 MB. Clear finished charts before adding more.';break;}
   const item={id:String(nextId++),file,name:file.name||`Pasted chart ${nextId-1}`,url:URL.createObjectURL(file),status:'queued',checked:false,saved:false,analysis:null,error:null};
   queue.push(item);if(!selected)selected=item.id;count++;
  }
  render();if(count)await pump();
 }
 function fileData(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('Cannot read the selected chart file.'));reader.readAsDataURL(file);});}
 async function pump(){
  if(processing)return;processing=true;
  try{
   if(readerReady!==true)await checkReader();
   if(!readerReady){for(const item of queue)if(item.status==='queued'){item.status='blocked';item.error=readerError;}$('aiProgress').textContent='Analysis has not started: '+readerError+' Queued charts retry automatically when the reader is ready.';render();return;}
   while(queue.some(item=>item.status==='queued')){
    const item=queue.find(item=>item.status==='queued');item.status='reading';item.error=null;render();$('aiProgress').textContent=`Reading ${item.name} on this computer...`;
    try{
     const image=await fileData(item.file);if(!queue.includes(item))continue;
     const result=await api('analyze',{image});if(!queue.includes(item))continue;
     if(Number(result.apiVersion)<9)throw Error('The running backend still has the old chart reader. Restart Deep Trading Desk, then retry; this chart was not reviewed with RapidOCR.');
     item.analysis=result.analysis;item.originalAnalysis=copy(result.analysis);item.correctedFields=[];item.editing=false;item.status='read';item.checked=false;
    }catch(error){if(queue.includes(item)){item.error=error.name==='TimeoutError'?'Chart reading timed out. Try a smaller image.':error.message;item.status='error';}}
    render();
   }
   $('aiProgress').textContent='Chart reading finished. Check each result before adding it to Trade Plans.';
  }finally{processing=false;render();}
 }
 async function telegramSend(item){
  if(!item?.saved||!item.reviewedPlan||item.telegramSent||item.sendingTelegram)return;
  item.sendingTelegram=true;item.error=null;render();
  try{
   const statusResponse=await fetch('/api/telegram/status',{cache:'no-store'}),status=await statusResponse.json().catch(()=>null);
   if(!statusResponse.ok||!status?.configured)throw Error(status?.error||(status?.tokenSaved?'Your bot token is already saved. The Telegram group ID is missing. In Settings, send a new message in your group that mentions the bot, then click Find group ID once; it will save the group automatically. You will not need to re-enter the token.':'Telegram is not configured. Enter the bot token and group ID once in Settings.'));
   const p=item.reviewedPlan,esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
   const risk=p.qty*(p.entry-p.sl),profit=p.qty*(p.target-p.entry),rr=(p.target-p.entry)/(p.entry-p.sl);
   const caption=`<b>NEW TRADE PLAN - ${esc(p.symbol)}</b>\n<b>Side:</b> BUY | <b>Product:</b> CNC Delivery\n<b>Setup:</b> ${esc(p.setup)} | <b>Execution:</b> ${esc(p.timeframe)}\n<b>Entry:</b> ${money(p.entry)}\n<b>Stop-loss:</b> ${money(p.sl)}\n<b>Target:</b> ${money(p.target)}\n<b>Quantity:</b> ${p.qty} shares\n<b>Planned risk:</b> ${money(risk)}\n<b>Potential profit:</b> ${money(profit)}\n<b>R:R:</b> 1:${Number.isFinite(rr)?rr.toFixed(2):'-'}${p.notes?`\n<b>Notes:</b> ${esc(p.notes).slice(0,250)}`:''}\n\n<i>Plan only - order not placed.</i>`;
   const image=await fileData(item.file),response=await fetch('/api/telegram/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({caption,image,filename:item.file.name||`${p.symbol}-chart.png`}),cache:'no-store',signal:AbortSignal.timeout(30000)});
   const result=await response.json().catch(()=>({error:'The app server returned an invalid Telegram response. Confirm Deep Trading Desk is running on port 8081.'}));
   if(!response.ok)throw Error(result.error||'Telegram send failed.');
   item.telegramSent=true;item.error=null;$('aiProgress').textContent=`${p.symbol}: plan saved and chart sent to the Telegram group.`;
  }catch(error){item.error=`Plan saved, but Telegram message failed: ${error.message||error}. After Telegram shows ready in Settings, click Retry Telegram send.`;$('aiProgress').textContent=item.error;}
  finally{item.sendingTelegram=false;render();}
 }
 async function add(item){
  if(!item||!item.checked||item.saved||item.saving||item.editing||!valid(item))return;
  const a=item.analysis;item.saving=true;item.error=null;render();
  try{
   let notes=(a.notes||[]).join('\n');if(item.defaultTarget)notes+=(notes?'\n':'')+`Target calculated from default R:R 1:${item.defaultRR}; no target was marked on chart.`;
   if(!item.defaultTarget&&(item.correctedFields||[]).includes('target'))notes+=(notes?'\n':'')+'Target corrected by you during chart review.';
   const sizing=TradeMath.calculate(a.entry,a.sl,a.target,TradeDeskData.settings());
   const input={symbol:a.symbol,setup:a.setup,timeframe:a.timeframe,entry:a.entry,sl:a.sl,target:a.target,notes};
   const id=TradeDeskData.addChartPlan(input);
   item.saved=true;item.checked=false;item.reviewedPlan={...input,qty:sizing.qty,id};
   $('aiProgress').textContent=`${a.symbol} saved to Trade Plans. Sending the reviewed chart to Telegram...`;await telegramSend(item);
  }catch(error){item.error=error.message;$('aiProgress').textContent=error.message;}
  finally{item.saving=false;render();}
 }
 document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-ai-action]');if(!button||button.dataset.aiAction==='check')return;
  const item=queue.find(row=>row.id===button.dataset.aiId);if(!item)return;
  const action=button.dataset.aiAction;
  if(action==='select')selected=item.id;
  if(action==='edit'&&!item.saved&&!item.editing){item.draft=copy(item.analysis);item.editing=true;item.checked=false;item.error=null;}
  if(action==='cancelEdit'){item.draft=null;item.editing=false;item.checked=false;}
  if(action==='resetEdits'&&!item.saved&&!item.editing&&item.originalAnalysis){item.analysis=copy(item.originalAnalysis);item.correctedFields=[];item.defaultTarget=false;item.checked=false;item.error=null;}
  if(action==='defaultTarget'&&!item.editing&&!item.saved&&item.analysis?.canCalculateTarget&&!item.analysis.target){const a=item.analysis;item.defaultRR=TradeDeskData.settings().rr;a.target=TradeMath.round(a.entry+(a.entry-a.sl)*item.defaultRR);a.targetSource=`Calculated at default 1:${item.defaultRR}; not read from chart`;item.defaultTarget=true;item.checked=false;}
  if(action==='add')await add(item);
  if(action==='retryTelegram')await telegramSend(item);
  if(action==='retry'&&!item.saved&&item.status!=='reading'){item.status='queued';item.analysis=null;item.error=null;item.checked=false;item.editing=false;item.draft=null;item.defaultTarget=false;render();await pump();}
  if(action==='remove'){URL.revokeObjectURL(item.url);queue=queue.filter(row=>row!==item);if(selected===item.id)selected=queue[0]?.id||null;}
  render();
 });
 document.addEventListener('submit',event=>{
  if(event.target.id!=='aiCorrectionForm')return;event.preventDefault();
  const item=queue.find(row=>row.id===event.target.dataset.aiId);if(!item||!item.editing||item.saved)return;
  const a=item.analysis,readNumber=id=>$(id).value.trim()===''?null:Number($(id).value);
  const next={symbol:$('aiEditSymbol').value.trim().toUpperCase()||null,setup:$('aiEditSetup').value||null,timeframe:$('aiEditTimeframe').value.trim()||null,entry:readNumber('aiEditEntry'),sl:readNumber('aiEditSL'),target:readNumber('aiEditTarget'),notes:$('aiEditNotes').value.split(/\r?\n/).map(line=>line.trim()).filter(Boolean)};
  let error='';
  if(next.symbol&&!/^[A-Z0-9&._ -]{1,30}$/.test(next.symbol))error='Use a valid stock trading symbol (up to 30 characters).';
  else if(next.timeframe&&next.timeframe.length>30)error='Keep the execution timeframe under 30 characters.';
  else if(next.setup&&!['YIT','HYIT','QIT','MIT','WIT','DIT'].includes(next.setup))error='Choose a supported trade setup.';
  else if(['entry','sl','target'].some(key=>next[key]!==null&&(!Number.isFinite(next[key])||next[key]<=0)))error='Prices must be positive numbers, or left blank if not known.';
  else if(next.entry&&next.sl&&next.sl>=next.entry)error='For delivery buys, stop-loss must be below entry.';
  else if(next.entry&&next.target&&next.target<=next.entry)error='For delivery buys, target must be above entry.';
  else if($('aiEditNotes').value.length>10000)error='Keep notes within 10,000 characters.';
  if(error){$('aiCorrectionError').textContent=error;return;}
  const targetChanged=next.target!==a.target;
  if(item.defaultTarget&&!targetChanged&&next.entry&&next.sl){next.target=TradeMath.round(next.entry+(next.entry-next.sl)*item.defaultRR);}
  if(targetChanged){item.defaultTarget=false;a.targetSource=next.target?'Your correction during chart review':'Missing / cleared during review';}
  const labels={D:'Daily',W:'Weekly',M:'Monthly',Q:'Quarterly',Y:'Yearly',H:'Hourly'};
  Object.assign(a,next);a.timeframeLabel=labels[a.timeframe]||a.timeframe||null;
  a.higherTimeframe={YIT:'Yearly',HYIT:'Half-yearly',QIT:'Quarterly',MIT:'Monthly',WIT:'Weekly',DIT:'Daily'}[a.setup]||null;
  a.canCalculateTarget=!!a.symbol&&!!a.timeframe&&!!a.setup&&!!a.entry&&!!a.sl&&a.sl<a.entry&&!a.target;
  const original=item.originalAnalysis||{};
  item.correctedFields=Object.keys(next).filter(key=>!(key==='target'&&item.defaultTarget)&&JSON.stringify(a[key]??null)!==JSON.stringify(original[key]??null));
  if(!item.correctedFields.includes('target')&&!item.defaultTarget)a.targetSource=original.targetSource||'missing';
  item.editing=false;item.draft=null;item.checked=false;item.error=null;
  $('aiProgress').textContent='Corrections saved for review. Check the updated values before adding the plan.';render();
 });
 function captureDraft(event){const item=active(),field={aiEditSymbol:'symbol',aiEditSetup:'setup',aiEditTimeframe:'timeframe',aiEditEntry:'entry',aiEditSL:'sl',aiEditTarget:'target',aiEditNotes:'notes'}[event.target.id];if(!item?.editing||!field)return;item.draft[field]=field==='notes'?event.target.value.split(/\r?\n/):event.target.value;}
 document.addEventListener('input',captureDraft);
 document.addEventListener('change',event=>{const input=event.target;if(input.dataset?.aiAction!=='check'){captureDraft(event);return;}const item=queue.find(row=>row.id===input.dataset.aiId);if(item&&!item.saved&&!item.editing&&valid(item)){item.checked=!!input.checked;render();}});
 document.addEventListener('paste',async event=>{
  if($('chartlab').classList.contains('hidden'))return;
  const files=Array.from(event.clipboardData?.items||[]).filter(item=>item.kind==='file'&&item.type.startsWith('image/')).map(item=>item.getAsFile()).filter(Boolean);
  if(files.length){event.preventDefault();await enqueue(files);}
 });
 $('aiPaste').onclick=async()=>{
  try{
   if(!globalThis.navigator?.clipboard?.read)throw Error('Click this tab and press Ctrl + V to paste your chart image(s).');
   const items=await navigator.clipboard.read(),files=[];
   for(const item of items){const type=item.types.find(type=>['image/png','image/jpeg'].includes(type));if(type)files.push(await item.getType(type));}
   if(!files.length)throw Error('No image is in the clipboard. Copy the chart screenshot itself, then paste again.');await enqueue(files);
  }catch(error){$('aiProgress').textContent=error.name==='NotAllowedError'?'Use Ctrl + V inside this tab to paste instead.':error.message;}
 };
 $('aiImage').onchange=async()=>{await enqueue($('aiImage').files);$('aiImage').value='';};
 $('aiPasteZone').addEventListener('dragover',event=>event.preventDefault());
 $('aiPasteZone').addEventListener('drop',async event=>{event.preventDefault();await enqueue(event.dataTransfer?.files);});
 $('aiClear').onclick=()=>{for(const item of queue)URL.revokeObjectURL(item.url);queue=[];selected=null;$('aiProgress').textContent='Chart queue cleared.';render();};
 $('aiAddAll').onclick=async()=>{for(const item of queue)if(item.checked&&!item.saved)await add(item);render();};
 $('aiCheck').onclick=async()=>{if(queue.some(item=>['queued','blocked'].includes(item.status)))return resumePending();readerReady=null;await checkReader();};
 $('aiAnalyzeAll').onclick=async()=>{for(const item of queue)if(item.status==='error'&&!item.analysis){item.status='queued';item.error=null;}await resumePending();};
 if(typeof setInterval==='function')setInterval(()=>{if(!document.hidden&&queue.some(item=>['queued','blocked'].includes(item.status)))resumePending();},15000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden&&queue.some(item=>['queued','blocked'].includes(item.status)))resumePending();});
 render();checkReader();
})();
