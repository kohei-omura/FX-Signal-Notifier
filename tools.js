const $=s=>document.querySelector(s);
const yen=x=>(x>=0?'+':'')+Math.round(x).toLocaleString()+'円';
const PS=0.01;
var CSV_GMO=null;
function _detectGMO(head){
  if(!head||!head.length) return null;
  var idx=function(name){ return head.findIndex(function(h){ return h&&h.indexOf(name)>=0; }); };
  var d=idx('約定日時'), p=idx('銘柄名'), sd=idx('売買区分'), pl=idx('実現損益');
  if(d<0||p<0||sd<0||pl<0) return null;
  return {date:d, pair:p, side:sd, pnl:pl,
          qty:idx('約定数量'), px:idx('約定単価'), entry:idx('建単価'), kind:idx('取引区分')};
}
function _scanDateCol(rows,head){
  if(!rows||!rows.length) return -1;
  var n=Math.min(rows.length,20), best=-1, bestHit=0;
  var cols=(head&&head.length)?head.length:(rows[0]?rows[0].length:0);
  for(var c=0;c<cols;c++){
    var hit=0;
    for(var r=0;r<n;r++){ if(rows[r]&&rows[r][c]&&_parseAnyDate(rows[r][c])) hit++; }
    if(hit>bestHit){ bestHit=hit; best=c; }
  }
  return (bestHit>=Math.max(1,Math.floor(n*0.6)))?best:-1;
}
function _parseAnyDate(v){
  if(v==null) return null;
  var t=String(v).trim(); if(!t) return null;
  var m=t.match(/(\d{4})[\/\-年.](\d{1,2})[\/\-月.](\d{1,2})(?:[日]?)(?:[ T　]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?/);
  if(!m) return null;
  var y=+m[1],mo=+m[2],d=+m[3],hh=(m[4]!=null?+m[4]:0),mi=(m[5]!=null?+m[5]:0);
  if(!(y>1990&&mo>=1&&mo<=12&&d>=1&&d<=31)) return null;
  var ms=Date.UTC(y,mo-1,d,hh-9,mi);   // JST入力として扱う
  var pad=function(n){return (n<10?'0':'')+n;};
  return {ms:ms, jst:y+'-'+pad(mo)+'-'+pad(d)+' '+pad(hh)+':'+pad(mi)+' JST'};
}
/* ===== 日時・時間帯ヘルパー（バックテスト分析用） ===== */
function _tJst(str){ if(!str)return null;
  var m=String(str).match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if(!m)return null; return new Date(Date.UTC(+m[1],+m[2]-1,+m[3],+m[4]-9,+m[5])); }
function _tMs(x){ var d=_tJst(x.closed_at||''); if(d)return d.getTime(); return x.ts||0; }
function _tOpen(x){ return _tJst(x.opened_at||'')||null; }
function _tHour(d){ return d?(new Date(d.getTime()+9*3600e3)).getUTCHours():null; }
function _tWd(d){ return d?['日','月','火','水','木','金','土'][(new Date(d.getTime()+9*3600e3)).getUTCDay()]:''; }
function _tSess(h){ if(h==null)return ''; if(h>=8&&h<15)return '東京'; if(h>=15&&h<21)return 'ロンドン'; if(h>=21||h<6)return 'ニューヨーク'; return 'オセアニア'; }
function _tHold(x){ var o=_tOpen(x),c=_tJst(x.closed_at||''); return (o&&c)?Math.max(0,Math.round((c-o)/60000)):''; }
function _scoreBand(v){ if(v==null||v==='')return ''; v=+v;
  if(v>=80)return '80%以上'; if(v>=65)return '65-79%'; if(v>=50)return '50-64%'; if(v>=35)return '35-49%'; return '35%未満'; }
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
    var _rec={pair:pair,side:side,yen:y,ts:Date.now()};
    var _d=_parseAnyDate(ln);
    if(_d){ _rec.ts=_d.ms; _rec.closed_at=_d.jst; _rec.opened_at=_d.jst; }
    if(_d){
      var _pk=[_rec.closed_at,_rec.pair,_rec.yen,''].join('|');
      if(!window.__pasteSeen){ window.__pasteSeen=new Set(t.map(function(x){return [x.closed_at||x.ts||'',x.pair||'',x.yen,(x.entry!=null?x.entry:'')].join('|');})); }
      if(window.__pasteSeen.has(_pk)){ continue; }
      window.__pasteSeen.add(_pk);
    }
    t.push(_rec); added++;
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
  ['cPnl','cPair','cSide','cDate','cOpen'].forEach(id=>{var e=$('#'+id); if(e) e.innerHTML='<option value="-1">（なし）</option>'+opts;});
  $('#cPnl').value=guessCol(CSV_HEAD,['決済損益','実現損益','約定損益','売買損益','損益']);
  CSV_GMO=_detectGMO(CSV_HEAD);
  if($('#cDate')){
    var di=CSV_GMO?CSV_GMO.date:guessCol(CSV_HEAD,['決済日時','決済約定日時','約定日時','決済時刻','決済日','取引日時','日時','日付','時刻','Date','date']);
    if(di<0) di=_scanDateCol(CSV_ROWS,CSV_HEAD);
    $('#cDate').value=di;
  }
  if(CSV_GMO){
    if($('#cPnl')) $('#cPnl').value=CSV_GMO.pnl;
    if($('#cPair')) $('#cPair').value=CSV_GMO.pair;
    if($('#cSide')) $('#cSide').value=CSV_GMO.side;
    if($('#cInvert')) $('#cInvert').checked=true;   // GMOの決済行は反対売買
  }
  if($('#cOpen')){
    var oi=guessCol(CSV_HEAD,['新規約定日時','建玉日時','新規日時','エントリー日時','建日時']);
    $('#cOpen').value=oi;
  }
  /* ★GMOとして認識済みの場合、ここで上書きしない。
     旧版は下の2行を無条件に実行しており、guessColが-1を返すとGMOの正しい列指定が
     「（なし）」に戻ってしまう危険があった。 */
  if(!CSV_GMO){
    var _pi=guessCol(CSV_HEAD,['通貨ペア','通貨対','銘柄','シンボル','通貨']);
    var _si=guessCol(CSV_HEAD,['売買区分','売買','取引区分','売り買い']);
    $('#cPair').value=_pi;
    $('#cSide').value=_si;
  }
  $('#csvmap').style.display='block';$('#csvpastebox').style.display='none';
  if(CSV_GMO){
    msg.innerHTML='<span class="good">GMOクリック証券（FXネオ 約定履歴）を自動認識しました。'
      +CSV_ROWS.length+'行を読込・列は自動設定済みです。「この設定で取り込む」を押してください。</span>';
  }else{
    /* 列を自動認識できない＝想定と違うCSVの可能性が高い。無言で（なし）を並べると
       そのまま取り込んでデータを壊すので、何が起きているかを明示する。 */
    var _pnlOK=(+$('#cPnl').value>=0);
    msg.innerHTML='<span class="warn">⚠️ GMOの「約定履歴」形式として認識できませんでした（'+CSV_ROWS.length+'行を読込）。</span>'
      +'<br>先頭の列名: '+CSV_HEAD.slice(0,5).map(function(h){return h||'(空)';}).join(' / ')
      +'<br>GMOの<b>約定履歴</b>CSV（「約定日時」「取引区分」「銘柄名」「実現損益（円貨）」などの列を持つもの）をご確認ください。'
      +'注文履歴・入出金履歴など別の種類だと列が合いません。'
      +(_pnlOK?'':'<br><span class="warn">損益の列が特定できていません。このまま取り込むとデータが壊れるため、手動で列を選ぶか、正しいCSVを読み込んでください。</span>');
  }
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
/* 損益セルの正規化。以下をすべて許容する：
   全角数字（－１２３４）／カンマ区切り（-1,234）／会計表記の▲△／全角カンマ読点／
   括弧書きのマイナス（(1,234)＝-1234）／通貨記号・単位（¥1,234円） */
function numFromCell(s){
  if(s==null)return NaN;
  let m=(''+s)
    // 全角英数字・記号を半角へ
    .replace(/[０-９]/g,function(c){return String.fromCharCode(c.charCodeAt(0)-0xFEE0);})
    .replace(/[．－＋（），]/g,function(c){return {'．':'.','－':'-','＋':'+','（':'(','）':')','，':','}[c];})
    .replace(/[‐‑‒–—―ー−]/g,'-')      // 各種ダッシュ・長音をマイナス扱い
    .replace(/[▲△]/g,'-')             // 会計表記のマイナス
    .replace(/[、]/g,',');
  const paren=/^\s*\(.*\)\s*$/.test(m); // 括弧書きはマイナス
  m=m.replace(/[^\d.,+-]/g,'').replace(/,/g,'');
  if(!m||m==='-'||m==='+'||m==='.')return NaN;
  let v=parseFloat(m);
  if(isNaN(v))return NaN;
  if(paren&&v>0)v=-v;
  return Math.round(v);
}
function csvImport(){
  if(!CSV_ROWS){alert('先にCSVを読み込んでください');return;}
  const pi=+$('#cPnl').value,ai=+$('#cPair').value,si=+$('#cSide').value,inv=$('#cInvert').checked;
  if(pi<0){alert('損益の列を選んでください');return;}
  const t=loadTrades();let added=0,dup=0;
  // 重複防止: 決済日時+ペア+損益+建値 を一意キーにする
  const csvKey=function(x){ return [x.closed_at||x.ts||'',x.pair||'',x.yen,(x.entry!=null?x.entry:''),(x.exit!=null?x.exit:''),(x.lot!=null?x.lot:'')].join('|'); };
  const seen=new Set(t.map(csvKey));
  for(const r of CSV_ROWS){
    const y=numFromCell(r[pi]);
    if(isNaN(y)) continue;                    // 数値でない行（見出し・空行）は除外
    /* 損益0の扱い：旧版は「0＝新規行」とみなして一律スキップしていたが、
       GMOのCSVには「建単価＝約定単価」で損益ちょうど0円になる同値決済が実在し（例 8/10 AUD/JPY）、
       正当な決済が1件失われて件数が合わなくなっていた。
       新規行は建単価が空なので、建単価の有無で判別する。判別できない形式のみ従来どおり0をスキップ。 */
    if(y===0){
      var _entryCol=(CSV_GMO&&CSV_GMO.entry>=0)?String(r[CSV_GMO.entry]||'').trim():'';
      if(!_entryCol) continue;                // 建単価が無い＝新規行 → スキップ
    }
    let pair=$('#jp').value; if(ai>=0&&r[ai]){const pm=(''+r[ai]).toUpperCase().match(PAIR_RE);pair=pm?(pm[1]+'/JPY'):(''+r[ai]).trim();}
    let side='-'; if(si>=0&&r[si]){const sv=''+r[si];let s=/売/.test(sv)?'売り':(/買/.test(sv)?'買い':'-');if(inv&&s!=='-')s=(s==='売り'?'買い':'売り');side=s;}
    var rec={pair:pair,side:side,yen:y,ts:Date.now()};
    var di=($('#cDate')? +$('#cDate').value : -1), oi=($('#cOpen')? +$('#cOpen').value : -1);
    if(di>=0&&r[di]){ var cd=_parseAnyDate(r[di]); if(cd){ rec.ts=cd.ms; rec.closed_at=cd.jst; } }
    if(oi>=0&&r[oi]){ var od=_parseAnyDate(r[oi]); if(od){ rec.opened_at=od.jst; } }
    if(CSV_GMO){
      var _q=(CSV_GMO.qty>=0)?numFromCell(r[CSV_GMO.qty]):NaN;
      var _px=(CSV_GMO.px>=0)?parseFloat(String(r[CSV_GMO.px]).replace(/[^0-9.\-]/g,'')):NaN;
      var _en=(CSV_GMO.entry>=0)?parseFloat(String(r[CSV_GMO.entry]).replace(/[^0-9.\-]/g,'')):NaN;
      if(!isNaN(_q)) rec.lot=_q;
      if(!isNaN(_px)) rec.exit=_px;
      if(!isNaN(_en)) rec.entry=_en;
      if(!isNaN(_px)&&!isNaN(_en)){
        var dir=(rec.side==='買い')?1:((rec.side==='売り')?-1:0);
        if(dir) rec.pips=Math.round(((_px-_en)/0.01)*dir*10)/10;
      }
    }
    // エントリー日時が無い場合は決済日時を建玉時刻として時間帯分析に使う（近似）
    if(!rec.opened_at&&rec.closed_at) rec.opened_at=rec.closed_at;
    var _k=csvKey(rec);
    if(seen.has(_k)){ dup++; continue; }     // 同じ取引は取り込まない
    seen.add(_k);
    t.push(rec);added++;
  }
  if(!added){
    if(dup){ $('#csvmap').style.display='none'; CSV_ROWS=null;
      $('#impmsg').innerHTML='<span class="good">すべて取込済みでした（重複 '+dup+' 件をスキップ）。二重登録はされていません。</span>';
      renderJournal(); return; }
    alert('損益のある行が見つかりません。列の選択をご確認ください。');return;}
  var withDate=t.filter(function(x){return !!x.opened_at;}).length;
  saveTrades(t);$('#csvmap').style.display='none';CSV_ROWS=null;
  $('#impmsg').innerHTML='<span class="good">'+added+'件を取り込みました。</span>'+
    (dup?('<br><span class="good">重複 '+dup+' 件は自動スキップしました</span>'):'')+
    (withDate?('<br><span class="good">日時あり: '+withDate+'件 → 時間帯別・セッション別を集計しました</span>')
             :'<br><span class="warn">日時が取り込めませんでした。「決済日時の列」を選び直して再取込してください</span>');
  try{attachSnapScores();}catch(e){}
  try{var _mg=mergeTradeSources(); if(_mg.merged||_mg.dropped){ $('#impmsg').innerHTML+='<br><span class="good">アプリ決済と突合: '+_mg.merged+'件を統合 / '+_mg.dropped+'件の重複を削除</span>'; }}catch(e){}
  renderJournal();
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
/* ===== 3-1 決済履歴の自動同期（未取込があればバナー表示） ===== */
function _syncSrcId(p){
  var yen=Math.round(+(p.close_yen!=null?p.close_yen:(p.yen!=null?p.yen:NaN)));
  if(isNaN(yen)) return null;
  return String(p.id||(((p.symbol||'')+'|'+(p.closed_at||p.close_at||'')+'|'+yen)));
}
async function _fetchAppClosed(){
  var cp=[];
  for(const u of ['./status.json','./positions.json']){
    try{
      const r=await fetch(u+'?t='+Date.now(),{cache:'no-store'});
      if(!r.ok) continue;
      const j=await r.json();
      if(Array.isArray(j.closed_positions)) cp=cp.concat(j.closed_positions);
      if(Array.isArray(j.positions)) cp=cp.concat(j.positions.filter(function(p){return p&&p.status==='closed';}));
    }catch(e){}
  }
  return cp;
}
async function autoSyncCheck(){
  var bar=document.getElementById('syncbar');
  if(!bar) return;
  try{
    var cp=await _fetchAppClosed();
    if(!cp.length){ bar.style.display='none'; return; }
    var have=new Set(loadTrades().map(function(x){return x.srcId;}).filter(Boolean));
    var fresh=0;
    cp.forEach(function(p){
      var yen=Math.round(+(p.close_yen!=null?p.close_yen:(p.yen!=null?p.yen:NaN)));
      if(isNaN(yen)||yen===0) return;
      var id=_syncSrcId(p);
      if(id&&!have.has(id)){ have.add(id); fresh++; }   // 同一IDは1件として数える
    });
    if(fresh<=0){ bar.style.display='none'; return; }
    bar.style.display='block';
    bar.innerHTML='<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'+
      '<span>新しい決済 <b class="good">'+fresh+'件</b> があります。取込みますか？</span>'+
      '<span style="margin-left:auto;display:flex;gap:8px">'+
      '<button class="bgo" style="padding:7px 12px;font-size:12px" onclick="autoSyncRun()">取込む</button>'+
      '<button class="bsub" style="padding:7px 12px;font-size:12px" onclick="autoSyncDismiss()">あとで</button>'+
      '</span></div>';
  }catch(e){ bar.style.display='none'; }
}
function autoSyncRun(){
  var bar=document.getElementById('syncbar');
  if(bar) bar.style.display='none';
  appSync();
}
function autoSyncDismiss(){
  var bar=document.getElementById('syncbar');
  if(bar) bar.style.display='none';
}
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
      var _ca=p.closed_at||p.close_at||'';
      var _cd=_tJst(_ca);
      t.push({pair:pair,side:side,yen:yen,ts:(_cd?_cd.getTime():Date.now()),srcId:id,
        opened_at:(p.opened_at||''), closed_at:_ca,
        entry:(p.entry!=null?p.entry:''), exit:(p.close_price!=null?p.close_price:''),
        lot:(p.lot!=null?p.lot:''), pips:(p.close_pips!=null?p.close_pips:''),
        reason:(p.close_reason||''), mode:(p.entry_mode||''),
        score:(p.entry_score!=null?p.entry_score:''), smark:(p.entry_mark||''),
        got:(p.entry_got!=null?p.entry_got:''), signal:(p.entry_signal||''),
        rsi:(p.entry_rsi!=null?p.entry_rsi:''), tech:(p.entry_tech!=null?p.entry_tech:''),
        fund:(p.entry_fund!=null?p.entry_fund:''),
        tp:(p.tp_pips!=null?p.tp_pips:''), sl:(p.sl_pips!=null?p.sl_pips:'')});
      have.add(id);added++;
    }
    if(!added){msg.innerHTML='<span class="good">新しい決済はありませんでした（取込済み）。</span>';return;}
    saveTrades(t);msg.innerHTML=`<span class="good">${added}件をアプリから取り込みました。</span>`;renderJournal();
  }catch(e){msg.innerHTML='<span class="warn">取得失敗: '+e.message+'</span>';}
}
function delTrade(i){const t=loadTrades();t.splice(i,1);saveTrades(t);try{attachSnapScores();}catch(e){}try{mergeTradeSources();}catch(e){}renderJournal();}
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
  var _be=_breakEven(t);
  if(ktb)ktb.innerHTML=MK.map(function(m){var arr=t.filter(function(x){return _markOf(x)===m[0];});if(!arr.length)return'';
    var nn=arr.length,w=arr.filter(a=>a.yen>0).length,nt=arr.reduce((a,b)=>a+b.yen,0);
    var wrp=w/nn*100;
    // 95%信頼区間を併記。n<30は偶然のブレが大きいので「参考値」と明示する。
    var _ci=wilsonCI(w,nn);
    var _ciTxt='<span style="color:#8893a4;font-size:10px"> 95%CI '+(_ci[0]*100).toFixed(0)+'〜'+(_ci[1]*100).toFixed(0)+'%</span>';
    var _ref=nn<30?'<span style="color:#8893a4;font-size:10px"> ※参考値(n&lt;30)</span>':'';
    return `<tr><td>${m[1]}</td><td>${nn}</td><td>${wrp.toFixed(0)}%${_ciTxt}${_ref}</td><td class="${nt>=0?'good':'warn'}">${yen(nt)}</td></tr>`+
      `<tr><td colspan=4 style="padding-top:0;border-top:none">${_markBar(wrp,_be)}</td></tr>`;}).join('')||'<tr><td colspan=4 style="text-align:center;color:#566">マーク記録なし</td></tr>';
  var _bel=document.getElementById('belabel');
  if(_bel) _bel.innerHTML=(_be!=null)
    ? '縦の金線＝損益分岐勝率 '+_be.toFixed(1)+'%（これを超えていれば勝ち越し）'
    : '勝ちと負けの両方が貯まると損益分岐勝率を表示します';
  renderEdgeTables(t);
  try{renderEdgeProfile();}catch(e){}
  $('#trlist').innerHTML=t.slice().reverse().map((x,ri)=>{const i=t.length-1-ri;
    const sd=x.side==='買い'?'<span style="color:var(--up)">買</span>':x.side==='売り'?'<span style="color:var(--down)">売</span>':'<span style="color:var(--mut)">—</span>';
    const dts=x.ts?new Date(x.ts).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'日時なし';
    return `<div><span>${_markOf(x)}${x.pair} ${sd} <span style="opacity:.6">${dts}</span></span><span class="${x.yen>=0?'good':'warn'}">${yen(x.yen)} <span class="del" onclick="delTrade(${i})">×</span></span></div>`;}).join('');
  renderTodaySummary();
}
/* 判定マークの取得。
   mark  = 手入力フォームで選んだマーク（CSV取込では入らない）
   smark = エントリー時スナップショットから自動取得した総合判定（'🟢 エントリーOK' 等）
   CSV中心の運用ではmarkが常に空になり、マーク別成績が永久に「記録なし」になっていたため、
   smarkも対象にし、先頭の絵文字だけを取り出して集計する。 */
