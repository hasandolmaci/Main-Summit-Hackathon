const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
const { chromium } = require('playwright');

const DIR = '/home/user/Main-Summit-Hackathon/demo';
const CSV = '/home/user/Main-Summit-Hackathon/engine/out/shortlist_2023.csv';

// Minimal CSV parser (handles quoted fields)
function parseCSV(text) {
  const rows = []; let row = [], field = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') q = false;
      else field += c;
    } else if (c === '"') q = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; }
    else if (c !== '\r') field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows;
}
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const csvRows = parseCSV(fs.readFileSync(CSV, 'utf8'));
const hdr = csvRows[0];
const top5 = csvRows.slice(1, 6).map(r => {
  const o = {}; hdr.forEach((h, i) => o[h] = r[i]); return o;
});
const shortlistTable = `<table class="data small"><thead><tr><th>Rank</th><th>Name</th><th>Country</th><th>Score</th><th>Why</th></tr></thead><tbody>
${top5.map(o => `<tr><td>${esc(o.rank)}</td><td>${esc(o.name)}</td><td>${esc(o.country)}</td><td>${(+o.score).toFixed(3)}</td><td>${esc(o.why)}</td></tr>`).join('\n')}
</tbody></table>`;

const css = `
:root { --bg:#0b1020; --fg:#ffffff; --muted:#a9b4cc; --acc:#4cc9f0; }
* { box-sizing: border-box; margin:0; padding:0; }
html, body { width:1920px; height:1080px; background:var(--bg); color:var(--fg);
  font-family: "DejaVu Sans", "Liberation Sans", Arial, sans-serif; overflow:hidden; }
.slide { position:absolute; inset:0; display:none; flex-direction:column; justify-content:center;
  padding:90px 130px; background:var(--bg); }
.slide.active { display:flex; }
h1 { font-size:124px; font-weight:800; letter-spacing:-2px; }
h2 { font-size:72px; font-weight:800; color:var(--acc); margin-bottom:44px; }
p { font-size:44px; line-height:1.4; margin-bottom:28px; }
.sub { font-size:50px; color:var(--muted); line-height:1.35; }
.foot { position:absolute; left:130px; bottom:60px; font-size:34px; color:var(--muted); }
.accent { color:var(--acc); }
.box { background:#14203a; border:3px solid var(--acc); border-radius:18px; padding:26px 22px;
  width:300px; font-size:32px; line-height:1.3; text-align:center; min-height:200px;
  display:flex; align-items:center; justify-content:center; }
.arrow { font-size:70px; color:var(--acc); width:50px; text-align:center; }
.pipe { display:flex; align-items:center; justify-content:center; margin:30px 0 40px; }
.caption { font-size:38px; color:var(--muted); font-style:italic; }
table.data { border-collapse:collapse; width:100%; font-size:36px; }
table.data th { text-align:left; color:var(--acc); font-size:34px; border-bottom:3px solid var(--acc); padding:14px 16px; }
table.data td { border-bottom:1px solid #2b3858; padding:16px; vertical-align:top; line-height:1.3; }
table.data.small { font-size:25px; }
table.data.small td, table.data.small th { padding:8px 12px; }
.card { background:#14203a; border-left:10px solid var(--acc); border-radius:14px; padding:40px 56px; margin:30px 0; font-size:50px; line-height:1.35; }
ul.next { font-size:36px; line-height:1.6; margin-left:0; list-style:none; color:var(--fg); }
ul.next li::before { content:"\\25B8  "; color:var(--acc); }
.close { font-size:64px; font-weight:800; color:var(--acc); margin-top:30px; }
.row { display:flex; gap:60px; align-items:flex-start; }
.col { flex:1; }
`;

