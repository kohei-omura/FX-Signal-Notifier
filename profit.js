/* ===== ⓪ 利益を伸ばす =====
   手取りを増やす方法は3つしかない。
     1) 期待値がマイナスの場所で戦わない（どこで戦うか）
     2) 自分の負けの中から、やめれば済むものをやめる（何をやめるか）
     3) 期待値に見合った大きさで張る（いくら張るか）
   それぞれを、1年検証(backtest.json)とあなたの実績(fxnavi_trades)から出す。
   tools.js の関数（loadTrades, _tradeR, _chron, _tOpen, _tJst, _tHour, escHtml,
   COVERED_PAIRS, MODE_SRC_OK, MODE_LABEL_T, MODE_TRADES_PER_MONTH, BT_CACHE）を使う。 */
var PF_MODES=['mtf','swing','day','scalp'];
var PF_SYMS=['USD_JPY','EUR_JPY','GBP_JPY','AUD_JPY'];
function _pf(v,d){ return (v>=0?'+':'')+(+v).toFixed(d==null?3:d); }
/* 95%区間で3つに分ける。区間が0をまたぐ間は、プラスでもマイナスでも「まだ分からない」。 */
function pfVerdict(a){
  if(!a||!a.n||a.ci_lo==null||a.ci_hi==null) return null;
  if(a.ci_hi<0) return 'bad';
  if(a.ci_lo>0) return 'good';
  return 'und';
}
function _pfAdvice(bt,mode,sym){
  try{ var v=bt.modes[mode]; var p=sym?v.symbols[sym].policies:v.policies; return (p&&p.advice&&p.advice.n)?p.advice:null; }
  catch(e){ return null; }
}
/* 取引のモード。出所が確かなもの（建玉ごとの保存・TP/SL幅からの割り出し）だけを信じる。 */
function _pfMode(x){ return (x&&x.mode&&MODE_SRC_OK[x.modeSrc])?x.mode:''; }

/* 絞り込みの裏付け。前半・後半の両方で、絞らない場合より良く、かつプラスのものだけ。
   片方だけなら偶然の可能性があるので出さない。 */
function pfFilterEvidence(bt,mode){
  var out=[];
  try{
    var fh=bt.modes[mode].filter_holdout, a=fh.first_half, b=fh.second_half;
    var base1=a['絞り込みなし'], base2=b['絞り込みなし'];
    Object.keys(a).forEach(function(k){
      if(k==='絞り込みなし'||!b[k]) return;
      var x=a[k], y=b[k];
      if(x.n<30||y.n<30) return;
      if(x.avg_r>0&&y.avg_r>0&&x.avg_r>base1.avg_r&&y.avg_r>base2.avg_r)
        out.push({name:k, h1:x, h2:y, b1:base1, b2:base2});
    });
  }catch(e){}
  return out;
}