function _markOf(x){
  if(!x) return '';
  var s=String(x.mark||x.smark||'');
  if(!s) return '';
  // 絵文字はサロゲートペアのため、uフラグ無しの文字クラスでは正しく一致しない。
  // 環境によってはuフラグ非対応なので、失敗時はindexOfで代替する。
  try{ var m=s.match(/[\u{1F7E2}\u{1F7E1}\u{1F534}]/u); if(m) return m[0]; }catch(e){}
  if(s.indexOf('🟢')>=0) return '🟢';
  if(s.indexOf('🟡')>=0) return '🟡';
  if(s.indexOf('🔴')>=0) return '🔴';
  return '';
}
/* ===== CSVとアプリ決済の突合・統合 =====
   証券会社CSV: 正確な約定日時・建値・決済値・pips
   アプリ決済 : エントリー時のスコア・判断根拠
   ★同じ「1つの取引」が2つの出所から入った場合だけを1件に統合する。
     旧版は許容1時間かつ出所を問わなかったため、「同じペア・同じ損益が近い時刻に2回」起きた
     別々の取引（実データで26分差・27分差の2組）まで潰していた。
     対策：(1)許容を5分に短縮 (2)CSV同士・アプリ同士は絶対に統合しない（出所が違う組み合わせのみ） */
