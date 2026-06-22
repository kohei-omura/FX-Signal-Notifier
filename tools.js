const $=s=>document.querySelector(s);
const yen=x=>(x>=0?'+':'')+Math.round(x).toLocaleString()+'円';
const PS=0.01;
/* ============ ① トレード記録 ============ */
function loadTrades(){try{return JSON.parse(localStorage.getItem('fxnavi_trades'))||[]}catch(e){return[]}}
function saveTrades(t){localStorage.setItem('fxnavi_trades',JSON.stringify(t));}
function addTrade(){
  const y=parseFloat($('#jy').value); if(isNaN(y)){alert('損益(円)を入力してください');return;}
  const t=loadTrades(); t.push({pair:$('#jp').value,side:$('#js').value,yen:y,ts:Date.now()}); saveTrades(t); $('#jy').value=''; renderJournal();
}
function importPaste(){
  const nums=($('#jpaste').value.match(/-?\d[\d,]*/g)||[]).map(s=>parseFloat(s.replace(/,/g,''))).filter(n=>!isNaN(n));
  if(!nums.length){alert('数字が見つかりません');return;}
  const t=loadTrades(); nums.forEach(n=>t.push({pair:$('#jp').value,side:'-',yen:n,ts:Date.now()})); saveTrades(t);
  $('#jpaste').value=''; $('#pastebox').style.display='none'; renderJournal();
}
function clearTrades(){if(confirm('記録を全削除しますか？')){saveTrades([]);renderJournal();}}
function delTrade(i){const t=loadTrades();t.splice(i,1);saveTrades(t);renderJournal();}
function renderJournal(){
  const t=loadTrades();
  const wins=t.filter(x=>x.yen>0),losses=t.filter(x=>x.yen<0);
  const net=t.reduce((a,b)=>a+b.yen,0), n=t.length;
  const wr=n?wins.length/n*100:0;
  const gw=wins.reduce((a,b)=>a+b.yen,0), gl=Math.abs(losses.reduce((a,b)=>a+b.yen,0));
  const pf=gl?gw/gl:(gw>0?Infinity:0);
  const exp=n?net/n:0;
  let cum=0,peak=0,dd=0;const curve=[0];
  t.forEach(x=>{cum+=x.yen;curve.push(cum);peak=Math.max(peak,cum);dd=Math.min(dd,cum-peak);});
  $('#kpis').innerHTML=`
    <div class="kpi"><div class="l">取引数</div><div class="v">${n}</div></div>
    <div class="kpi"><div class="l">勝率</div><div class="v ${wr>=50?'up':'dn'}">${wr.toFixed(0)}%</div></div>
    <div class="kpi"><div class="l">純損益</div><div class="v ${net>=0?'up':'dn'}">${yen(net)}</div></div>
    <div class="kpi"><div class="l">期待値/回</div><div class="v ${exp>=0?'up':'dn'}">${yen(exp)}</div></div>
    <div class="kpi"><div class="l">PF</div><div class="v ${pf>=1?'up':'dn'}">${pf===Infinity?'∞':pf.toFixed(2)}</div></div>
    <div class="kpi"><div class="l">最大DD</div><div class="v dn">${Math.round(dd).toLocaleString()}円</div></div>`;
  const W=320,H=120,mn=Math.min(...curve),mx=Math.max(...curve),rg=(mx-mn)||1;
  const X=i=>i/(curve.length-1||1)*W,Y=v=>H-((v-mn)/rg*(H-12))-6;
  let d='';curve.forEach((v,i)=>{d+=(i?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)+' ';});
  const zeroY=Y(0);
  $('#eq').innerHTML=`<line x1="0" y1="${zeroY}" x2="${W}" y2="${zeroY}" stroke="#2a3340" stroke-width="1" stroke-dasharray="3 3"/><path d="${d}" fill="none" stroke="${net>=0?'#4ade9b':'#ff7a8a'}" stroke-width="2"/>`;
  const byp={};t.forEach(x=>{(byp[x.pair]=byp[x.pair]||[]).push(x);});
  $('#bypair').querySelector('tbody').innerHTML=Object.entries(byp).map(([p,arr])=>{
    const nn=arr.length,w=arr.filter(a=>a.yen>0).length,nt=arr.reduce((a,b)=>a+b.yen,0);
    return `<tr><td>${p}</td><td>${nn}</td><td>${(w/nn*100).toFixed(0)}%</td><td class="${nt>=0?'good':'warn'}">${yen(nt)}</td></tr>`;}).join('')||'<tr><td colspan=4 style="text-align:center;color:#566">記録なし</td></tr>';
  $('#trlist').innerHTML=t.slice().reverse().map((x,ri)=>{const i=t.length-1-ri;
    return `<div><span>${x.pair} ${x.side}</span><span class="${x.yen>=0?'good':'warn'}">${yen(x.yen)} <span class="del" onclick="delTrade(${i})">×</span></span></div>`;}).join('');
}
/* ============ ② リスク/ロット計算 ============ */
function loadRisk(){try{return JSON.parse(localStorage.getItem('fxnavi_risk'))||{cap:10000,rpct:2,units:1000}}catch(e){return{cap:10000,rpct:2,units:1000}}}
function calcRisk(){
  const cap=parseFloat($('#cap').value),rpct=parseFloat($('#rpct').value),slp=parseFloat($('#slp').value),units=parseFloat($('#units').value);
  if([cap,rpct,slp,units].some(isNaN)){alert('数値を入力してください');return;}
  const allow=cap*rpct/100;
  const recUnits=Math.floor(allow/(slp*PS)/100)*100;
  const lossAtUnits=slp*PS*units;
  const maxSL=allow/(PS*units);
  localStorage.setItem('fxnavi_risk',JSON.stringify({cap,rpct,units}));
  const over=(lossAtUnits>allow);
  $('#riskout').innerHTML=`
    <div class="kpi"><div class="l">許容損失</div><div class="v go">${Math.round(allow).toLocaleString()}円</div></div>
    <div class="kpi"><div class="l">推奨通貨量</div><div class="v">${recUnits.toLocaleString()}</div></div>
    <div class="kpi"><div class="l">この量での想定損失</div><div class="v ${over?'dn':'up'}">${Math.round(lossAtUnits).toLocaleString()}円</div></div>
    <div class="kpi"><div class="l">この量での最大SL</div><div class="v">${maxSL.toFixed(1)}pips</div></div>
    <div class="kpi" style="grid-column:span 3"><div class="l">判定</div><div class="v ${over?'dn':'up'}">${over?'⚠ 今のSL/通貨量はリスク過大（許容超過）':'✅ 許容内。資金/リスク%を保存しました'}</div></div>`;
}
/* ============ ③ スプレッド/コスト判定 ============ */
async function loadSpreads(){
  if(!LIVE_PRICE_URL){$('#sprtbl').querySelector('tbody').innerHTML='<tr><td colspan="4" class="warn" style="text-align:center">WorkerのURLを設定してください</td></tr>';return;}
  const tp=parseFloat($('#ctp').value)||2;
  try{
    const r=await fetch(LIVE_PRICE_URL.split('?')[0]+'?t='+Date.now(),{cache:'no-store'});
    const j=await r.json();const want=['USD_JPY','EUR_JPY','GBP_JPY','AUD_JPY'];
    const rows=(j.data||[]).filter(d=>want.includes(d.symbol)).map(d=>{
      const sp=(+d.ask - +d.bid)/PS, ratio=sp/tp, ok=ratio<0.3;
      return `<tr><td>${d.symbol.replace('_','/')}</td><td>${sp.toFixed(1)}pips</td><td class="${ok?'good':'warn'}">${(ratio*100).toFixed(0)}%</td><td class="${ok?'good':'warn'}">${ok?'✅ 可':'⛔ コスト負け注意'}</td></tr>`;
    }).join('');
    $('#sprtbl').querySelector('tbody').innerHTML=rows||'<tr><td colspan="4" style="text-align:center;color:#566">取得失敗</td></tr>';
  }catch(e){$('#sprtbl').querySelector('tbody').innerHTML='<tr><td colspan="4" class="warn" style="text-align:center">取得失敗</td></tr>';}
}
/* ============ ④ バックテスト（FX Naviエンジン） ============ */
const W_EMA=.35,W_MACD=.25,W_RSI=.20,W_BB=.20,TECH_W=.9,FUND_W=.1;
const FUND_BIAS={USD_JPY:0.5,EUR_JPY:0.4,GBP_JPY:0.5,AUD_JPY:0.4};
const BP={scalp:{interval:'1min',ema_f:5,ema_s:13,rsi:7,macd:[6,13,5],bb:[20,2],adx:14,atr:14,th:.35},
          day:{interval:'5min',ema_f:9,ema_s:21,rsi:14,macd:[12,26,9],bb:[20,2],adx:14,atr:14,th:.40}};
