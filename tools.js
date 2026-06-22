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
  const lines=$('#jpaste').value.split('\n').map(s=>s.trim()).filter(Boolean);
  const t=loadTrades(); let added=0;
  for(const ln of lines){
    const pm=ln.match(PAIR_RE); const pair=pm?(pm[1].toUpperCase()+'/JPY'):$('#jp').value;
    let side; if(/売り|売/.test(ln))side='売り'; else if(/買い|買/.test(ln))side='買い'; else side=$('#js').value;
    const cleaned=ln.replace(PAIR_RE,' ').replace(/\d{1,4}\.\d+/g,' ');
    const nm=cleaned.match(/-?\d{1,3}(?:,\d{3})+|-?\d+/);
    if(!nm)continue; const y=parseInt(nm[0].replace(/,/g,''),10); if(isNaN(y))continue;
    t.push({pair,side,yen:y,ts:Date.now()}); added++;
  }
  if(!added){alert('数字が見つかりません');return;}
  saveTrades(t); $('#jpaste').value=''; $('#pastebox').style.display='none'; renderJournal();
}
function clearTrades(){if(confirm('記録を全削除しますか？')){saveTrades([]);renderJournal();}}
/* ===== 取込方式：CSV / iPhone文字貼付 / アプリ決済 ===== */
const PAIR_RE=/(USD|EUR|GBP|AUD|NZD|CAD|CHF|TRY|ZAR|MXN|HUF|SEK)\/?JPY/i;
function _intTok(t){t=(t||'').replace(/[^\d,+-]/g,'');if(/^[+-]?\d{1,3}(,\d{3})+$/.test(t)||/^[+-]?\d+$/.test(t))return parseInt(t.replace(/,/g,''),10);return NaN;}
function toggleBox(id){const el=document.getElementById(id);el.style.display=(el.style.display==='none'||!el.style.display)?'block':'none';}

/* ---- A. CSV取込（Shift_JIS自動判定・列マッピング） ---- */
let CSV_ROWS=null,CSV_HEAD=null;
function readCSVFile(file){return new Promise((res,rej)=>{const r=new FileReader();r.onload=()=>{const buf=r.result;let txt='';
  try{txt=new TextDecoder('utf-8',{fatal:false}).decode(buf);}catch(e){}
  if(!txt||(txt.match(/\uFFFD/g)||[]).length>3){try{txt=new TextDecoder('shift_jis').decode(buf);}catch(e){}}
  res(txt);};r.onerror=()=>rej(new Error('読込失敗'));r.readAsArrayBuffer(file);});}
function parseCSV(text){
  text=(text||'').replace(/^\uFEFF/,'');
  const rows=[];let i=0,f='',row=[],q=false;
  while(i<text.length){const c=text[i];
    if(q){ if(c==='"'){ if(text[i+1]==='"'){f+='"';i++;} else q=false; } else f+=c; }
    else { if(c==='"')q=true; else if(c===','){row.push(f);f='';} else if(c==='\n'){row.push(f);rows.push(row);row=[];f='';} else if(c!=='\r')f+=c; }
    i++;
  }
  if(f.length||row.length){row.push(f);rows.push(row);}
  return rows.filter(r=>r.some(x=>(x||'').trim()!==''));
}
function guessCol(head,keys){for(const k of keys){const i=head.findIndex(h=>h&&h.indexOf(k)>=0);if(i>=0)return i;}return -1;}
async function csvLoad(file){
  if(!file)return;const msg=$('#impmsg');msg.textContent='CSV読込中…';
  try{
    const txt=await readCSVFile(file);const rows=parseCSV(txt);
    if(rows.length<2){msg.innerHTML='<span class="warn">データ行が見つかりません。</span>';return;}
    CSV_HEAD=rows[0].map(s=>(s||'').trim());CSV_ROWS=rows.slice(1);
    const opts=CSV_HEAD.map((h,i)=>`<option value="${i}">${(h||('列'+(i+1))).slice(0,16)}</option>`).join('');
    ['cPnl','cPair','cSide'].forEach(id=>{$('#'+id).innerHTML='<option value="-1">（なし）</option>'+opts;});
    $('#cPnl').value=guessCol(CSV_HEAD,['決済損益','実現損益','約定損益','売買損益','損益']);
    $('#cPair').value=guessCol(CSV_HEAD,['通貨ペア','通貨対','銘柄','シンボル','通貨']);
    $('#cSide').value=guessCol(CSV_HEAD,['売買区分','売買','取引区分','売り買い']);
    $('#csvmap').style.display='block';
    msg.innerHTML=`<span class="good">${CSV_ROWS.length}行を読込。列を確認して「この設定で取り込む」を押してください。</span>`;
  }catch(e){msg.innerHTML='<span class="warn">CSV読込失敗: '+e.message+'</span>';}
  finally{const f=$('#csvfile');if(f)f.value='';}
}
function numFromCell(s){if(s==null)return NaN;let m=(''+s).replace(/[▲△]/g,'-').replace(/[^\d.,+-]/g,'').replace(/,/g,'');if(!m||m==='-'||m==='+'||m==='.')return NaN;const v=parseFloat(m);return isNaN(v)?NaN:Math.round(v);}
function csvImport(){
  if(!CSV_ROWS){alert('先にCSVを読み込んでください');return;}
  const pi=+$('#cPnl').value,ai=+$('#cPair').value,si=+$('#cSide').value,inv=$('#cInvert').checked;
  if(pi<0){alert('損益の列を選んでください');return;}
  const t=loadTrades();let added=0;
  for(const r of CSV_ROWS){
    const y=numFromCell(r[pi]); if(isNaN(y)||y===0)continue;
    let pair=$('#jp').value; if(ai>=0&&r[ai]){const pm=(''+r[ai]).toUpperCase().match(PAIR_RE);pair=pm?(pm[1]+'/JPY'):(''+r[ai]).trim();}
    let side='-'; if(si>=0&&r[si]){const sv=''+r[si];let s=/売/.test(sv)?'売り':(/買/.test(sv)?'買い':'-');if(inv&&s!=='-')s=(s==='売り'?'買い':'売り');side=s;}
    t.push({pair,side,yen:y,ts:Date.now()});added++;
  }
  if(!added){alert('損益のある行が見つかりません。列の選択をご確認ください。');return;}
  saveTrades(t);$('#csvmap').style.display='none';CSV_ROWS=null;$('#impmsg').innerHTML=`<span class="good">${added}件を取り込みました。</span>`;renderJournal();
}