const slides = [
`<section class="slide">
  <h1>Startup Radar</h1>
  <p class="sub" style="margin-top:30px;max-width:1500px">Find promising deep-tech startups before their first funding round.</p>
  <div class="foot">HTGF track &middot; MAIN AI Hackathon 2026</div>
</section>`,

`<section class="slide">
  <h2>The problem</h2>
  <p>Investors find most deals through networks and pitches.</p>
  <p>By the time a startup is in Dealroom or the press, <span class="accent">everyone has seen it.</span></p>
  <p>But early signs are already public, scattered across sources: <span class="accent">patents, EU grants, trademarks, GitHub activity, web presence.</span></p>
</section>`,

`<section class="slide">
  <h2>How it works</h2>
  <div class="pipe">
    <div class="box">companies.csv + metrics.csv</div>
    <div class="arrow">&rarr;</div>
    <div class="box">Transform each metric to 0&ndash;1 (log / robust z / sigmoid)</div>
    <div class="arrow">&rarr;</div>
    <div class="box">Profile vector per company</div>
    <div class="arrow">&rarr;</div>
    <div class="box">Score + ranked shortlist</div>
    <div class="arrow">&rarr;</div>
    <div class="box">Leak-free backtest</div>
  </div>
  <p class="caption">No hidden logic: every metric, paper and rule lives in metrics.csv.</p>
</section>`,

`<section class="slide">
  <h2>Research-backed metrics</h2>
  <table class="data">
    <thead><tr><th style="width:38%">Metric</th><th style="width:36%">Paper</th><th>Source</th></tr></thead>
    <tbody>
      <tr><td>Web presence &amp; centrality</td><td>Sharchilev et al. 2018; Garkavenko et al. 2022</td><td>Common Crawl web graph</td></tr>
      <tr><td>R&amp;D grant won / amount</td><td>Howell 2017 (AER); Hottenrott &amp; Richstein 2020</td><td>CORDIS / Innovate UK</td></tr>
      <tr><td>Patents</td><td>Haeussler, Harhoff &amp; Mueller 2014</td><td>EPO OPS</td></tr>
      <tr><td>Trademarks</td><td>Block et al. 2014</td><td>EUIPO</td></tr>
      <tr><td>Outside open-source work</td><td>Conti, Peukert &amp; Roche 2025 (+36% early-funding odds)</td><td>GitHub</td></tr>
    </tbody>
  </table>
</section>`,

`<section class="slide">
  <h2>An LLM, in one place only</h2>
  <div class="card">Crunchbase categories &rarr; <span class="accent">technical-depth score (0&ndash;1)</span>, encoded once and frozen.</div>
  <p>The LLM sees category labels, <span class="accent">never company names</span> &mdash; so the backtest stays leak-free.</p>
  <p>Scoring and backtest are pure math.</p>
</section>`,

`<section class="slide">
  <h2>Backtest on real data</h2>
  <p><span class="accent">19,711</span> European startups (as-of 2021&ndash;2023); <span class="accent">2,137</span> later raised a first equity round within 24 months.</p>
  <p>Freeze the data at 1 Jan of the test year &rarr; score using only what was known then &rarr; check who raised.</p>
  <div class="card" style="margin-top:50px">
    <span class="accent">2023 test</span> (817 grant-funded startups): <b>AUC 0.594</b>, top-10% hit rate <b>2.3&times;</b> the base rate.<br>
    Top 50: <b>26%</b> raised vs <b>10.5%</b> base rate.
  </div>
</section>`,

`<section class="slide">
  <h2>An honest finding</h2>
  <p>We tested our own idea and it failed: &lsquo;distance to past winners&rsquo; scored <span class="accent">AUC &asymp; 0.50</span> &mdash; no better than shuffled labels.</p>
  <div class="card">A simple, label-free weighted sum of research-backed signals wins.</div>
  <p>The strongest signal: <span class="accent">R&amp;D grant size (AUC 0.60&ndash;0.66)</span>.</p>
</section>`,

`<section class="slide">
  <h2>Output &amp; next steps</h2>
  <p style="font-size:38px;margin-bottom:20px">Every lead comes with: a score, the nearest past winner it resembles, and the shared signals behind it.</p>
  ${shortlistTable}
  <ul class="next" style="margin-top:34px">
    <li>Plug in patents, trademarks and GitHub</li>
    <li>Match grants for all companies</li>
    <li>Measure lead time vs HTGF&rsquo;s own investment dates</li>
  </ul>
  <div class="close">Startup Radar &mdash; see them first.</div>
</section>`
];

const html = `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head>
<body>${slides.map(s => s).join('\n')}</body></html>`;
const htmlPath = path.join(DIR, 'slides.html');
fs.writeFileSync(htmlPath, html);

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await page.goto('file://' + htmlPath);
  const n = await page.$$eval('section.slide', s => s.length);
  for (let i = 0; i < n; i++) {
    await page.evaluate(i => {
      document.querySelectorAll('section.slide').forEach((el, j) => el.classList.toggle('active', j === i));
    }, i);
    await page.screenshot({ path: path.join(DIR, `shots/slide${i + 1}.png`) });
  }
  await browser.close();
  console.log('shots', n);

  // Each slide 8.5s, 0.5s xfade => offsets 8,16,...
  const dur = 8.5, fade = 0.5, step = dur - fade;
  const inputs = [];
  for (let i = 1; i <= n; i++) inputs.push(`-loop 1 -t ${dur} -i ${path.join(DIR, `shots/slide${i}.png`)}`);
  let filter = '', last = '[0:v]';
  for (let i = 1; i < n; i++) {
    const out = i === n - 1 ? '[vout]' : `[x${i}]`;
    filter += `${last}[${i}:v]xfade=transition=fade:duration=${fade}:offset=${(step * i).toFixed(2)}${out};`;
    last = `[x${i}]`;
  }
  filter = filter.replace(/;$/, '');
  const out = path.join(DIR, 'startup_radar_demo.mp4');
  const cmd = `/usr/bin/ffmpeg -y ${inputs.join(' ')} -filter_complex "${filter}" -map "[vout]" -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -r 30 -movflags +faststart -an "${out}"`;
  execSync(cmd, { stdio: 'inherit' });
  console.log('done', out);
})().catch(e => { console.error(e); process.exit(1); });
