#pragma once
// the whole website lives in this one string, so the board can serve it with no internet
const char PAGE_HTML[] PROGMEM = R"HTML(<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flood and air station</title>
<style>
/* green, amber and red only ever mean status. everything else stays grey and ink */
:root{--page:#e7eaeb;--board:#fff;--ink:#16202a;--mid:#44525b;--rule:#b4bec3;--ok:#1a7f4b;--warn:#f2b20a;--danger:#b9291c;--none:#5b6770}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--page);color:var(--ink);font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums}
.c{font-family:"Avenir Next Condensed","Roboto Condensed","Arial Narrow",sans-serif-condensed,system-ui,sans-serif;font-weight:700;font-stretch:condensed}
.wrap{max-width:1080px;margin:0 auto;padding:0 16px}
p{margin:0}
#demo{position:sticky;top:0;z-index:2;background:var(--ink);color:#fff;padding:9px 0;font-weight:700}
.id{display:flex;justify-content:space-between;gap:12px;padding-top:10px;padding-bottom:10px;font-size:14px;color:var(--mid)}
.id b{color:var(--ink)}
#head{background:var(--none);color:#fff;padding:14px 0 17px}
#head.l0{background:var(--ok)}
#head.l1{background:var(--warn);color:var(--ink)}
#head.l2{background:var(--danger)}
h1{margin:0;font-size:clamp(30px,8.5vw,48px);line-height:1.04}
#sub{margin-top:6px;max-width:40em}
main{display:grid;grid-template-columns:1fr;column-gap:48px;padding-bottom:36px}
section{border-top:2px solid var(--ink);margin-top:22px;padding:11px 0 4px;min-width:0}
.hd{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:16px}
h2{margin:0;font-size:22px}
.tag{font-weight:700;font-size:15px;padding:3px 9px;border-radius:2px;background:var(--none);color:#fff;white-space:nowrap}
.tag.l0{background:var(--ok)}
.tag.l1{background:var(--warn);color:var(--ink)}
.tag.l2{background:var(--danger)}
.num{font-size:68px;line-height:.9}
.unit{font-size:22px;margin-left:5px}
.num.na{color:var(--none)}
.cap,.age{font-size:14px;color:var(--mid)}
.tip{margin:14px 0 4px;font-size:17px}
/* the flood reading is a staff gauge, the measuring board bolted to bridge piers */
.fl{display:flex;gap:18px;align-items:flex-start}
.gauge{position:relative;flex:0 0 152px;height:220px;font-size:13px}
.end{position:absolute;left:0;color:var(--mid)}
.staff{position:absolute;top:0;bottom:0;right:0;width:62px;border:2px solid var(--ink);background:var(--board);overflow:hidden}
.off .staff{border-style:dashed}
.staff:after{content:"";position:absolute;top:0;bottom:0;right:0;width:42%;background:repeating-linear-gradient(to top,var(--ink) 0 2px,transparent 2px 10%)}
#water{position:absolute;left:0;right:0;bottom:0;height:0;background:var(--none);transition:height .6s}
#water.l0{background:var(--ok)}
#water.l1{background:var(--warn)}
#water.l2{background:var(--danger)}
.mk{position:absolute;left:0;right:0;height:0;border-top:3px solid var(--warn)}
.mk.d{border-color:var(--danger)}
.mk span{position:absolute;left:0;bottom:1px;font-weight:600}
/* the model's number stays neutral on purpose, it doesn't set the warning level */
.ai{display:grid;grid-template-columns:1fr auto;gap:4px 16px;align-items:baseline;margin-top:18px;padding-top:12px;border-top:1px solid var(--rule)}
.ai b{font-size:17px}
.ai .c{font-size:36px;line-height:1}
.ai p{grid-column:1/-1;font-size:14px;color:var(--mid);max-width:44em}
/* air sits on the index's own scale */
.sc{position:relative;margin-top:22px;height:56px;font-size:13px}
#band{display:flex;gap:2px;height:14px}
#band i{display:block;background:var(--ok)}
#band i+i{background:var(--warn)}
#band i+i+i{background:var(--danger);flex:1}
.off #band{opacity:.3}
#pin{position:absolute;top:-7px;width:6px;height:28px;margin-left:-3px;background:var(--ink);outline:2px solid var(--page);transition:left .6s}
.sc span{position:absolute;top:19px;line-height:1.35}
.sc b{font-weight:400}
.sc em{display:block;font-style:normal;font-weight:600;color:var(--mid)}
ul{margin:0;padding:0;list-style:none}
li{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid var(--rule)}
li i{display:inline-block;width:10px;height:10px;margin-right:9px;background:var(--none)}
li i.l0{background:var(--ok)}
li i.l1{background:var(--warn)}
li i.l2{background:var(--danger)}
li span{color:var(--mid);white-space:nowrap}
dl{display:grid;grid-template-columns:max-content 1fr;gap:7px 18px;margin:0}
dt{color:var(--mid)}
dd{margin:0}
.lost main{opacity:.45}
@media (min-width:860px){
  main{grid-template-columns:1fr 1fr}
  .wrap{padding-left:32px;padding-right:32px}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body>
<div id="demo" hidden><div class="wrap">Demo mode. These readings are simulated, not measured.</div></div>
<div class="wrap id"><b>Flood and air station</b><span id="contact">Contacting the station</span></div>
<header id="head" role="status">
  <div class="wrap">
    <h1 class="c" id="title">Connecting to the station</h1>
    <p id="sub">Readings appear here as soon as the station answers.</p>
  </div>
</header>
<main class="wrap" id="main" hidden>
  <section>
    <div class="hd"><h2 class="c">Flood</h2><span class="tag" id="fword">No data</span></div>
    <div class="fl">
      <div class="gauge off" id="fg" aria-hidden="true">
        <span class="end" style="top:0">100%</span><span class="end" style="bottom:0">0%</span>
        <div class="staff"><div id="water"></div></div>
        <div class="mk" id="fw"><span></span></div>
        <div class="mk d" id="fd"><span></span></div>
      </div>
      <div>
        <div class="c"><span class="num na" id="fnum">&ndash;</span><span class="unit" id="funit"></span></div>
        <p class="cap">Water level, percent of sensor range</p>
      </div>
    </div>
    <p class="tip" id="ftip"></p>
    <p class="age" id="fage"></p>
    <div class="ai">
      <b>Surge probability, next 6 hours</b><span class="c" id="fai">&ndash;</span>
      <p id="fwhy"></p>
    </div>
  </section>
  <section>
    <div class="hd"><h2 class="c">Air quality</h2><span class="tag" id="aword">No data</span></div>
    <div class="c"><span class="num na" id="anum">&ndash;</span><span class="unit" id="aunit"></span></div>
    <p class="cap">Air quality index, higher is worse</p>
    <div class="sc off" id="asc" aria-hidden="true">
      <div id="band"><i></i><i></i><i></i></div>
      <div id="pin" hidden></div>
      <span style="left:0"><b>0</b><em>Normal</em></span>
      <span id="aw"><b></b><em>Warning</em></span>
      <span id="ad"><b></b><em>Danger</em></span>
      <span id="amax" style="right:0"></span>
    </div>
    <p class="tip" id="atip"></p>
    <p class="age" id="aage"></p>
    <div class="ai" id="aaibox" hidden>
      <b>Model probability</b><span class="c" id="aai"></span>
    </div>
  </section>
  <section>
    <div class="hd"><h2 class="c">Recent changes</h2></div>
    <ul id="log"></ul>
  </section>
  <section>
    <div class="hd"><h2 class="c">About these numbers</h2></div>
    <dl>
      <dt>Flood levels</dt><dd id="iflood">--</dd>
      <dt>Air levels</dt><dd id="iair">--</dd>
      <dt>Surge model</dt><dd>NOAA tide station 8726520, St. Petersburg, Florida</dd>
      <dt>Internet link</dt><dd id="inet">--</dd>
      <dt>Running for</dt><dd id="iup">--</dd>
      <dt>Refresh</dt><dd>This page asks the station every 2 seconds</dd>
    </dl>
  </section>
</main>
<script>
const $ = id => document.getElementById(id);
const WORD = {'-1': 'No data', 0: 'Normal', 1: 'Warning', 2: 'Danger'};
const TIP = {
  flood: ['No reading from the water sensor.', 'Water level is normal.',
    'Water is rising. Move valuables off the floor and avoid low roads.',
    'Flooding is likely. Move to higher ground and never drive through water.'],
  air: ['No reading from the air sensor.', 'Air quality is fine.',
    'Children, older adults and people with asthma should limit time outside.',
    'The air is unhealthy. Stay indoors, close the windows, and wear a mask outside.']
};
const HEAD = {flood: ['', 'Flood warning', 'Flood danger'], air: ['', 'Air quality warning', 'Unhealthy air']};
const SURGE = 'How sure the model is that water at the St. Petersburg tide gauge will reach 0.3 m above the predicted tide within 6 hours. Worked out from live NOAA readings.';
const ago = s => s < 60 ? s + ' s ago' : Math.round(s / 60) + ' min ago';
const span = s => s < 60 ? s + ' s' : s < 3600 ? Math.floor(s / 60) + ' min' : Math.floor(s / 3600) + ' h ' + Math.floor(s % 3600 / 60) + ' min';
let lastOk = 0;

function sensor(name, pre, s, d) {
  $(pre + 'num').textContent = s.value == null ? '\u2013' : Math.round(s.value);
  $(pre + 'num').className = 'num' + (s.value == null ? ' na' : '');
  $(pre + 'unit').textContent = s.value == null ? '' : s.suffix || 'AQI';
  $(pre + 'word').textContent = WORD[s.level];
  $(pre + 'word').className = 'tag l' + s.level;
  $(pre + 'tip').textContent = TIP[name][s.level + 1];
  // i say how old the number is, and say so plainly when there isn't a real one
  $(pre + 'age').textContent = d.demo ? 'Simulated reading, not from a sensor.'
    : s.age == null ? 'No reading received since the station started.'
    : s.level < 0 ? 'Last reading ' + ago(s.age) + '. Readings older than ' + d.staleAfter + ' s are not shown.'
    : 'Reading received ' + ago(s.age) + '.';
}

function render(d) {
  const f = d.flood, a = d.air, top = Math.max(f.level, a.level);
  const quiet = f.level < 0 || a.level < 0;
  $('demo').hidden = !d.demo;
  $('main').hidden = false;

  let title, sub;
  if (top < 0) {
    title = 'Waiting for the sensors';
    sub = 'No reading has arrived in the last ' + d.staleAfter + ' seconds. Check that the sensor unit has power and Wi-Fi.';
  } else if (top == 0 && quiet) {
    // one sensor being fine isn't an all clear while the other one is silent
    title = f.level < 0 ? 'No flood reading' : 'No air reading';
    sub = (f.level < 0 ? 'Air quality is normal' : 'Water level is normal') + ', but the other sensor has sent nothing in the last ' + d.staleAfter + ' seconds.';
  } else if (top == 0) {
    title = 'All clear';
    sub = 'Water level and air quality are both normal.';
  } else {
    const parts = [];
    if (f.level > 0) parts.push(HEAD.flood[f.level]);
    if (a.level > 0) parts.push(parts.length ? HEAD.air[a.level].toLowerCase() : HEAD.air[a.level]);
    title = parts.join(' and ');
    sub = [f.level > 0 ? TIP.flood[f.level + 1] : '', a.level > 0 ? TIP.air[a.level + 1] : '',
      f.level < 0 ? TIP.flood[0] : '', a.level < 0 ? TIP.air[0] : ''].join(' ').trim();
  }
  $('head').className = top == 0 && quiet ? '' : 'l' + top;
  $('title').textContent = title;
  $('sub').textContent = sub;
  document.title = top > 0 ? title + ' | Flood and air station' : 'Flood and air station';

  sensor('flood', 'f', f, d);
  $('fg').className = 'gauge' + (f.value == null ? ' off' : '');
  $('water').className = 'l' + f.level;
  $('water').style.height = (f.value == null ? 0 : Math.min(100, f.value / f.max * 100)) + '%';
  $('fw').style.bottom = f.warnAt / f.max * 100 + '%';
  $('fd').style.bottom = f.dangerAt / f.max * 100 + '%';
  $('fw').firstChild.textContent = 'Warning ' + f.warnAt + f.suffix;
  $('fd').firstChild.textContent = 'Danger ' + f.dangerAt + f.suffix;
  $('fai').textContent = f.predicted == null ? '\u2013' : f.predicted + '%';
  $('fwhy').textContent = f.predicted != null ? SURGE
    : d.internet === false ? 'No prediction. The station has no internet link to reach the model.'
    : 'No prediction has arrived from the model yet.';

  sensor('air', 'a', a, d);
  const w = a.warnAt / a.max * 100, g = a.dangerAt / a.max * 100;
  const zone = $('band').children;
  zone[0].style.width = w + '%';
  zone[1].style.width = g - w + '%';
  $('asc').className = 'sc' + (a.value == null ? ' off' : '');
  $('pin').hidden = a.value == null;
  $('pin').style.left = Math.min(100, (a.value || 0) / a.max * 100) + '%';
  $('aw').style.left = w + '%';
  $('ad').style.left = g + '%';
  $('aw').firstChild.textContent = a.warnAt;
  $('ad').firstChild.textContent = a.dangerAt;
  $('amax').textContent = a.max + '+';
  $('aaibox').hidden = a.predicted == null;
  $('aai').textContent = a.predicted == null ? '' : a.predicted + '%';

  $('log').innerHTML = d.log.length ? '' : '<li>Nothing has changed since the station started.</li>';
  for (const e of d.log) {
    const li = document.createElement('li');
    const what = e.what == 'flood' ? 'Flood' : 'Air quality';
    const text = document.createElement('div');
    text.innerHTML = '<i class="l' + e.level + '"></i>';
    text.append(e.level == 0 ? what + ' back to normal' : what + (e.level == 2 ? ' reached danger' : ' reached warning'));
    const t = document.createElement('span');
    t.textContent = ago(e.ago);
    li.append(text, t);
    $('log').appendChild(li);
  }

  $('iflood').textContent = 'Warning at ' + f.warnAt + f.suffix + ', danger at ' + f.dangerAt + f.suffix + ' of sensor range';
  $('iair').textContent = 'Warning at index ' + a.warnAt + ', danger at index ' + a.dangerAt;
  $('inet').textContent = d.internet == null ? '--' : d.internet ? 'Connected, the model can be reached' : 'Not connected, no model predictions';
  $('iup').textContent = span(d.uptime);
}

function lost() {
  document.body.className = 'lost';
  $('head').className = '';
  $('title').textContent = lastOk ? 'Lost contact with the station' : 'No answer from the station';
  $('sub').textContent = 'Check that this device is on the same Wi-Fi as the station. The page keeps trying.' + (lastOk ? ' The numbers below are the last ones received.' : '');
  // the old "4 s ago" would be a lie by now
  if (lastOk) $('fage').textContent = $('aage').textContent = 'From before contact was lost.';
}

// the little clock in the corner, so a frozen page can't pass for a live one
function contact() {
  const s = Math.round((Date.now() - lastOk) / 1000);
  $('contact').textContent = !lastOk ? 'No answer from the station yet' : s < 4 ? 'Station answered just now' : 'Station last answered ' + ago(s);
}

async function tick() {
  try {
    const r = await fetch('/api/status', {cache: 'no-store'});
    render(await r.json());
    lastOk = Date.now();
    document.body.className = '';
  } catch (e) { lost(); }
  contact();
}
tick();
setInterval(tick, 2000);
setInterval(contact, 1000);
</script>
</body>
</html>)HTML";