function renderWhereToFight(bt){
  var el=document.getElementById('wherefight'); if(!el) return;
  bt=bt||BT_CACHE;
  if(!bt||!bt.modes){ el.innerHTML='<div class="note">1年検証(backtest.json)を読み込み中です。出ない時は「🧠 記録簿を取込」を押してください。</div>'; return; }
  var icon={bad:'⛔',und:'',good:'✅'};
  var cell=function(a,bold){
    if(!a) return '<td style="opacity:.5">—</td>';
    var v=pfVerdict(a);
    return '<td class="'+(v==='bad'?'bad':(v==='good'?'good':''))+'" style="font-size:11px'+(bold?';font-weight:700':'')+'">'
      +(icon[v]||'')+_pf(a.avg_r)+'<br><span style="opacity:.6">n='+a.n+'</span></td>';
  };
  var rows=PF_MODES.map(function(m){
    if(!bt.modes[m]) return '';
    return '<tr><td>'+(MODE_LABEL_T[m]||m)+'</td>'+PF_SYMS.map(function(s){return cell(_pfAdvice(bt,m,s));}).join('')
      +cell(_pfAdvice(bt,m,null),true)+'</tr>';
  }).join('');
  // あなたの取引がどのモードに寄っているか
  var t=loadTrades(), cnt={}, known=0;
  t.forEach(function(x){ var m=_pfMode(x); if(m){ cnt[m]=(cnt[m]||0)+1; known++; } });
  var lines=[];
  PF_MODES.forEach(function(m){
    var a=_pfAdvice(bt,m,null); if(!a) return;
    var v=pfVerdict(a), share=known?Math.round((cnt[m]||0)/known*100):null;
    var you=(share!=null&&cnt[m])?'（あなたの取引の<b>'+share+'%</b>）':'';
    if(v==='bad') lines.push('⛔ <b>'+MODE_LABEL_T[m]+'</b>は1年通して1回あたり<b>'+_pf(a.avg_r)+'R</b>。'
      +'95%区間('+_pf(a.ci_lo)+'〜'+_pf(a.ci_hi)+')が0未満で、負けは偶然ではありません。続けるほど減ります'+you+'。');
    else if(v==='good') lines.push('✅ <b>'+MODE_LABEL_T[m]+'</b>は'+_pf(a.avg_r)+'Rで、95%区間も0より上です'+you+'。');
    else lines.push('△ <b>'+MODE_LABEL_T[m]+'</b>は'+_pf(a.avg_r)+'R。区間('+_pf(a.ci_lo)+'〜'+_pf(a.ci_hi)
      +')が0をまたぐので、プラスともマイナスともまだ言えません'+you+'。');
  });
  PF_MODES.forEach(function(m){
    pfFilterEvidence(bt,m).forEach(function(f){
      lines.push('💡 <b>'+MODE_LABEL_T[m]+'</b>は「<b>'+escHtml(f.name)+'</b>」に絞ると、前半 <b class="good">'+_pf(f.h1.avg_r)
        +'R</b>・後半 <b class="good">'+_pf(f.h2.avg_r)+'R</b>（絞らないと '+_pf(f.b1.avg_r)+'R／'+_pf(f.b2.avg_r)+'R）。'
        +'前半・後半の両方で良くなった絞り込みです（件数 '+f.h1.n+'／'+f.h2.n+'・区間はまだ0をまたぎます）。');
    });
  });
  el.innerHTML='<table><thead><tr><th>モード</th>'+PF_SYMS.map(function(s){return '<th>'+s.replace('_JPY','')+'</th>';}).join('')
    +'<th>全体</th></tr></thead><tbody>'+rows+'</tbody></table>'
    +'<div style="margin-top:10px;padding:9px 12px;border:1px solid var(--line);border-radius:9px;font-size:12.5px;line-height:1.85">'
    +lines.join('<br>')+'</div>'
    +'<div class="note">1回あたりの期待R（🎯推奨で決済・スプレッド控除後・'+escHtml(bt.generated_at||'')+' 時点の1年ぶん）。'
    +'⛔＝95%区間が0未満（負けが確定的）／✅＝0より上。印の無いものは区間が0をまたぐ「まだ分からない」です。<br>'
    +'通貨ごとの差の多くは偶然の範囲です。<b>良かった通貨だけを選ぶのは過去に合わせるだけ</b>なので、避ける根拠になるのは⛔だけです。</div>';
}

/* ===== 何をやめるか（あなたの実績で、ルールを守っていたらどうだったか） =====
   ルールは先に決めた一般的なもの（ここで何通りも試して良かったものを選ぶと、過去に合わせるだけになる）。
   お金の差は【円】で出す（実際に減った・増えたのは円なので）。 */
var PF_ROLLOVER_H=[6,7];     // 早朝のロールオーバー。スプレッドが普段の10倍前後に開く
var PF_TILT_LOSSES=2;        // 同じ日に何連敗したらその日をやめるか
var PF_MAX_PER_DAY=5;        // 1日に何回まで
function _pfDay(d){ return d?new Date(d.getTime()+9*3600e3).toISOString().slice(0,10):''; }
function _pfOpenKnown(x){ return x.openSrc==='gmo'||x.elSrc==='entrylog'||(x.opened_at&&x.closed_at&&x.opened_at!==x.closed_at); }
/* 取引ごとに「その日何回目か」「その日の直前が何連敗だったか」を付ける。
   連敗は、この取引を建てる前に決済が済んでいたものだけで数える（後から知った結果は使わない）。 */
