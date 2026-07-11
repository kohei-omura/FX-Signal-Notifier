const $=s=>document.querySelector(s);
const yen=x=>(x>=0?'+':'')+Math.round(x).toLocaleString()+'円';
const PS=0.01;
/* ============ ① トレード記録 ============ */
function loadTrades(){try{return JSON.parse(localStorage.getItem('fxnavi_trades'))||[]}catch(e){return[]}}
function saveTrades(t){localStorage.setItem('fxnavi_trades',JSON.stringify(t));}
function addTrade(){
  const y=parseFloat($('#jy').value); if(isNaN(y)){alert('損益(円)を入力してください');return;}
  const mk=($('#jmark')?$('#jmark').value:'')||'';
  const t=loadTrades(); t.push({pair:$('#jp').value,side:$('#js').value,yen:y,ts:Date.now(),mark:mk}); saveTrades(t); $('#jy').value=''; renderJournal();
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
function csvIngest(txt){
  const msg=$('#impmsg');
  const rows=parseCSV(txt);
  if(rows.length<2){msg.innerHTML='<span class="warn">データ行が見つかりません（1行目=見出し、2行目以降=データ）。</span>';return;}
  CSV_HEAD=rows[0].map(s=>(s||'').trim());CSV_ROWS=rows.slice(1);
  const opts=CSV_HEAD.map((h,i)=>`<option value="${i}">${(h||('列'+(i+1))).slice(0,16)}</option>`).join('');
  ['cPnl','cPair','cSide'].forEach(id=>{$('#'+id).innerHTML='<option value="-1">（なし）</option>'+opts;});
  $('#cPnl').value=guessCol(CSV_HEAD,['決済損益','実現損益','約定損益','売買損益','損益']);
  $('#cPair').value=guessCol(CSV_HEAD,['通貨ペア','通貨対','銘柄','シンボル','通貨']);
  $('#cSide').value=guessCol(CSV_HEAD,['売買区分','売買','取引区分','売り買い']);
  $('#csvmap').style.display='block';$('#csvpastebox').style.display='none';
  msg.innerHTML=`<span class="good">${CSV_ROWS.length}行を読込。列を確認して「この設定で取り込む」を押してください。</span>`;
}
async function csvLoad(file){
  if(!file)return;const msg=$('#impmsg');msg.textContent='CSV読込中…';
  try{ const txt=await readCSVFile(file); csvIngest(txt); }
  catch(e){msg.innerHTML='<span class="warn">CSV読込失敗: '+e.message+'</span>';}
  finally{const f=$('#csvfile');if(f)f.value='';}
}
function csvPaste(){
  const txt=$('#csvpaste').value||'';
  if(!txt.trim()){alert('CSVの中身を貼り付けてください');return;}
  try{ csvIngest(txt); }catch(e){$('#impmsg').innerHTML='<span class="warn">読込失敗: '+e.message+'</span>';}
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
  // ①-2: 最大DD・最大連敗・ペイオフレシオ
  let cum=0,peak=0,dd=0;const curve=[0];
  t.forEach(x=>{cum+=x.yen;curve.push(cum);peak=Math.max(peak,cum);dd=Math.min(dd,cum-peak);});
  let ls=0,maxLS=0;t.forEach(x=>{if(x.yen<0){ls++;maxLS=Math.max(maxLS,ls);}else if(x.yen>0){ls=0;}});
  const avgWin=wins.length?gw/wins.length:0,avgLose=losses.length?gl/losses.length:0;
  const payoff=avgLose?avgWin/avgLose:(avgWin>0?Infinity:0);
  $('#kpis').innerHTML=`
    <div class="kpi"><div class="l">勝率</div><div class="v ${wr>=50?'up':'dn'}">${wr.toFixed(0)}%</div></div>
    <div class="kpi"><div class="l">純損益</div><div class="v ${net>=0?'up':'dn'}">${yen(net)}</div></div>
    <div class="kpi"><div class="l">PF</div><div class="v ${pf>=1?'up':'dn'}">${pf===Infinity?'∞':pf.toFixed(2)}</div></div>
    <div class="kpi"><div class="l">最大DD</div><div class="v dn">${Math.round(dd).toLocaleString()}円</div></div>
    <div class="kpi"><div class="l">最大連敗</div><div class="v ${maxLS>=5?'dn':''}">${maxLS}</div></div>
    <div class="kpi"><div class="l">ペイオフ</div><div class="v ${payoff>=1?'up':'dn'}">${payoff===Infinity?'∞':payoff.toFixed(2)}</div></div>`;
  // ①-4: 損益曲線（0円破線＋DD区間を薄赤網掛け＋最終値ラベル）
  const W=320,H=120,mn=Math.min(...curve),mx=Math.max(...curve),rg=(mx-mn)||1;
  const X=i=>i/(curve.length-1||1)*W,Y=v=>H-((v-mn)/rg*(H-12))-6;
  let d='';curve.forEach((v,i)=>{d+=(i?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)+' ';});
  const zeroY=Y(0);
  // ドローダウン区間（直近ピークを下回っている連続区間）を薄赤網掛け
  let pk=curve[0],ddSeg='';let segStart=-1;
  for(let i=0;i<curve.length;i++){if(curve[i]>=pk){if(segStart>=0){ddSeg+=`<rect x="${X(segStart).toFixed(1)}" y="0" width="${(X(i)-X(segStart)).toFixed(1)}" height="${H}" fill="rgba(255,122,138,.10)"/>`;segStart=-1;}pk=curve[i];}else{if(segStart<0)segStart=i;}}
  if(segStart>=0)ddSeg+=`<rect x="${X(segStart).toFixed(1)}" y="0" width="${(W-X(segStart)).toFixed(1)}" height="${H}" fill="rgba(255,122,138,.10)"/>`;
  const lastV=curve[curve.length-1],lx=Math.min(W-2,X(curve.length-1)),ly=Math.max(9,Math.min(H-2,Y(lastV)));
  const lbl=n?`<text x="${(lx-2).toFixed(1)}" y="${(ly-4).toFixed(1)}" text-anchor="end" font-size="10" font-family="monospace" fill="${lastV>=0?'#4ade9b':'#ff7a8a'}">${(lastV>=0?'+':'')+Math.round(lastV).toLocaleString()}</text>`:'';
  $('#eq').innerHTML=`${ddSeg}<line x1="0" y1="${zeroY}" x2="${W}" y2="${zeroY}" stroke="#2a3340" stroke-width="1" stroke-dasharray="3 3"/><path d="${d}" fill="none" stroke="${net>=0?'#4ade9b':'#ff7a8a'}" stroke-width="2"/>${lbl}`;
  // ペア別
  const byp={};t.forEach(x=>{(byp[x.pair]=byp[x.pair]||[]).push(x);});
  $('#bypair').querySelector('tbody').innerHTML=Object.entries(byp).map(([p,arr])=>{
    const nn=arr.length,w=arr.filter(a=>a.yen>0).length,nt=arr.reduce((a,b)=>a+b.yen,0);
    return `<tr><td>${p}</td><td>${nn}</td><td>${(w/nn*100).toFixed(0)}%</td><td class="${nt>=0?'good':'warn'}">${yen(nt)}</td></tr>`;}).join('')||'<tr><td colspan=4 style="text-align:center;color:#566">記録なし</td></tr>';
  // ①-3: 月別サマリー（日時なしは「日時なし」行）
  const bym={};t.forEach(x=>{var k=x.ts?new Date(x.ts).toLocaleDateString('sv-SE',{timeZone:'Asia/Tokyo'}).slice(0,7):'日時なし';(bym[k]=bym[k]||[]).push(x);});
  var mkeys=Object.keys(bym).sort();
  var mtb=document.querySelector('#bymonth tbody');
  if(mtb)mtb.innerHTML=mkeys.map(function(k){var arr=bym[k],nn=arr.length,w=arr.filter(a=>a.yen>0).length,nt=arr.reduce((a,b)=>a+b.yen,0);
    return `<tr><td>${k}</td><td>${nn}</td><td>${(w/nn*100).toFixed(0)}%</td><td class="${nt>=0?'good':'warn'}">${yen(nt)}</td></tr>`;}).join('')||'<tr><td colspan=4 style="text-align:center;color:#566">記録なし</td></tr>';
  // ①-3: マーク別成績（未記録は除外）
  var MK=[['🟢','🟢'],['🟡','🟡'],['🔴','🔴']];
  var ktb=document.querySelector('#bymark tbody');
  if(ktb)ktb.innerHTML=MK.map(function(m){var arr=t.filter(function(x){return x.mark===m[0];});if(!arr.length)return'';
    var nn=arr.length,w=arr.filter(a=>a.yen>0).length,nt=arr.reduce((a,b)=>a+b.yen,0);
    return `<tr><td>${m[1]}</td><td>${nn}</td><td>${(w/nn*100).toFixed(0)}%</td><td class="${nt>=0?'good':'warn'}">${yen(nt)}</td></tr>`;}).join('')||'<tr><td colspan=4 style="text-align:center;color:#566">マーク記録なし</td></tr>';
  $('#trlist').innerHTML=t.slice().reverse().map((x,ri)=>{const i=t.length-1-ri;
    const sd=x.side==='買い'?'<span style="color:var(--up)">買</span>':x.side==='売り'?'<span style="color:var(--down)">売</span>':'<span style="color:var(--mut)">—</span>';
    const dts=x.ts?new Date(x.ts).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'日時なし';
    return `<div><span>${x.mark||''}${x.pair} ${sd} <span style="opacity:.6">${dts}</span></span><span class="${x.yen>=0?'good':'warn'}">${yen(x.yen)} <span class="del" onclick="delTrade(${i})">×</span></span></div>`;}).join('');
  renderTodaySummary();
}
// 共通UX-2: 今日の1行サマリー
function renderTodaySummary(){var el=document.getElementById('todaybar');if(!el)return;
  var today=new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Tokyo'});
  var tt=loadTrades().filter(function(x){return x.ts&&new Date(x.ts).toLocaleDateString('sv-SE',{timeZone:'Asia/Tokyo'})===today;});
  if(!tt.length){el.innerHTML='<span style="color:var(--mut)">今日の記録なし</span>';return;}
  var w=tt.filter(function(x){return x.yen>0;}).length,l=tt.filter(function(x){return x.yen<0;}).length,net=tt.reduce(function(a,b){return a+b.yen;},0);
  el.innerHTML='📅 今日：<b>'+w+'勝'+l+'敗</b> <b class="'+(net>=0?'good':'warn')+'">'+yen(net)+'</b>';
}
// ①-5: 全記録CSV書き出し（BOM付きUTF-8・GOV3の3段同期配布）
function _toolsCsvDist(blob,filename){var file=null;try{file=new File([blob],filename,{type:blob.type});}catch(e){}
  if(file&&navigator.canShare&&navigator.canShare({files:[file]})){navigator.share({files:[file],title:filename}).catch(function(){});return;}
  try{var u=URL.createObjectURL(blob);var a=document.createElement('a');a.href=u;a.download=filename;a.style.display='none';document.body.appendChild(a);a.click();setTimeout(function(){try{document.body.removeChild(a);}catch(_e){}URL.revokeObjectURL(u);},150);return;}catch(e){}
  try{window.open(URL.createObjectURL(blob),'_blank');}catch(e){}}
function exportTradesCSV(){var t=loadTrades();
  var rows=[['日時','ペア','売買','損益円','マーク']];
  t.forEach(function(x){var dt=x.ts?new Date(x.ts).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo'}):'';rows.push([dt,x.pair,x.side||'',x.yen,x.mark||'']);});
  var body='\ufeff'+rows.map(function(r){return r.map(function(c){c=(c==null?'':''+c);return /[",\n]/.test(c)?'"'+c.replace(/"/g,'""')+'"':c;}).join(',');}).join('\r\n');
  _toolsCsvDist(new Blob([body],{type:'text/csv;charset=utf-8'}),'fxnavi-trades.csv');}
// 共通UX-1: セクションのアコーディオン
function toggleSection(idx){try{var st=JSON.parse(localStorage.getItem('fxnavi_sec_open')||'null')||{0:true};st[idx]=!st[idx];localStorage.setItem('fxnavi_sec_open',JSON.stringify(st));applySections();}catch(e){}}
function applySections(){var st;try{st=JSON.parse(localStorage.getItem('fxnavi_sec_open')||'null');}catch(e){st=null;}if(!st)st={0:true};
  var secs=document.querySelectorAll('.sec[data-sec]');
  secs.forEach(function(s){var idx=s.getAttribute('data-sec');var open=!!st[idx];s.setAttribute('data-open',open?'1':'0');
    var card=s.nextElementSibling;if(card&&card.classList.contains('card'))card.style.display=open?'':'none';
    var mk=s.querySelector('.secarrow');if(mk)mk.textContent=open?'▲':'▼';});}
/* ============ ② リスク/ロット計算 ============ */
function loadRisk(){try{return JSON.parse(localStorage.getItem('fxnavi_risk'))||{cap:10000,rpct:2,units:1000}}catch(e){return{cap:10000,rpct:2,units:1000}}}
async function calcRisk(){
  const cap=parseFloat($('#cap').value),rpct=parseFloat($('#rpct').value),slp=parseFloat($('#slp').value),units=parseFloat($('#units').value);
  if([cap,rpct,slp,units].some(isNaN)){alert('数値を入力してください');return;}
  const allow=cap*rpct/100;
  const recUnits=Math.floor(allow/(slp*PS)/100)*100;
  const lossAtUnits=slp*PS*units;
  const maxSL=allow/(PS*units);
  localStorage.setItem('fxnavi_risk',JSON.stringify({cap,rpct,units}));
  const over=(lossAtUnits>allow);
  // ②-3: ④バックテストの資金・リスク欄へ自動引き継ぎ
  if($('#bcap'))$('#bcap').value=cap; if($('#brisk'))$('#brisk').value=rpct;
  // ②-1: 証拠金チェック（レバ25倍・③と同じWorker経由レート／未取得は161円概算）
  var rate=await getWorkerRate('USD_JPY'),approx=false; if(rate==null||!isFinite(rate)){rate=161;approx=true;}
  var maxUnitsMargin=Math.floor(cap*25/rate/100)*100;
  var marginOver=recUnits>maxUnitsMargin;
  var effRisk=cap?(maxUnitsMargin*slp*PS/cap*100):0;
  var marginBox=marginOver
    ? `<div class="kpi" style="grid-column:span 3"><div class="l">証拠金チェック${approx?'（概算 1$≈161円）':''}</div><div class="v dn">⚠ 証拠金不足：実際は${maxUnitsMargin.toLocaleString()}通貨が上限（実効リスク${effRisk.toFixed(1)}%）</div></div>`
    : `<div class="kpi" style="grid-column:span 3"><div class="l">証拠金チェック${approx?'（概算 1$≈161円）':''}</div><div class="v up">✅ 証拠金OK（上限${maxUnitsMargin.toLocaleString()}通貨）</div></div>`;
  // ②-2: ケリー推奨（①記録 直近50件から勝率p・ペイオフb）
  var kellyBox=_kellyRiskBox();
  $('#riskout').innerHTML=`
    <div class="kpi"><div class="l">許容損失</div><div class="v go">${Math.round(allow).toLocaleString()}円</div></div>
    <div class="kpi"><div class="l">推奨通貨量</div><div class="v">${recUnits.toLocaleString()}</div></div>
    <div class="kpi"><div class="l">この量での想定損失</div><div class="v ${over?'dn':'up'}">${Math.round(lossAtUnits).toLocaleString()}円</div></div>
    <div class="kpi"><div class="l">この量での最大SL</div><div class="v">${maxSL.toFixed(1)}pips</div></div>
    <div class="kpi" style="grid-column:span 3"><div class="l">判定</div><div class="v ${over?'dn':'up'}">${over?'⚠ 今のSL/通貨量はリスク過大（許容超過）':'✅ 許容内。資金/リスク%を保存しました'}</div></div>
    ${marginBox}${kellyBox}`;
}
// Worker経由レート取得（③と同じ）。取得不可はnull
async function getWorkerRate(sym){try{if(!LIVE_PRICE_URL)return null;var r=await fetch(LIVE_PRICE_URL.split('?')[0]+'?t='+Date.now(),{cache:'no-store'});var j=await r.json();var d=(j.data||[]).find(function(x){return x.symbol===sym;});if(d&&!isNaN(parseFloat(d.bid)))return parseFloat(d.bid);}catch(e){}return null;}
// ②-2: 実績ベースのケリー推奨リスク%（f*=(b·p−(1−p))/b の1/4・上限2%・フルケリーは破産リスク大のため1/4採用）
function _kellyRiskBox(){var t=loadTrades().slice(-50),n=t.length;
  if(n<30)return `<div class="kpi" style="grid-column:span 3"><div class="l">実績ベース推奨リスク</div><div class="v">📊 記録蓄積中（n=${n}）</div></div>`;
  var wins=t.filter(function(x){return x.yen>0;}),losses=t.filter(function(x){return x.yen<0;});
  var p=wins.length/n,gw=wins.reduce(function(a,b){return a+b.yen;},0),gl=Math.abs(losses.reduce(function(a,b){return a+b.yen;},0));
  var b=(losses.length&&gl)?((gw/Math.max(1,wins.length))/(gl/losses.length)):(gw>0?2:1);
  var f=b>0?(b*p-(1-p))/b:0;
  if(f<=0)return `<div class="kpi" style="grid-column:span 3"><div class="l">実績ベース推奨リスク</div><div class="v dn">推奨0%＝実績上エッジなし（勝率${(p*100).toFixed(0)}%/b${b.toFixed(2)}）</div></div>`;
  var rec=Math.min(2,f/4*100);
  return `<div class="kpi" style="grid-column:span 3"><div class="l">実績ベース推奨リスク（1/4ケリー）</div><div class="v go">${(Math.round(rec*100)/100)}%<span style="font-size:10px;color:var(--mut)"> （勝率${(p*100).toFixed(0)}%/b${b.toFixed(2)}/n${n}）</span></div></div>`;
}
/* ============ ③ スプレッド/コスト判定 ============ */
// FX市場が休場か（JST土曜7時〜月曜7時）
function isFxClosed(){var p=new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Tokyo',weekday:'short',hour:'2-digit',hour12:false}).formatToParts(new Date());var wd='',h=0;p.forEach(function(x){if(x.type==='weekday')wd=x.value;if(x.type==='hour')h=+x.value;});
  if(wd==='Sat'&&h>=7)return true; if(wd==='Sun')return true; if(wd==='Mon'&&h<7)return true; return false;}
async function loadSpreads(){
  if(!LIVE_PRICE_URL){$('#sprtbl').querySelector('tbody').innerHTML='<tr><td colspan="4" class="warn" style="text-align:center">WorkerのURLを設定してください</td></tr>';return;}
  if(isFxClosed()){$('#sprtbl').querySelector('tbody').innerHTML='<tr><td colspan="4" style="text-align:center;color:var(--mut)">🛌 週末休場（JST土7時〜月7時）</td></tr>';var st0=document.getElementById('sprtime');if(st0)st0.textContent='';return;}
  const tp=parseFloat($('#ctp').value)||2;
  try{
    const r=await fetch(LIVE_PRICE_URL.split('?')[0]+'?t='+Date.now(),{cache:'no-store'});
    const j=await r.json();const want=['USD_JPY','EUR_JPY','GBP_JPY','AUD_JPY'];
    const rows=(j.data||[]).filter(d=>want.includes(d.symbol)).map(d=>{
      const sp=(+d.ask - +d.bid)/PS, ratio=sp/tp;
      // ③-2: 3段階（15%未満🟢 / 15〜30%🟡 / 30%以上🔴）
      var cls,mk; if(ratio<0.15){cls='good';mk='🟢 良好';}else if(ratio<0.30){cls='warn';mk='🟡 注意';}else{cls='warn';mk='🔴 見送り';}
      return `<tr><td>${d.symbol.replace('_','/')}</td><td>${sp.toFixed(1)}pips</td><td class="${cls}">${(ratio*100).toFixed(0)}%</td><td class="${cls}">${mk}</td></tr>`;
    }).join('');
    $('#sprtbl').querySelector('tbody').innerHTML=rows||'<tr><td colspan="4" style="text-align:center;color:#566">取得失敗</td></tr>';
    var st=document.getElementById('sprtime');if(st)st.textContent='取得 '+new Date().toLocaleTimeString('ja-JP',{timeZone:'Asia/Tokyo',hour12:false});
  }catch(e){$('#sprtbl').querySelector('tbody').innerHTML='<tr><td colspan="4" class="warn" style="text-align:center">取得失敗</td></tr>';}
}
// ③-1: 自動更新トグル（ONで30秒毎・休場は停止）
var _sprTimer=null;
function toggleSprAuto(cb){try{if(cb.checked){localStorage.setItem('fxnavi_spr_auto','1');if(_sprTimer)clearInterval(_sprTimer);loadSpreads();_sprTimer=setInterval(function(){if(isFxClosed()){loadSpreads();}else{loadSpreads();}},30000);}else{localStorage.removeItem('fxnavi_spr_auto');if(_sprTimer){clearInterval(_sprTimer);_sprTimer=null;}}}catch(e){}}
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
  try{const r=await fetch(`${base}?path=${encodeURIComponent(path)}&t=${Date.now()}`,{cache:'no-store'});const j=await r.json();(j.data||[]).forEach(k=>{rows[+k.openTime]=[+k.high,+k.low,+k.close,+k.openTime];});}catch(e){}}
  return Object.keys(rows).map(Number).sort((a,b)=>a-b).map(t=>rows[t]);}
// ④-1: 簡易コンフルエンス（index.html同等・シグナル判定式は不変、集計フィルタのみ）
function _zoneJSTgreen(ts){if(ts==null)return false;var pp=new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Tokyo',hour:'2-digit',hour12:false}).formatToParts(new Date(ts));var h=0;pp.forEach(function(x){if(x.type==='hour')h=+x.value;});return (h>=16&&h<24);}
function _dowTools(oh){var n=oh.length;if(n<11)return 'range';var H=[],L=[];for(var i=2;i<n-2;i++){var h=oh[i][0],l=oh[i][1];if(h>oh[i-1][0]&&h>oh[i-2][0]&&h>oh[i+1][0]&&h>oh[i+2][0])H.push(h);if(l<oh[i-1][1]&&l<oh[i-2][1]&&l<oh[i+1][1]&&l<oh[i+2][1])L.push(l);}if(H.length<2||L.length<2)return 'range';var h2=H.slice(-2),l2=L.slice(-2);if(h2[1]>h2[0]&&l2[1]>l2[0])return 'up';if(h2[1]<h2[0]&&l2[1]<l2[0])return 'down';return 'range';}
function _confTools(oh,i,side,e200){var cnt=0;
  var ev=e200[i],ev6=e200[i-6];if(ev!=null&&ev6!=null){if((side==='買い'&&ev-ev6>0)||(side==='売り'&&ev-ev6<0))cnt++;}
  var ax=adxL(oh.slice(0,i+1),14);if(ax!=null&&ax>=30&&ax<=40)cnt++;
  if(_zoneJSTgreen(oh[i][3]))cnt++;
  var dw=_dowTools(oh.slice(0,i+1));if((side==='買い'&&dw==='up')||(side==='売り'&&dw==='down'))cnt++;
  return cnt>=3;}
function _statsOf(arr){var n=arr.length;if(!n)return null;var wins=arr.filter(x=>x.pips>0),losses=arr.filter(x=>x.pips<=0);
  var net=arr.reduce((a,b)=>a+b.pips,0),wr=wins.length/n*100,gw=wins.reduce((a,b)=>a+b.pips,0),gl=Math.abs(losses.reduce((a,b)=>a+b.pips,0));
  var pf=gl?gw/gl:Infinity,exp=net/n,cum=0,peak=0,dd=0;arr.forEach(x=>{cum+=x.pips;peak=Math.max(peak,cum);dd=Math.min(dd,cum-peak);});
  return {n:n,wr:wr,net:net,pf:pf,exp:exp,dd:dd};}
// ④-2: 資金シミュ（複利・スプレッド控除後。SL到達=リスク%損失として建玉）
function _simOf(arr,cap,risk){if(!arr.length||!cap||!risk)return null;var c=cap,peak=cap,ddp=0;
  arr.forEach(function(x){var r=x.slp?x.pips/x.slp:0;c=c*(1+r*risk/100);if(c<0)c=0;peak=Math.max(peak,c);if(peak>0)ddp=Math.min(ddp,(c-peak)/peak*100);});
  return {finalCap:c,retPct:(c/cap-1)*100,maxDDpct:ddp};}
async function runBacktestCore(sym,mode,days,spr){
  const P=BP[mode],base=LIVE_PRICE_URL.split('?')[0].replace(/\/$/,'');
  let oh=await fetchHist(base,sym,P.interval,days);
  const CAP=mode==='scalp'?2200:1200; if(oh.length>CAP)oh=oh.slice(-CAP);
  if(oh.length<120)return {error:'データ不足'};
  const closes=oh.map(r=>r[2]),e200=emaS(closes,200);
  const warm=Math.max(P.ema_s,P.macd[1],P.adx*2)+5;
  const trades=[];let i=warm;
  while(i<oh.length-1){
    const side=sideAt(oh.slice(0,i+1),sym,P);
    if(!side){i++;continue;}
    const a=atrL(oh.slice(0,i+1),P.atr); if(!a){i++;continue;}
    const slp=a/PS, tpp=slp*1.5, entry=oh[i][2], entryIdx=i;
    const tp=side==='買い'?entry+tpp*PS:entry-tpp*PS, sl=side==='買い'?entry-slp*PS:entry+slp*PS;
    let exit=null;
    for(let j=i+1;j<oh.length;j++){const h=oh[j][0],l=oh[j][1];
      if(side==='買い'){if(l<=sl){exit=-slp;break;}if(h>=tp){exit=tpp;break;}}
      else{if(h>=sl){exit=-slp;break;}if(l<=tp){exit=tpp;break;}}
      i=j;}
    if(exit==null)break;
    const conf=_confTools(oh,entryIdx,side,e200);
    trades.push({pips:exit-spr,slp:slp,conf:conf}); i++;
  }
  return {trades:trades,ohlen:oh.length};
}
function _kpiRow(label,s){if(!s)return `<div class="kpi" style="grid-column:span 3"><div class="l">${label}</div><div class="v">シグナルなし</div></div>`;
  return `<div class="kpi"><div class="l">${label}・回数</div><div class="v">${s.n}</div></div>
    <div class="kpi"><div class="l">勝率</div><div class="v ${s.wr>=50?'up':'dn'}">${s.wr.toFixed(0)}%</div></div>
    <div class="kpi"><div class="l">純pips</div><div class="v ${s.net>=0?'up':'dn'}">${s.net>=0?'+':''}${s.net.toFixed(1)}</div></div>`;}
async function runBacktest(){
  if(!LIVE_PRICE_URL){$('#btmsg').innerHTML='<span class="warn">WorkerのURL（LIVE_PRICE_URL）を tools.html に設定してください</span>';return;}
  if(_btBusy)return; _btBusy=true;
  const sym=$('#bsym').value,mode=$('#bmode').value,days=Math.min(14,Math.max(1,parseInt($('#bdays').value)||3)),spr=parseFloat($('#bspr').value)||0;
  const onlyGreen=$('#btGreen')&&$('#btGreen').checked;
  const cap=parseFloat(($('#bcap')||{}).value)||0,risk=parseFloat(($('#brisk')||{}).value)||0;
  $('#btbtn').textContent='計算中…';$('#btbtn').disabled=true;$('#btmsg').textContent='';
  try{
    const R=await runBacktestCore(sym,mode,days,spr);
    if(R.error){$('#btmsg').innerHTML='<span class="warn">データ不足（市場休場や期間不足）。期間を増やすか平日に実行してください</span>';return;}
    if(!R.trades.length){$('#btmsg').innerHTML='<span class="warn">この期間ではシグナル発生なし</span>';return;}
    const all=_statsOf(R.trades), sel=_statsOf(R.trades.filter(x=>x.conf));
    // ④-1: 全シグナル vs 🟢厳選 の並列
    let html=`<div style="grid-column:span 3;font-size:11px;color:var(--mut);font-family:var(--mono)">全シグナル</div>${_kpiRow('全',all)}`;
    if(onlyGreen)html+=`<div style="grid-column:span 3;font-size:11px;color:var(--gold);font-family:var(--mono);margin-top:4px">🟢 総合判定OKのみ（4項目中3以上）</div>${_kpiRow('🟢厳選',sel)}`;
    // ④-2: 資金シミュ（複利）
    const simSet=onlyGreen&&sel?R.trades.filter(x=>x.conf):R.trades;
    const sim=_simOf(simSet,cap,risk);
    if(sim)html+=`<div class="kpi"><div class="l">最終資金</div><div class="v ${sim.finalCap>=cap?'up':'dn'}">${Math.round(sim.finalCap).toLocaleString()}円</div></div>
      <div class="kpi"><div class="l">収益率</div><div class="v ${sim.retPct>=0?'up':'dn'}">${sim.retPct>=0?'+':''}${sim.retPct.toFixed(1)}%</div></div>
      <div class="kpi"><div class="l">最大DD%</div><div class="v dn">${sim.maxDDpct.toFixed(1)}%</div></div>`;
    $('#btout').innerHTML=html;
    // ④-4: 免責＋サンプル注記
    const useN=(onlyGreen&&sel)?sel.n:all.n;
    $('#btmsg').innerHTML=`${sym} ${mode} 直近${R.ohlen}本・スプレッド${spr}pips控除後。サンプル${useN}件${useN<30?'（30件未満は参考値）':''}。<br>${all.exp>=0?'<span class="good">全シグナル期待値プラス</span>':'<span class="warn">全シグナル期待値マイナス</span>'}${onlyGreen&&sel?(sel.exp>=0?' ／ <span class="good">🟢厳選もプラス</span>':' ／ <span class="warn">🟢厳選はマイナス</span>'):''}<br><span style="color:var(--mut)">過去成績は将来を保証しません。</span>`;
  }finally{$('#btbtn').textContent='バックテスト実行';$('#btbtn').disabled=false;_btBusy=false;}
}
// ④-3: 4ペア一括実行（順次・プログレス・二重実行防止）
var _btBusy=false;
async function runBacktestAll(){
  if(!LIVE_PRICE_URL){$('#btmsg').innerHTML='<span class="warn">WorkerのURLを設定してください</span>';return;}
  if(_btBusy)return; _btBusy=true;
  const mode=$('#bmode').value,days=Math.min(14,Math.max(1,parseInt($('#bdays').value)||3)),spr=parseFloat($('#bspr').value)||0;
  const onlyGreen=$('#btGreen')&&$('#btGreen').checked;
  const cap=parseFloat(($('#bcap')||{}).value)||0,risk=parseFloat(($('#brisk')||{}).value)||0;
  const syms=['USD_JPY','EUR_JPY','GBP_JPY','AUD_JPY'];
  $('#btbtn').disabled=true;
  try{
    let allTr=[],rows='';
    for(let k=0;k<syms.length;k++){
      $('#btmsg').innerHTML=`⏳ 一括実行中… ${k+1}/${syms.length}（${syms[k].replace('_','/')}）`;
      const R=await runBacktestCore(syms[k],mode,days,spr);
      if(R.error||!R.trades.length){rows+=`<tr><td>${syms[k].replace('_','/')}</td><td colspan="3" style="text-align:center;color:var(--mut)">—</td></tr>`;continue;}
      const set=onlyGreen?R.trades.filter(x=>x.conf):R.trades;const s=_statsOf(set)||{n:0,wr:0,net:0};
      allTr=allTr.concat(set);
      rows+=`<tr><td>${syms[k].replace('_','/')}</td><td>${s.n}</td><td class="${s.wr>=50?'good':'warn'}">${s.wr.toFixed(0)}%</td><td class="${s.net>=0?'good':'warn'}">${s.net>=0?'+':''}${s.net.toFixed(1)}</td></tr>`;
    }
    const tot=_statsOf(allTr),sim=_simOf(allTr,cap,risk);
    const totRow=tot?`<tr style="border-top:2px solid var(--gold)"><td><b>合算</b></td><td><b>${tot.n}</b></td><td class="${tot.wr>=50?'good':'warn'}"><b>${tot.wr.toFixed(0)}%</b></td><td class="${tot.net>=0?'good':'warn'}"><b>${tot.net>=0?'+':''}${tot.net.toFixed(1)}</b></td></tr>`:'';
    $('#btout').innerHTML=`<table style="grid-column:span 3"><thead><tr><th>ペア</th><th>回数</th><th>勝率</th><th>純pips</th></tr></thead><tbody>${rows}${totRow}</tbody></table>`
      +(sim?`<div class="kpi"><div class="l">合算 最終資金</div><div class="v ${sim.finalCap>=cap?'up':'dn'}">${Math.round(sim.finalCap).toLocaleString()}円</div></div><div class="kpi"><div class="l">収益率</div><div class="v ${sim.retPct>=0?'up':'dn'}">${sim.retPct>=0?'+':''}${sim.retPct.toFixed(1)}%</div></div><div class="kpi"><div class="l">最大DD%</div><div class="v dn">${sim.maxDDpct.toFixed(1)}%</div></div>`:'');
    $('#btmsg').innerHTML=`4ペア一括・${mode}・スプレッド${spr}pips控除後${onlyGreen?'・🟢厳選':''}。サンプル${tot?tot.n:0}件${(tot&&tot.n<30)?'（30件未満は参考値）':''}。<br><span style="color:var(--mut)">過去成績は将来を保証しません。</span>`;
  }finally{$('#btbtn').disabled=false;_btBusy=false;}
}
loadRisk&&(()=>{const r=loadRisk();$('#cap').value=r.cap;$('#rpct').value=r.rpct;$('#units').value=r.units;
  if($('#bcap'))$('#bcap').value=r.cap; if($('#brisk'))$('#brisk').value=r.rpct;})();
try{if(localStorage.getItem('fxnavi_spr_auto')==='1'){var _sa=document.getElementById('sprAuto');if(_sa){_sa.checked=true;toggleSprAuto(_sa);}}}catch(e){}
renderJournal();
try{applySections();}catch(e){}