const cl=(x,lo=-1,hi=1)=>Math.max(lo,Math.min(hi,x));
function emaS(v,p){if(v.length<p)return[];const k=2/(p+1);let e=v.slice(0,p).reduce((a,b)=>a+b,0)/p;const o=new Array(p-1).fill(null);o.push(e);for(let i=p;i<v.length;i++){e=v[i]*k+e*(1-k);o.push(e);}return o;}
function emaL(v,p){const s=emaS(v,p);return s.length?s[s.length-1]:null;}
function rsiL(v,p){if(v.length<p+1)return null;const d=[];for(let i=1;i<v.length;i++)d.push(v[i]-v[i-1]);const g=d.map(x=>Math.max(x,0)),l=d.map(x=>Math.max(-x,0));let ag=g.slice(0,p).reduce((a,b)=>a+b,0)/p,al=l.slice(0,p).reduce((a,b)=>a+b,0)/p;for(let i=p;i<d.length;i++){ag=(ag*(p-1)+g[i])/p;al=(al*(p-1)+l[i])/p;}return al===0?100:100-100/(1+ag/al);}
function macdL(v,f,s,sig){const ef=emaS(v,f),es=emaS(v,s);const ml=[];for(let i=0;i<v.length;i++)ml.push((ef[i]!=null&&es[i]!=null)?ef[i]-es[i]:null);const vv=ml.filter(m=>m!=null);if(vv.length<sig+1)return null;const ss=emaS(vv,sig);if(ss[ss.length-1]==null||ss[ss.length-2]==null)return null;return[vv[vv.length-1],ss[ss.length-1],vv[vv.length-1]-ss[ss.length-1],vv[vv.length-2]-ss[ss.length-2]];}
function bollL(v,p,k){if(v.length<p)return null;const w=v.slice(-p),mid=w.reduce((a,b)=>a+b,0)/p;const sd=Math.sqrt(w.reduce((a,b)=>a+(b-mid)**2,0)/p);return[mid,sd];}
function atrL(o,p){if(o.length<p+1)return null;const tr=[];for(let i=1;i<o.length;i++){const h=o[i][0],l=o[i][1],pc=o[i-1][2];tr.push(Math.max(h-l,Math.abs(h-pc),Math.abs(l-pc)));}let a=tr.slice(0,p).reduce((x,y)=>x+y,0)/p;for(let i=p;i<tr.length;i++)a=(a*(p-1)+tr[i])/p;return a;}
function adxL(o,p){if(o.length<2*p+1)return null;const pdm=[],mdm=[],tr=[];for(let i=1;i<o.length;i++){const h=o[i][0],l=o[i][1],ph=o[i-1][0],pl=o[i-1][1],pc=o[i-1][2];const up=h-ph,dn=pl-l;pdm.push((up>dn&&up>0)?up:0);mdm.push((dn>up&&dn>0)?dn:0);tr.push(Math.max(h-l,Math.abs(h-pc),Math.abs(l-pc)));}const w=a=>{let s=a.slice(0,p).reduce((x,y)=>x+y,0);const o2=[s];for(let i=p;i<a.length;i++){s=s-s/p+a[i];o2.push(s);}return o2;};const at=w(tr),pd=w(pdm),md=w(mdm);const pdi=at.map((x,i)=>x?100*pd[i]/x:0),mdi=at.map((x,i)=>x?100*md[i]/x:0);const dx=pdi.map((x,i)=>100*Math.abs(x-mdi[i])/((x+mdi[i])||1));let av;if(dx.length<p)av=dx.reduce((x,y)=>x+y,0)/dx.length;else{av=dx.slice(0,p).reduce((x,y)=>x+y,0)/p;for(let i=p;i<dx.length;i++)av=(av*(p-1)+dx[i])/p;}return av;}
function sideAt(o,sym,P){const c=o.map(r=>r[2]);if(c.length<Math.max(P.ema_s,P.macd[1],P.adx*2)+2)return null;
  const price=c[c.length-1],ef=emaL(c,P.ema_f),es=emaL(c,P.ema_s),rv=rsiL(c,P.rsi),md=macdL(c,P.macd[0],P.macd[1],P.macd[2]),bb=bollL(c,P.bb[0],P.bb[1]),a=atrL(o,P.atr),ax=adxL(o,P.adx);
  if([ef,es,rv,a,ax].some(x=>x==null)||!md||!bb)return null;
  const adxf=Math.max(cl(ax/40,0,1),0.25);
  let emaSig=cl((ef-es)/(a||1e-9)),macdSig=cl(md[2]/(0.6*(a||1e-9)));
  if(md[2]>md[3])macdSig=cl(macdSig+0.1);else if(md[2]<md[3])macdSig=cl(macdSig-0.1);
  const rsiSig=cl((rv-50)/50),bbSig=cl((price-bb[0])/(P.bb[1]*(bb[1]||1e-9)));
  const tech=cl(W_EMA*emaSig*adxf+W_MACD*macdSig*adxf+W_RSI*rsiSig+W_BB*bbSig);
  const total=cl(TECH_W*tech+FUND_W*cl(FUND_BIAS[sym]||0));
  return total>=P.th?'買い':(total<=-P.th?'売り':null);
}
function jstYmd(off){return new Date(Date.now()+off*86400000).toLocaleDateString('sv-SE',{timeZone:'Asia/Tokyo'}).replace(/-/g,'');}
async function fetchHist(base,sym,interval,days){let rows={};for(let off=0;off>=-(days+1);off--){const path=`/public/v1/klines?symbol=${sym}&priceType=BID&interval=${interval}&date=${jstYmd(off)}`;
  try{const r=await fetch(`${base}?path=${encodeURIComponent(path)}&t=${Date.now()}`,{cache:'no-store'});const j=await r.json();(j.data||[]).forEach(k=>{rows[+k.openTime]=[+k.high,+k.low,+k.close];});}catch(e){}}
  return Object.keys(rows).map(Number).sort((a,b)=>a-b).map(t=>rows[t]);}