function pfBehaviorFlags(t){
  var rows=t.map(function(x,i){
    var o=_tOpen(x)||_tJst(x.closed_at||''), c=_tJst(x.closed_at||'')||o;
    return {i:i, x:x, o:o?o.getTime():(x.ts||0), c:c?c.getTime():(x.ts||0), day:_pfDay(o||(x.ts?new Date(x.ts):null))};
  }).sort(function(a,b){return a.o-b.o;});
  var byDay={}, flags=t.map(function(){return {nth:0, streak:0};});
  rows.forEach(function(r){
    var prev=byDay[r.day]||(byDay[r.day]=[]);
    flags[r.i].nth=prev.length+1;
    var done=prev.filter(function(p){return p.c<=r.o;}).sort(function(a,b){return a.c-b.c;});
    var s=0; for(var k=done.length-1;k>=0;k--){ if((+done[k].x.yen||0)<0) s++; else break; }
    flags[r.i].streak=s;
    prev.push(r);
  });
  return flags;
}
function pfRules(){
  return [
    {id:'offpair', label:'アプリが計算していないペアをやめる',
     test:function(x){ return !!x.pair&&COVERED_PAIRS.indexOf(x.pair)<0; }, basis:'判定材料が一切無い通貨'},
    {id:'scalp', label:'スキャルをやめる', test:function(x){ return _pfMode(x)==='scalp'; }, bt:'scalp'},
    {id:'daylowadx', label:'デイでADX40未満は見送る',
     test:function(x){ return _pfMode(x)==='day'&&x.adx!=null&&x.adx!==''&&+x.adx<40; }, bt:'dayadx'},
    {id:'counter', label:'上位足と逆行する取引をやめる', test:function(x){ return /逆行/.test(String(x.mtf||'')); }},
    {id:'rollover', label:'早朝6〜8時（ロールオーバー）に建てない',
     test:function(x){ var h=_tHour(_tOpen(x)); return _pfOpenKnown(x)&&h!=null&&PF_ROLLOVER_H.indexOf(h)>=0; },
     basis:'スプレッドが普段の10倍前後に開く時間帯'},
    {id:'news', label:'重要指標の前後に建てない',
     test:function(x){ return x.newsNear===true||x.newsNear===1||x.newsNear==='true'; }},
    {id:'tilt', label:'同じ日に'+PF_TILT_LOSSES+'連敗したら、その日はやめる',
     test:function(x,f){ return f.streak>=PF_TILT_LOSSES; }},
    {id:'over', label:'1日'+(PF_MAX_PER_DAY+1)+'回目以降はやめる',
     test:function(x,f){ return f.nth>PF_MAX_PER_DAY; }}
  ];
}
function _pfMeanCI(vals){
  var n=vals.length; if(!n) return null;
  var m=vals.reduce(function(a,b){return a+b;},0)/n;
  var sd=n>1?Math.sqrt(vals.reduce(function(a,v){return a+(v-m)*(v-m);},0)/(n-1)):0;
  var se=n>1?sd/Math.sqrt(n):Infinity;
  return {n:n, mean:m, lo:m-1.96*se, hi:m+1.96*se};
}
/* 1年検証での裏付け（あれば）。 */
function _pfRuleBacking(r,bt){
  if(!bt||!bt.modes) return null;
  if(r.bt==='scalp'){
    var a=_pfAdvice(bt,'scalp',null); if(!a) return null;
    return {ok:pfVerdict(a)==='bad', text:'1年検証 '+_pf(a.avg_r)+'R（区間 '+_pf(a.ci_lo)+'〜'+_pf(a.ci_hi)+'）'};
  }
  if(r.bt==='dayadx'){
    var ev=pfFilterEvidence(bt,'day').filter(function(f){return f.name==='ADX40以上';})[0];
    if(!ev) return null;
    return {ok:true, text:'1年検証 ADX40以上 前半'+_pf(ev.h1.avg_r)+'R・後半'+_pf(ev.h2.avg_r)
      +'R ⇔ 絞らない '+_pf(ev.b1.avg_r)+'R・'+_pf(ev.b2.avg_r)+'R'};
  }
  return null;
}
function pfRuleWhatIf(t,bt){
  var flags=pfBehaviorFlags(t);
  var total=t.reduce(function(a,x){return a+(+x.yen||0);},0);
  var res=pfRules().map(function(r){
    var hit=[]; t.forEach(function(x,i){ if(r.test(x,flags[i])) hit.push(i); });
    var ys=hit.map(function(i){return +t[i].yen||0;});
    var ci=_pfMeanCI(ys);
    var rs=hit.map(function(i){return _tradeR(t[i]);}).filter(function(v){return v!=null&&isFinite(v);});
    var net=ys.reduce(function(a,b){return a+b;},0);
    var back=_pfRuleBacking(r,bt);
    var verdict=!hit.length?'none':(hit.length<5?'few':(net>=0?'keep':(ci&&ci.hi<0?'stop':'maybe')));
    return {id:r.id, label:r.label, basis:r.basis||'', idx:hit, n:hit.length, net:net, ci:ci,
            wins:ys.filter(function(v){return v>0;}).length,
            avgR:rs.length>=Math.max(5,hit.length*0.6)?rs.reduce(function(a,b){return a+b;},0)/rs.length:null,
            back:back, verdict:verdict,
            // 採用してよいか：あなたの実績でマイナス、かつ（偶然でない or 1年検証の裏付け or 構造的に根拠がある）
            adopt:(net<0&&hit.length>0&&(verdict==='stop'||(back&&back.ok)||r.id==='offpair'))};
  });
  var drop={}; res.forEach(function(r){ if(r.adopt) r.idx.forEach(function(i){drop[i]=1;}); });
  var kept=t.filter(function(x,i){return !drop[i];});
  return {rules:res, total:total, n:t.length,
          after:kept.reduce(function(a,x){return a+(+x.yen||0);},0), dropped:Object.keys(drop).length};
}
function renderRuleWhatIf(){
  var el=document.getElementById('whatif'); if(!el) return;
  var t=loadTrades();
  if(t.length<10){ el.innerHTML='<div class="note">取引が10件以上たまると出ます（現在'+t.length+'件）。GMOの約定履歴CSVを取り込んでください。</div>'; return; }
  var w=pfRuleWhatIf(t,BT_CACHE);
  var yen=function(v){return (v>=0?'+':'')+Math.round(v).toLocaleString()+'円';};
  var vlab={stop:'✅ 負けは偶然ではない', maybe:'△ マイナスだが偶然の範囲', keep:'— やめると勝ち分を捨てる', few:'件数不足', none:'該当なし'};
  var rows=w.rules.slice().sort(function(a,b){return a.net-b.net;}).map(function(r){
    return '<tr'+(r.adopt?' style="background:#141b24"':'')+'><td style="text-align:left">'+(r.adopt?'★ ':'')+escHtml(r.label)
      +(r.basis?'<br><span style="font-size:10px;opacity:.7">'+escHtml(r.basis)+'</span>':'')
      +(r.back?'<br><span style="font-size:10px;opacity:.7">'+r.back.text+'</span>':'')+'</td>'
      +'<td>'+r.n+'</td><td>'+(r.n?Math.round(r.wins/r.n*100)+'%':'—')+'</td>'
      +'<td class="'+(r.net<0?'bad':(r.net>0?'good':''))+'">'+(r.n?yen(r.net):'—')
      +(r.avgR!=null?'<br><span style="font-size:10px;opacity:.75">'+_pf(r.avgR,2)+'R/回</span>':'')+'</td>'
      +'<td style="font-size:11px">'+vlab[r.verdict]+'</td></tr>';
  }).join('');
  var gain=w.after-w.total;
  var head=w.dropped
    ? '<div style="padding:10px 12px;border:1px solid '+(gain>0?'var(--up)':'var(--line)')+';border-radius:9px;font-size:12.5px;line-height:1.85;margin-bottom:8px">'
      +'★のルールだけ守っていたら：純損益 <b>'+yen(w.total)+'</b> → <b class="'+(w.after>=0?'good':'bad')+'">'+yen(w.after)+'</b>'
      +'（<b class="'+(gain>=0?'good':'bad')+'">'+yen(gain)+'</b>・'+w.dropped+'件を見送り）<br>'
      +'<span style="font-size:11px;opacity:.8">★＝あなたの実績でマイナス、かつ「負けが偶然でない」か「1年検証の裏付けがある」か「判定材料が無い通貨」のもの。'
      +'あなたの結果を見て選んだぶん実際より良く見えます。1年検証の裏付けがあるものほど、この先も効きやすいルールです。</span></div>'
    : '<div class="note">いまの実績では、やめて確実に得をするルールは見つかりませんでした。</div>';
  el.innerHTML=head+'<table><thead><tr><th>ルール</th><th>該当</th><th>勝率</th><th>その取引の損益</th><th>読み方</th></tr></thead>'
    +'<tbody>'+rows+'</tbody></table>'
    +'<div class="note">「その取引の損益」がマイナスなら、そのルールを守っていればその分だけ減らずに済んだ、という意味です。'
    +'判定は損益の平均の95%区間で出しています（件数が少ないと区間が広がり、ほとんどが「偶然の範囲」になります）。<br>'
    +'ADX・上位足・指標の3つは記録簿（アプリで建てた取引）にしか残りません。「🧠 記録簿を取込」で埋まります。'
    +'時間帯と連敗・回数は、建てた時刻が分かる取引だけで数えています。</div>';
}