/* ---- B. iPhone標準OCRの文字を貼付 → 決済行を解析 ---- */
function iosParse(){
  const text=$('#iospaste').value||'';
  if(!text.trim()){alert('コピーした文字を貼り付けてください');return;}
  const toks=text.split(/\s+/).filter(Boolean);
  const idx=[];toks.forEach((t,i)=>{if(PAIR_RE.test(t))idx.push(i);});
  const lines=[];
  for(let k=0;k<idx.length;k++){
    const a=idx[k],b=(k+1<idx.length?idx[k+1]:toks.length);const cell=toks.slice(a,b);
    if(!cell.some(t=>/決|済/.test(t)))continue;                 // 決済のみ
    const sell=cell.some(t=>/売/.test(t)),buy=cell.some(t=>/買/.test(t));
    const held=sell?'買い':(buy?'売り':'-');
    const ints=cell.filter(t=>!/\d{2}\/\d{2}\/\d{2}/.test(t)&&!/\d{1,2}:\d{2}/.test(t)&&!/\./.test(t)).map(_intTok).filter(v=>!isNaN(v)&&v!==0&&Math.abs(v)<100000);
    if(!ints.length)continue;
    const pm=toks[a].toUpperCase().match(PAIR_RE);
    lines.push(`${pm?pm[1]+'/JPY':'USD/JPY'} ${held} ${ints[0]}`);
  }
  if(!lines.length){$('#impmsg').innerHTML='<span class="warn">決済が見つかりませんでした。コピー範囲に「決済」行が含まれているかご確認ください。</span>';return;}
  $('#pastebox').style.display='block';$('#jpaste').value=lines.join('\n');$('#iosbox').style.display='none';
  $('#impmsg').innerHTML=`<span class="good">${lines.length}件の決済を抽出。</span> サイドと−符号を確認して「取り込む」を押してください。`;
}

/* ---- C. アプリの決済履歴（status.json / positions.json）から自動取込 ---- */
async function appSync(){
  const msg=$('#impmsg');msg.textContent='アプリの決済履歴を取得中…';
  try{
    let cp=[];
    for(const u of ['./status.json','./positions.json']){
      try{const r=await fetch(u+'?t='+Date.now(),{cache:'no-store'});if(!r.ok)continue;const j=await r.json();
        if(Array.isArray(j.closed_positions))cp=cp.concat(j.closed_positions);
        if(Array.isArray(j.positions))cp=cp.concat(j.positions.filter(p=>p&&p.status==='closed'));
      }catch(e){}
    }
    if(!cp.length){msg.innerHTML='<span class="warn">アプリ側に決済履歴がありませんでした。ダッシュボードで「決済」を記録すると貯まります。</span>';return;}
    const t=loadTrades();const have=new Set(t.map(x=>x.srcId).filter(Boolean));let added=0;
    for(const p of cp){
      const yen=Math.round(+(p.close_yen!=null?p.close_yen:(p.yen!=null?p.yen:NaN)));if(isNaN(yen)||yen===0)continue;
      const id=String(p.id||(((p.symbol||'')+'|'+(p.closed_at||p.close_at||'')+'|'+yen)));if(have.has(id))continue;
      const pair=(p.symbol||p.pair||'').replace('_','/');
      const side=p.side==='long'?'買い':(p.side==='short'?'売り':(p.side==='買い'||p.side==='売り'?p.side:'-'));
      t.push({pair,side,yen,ts:Date.now(),srcId:id});have.add(id);added++;
    }
    if(!added){msg.innerHTML='<span class="good">新しい決済はありませんでした（取込済み）。</span>';return;}
    saveTrades(t);msg.innerHTML=`<span class="good">${added}件をアプリから取り込みました。</span>`;renderJournal();
  }catch(e){msg.innerHTML='<span class="warn">取得失敗: '+e.message+'</span>';}
}
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
    const sd=x.side==='買い'?'<span style="color:var(--up)">買</span>':x.side==='売り'?'<span style="color:var(--down)">売</span>':'<span style="color:var(--mut)">—</span>';
    return `<div><span>${x.pair} ${sd}</span><span class="${x.yen>=0?'good':'warn'}">${yen(x.yen)} <span class="del" onclick="delTrade(${i})">×</span></span></div>`;}).join('');
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