async function runBacktest(){
  if(!LIVE_PRICE_URL){$('#btmsg').innerHTML='<span class="warn">WorkerのURL（LIVE_PRICE_URL）を tools.html に設定してください</span>';return;}
  const sym=$('#bsym').value,mode=$('#bmode').value,days=Math.min(14,Math.max(1,parseInt($('#bdays').value)||3)),spr=parseFloat($('#bspr').value)||0;
  const P=BP[mode],base=LIVE_PRICE_URL.split('?')[0].replace(/\/$/,'');
  $('#btbtn').textContent='計算中…';$('#btbtn').disabled=true;$('#btmsg').textContent='';
  try{
    let oh=await fetchHist(base,sym,P.interval,days);
    const CAP=mode==='scalp'?2200:1200; if(oh.length>CAP)oh=oh.slice(-CAP);
    if(oh.length<120){$('#btmsg').innerHTML='<span class="warn">データ不足（市場休場や期間不足）。期間を増やすか平日に実行してください</span>';return;}
    const warm=Math.max(P.ema_s,P.macd[1],P.adx*2)+5;
    const trades=[];let i=warm;
    while(i<oh.length-1){
      const side=sideAt(oh.slice(0,i+1),sym,P);
      if(!side){i++;continue;}
      const a=atrL(oh.slice(0,i+1),P.atr); if(!a){i++;continue;}
      const slp=a/PS, tpp=slp*1.5, entry=oh[i][2];
      const tp=side==='買い'?entry+tpp*PS:entry-tpp*PS, sl=side==='買い'?entry-slp*PS:entry+slp*PS;
      let exit=null;
      for(let j=i+1;j<oh.length;j++){const h=oh[j][0],l=oh[j][1];
        if(side==='買い'){if(l<=sl){exit=-slp;break;}if(h>=tp){exit=tpp;break;}}
        else{if(h>=sl){exit=-slp;break;}if(l<=tp){exit=tpp;break;}}
        i=j;}
      if(exit==null)break;
      trades.push(exit-spr); i++;
    }
    if(!trades.length){$('#btmsg').innerHTML='<span class="warn">この期間ではシグナル発生なし</span>';return;}
    const wins=trades.filter(x=>x>0),losses=trades.filter(x=>x<=0);
    const net=trades.reduce((a,b)=>a+b,0),wr=wins.length/trades.length*100;
    const gw=wins.reduce((a,b)=>a+b,0),gl=Math.abs(losses.reduce((a,b)=>a+b,0));
    const pf=gl?gw/gl:Infinity, exp=net/trades.length;
    let cum=0,peak=0,dd=0;trades.forEach(x=>{cum+=x;peak=Math.max(peak,cum);dd=Math.min(dd,cum-peak);});
    $('#btout').innerHTML=`
      <div class="kpi"><div class="l">トレード</div><div class="v">${trades.length}</div></div>
      <div class="kpi"><div class="l">勝率</div><div class="v ${wr>=50?'up':'dn'}">${wr.toFixed(0)}%</div></div>
      <div class="kpi"><div class="l">合計</div><div class="v ${net>=0?'up':'dn'}">${net>=0?'+':''}${net.toFixed(1)}pips</div></div>
      <div class="kpi"><div class="l">期待値/回</div><div class="v ${exp>=0?'up':'dn'}">${exp>=0?'+':''}${exp.toFixed(2)}pips</div></div>
      <div class="kpi"><div class="l">PF</div><div class="v ${pf>=1?'up':'dn'}">${pf===Infinity?'∞':pf.toFixed(2)}</div></div>
      <div class="kpi"><div class="l">最大DD</div><div class="v dn">${dd.toFixed(1)}pips</div></div>`;
    $('#btmsg').innerHTML=`${sym} ${mode} 直近${oh.length}本・スプレッド${spr}pips控除後。${exp>=0?'<span class="good">期待値プラス＝エッジの可能性</span>':'<span class="warn">期待値マイナス＝この設定では不利</span>'}`;
  }finally{$('#btbtn').textContent='バックテスト実行';$('#btbtn').disabled=false;}
}
loadRisk&&(()=>{const r=loadRisk();$('#cap').value=r.cap;$('#rpct').value=r.rpct;$('#units').value=r.units;})();
renderJournal();