/* ===== いくら張るか（資金シミュレーション） =====
   1回ごとに「資金 × リスク% × その回のR」だけ増減させ、これを何千通りも繰り返す。
   期待値がマイナスなら、リスクを上げるほど早く減る。プラスでも張りすぎると
   途中の落ち込みで退場する。その両方を、同じ土台で数字にする。 */
function pfRng(seed){ var a=seed>>>0; return function(){ a|=0; a=a+0x6D2B79F5|0; var t=Math.imul(a^a>>>15,1|a);
  t=t+Math.imul(t^t>>>7,61|t)^t; return ((t^t>>>14)>>>0)/4294967296; }; }
/* 1年検証の集計（勝率・平均R・コスト）から、平均と勝率が一致する2点分布を作る。
   負けはSL（−1R）＋往復コスト。1回ごとの結果は手元に無いので近似。 */
function pfTwoPoint(a){
  if(!a||!a.n) return null;
  var p=a.winrate/100, loss=-(1+(a.cost_r||0));
  if(!(p>0&&p<1)) return null;
  var win=(a.avg_r-(1-p)*loss)/p;
  return {p:p, win:win, loss:loss, draw:function(u){ return u<p?win:loss; }};
}
function pfOwnSampler(){
  var rs=loadTrades().map(_tradeR).filter(function(v){return v!=null&&isFinite(v);})
    .map(function(v){ return Math.max(-3,Math.min(6,v)); });   // SL幅の代用値による極端な値を丸める
  if(rs.length<30) return {n:rs.length};
  var m=rs.reduce(function(a,b){return a+b;},0)/rs.length;
  var sd=Math.sqrt(rs.reduce(function(a,v){return a+(v-m)*(v-m);},0)/(rs.length-1)), se=sd/Math.sqrt(rs.length);
  return {n:rs.length, mean:m, lo:m-1.96*se, hi:m+1.96*se,
          draw:function(u){ return rs[Math.min(rs.length-1,Math.floor(u*rs.length))]; }};
}
function pfSimulate(draw, riskPct, trades, paths, seed){
  var rnd=pfRng(seed||20260925), fin=[], dd30=0, half=0, loss=0;
  for(var k=0;k<paths;k++){
    var c=1, peak=1, mdd=0;
    for(var i=0;i<trades;i++){
      c*=1+draw(rnd())*riskPct/100; if(c<=0){ c=0; mdd=1; break; }
      if(c>peak) peak=c; else { var d=1-c/peak; if(d>mdd) mdd=d; }
    }
    fin.push(c); if(mdd>=0.3) dd30++; if(c<=0.5) half++; if(c<1) loss++;
  }
  fin.sort(function(a,b){return a-b;});
  var q=function(p){ return fin[Math.min(fin.length-1,Math.floor(p*fin.length))]; };
  return {p10:q(0.1), p50:q(0.5), p90:q(0.9), pLoss:loss/paths, pDD30:dd30/paths, pHalf:half/paths};
}
function pfDefaultFreq(src){
  if(src!=='own') return MODE_TRADES_PER_MONTH[src.slice(3)]||20;
  var t=loadTrades(), now=Date.now(), n=t.filter(function(x){ var d=_tJst(x.closed_at||''); var ms=d?d.getTime():(x.ts||0); return now-ms<=60*86400e3; }).length;
  // 直近60日に取引が無ければ（久しぶりに開いた等）、上位足フォローの実測頻度を仮に置く
  return n?Math.max(1,Math.round(n/2)):(MODE_TRADES_PER_MONTH.mtf||15);
}
function pfSrcChanged(){ var s=document.getElementById('mcsrc'), f=document.getElementById('mcfreq'); if(s&&f) f.value=pfDefaultFreq(s.value); }
function runMonteCarlo(){
  var el=document.getElementById('mcout'); if(!el) return;
  var src=($('#mcsrc')||{}).value||'own', risk=parseFloat(($('#mcrisk')||{}).value)||1;
  var freq=parseInt(($('#mcfreq')||{}).value,10)||pfDefaultFreq(src), mon=parseInt(($('#mcmon')||{}).value,10)||12;
  var cap=parseFloat(($('#cap')||{}).value)||10000;
  var trades=Math.min(5000,freq*mon), draw, desc, und=false, slTyp=null;
  if(src==='own'){
    var o=pfOwnSampler();
    if(!o.draw){ el.innerHTML='<div class="note warn">R（損益÷SL幅）が分かる取引が30件以上必要です（現在'+o.n+'件）。1年検証を選ぶか、記録簿を取り込んでください。</div>'; return; }
    draw=o.draw; desc='あなたの実績 '+o.n+'件（平均 '+_pf(o.mean,3)+'R）から無作為に引き直し';
    und=(o.lo<0&&o.hi>0);
    var sls=loadTrades().map(_slOf).filter(function(v){return v!=null;}).sort(function(a,b){return a-b;});
    if(sls.length) slTyp=sls[Math.floor(sls.length/2)];
  }else{
    var a=_pfAdvice(BT_CACHE,src.slice(3),null), tp=pfTwoPoint(a);
    if(!tp){ el.innerHTML='<div class="note warn">1年検証がまだ読み込まれていません。「🧠 記録簿を取込」を押してください。</div>'; return; }
    draw=tp.draw; desc=(MODE_LABEL_T[src.slice(3)]||src)+'の1年検証（勝率'+Math.round(tp.p*100)+'%・平均 '+_pf(a.avg_r)+'R）を近似';
    und=(pfVerdict(a)==='und');
    var sv=PF_SYMS.map(function(sy){return slFallback(src.slice(3),sy);}).filter(function(v){return v>0;});
    if(sv.length) slTyp=sv.reduce(function(a2,b2){return a2+b2;},0)/sv.length;
  }
  var PATHS=2000, r=pfSimulate(draw,risk,trades,PATHS);
  var yen=function(v){ return Math.round(v*cap).toLocaleString()+'円'; };
  // 1,000通貨（最小単位）で張った時のリスク%。これより小さいリスクは実際には選べない。
  var minRisk=slTyp?LOT_STEP*slTyp*PS/cap*100:0;
  var levels=[0.5,1,2,3,5];
  if(minRisk>0.5&&minRisk<5){ var mr=Math.ceil(minRisk*10)/10; if(levels.indexOf(mr)<0) levels.push(mr); minRisk=mr; }
  levels.sort(function(a,b){return a-b;});
  var scan=levels.map(function(rp){ return {rp:rp, s:pfSimulate(draw,rp,trades,PATHS)}; });
  var ok=scan.filter(function(x){return x.s.pDD30<0.1&&x.rp>=minRisk;});
  var best=ok.length?ok.reduce(function(a,b){return b.s.p50>a.s.p50?b:a;}):null;
  var noEdge=scan.every(function(x){return x.s.p50<1;});
  el.innerHTML='<div class="stat">'
    +'<div class="kpi"><div class="l">'+mon+'か月後（中央値）</div><div class="v '+(r.p50>=1?'up':'dn')+'">'+yen(r.p50)+'</div></div>'
    +'<div class="kpi"><div class="l">悪い方10%</div><div class="v dn">'+yen(r.p10)+'</div></div>'
    +'<div class="kpi"><div class="l">良い方10%</div><div class="v up">'+yen(r.p90)+'</div></div>'
    +'<div class="kpi"><div class="l">元本割れの確率</div><div class="v '+(r.pLoss>0.5?'dn':'')+'">'+Math.round(r.pLoss*100)+'%</div></div>'
    +'<div class="kpi"><div class="l">途中で−30%以上</div><div class="v '+(r.pDD30>0.1?'dn':'')+'">'+Math.round(r.pDD30*100)+'%</div></div>'
    +'<div class="kpi"><div class="l">資金が半分以下</div><div class="v '+(r.pHalf>0.05?'dn':'')+'">'+Math.round(r.pHalf*100)+'%</div></div></div>'
    +'<table style="margin-top:10px"><thead><tr><th>1回のリスク</th><th>中央値</th><th>元本割れ</th><th>−30%以上</th></tr></thead><tbody>'
    +scan.map(function(x){ var no=x.rp<minRisk; return '<tr'+(best&&x.rp===best.rp?' style="font-weight:800"':(no?' style="opacity:.45"':''))+'><td>'+x.rp+'%'+(best&&x.rp===best.rp?' ★':'')+(no?'<br><span style="font-size:10px">1,000通貨では不可</span>':'')+'</td>'
      +'<td class="'+(x.s.p50>=1?'good':'bad')+'">'+yen(x.s.p50)+'</td><td>'+Math.round(x.s.pLoss*100)+'%</td>'
      +'<td class="'+(x.s.pDD30>0.1?'bad':'')+'">'+Math.round(x.s.pDD30*100)+'%</td></tr>'; }).join('')
    +'</tbody></table>'
    +'<div style="margin-top:8px;padding:9px 12px;border:1px solid '+(noEdge?'var(--down)':'var(--line)')+';border-radius:9px;font-size:12.5px;line-height:1.8">'
    +(noEdge?'⛔ <b>どのリスク%でも中央値が元本を下回ります。</b>期待値がマイナスなので、張るほど早く減ります。'
       +'張り方では直りません。上の「どこで戦うか」「何をやめるか」を先に直してください。'
      :(best?'★ 途中で−30%以上になる確率を10%未満に抑えたうえで、中央値が一番大きいのは <b>1回'+best.rp+'%</b> です。'
             +'これより上げても、増え方より途中の落ち込みの方が大きくなります。'
            :(minRisk>0.5?'⚠️ いまの資金では、最小の1,000通貨でも途中で−30%以上になる確率が10%を超えます。資金を増やすか、SL幅の狭いモードでないと安全な大きさで張れません。'
                         :'⚠️ 0.5%でも途中で−30%以上になる確率が10%を超えます。期待値に対して値動きの振れが大きすぎます。')))
    +'</div>'
    +(und&&!noEdge?'<div style="margin-top:8px;padding:9px 12px;border:1px solid var(--down);border-radius:9px;font-size:12.5px;line-height:1.8">'
       +'⚠️ この計算は<b>平均が本当の実力だと仮定</b>しています。元の成績の95%区間は0をまたいでいるので、'
       +'実際の期待値はマイナスの可能性もあります。その場合は上の表の「元本割れ」より悪くなります。</div>':'')
    +(slTyp?(function(){
       return minRisk>risk?'<div style="margin-top:8px;padding:9px 12px;border:1px solid var(--down);border-radius:9px;font-size:12.5px;line-height:1.8">'
         +'⚠️ <b>最小の1,000通貨でも、1回の損失は約'+Math.round(LOT_STEP*slTyp*PS).toLocaleString()+'円</b>（SL幅 約'+slTyp.toFixed(1)+'pips）＝資金の<b>'+minRisk.toFixed(1)+'%</b>です。'
         +'資金'+cap.toLocaleString()+'円では、'+risk+'%で張ることはできません（実際は'+minRisk.toFixed(1)+'%になります）。'
         +'リスクを'+risk+'%に収めるには資金が約'+Math.ceil(LOT_STEP*slTyp*PS/(risk/100)/1000)*1000+'円以上必要です。</div>':'';
     })():'')
    +'<div class="note">'+escHtml(desc)+'・月'+freq+'回×'+mon+'か月＝'+trades+'回を'+PATHS+'通り。資金 '+cap.toLocaleString()+'円（②の値）。'
    +'1回の損益＝資金×リスク%×R で複利計算。1,000通貨単位への丸めとスプレッド拡大時の追加コストは含みません。'
    +'<br>同じ時間帯に同じ方向で複数持つと、実際の振れはこれより大きくなります（4通貨とも対円なので一斉に動く）。</div>';
}
function renderProfitPanels(){
  try{ renderWhereToFight(BT_CACHE); }catch(e){ console.warn(e); }
  try{ renderRuleWhatIf(); }catch(e){ console.warn(e); }
  try{ var f=document.getElementById('mcfreq'); if(f&&!f.value) pfSrcChanged(); }catch(e){}
  try{ var rk=document.getElementById('mcrisk'), r0=document.getElementById('rpct'); if(rk&&!rk.value&&r0) rk.value=r0.value; }catch(e){}
}
try{ renderProfitPanels(); }catch(e){}