function _hasScore(x){ return x&&x.score!=null&&x.score!==''; }
function _hasPx(x){ return x&&x.entry!=null&&x.entry!==''; }
function _tsOf(x){ var d=_tJst(x.closed_at||''); return d?d.getTime():(x.ts||0); }
/* 出所の判定: 'csv'=約定価格を持つ証券会社データ / 'app'=スコアを持つアプリ決済 / 'manual'=手入力 */
function _srcOf(x){
  var px=_hasPx(x), sc=_hasScore(x);
  if(px&&!sc) return 'csv';
  if(sc&&!px) return 'app';
  if(px&&sc)  return 'both';   // 既に統合済み。これ以上まとめない
  return 'manual';
}
var MERGE_TOL_MS=5*60e3;       // 突合の許容時間（5分）
function mergeTradeSources(tol){
  tol=(tol==null?MERGE_TOL_MS:tol);
  var t=loadTrades();
  var hasPx=_hasPx, hasScore=_hasScore;
  var rich=function(x){ var n=0; if(hasPx(x))n+=2; if(x.pips!=null&&x.pips!=='')n+=1; if(hasScore(x))n+=1; return n; };
  var used=new Array(t.length).fill(false), merged=0, out=[];
  var order=t.map(function(x,i){return i;}).sort(function(p,q){return _tsOf(t[p])-_tsOf(t[q]);});
  for(var oi=0;oi<order.length;oi++){
    var i=order[oi]; if(used[i]) continue;
    var group=[i]; used[i]=true;
    var srcA=_srcOf(t[i]);
    for(var oj=oi+1;oj<order.length;oj++){
      var j=order[oj]; if(used[j]) continue;
      var A=t[i], B=t[j];
      if(A.pair!==B.pair) continue;
      if(+A.yen!==+B.yen) continue;
      if(Math.abs(_tsOf(A)-_tsOf(B))>tol) continue;
      // ★出所が同じものは別取引とみなし統合しない（CSVのみのデータが潰れる事故を防ぐ）
      var srcB=_srcOf(B);
      if(srcA===srcB) continue;
      if(srcA==='both'||srcB==='both') continue;
      if(!((srcA==='csv'&&srcB==='app')||(srcA==='app'&&srcB==='csv'))) continue;
      group.push(j); used[j]=true;
      break;   // 1つのCSV取引に対して統合するアプリ決済は1件だけ
    }
    if(group.length===1){ out.push(t[i]); continue; }
    var repIdx=group.slice().sort(function(x,y){ return rich(t[y])-rich(t[x]); })[0];
    var rep=Object.assign({}, t[repIdx]);
    var csvRec=null, scoreRec=null;
    group.forEach(function(k){ var r=t[k];
      if(hasPx(r)&&!csvRec) csvRec=r;
      if(hasScore(r)&&!scoreRec) scoreRec=r; });
    if(csvRec){ ['closed_at','ts','entry','exit','lot','pips'].forEach(function(k){
      if(csvRec[k]!=null&&csvRec[k]!=='') rep[k]=csvRec[k]; }); }
    if(scoreRec){ ['score','smark','got','signal','rsi','tech','fund','mode','reason','tp','sl','srcId'].forEach(function(k){
      if(scoreRec[k]!=null&&scoreRec[k]!=='') rep[k]=scoreRec[k]; });
      if(scoreRec.opened_at&&scoreRec.closed_at&&scoreRec.opened_at!==scoreRec.closed_at) rep.opened_at=scoreRec.opened_at; }
    if(!rep.opened_at&&rep.closed_at) rep.opened_at=rep.closed_at;
    merged+=(group.length-1);
    out.push(rep);
  }
  saveTrades(out);
  return {merged:merged,dropped:0,total:out.length};
}
function mergeTradesUI(){
  var r=mergeTradeSources();
  renderJournal();
  var m=$('#impmsg');
  if(m) m.innerHTML='<span class="good">統合しました：'+r.merged+'件をCSVとアプリで突合、'+r.dropped+'件の重複を削除 → 現在 '+r.total+'件</span>';
}
/* ===== スコア自動紐付け: 約定時刻に最も近いスナップショットを採用 ===== */
function _loadSnap(){ try{ return JSON.parse(localStorage.getItem('fxnavi_snap')||'[]'); }catch(e){ return []; } }
function attachSnapScores(tolMin){
  var tol=(tolMin==null?90:tolMin)*60000;
  var snap=_loadSnap(); if(!snap.length) return {filled:0,total:0,noSnap:true};
  var t=loadTrades(), filled=0;
  t.forEach(function(x){
    if(x.score!=null&&x.score!=='') return;
    var od=_tOpen(x)||_tJst(x.closed_at||''); if(!od) return;
    var want=String(x.pair||'').replace('/','_');
    var ms=od.getTime(), best=null, bd=Infinity;
    snap.forEach(function(s){
      if(s.sym!==want) return;
      var d=Math.abs(s.t-ms); if(d>tol) return;
      if(d<bd){ bd=d; best=s; }
    });
    if(best){ x.score=best.score; x.smark=best.mark; x.got=best.got;
      if(!x.signal) x.signal=best.signal; if(x.rsi==null||x.rsi==='') x.rsi=best.rsi;
      if(x.tech==null||x.tech==='') x.tech=best.tech; if(x.fund==null||x.fund==='') x.fund=best.fund;
      if(!x.mode) x.mode=best.mode; x.scoreSrc='snap'; filled++; }
  });
  if(filled) saveTrades(t);
  return {filled:filled,total:t.length};
}
/* ===== 段階3: 成績から学習する補正プロファイル（統計的に健全化 v7） =====
   旧版の問題と対策：
   (1) 10件/8件から補正が出ていた → 全体n≥100・各バケットn≥30でのみ算出（EDGE_TOTAL_MIN/EDGE_MIN）
   (2) session+hour+pair を単純加算＝同じトレードを三重計上 → 採用は「|補正|が最大の1バケットだけ」
   (3) 24時間×4セッション×4ペア=32区分の多重比較で偶然の優位が必ず出る
       → 時刻は5ゾーンに集約し、Wilson95%信頼区間がベース勝率を跨がない時のみ補正
   (4) 最大±15ptがエントリー判定を動かしていた → EDGE_CAP=5（±5pt）
   区分定義はこのファイルが唯一の実装。index.html は profile 内の対応表を参照するだけにする（二重定義の解消） */
