#pragma once
// the whole website lives in this one string, so the board can serve it with no internet
const char PAGE_HTML[] PROGMEM = R"HTML(<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flood and air watch</title>
<style>
:root{--ink:#12303f;--paper:#e9f0f2;--card:#fff;--water:#2f7fa6;--line:#c3d2d9;--soft:#5d7682;--ok:#23855a;--warn:#f0b21a;--danger:#c4352b;--none:#637a85}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.cond{font-family:"Avenir Next Condensed","Roboto Condensed","Arial Narrow",system-ui,sans-serif;font-weight:700;letter-spacing:-.01em}
header{padding:22px 20px 26px;background:var(--none);color:#fff;transition:background .4s}
header.l0{background:var(--ok)}
header.l1{background:var(--warn);color:var(--ink)}
header.l2{background:var(--danger)}
.top{display:flex;justify-content:space-between;align-items:center;font-size:14px;opacity:.9}
#demo{border:1.5px solid currentColor;border-radius:99px;padding:1px 10px;font-weight:600}
h1{margin:26px 0 6px;font-size:clamp(40px,11vw,68px);line-height:.98}
#sub{margin:0;max-width:34em;font-size:17px}
main{max-width:760px;margin:0 auto;padding:8px 20px 40px}
section{padding:22px 0;border-bottom:1px solid var(--line)}
h2{margin:0 0 14px;font-size:22px}
.read{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.num{font-size:56px;line-height:1}
.word{font-weight:700;padding:2px 10px;border-radius:4px;background:var(--none);color:#fff}
.word.l0{background:var(--ok)}
.word.l1{background:var(--warn);color:var(--ink)}
.word.l2{background:var(--danger)}
.tip{margin:10px 0 4px}
.age{margin:0;font-size:14px;color:var(--soft)}
/* the flood reading is drawn as a staff gauge, the striped board you see bolted to bridge piers */
.flood{display:flex;gap:22px;align-items:stretch}
.staff{position:relative;flex:0 0 64px;height:210px;border:2px solid var(--ink);background:repeating-linear-gradient(to top,transparent 0 19px,var(--line) 19px 21px) var(--card);overflow:hidden}
#water{position:absolute;left:0;right:0;bottom:0;height:0;background:var(--water);opacity:.85;transition:height .8s}
.mark{position:absolute;left:0;right:0;height:0;border-top:3px solid var(--warn)}
.mark.d{border-color:var(--danger)}
.mark span{position:absolute;right:3px;bottom:1px;font-size:11px;font-weight:700}
/* air sits on the index's own scale */
.band{position:relative;height:16px;margin:26px 0 6px;border-radius:3px;background:var(--line)}
#pin{position:absolute;top:-10px;bottom:-4px;width:4px;margin-left:-2px;border-radius:2px;background:var(--ink);transition:left .8s}
.ends{display:flex;justify-content:space-between;font-size:13px;color:var(--soft)}
ul{margin:0;padding:0;list-style:none}
li{display:flex;justify-content:space-between;gap:12px;padding:7px 0}
li span{color:var(--soft);white-space:nowrap}
footer{padding-top:18px;font-size:14px;color:var(--soft)}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body>
<header id="head">
  <div class="top"><span>Flood and air watch</span><span id="demo" hidden>Demo data</span></div>
  <h1 class="cond" id="title">Connecting to the station</h1>
  <p id="sub">Readings appear here as soon as the station answers.</p>
</header>
<main>
  <section>
    <h2 class="cond">Flood</h2>
    <div class="flood">
      <div class="staff" aria-hidden="true">
        <div id="water"></div>
        <div class="mark" id="fw"><span>warning</span></div>
        <div class="mark d" id="fd"><span>danger</span></div>
      </div>
      <div>
        <div class="read"><span class="num cond" id="fnum">--</span><span class="word" id="fword">No data</span></div>
        <p class="tip" id="ftip"></p>
        <p class="age" id="fage"></p>
      </div>
    </div>
  </section>
  <section>
    <h2 class="cond">Air quality</h2>
    <div class="read"><span class="num cond" id="anum">--</span><span class="word" id="aword">No data</span></div>
    <div class="band" id="band" aria-hidden="true"><div id="pin" hidden></div></div>
    <div class="ends"><span>0, clean</span><span id="amax"></span></div>
    <p class="tip" id="atip"></p>
    <p class="age" id="aage"></p>
  </section>
  <section>
    <h2 class="cond">Recent changes</h2>
    <ul id="log"></ul>
  </section>
  <footer>This page asks the station for new readings every 2 seconds.</footer>
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
const ago = s => s < 60 ? s + ' s ago' : Math.round(s / 60) + ' min ago';

function sensor(name, pre, s) {
  $(pre + 'num').textContent = s.value == null ? '--' : Math.round(s.value) + s.suffix;
  $(pre + 'word').textContent = WORD[s.level];
  $(pre + 'word').className = 'word l' + s.level;
  $(pre + 'tip').textContent = TIP[name][s.level + 1];
  $(pre + 'age').textContent = s.age == null ? '' : 'Last reading ' + ago(s.age);
}

function render(d) {
  const f = d.flood, a = d.air, top = Math.max(f.level, a.level);
  $('head').className = 'l' + top;
  $('demo').hidden = !d.demo;

  let title, sub;
  if (top < 0) {
    title = 'Waiting for the sensors';
    sub = 'No reading has arrived in the last ' + d.staleAfter + ' seconds. Check that the sensor unit has power and Wi-Fi.';
  } else if (top == 0) {
    title = 'All clear';
    sub = 'Water level and air quality are both normal.';
  } else {
    const parts = [];
    if (f.level > 0) parts.push(HEAD.flood[f.level]);
    if (a.level > 0) parts.push(parts.length ? HEAD.air[a.level].toLowerCase() : HEAD.air[a.level]);
    title = parts.join(' and ');
    sub = [f.level > 0 ? TIP.flood[f.level + 1] : '', a.level > 0 ? TIP.air[a.level + 1] : ''].join(' ').trim();
  }
  $('title').textContent = title;
  $('sub').textContent = sub;
  document.title = top > 0 ? title + ' | Flood and air watch' : 'Flood and air watch';

  sensor('flood', 'f', f);
  $('water').style.height = (f.value == null ? 0 : Math.min(100, f.value / f.max * 100)) + '%';
  $('fw').style.bottom = f.warnAt / f.max * 100 + '%';
  $('fd').style.bottom = f.dangerAt / f.max * 100 + '%';

  sensor('air', 'a', a);
  const w = a.warnAt / a.max * 100, g = a.dangerAt / a.max * 100;
  $('band').style.background = 'linear-gradient(to right,var(--ok) 0 ' + w + '%,var(--warn) ' + w + '% ' + g + '%,var(--danger) ' + g + '%)';
  $('pin').hidden = a.value == null;
  $('pin').style.left = Math.min(100, (a.value || 0) / a.max * 100) + '%';
  $('amax').textContent = a.max + '+, hazardous';

  $('log').innerHTML = d.log.length ? '' : '<li>Nothing has changed since the station started.</li>';
  for (const e of d.log) {
    const li = document.createElement('li');
    const what = e.what == 'flood' ? 'Flood' : 'Air quality';
    li.textContent = e.level == 0 ? what + ' back to normal' : what + (e.level == 2 ? ' reached danger' : ' reached warning');
    const t = document.createElement('span');
    t.textContent = ago(e.ago);
    li.appendChild(t);
    $('log').appendChild(li);
  }
}

function lost() {
  $('head').className = '';
  $('title').textContent = 'Lost contact with the station';
  $('sub').textContent = 'Check that this device is on the same Wi-Fi as the station. The page keeps trying.';
}

async function tick() {
  try {
    const r = await fetch('/api/status', {cache: 'no-store'});
    render(await r.json());
  } catch (e) { lost(); }
}
tick();
setInterval(tick, 2000);
</script>
</body>
</html>)HTML";
