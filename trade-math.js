'use strict';
(function(root){
 const round=n=>Math.round((n+Number.EPSILON)*100)/100;
 function calculate(entry,sl,target,settings){const qty=entry>sl&&sl>0?Math.max(0,Math.min(Math.floor(settings.capital*settings.risk/100/(entry-sl)),Math.floor(settings.capital/entry))):0;return {qty,risk:qty*(entry-sl),profit:qty*(target-entry),rr:(target-entry)/(entry-sl),cost:qty*entry};}
 function deliveryCharges(p){
  if(!(p.qty>0&&p.exit>0)||p.status==='Completed - details pending')return null;
  if(p.actualTotalCharges!==undefined&&p.actualTotalCharges!==null&&Number.isFinite(p.actualTotalCharges)&&p.actualTotalCharges>=0)return {total:round(p.actualTotalCharges),mode:'Actual contract-note total'};
  if(p.holdingBroker==='Zerodha')return null;
  const buy=p.qty*(p.fill||p.entry),sell=p.qty*p.exit,turnover=buy+sell,brokerage=p.brokerage||0;
  const transaction=round(turnover*(p.exchange==='BSE'?0.0000375:0.000030699)),sebi=round(turnover*0.000001),ipft=round(turnover*0.000000001);
  const stt=Math.round(turnover*0.001),stamp=Math.round(buy*0.00015),gst=round((brokerage+transaction+sebi+ipft)*0.18);
  const dp=12.5,dpGst=2.25;
  return {brokerage,transaction,sebi,ipft,stt,stamp,gst,dp,dpGst,total:round(brokerage+transaction+sebi+ipft+stt+stamp+gst+dp+dpGst),mode:'Dhan delivery estimate'};
 }
 function realized(p){const charges=deliveryCharges(p);return p.qty>0&&p.exit>0?round((p.exit-(p.fill||p.entry))*p.qty-(charges?charges.total:p.brokerage||0)):NaN;}
 function derive(p,settings){
  if(p.status==='Watching'){
   if(p.targetMode==='auto')p.target=round(p.entry+(p.entry-p.sl)*settings.rr);
   const automatic=calculate(p.entry,p.sl,p.target,settings).qty;
   if(p.qtyMode!=='manual')p.qty=automatic;
   p.validationWarning=p.qtyMode==='manual'&&(!Number.isInteger(p.qty)||p.qty<1)?'Manual quantity must be a positive whole number.':'';
  }
  // Historical quantities and fill costs remain immutable.
  p.brokerage=p.sourceBrokerage>0&&p.status!=='Watching'?p.sourceBrokerage:p.brokerageMode==='manual'?round(p.manualBrokerage||0):0;
  p.requiredMargin=round(p.qty*(p.fill||p.entry));
  p.rr=(p.target-p.entry)/(p.entry-p.sl);
  p.diff=p.cmp>0?round(p.cmp-p.entry):null;
  p.diffPercent=p.cmp>0?(p.cmp-p.entry)/p.entry*100:null;
  p.charges=deliveryCharges(p);p.totalCharges=p.charges?.total??null;
  p.pnl=p.holdingDetailsPending||p.status==='Completed - details pending'?null:p.exit>0?realized(p):['Holding','Imported holding','Live holding'].includes(p.status)&&p.cmp>0?round((p.cmp-(p.fill||p.entry))*p.qty-p.brokerage):null;
  return p;
 }
 root.TradeMath={calculate,derive,round,deliveryCharges,realized};if(typeof module!=='undefined')module.exports=root.TradeMath;
})(globalThis);