var EDGE_KEY='fxnavi_edge';
var EDGE_MIN=30;          // 各バケットに必要な件数
var EDGE_TOTAL_MIN=100;   // 全体に必要な件数
var EDGE_CAP=5;           // 補正の上限（±pt）
var EDGE_Z=1.96;          // 95%信頼区間

/* 時間帯ゾーン（JST時→5区分）。index.html の時間帯判定と同じ考え方に揃える。
   golden=ロンドン/NY重複帯, eu=欧州序盤, avoid=回避帯, normal=標準 */
var EDGE_ZONE_OF_HOUR=(function(){
  var m={};
  for(var h=0;h<24;h++){
    if(h>=21||h<=0) m[h]='golden';        // 21-24,0時 ロンドン×NY重複
    else if(h>=16&&h<=20) m[h]='eu';      // 16-20時 欧州序盤
    else if((h>=6&&h<8)||(h>=12&&h<15)) m[h]='avoid'; // 6-7,12-14時 回避帯
    else m[h]='normal';
  }
  return m;
})();
var EDGE_ZONE_LABEL={golden:'🟢ゴールデン',eu:'🟡欧州序盤',avoid:'🔴回避帯',normal:'⚪標準'};

/* Wilson score interval（95%）。
   (p + z²/2n ± z√((p(1−p) + z²/4n)/n)) / (1 + z²/n)
   例: 16勝/30回 → 下限 36.1% / 上限 69.6% */
function wilsonCI(wins,n,z){
  z=z||EDGE_Z;
  if(!n) return [0,1];
  var p=wins/n, z2=z*z, d=1+z2/n;
  var c=(p+z2/(2*n))/d;
  var m=(z*Math.sqrt((p*(1-p)+z2/(4*n))/n))/d;
  return [Math.max(0,c-m),Math.min(1,c+m)];
}
function _eStat(arr){
  var n=arr.length,w=arr.filter(function(a){return a.yen>0;}).length,
      net=arr.reduce(function(a,b){return a+b.yen;},0);
  var ci=wilsonCI(w,n);
  return {n:n,w:w,wr:n?w/n*100:0,net:net,lo:ci[0]*100,hi:ci[1]*100};
}
function buildEdgeProfile(){
  var t=loadTrades().filter(function(x){return !!x.opened_at;});
  var baseWr=t.length?t.filter(function(x){return x.yen>0;}).length/t.length*100:0;
  var ready=t.length>=EDGE_TOTAL_MIN;
  var grp=function(fn){var m={};t.forEach(function(x){var k=fn(x);
    if(k===null||k===undefined||k==='')return;(m[k]=m[k]||[]).push(x);});return m;};
  /* 補正の決定：件数条件を満たし、かつWilson区間がベース勝率を跨がない時だけ値を返す。
     跨ぐ（＝偶然の範囲）なら 0。大きさは信頼下限/上限とベースの差を2で割ってpt化し±EDGE_CAPで頭打ち。 */
  var adjOf=function(st){
    if(!ready||st.n<EDGE_MIN) return 0;
    if(st.lo>baseWr) return Math.min(EDGE_CAP,Math.max(1,Math.round((st.lo-baseWr)/2)));
    if(st.hi<baseWr) return Math.max(-EDGE_CAP,Math.min(-1,Math.round((st.hi-baseWr)/2)));
    return 0;
  };
  var out=function(m){var o={};Object.keys(m).forEach(function(k){var st=_eStat(m[k]);
    o[k]={n:st.n,wr:Math.round(st.wr),net:Math.round(st.net),
          lo:Math.round(st.lo*10)/10,hi:Math.round(st.hi*10)/10,adj:adjOf(st)};});return o;};
  var prof={
    v:7, updated:Date.now(), n:t.length, baseWr:Math.round(baseWr*10)/10,
    ready:ready, need:{total:EDGE_TOTAL_MIN,bucket:EDGE_MIN}, cap:EDGE_CAP,
    sessions:out(grp(function(x){return _tSess(_tHour(_tOpen(x)));})),
    zones:out(grp(function(x){var h=_tHour(_tOpen(x));return h==null?null:EDGE_ZONE_OF_HOUR[h];})),
    pairs:out(grp(function(x){return x.pair;})),
    // ★区分定義の唯一の出所。index.html はこの対応表を引くだけ（セッション判定を二重に持たない）
    sessOfHour:(function(){var a=[];for(var h=0;h<24;h++)a.push(_tSess(h));return a;})(),
    zoneOfHour:(function(){var a=[];for(var h=0;h<24;h++)a.push(EDGE_ZONE_OF_HOUR[h]);return a;})(),
    zoneLabel:EDGE_ZONE_LABEL
  };
  try{localStorage.setItem(EDGE_KEY,JSON.stringify(prof));}catch(e){}
  return prof;
}
function renderEdgeProfile(){
  var el=document.querySelector('#edgeprof tbody'); if(!el) return;
  var stEl=document.getElementById('edgestat');
  var prof=buildEdgeProfile();
  var rows=[];
  var ordS={'東京':1,'ロンドン':2,'ニューヨーク':3,'オセアニア':4};
  var ordZ={'golden':1,'eu':2,'normal':3,'avoid':4};
  var add=function(label,map,order,suffix,labeler){
    var keys=Object.keys(map||{});
    keys.sort(order?function(a,b){return (order[a]||99)-(order[b]||99);}:function(a,b){return String(a).localeCompare(String(b));});
    keys.forEach(function(k){var v=map[k];
      var nm=labeler?(labeler[k]||k):k;
      var short=v.n<EDGE_MIN;
      rows.push('<tr'+(v.adj?' style="background:#141b24"':'')+'><td>'+label+' '+nm+(suffix||'')
        +'</td><td>'+v.n+(short?'<span style="color:#8893a4">/'+EDGE_MIN+'</span>':'')+'</td><td>'+v.wr+'%'
        +'<span style="color:#8893a4;font-size:10px"> ('+v.lo+'〜'+v.hi+')</span></td>'
        +'<td class="'+(v.adj>0?'good':(v.adj<0?'warn':''))+'">'+(v.adj>0?'+':'')+(v.adj||0)+'</td></tr>');});
  };
  add('市場',prof.sessions,ordS,'',null);
  add('時間',prof.zones,ordZ,'',prof.zoneLabel);
  add('ペア',prof.pairs,null,'',null);
  el.innerHTML=rows.length?rows.join(''):'<tr><td colspan=4 style="text-align:center;color:#566">日時つきの記録がまだありません</td></tr>';
  if(stEl){
    var on=false; try{on=localStorage.getItem('fxnavi_edge_on')==='1';}catch(e){}
    var msg;
    if(!prof.ready){
      msg='<span class="warn">学習には全体'+EDGE_TOTAL_MIN+'件必要（現在'+prof.n+'件）</span>'
         +' ／ 各区分は'+EDGE_MIN+'件以上で有効。条件を満たすまで補正は 0 のままです。';
    }else{
      var live=[].concat(
        Object.keys(prof.sessions).map(function(k){return prof.sessions[k].adj;}),
        Object.keys(prof.zones).map(function(k){return prof.zones[k].adj;}),
        Object.keys(prof.pairs).map(function(k){return prof.pairs[k].adj;})
      ).filter(function(a){return a;}).length;
      msg='学習済み '+prof.n+'件 / 基準勝率 '+prof.baseWr+'% / 有効な補正 '+live+'区分（上限±'+EDGE_CAP+'pt）';
    }
    msg+='<br>括弧内は勝率の95%信頼区間。区間が基準勝率を跨ぐ区分は「偶然の範囲」とみなし補正しません。';
    msg+='<br>この補正は現在ダッシュボードで<b class="'+(on?'good':'warn')+'">'+(on?'使用中':'未使用（既定OFF）')+'</b>です。';
    stEl.innerHTML=msg;
  }
}
function clearEdgeProfile(){ try{localStorage.removeItem(EDGE_KEY);}catch(e){} renderEdgeProfile(); alert('学習データを消去しました'); }
/* ===== 勝ちパターン分析（時間帯・セッション・スコア帯） ===== */
function _grpRows(map){
  var keys=Object.keys(map);
  if(!keys.length) return '<tr><td colspan=4 style="text-align:center;color:#566">日時が未取込です。CSV取込で「決済日時の列」を選んで取り込むと集計されます</td></tr>';
  return keys.map(function(k){
    var arr=map[k], n=arr.length, w=arr.filter(function(a){return a.yen>0;}).length;
    var net=arr.reduce(function(a,b){return a+b.yen;},0);
    return '<tr><td>'+k+'</td><td>'+n+'</td><td>'+(n?(w/n*100).toFixed(0):0)+'%</td><td class="'+(net>=0?'good':'warn')+'">'+yen(net)+'</td></tr>';
  }).join('');
}
/* ===== 3-3 マーク別のミニグラフ（損益分岐勝率を基準線に） ===== */
function _breakEven(rows){
  // 損益分岐勝率 = 平均損失 ÷ (平均利益 + 平均損失)
  var w=rows.filter(function(x){return x.yen>0;});
  var l=rows.filter(function(x){return x.yen<0;});
  if(!w.length||!l.length) return null;
  var aw=w.reduce(function(a,b){return a+b.yen;},0)/w.length;
  var al=Math.abs(l.reduce(function(a,b){return a+b.yen;},0)/l.length);
  if(aw+al<=0) return null;
  return al/(aw+al)*100;
}
function _markBar(wr,be){
  var w=Math.max(0,Math.min(100,wr));
  var ok=(be!=null)&&(wr>=be);
  var line=(be!=null)
    ? '<i style="position:absolute;left:'+Math.max(0,Math.min(100,be)).toFixed(1)+'%;top:-2px;bottom:-2px;width:2px;background:var(--gold);box-shadow:0 0 4px var(--gold)"></i>'
    : '';
  return '<div style="position:relative;height:9px;border-radius:5px;background:#0c1118;border:1px solid var(--line);overflow:visible;margin-top:4px">'+
    '<span style="display:block;height:100%;width:'+w.toFixed(1)+'%;border-radius:5px;background:'+(ok?'var(--up)':'var(--down)')+';opacity:.85"></span>'+
    line+'</div>';
}
function renderEdgeTables(t){
  var byh={}, bys={}, bysc={};
  t.forEach(function(x){
    var od=_tOpen(x); var h=_tHour(od);
    if(h!=null){ var hk=(h<10?'0':'')+h+'時台'; (byh[hk]=byh[hk]||[]).push(x);
      var sk=_tSess(h); if(sk)(bys[sk]=bys[sk]||[]).push(x); }
    var sb=_scoreBand(x.score); if(sb)(bysc[sb]=bysc[sb]||[]).push(x);
  });
  var sorted={}; Object.keys(byh).sort().forEach(function(k){sorted[k]=byh[k];});
  var e1=document.querySelector('#byhour tbody'); if(e1) e1.innerHTML=_grpRows(sorted);
  var ordS={'東京':1,'ロンドン':2,'ニューヨーク':3,'オセアニア':4}, ss={};
  Object.keys(bys).sort(function(a,b){return (ordS[a]||9)-(ordS[b]||9);}).forEach(function(k){ss[k]=bys[k];});
  var e2=document.querySelector('#bysession tbody'); if(e2) e2.innerHTML=_grpRows(ss);
  var ordB={'80%以上':1,'65-79%':2,'50-64%':3,'35-49%':4,'35%未満':5}, sb2={};
  Object.keys(bysc).sort(function(a,b){return (ordB[a]||9)-(ordB[b]||9);}).forEach(function(k){sb2[k]=bysc[k];});
  var e3=document.querySelector('#byscore tbody');
  if(e3) e3.innerHTML=Object.keys(bysc).length?_grpRows(sb2)
    :'<tr><td colspan=4 style="text-align:center;color:#566">エントリー時のスコアは、ナビゲーターで建てた取引に記録されます（今後の取引から集計）</td></tr>';
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
function exportTradesCSV(){var t=loadTrades().slice().sort(function(a,b){return _tMs(a)-_tMs(b);});
  var rows=[['エントリー日時','決済日時','保有分','曜日(建)','時(建)','セッション(建)','ペア','売買','ロット',
             '建値','決済値','pips','損益円','決済理由','モード',
             'スコア','スコア帯','判定マーク','根拠数','シグナル','RSI','テク','ファンダ',
             'TP幅pips','SL幅pips','R倍率','記録マーク']];
  t.forEach(function(x){
    var od=_tOpen(x), hr=_tHour(od);
    var oa=x.opened_at||'', ca=x.closed_at||(x.ts?new Date(x.ts).toLocaleString('sv-SE',{timeZone:'Asia/Tokyo'}).slice(0,16)+' JST':'');
    var sl=(x.sl!=null&&x.sl!==''&&+x.sl>0)?+x.sl:'';
    var R=(sl!==''&&x.pips!==''&&x.pips!=null)?(Math.round((+x.pips/sl)*100)/100):'';
    rows.push([oa,ca,_tHold(x),_tWd(od),(hr==null?'':hr),_tSess(hr),
      x.pair,x.side||'',(x.lot||''),(x.entry||''),(x.exit||''),(x.pips!=null?x.pips:''),x.yen,
      (x.reason||''),(x.mode||''),(x.score!=null?x.score:''),_scoreBand(x.score),(x.smark||''),
      (x.got!=null?x.got:''),(x.signal||''),(x.rsi!=null?x.rsi:''),(x.tech!=null?x.tech:''),(x.fund!=null?x.fund:''),
      (x.tp!=null?x.tp:''),(sl===''?'':sl),R,(x.mark||'')]);
  });
  var body='\ufeff'+rows.map(function(r){return r.map(function(c){c=(c==null?'':''+c);return /[",\n]/.test(c)?'"'+c.replace(/"/g,'""')+'"':c;}).join(',');}).join('\r\n');
  _toolsCsvDist(new Blob([body],{type:'text/csv;charset=utf-8'}),'fxnavi-trades.csv');}
/* ===== 3-C: 全データのバックアップ / 復元 =====
   端末のlocalStorageに入っている記録は、Safariのデータ消去やPWA再インストールで消える。
   書き出しは既存CSVと同じ同期3段方式（_toolsCsvDist）を使う（非同期を挟むとiOSでブロックされるため）。 */
var BACKUP_KEYS=['fxnavi_trades','fxnavi_edge','fxnavi_edge_on','fxnavi_forward',
                 'fxnavi_sec_open','fxnavi_risk','fxnavi_bthist','fxnavi_snap'];
function exportBackup(){
  var data={_type:'fxnavi-backup',_v:7,_at:new Date().toISOString(),store:{}};
  BACKUP_KEYS.forEach(function(k){ try{ var v=localStorage.getItem(k); if(v!=null) data.store[k]=v; }catch(e){} });
  var n=0; try{ n=(JSON.parse(data.store['fxnavi_trades']||'[]')||[]).length; }catch(e){}
  var name='fxnavi-backup-'+new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Tokyo'})+'.json';
  _toolsCsvDist(new Blob([JSON.stringify(data)],{type:'application/json'}),name);
  var m=document.getElementById('impmsg'); if(m) m.textContent='バックアップを書き出しました（トレード'+n+'件・'+Object.keys(data.store).length+'項目）';
}
/* 復元は「置き換え」ではなく既存データとのマージ。
   トレードは決済ID（無ければ 日時+ペア+損益）で重複排除する。 */
function _tradeKey(x){ return String(x.id||x.deal_id||((x.closed_at||x.ts||'')+'|'+(x.pair||'')+'|'+(x.yen||'')+'|'+(x.entry||''))); }
function importBackup(){
  var ta=document.getElementById('bkpaste'); if(!ta) return;
  var raw=(ta.value||'').trim();
  var m=document.getElementById('impmsg');
  if(!raw){ if(m)m.textContent='復元するJSONを貼り付けてください'; return; }
  var d; try{ d=JSON.parse(raw); }catch(e){ if(m)m.innerHTML='<span class="warn">JSONとして読めませんでした</span>'; return; }
  if(!d||!d.store){ if(m)m.innerHTML='<span class="warn">バックアップ形式ではありません</span>'; return; }
  var added=0, dup=0, keys=0;
  // トレードはマージ（重複排除）
  try{
    var cur=loadTrades()||[], seen={}; cur.forEach(function(x){ seen[_tradeKey(x)]=1; });
    var inc=JSON.parse(d.store['fxnavi_trades']||'[]')||[];
    inc.forEach(function(x){ var k=_tradeKey(x); if(seen[k]){dup++;return;} seen[k]=1; cur.push(x); added++; });
    saveTrades(cur);
  }catch(e){}
  // トレード以外は、現在が空の項目だけ復元（既存設定を壊さない）
  BACKUP_KEYS.forEach(function(k){
    if(k==='fxnavi_trades') return;
    try{ if(d.store[k]!=null && localStorage.getItem(k)==null){ localStorage.setItem(k,d.store[k]); keys++; } }catch(e){}
  });
  if(m) m.innerHTML='復元しました：トレード <b>'+added+'件を追加</b>／'+dup+'件は重複のためスキップ／設定'+keys+'項目を復元';
  try{ renderJournal(); }catch(e){}
  try{ renderEdgeProfile(); }catch(e){}
}
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
/* ===== 3-2 バックテスト履歴（直近5件） ===== */
var BT_HIST_KEY='fxnavi_bt_hist';
function _btHistLoad(){ try{ return JSON.parse(localStorage.getItem(BT_HIST_KEY)||'[]'); }catch(e){ return []; } }
function _btHistPush(rec){
  try{
    var h=_btHistLoad();
    h.unshift(rec);
    h=h.slice(0,5);
    localStorage.setItem(BT_HIST_KEY,JSON.stringify(h));
  }catch(e){}
  _btHistRender(rec);
}
function _btArrow(d,unit,digits){
  if(d===null||d===undefined||isNaN(d)) return '';
  var v=(digits!=null)?(+d).toFixed(digits):Math.round(d);
  if(Math.abs(+v)<1e-9) return '<span style="color:var(--mut)">±0'+(unit||'')+'</span>';
  var up=+v>0;
  return '<span class="'+(up?'good':'warn')+'">'+(up?'▲+':'▼')+v+(unit||'')+'</span>';
}
function _btHistRender(cur){
  var el=document.getElementById('bthist'); if(!el) return;
  var h=_btHistLoad();
  if(!h.length){ el.innerHTML=''; return; }
  var now=cur||h[0];
  // 同じ条件（ペア・モード）の1つ前を探す
  var prev=null;
  for(var i=1;i<h.length;i++){
    if(h[i].sym===now.sym && h[i].mode===now.mode){ prev=h[i]; break; }
  }
  var cmp='';
  if(prev){
    cmp='<div style="margin-top:5px">前回（'+_btWhen(prev.ts)+'）との比較：'+
      '勝率 '+_btArrow(now.wr-prev.wr,'%',1)+'　'+
      '純pips '+_btArrow(now.pips-prev.pips,'p',1)+'</div>';
  }else{
    cmp='<div style="margin-top:5px;color:var(--mut)">同じ条件の前回結果がまだありません</div>';
  }
  var list=h.slice(0,5).map(function(x){
    return '<div style="display:flex;gap:8px;padding:2px 0;border-top:1px solid var(--line)">'+
      '<span>'+_btWhen(x.ts)+'</span>'+
      '<span>'+x.sym+' / '+x.mode+'</span>'+
      '<span style="margin-left:auto">勝率'+(+x.wr).toFixed(1)+'% ／ '+(+x.pips).toFixed(1)+'p</span></div>';
  }).join('');
  el.innerHTML='<div style="margin-top:8px"><b>バックテスト履歴</b>'+cmp+
    '<div style="margin-top:6px">'+list+'</div></div>';
}
function _btWhen(ts){
  try{ return new Date(ts).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}); }
  catch(e){ return ''; }
}
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
    // ④-2b: 履歴に残して前回と比べる
    try{
      var _base=(onlyGreen&&sel)?sel:all;
      if(_base&&_base.n){
        _btHistPush({ts:Date.now(),sym:sym,mode:mode,
          wr:+_base.wr, pips:+(_base.net!=null?_base.net:(_base.sum!=null?_base.sum:0)), n:_base.n});
      }
    }catch(e){}
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
try{ setTimeout(function(){ autoSyncCheck(); }, 400); }catch(e){}
try{ _btHistRender(); }catch(e){}
