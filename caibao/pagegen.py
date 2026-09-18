# -*- coding: utf-8 -*-
"""生成 dist/caibao.html：自包含「穿透财报分析」实时工具页。

数据来源全部在浏览器端实时拉取：
  - 东方财富 F10 三张报表 + 主要指标（CORS: *）
  - 东财 push2delay / 腾讯 gtimg 行情（兜底）
规则引擎 caibao/engine.js 内联进页面。
"""
import os
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(BASE, "dist")
ENGINE = os.path.join(BASE, "caibao", "engine.js")

CSS = """
:root{--bg:#f8f9fa;--card:#fff;--text:#212529;--muted:#6c757d;--border:#dee2e6;
--accent:#7c3aed;--acc-l:#ede9fe;--red:#dc2626;--red-l:#fee2e2;--yel:#d97706;--yel-l:#fef3c7;
--green:#16a34a;--grn-l:#dcfce7;--blue:#2563eb;--blue-l:#dbeafe;}
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',Roboto,sans-serif;
background:var(--bg);color:var(--text);line-height:1.65;-webkit-font-smoothing:antialiased;}
header{background:var(--card);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:20;}
header .inner{max-width:1080px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;padding:0 16px;height:52px;}
header .brand{font-weight:700;font-size:16px;}
header .ts{color:var(--muted);font-size:12px;}
.container{max-width:1080px;margin:0 auto;padding:14px 16px 60px;}
nav.breadcrumb a{margin-right:12px;font-size:13px;color:var(--accent);text-decoration:none;font-weight:500;}
h1{font-size:20px;margin:14px 0 4px;}
.sub{color:var(--muted);font-size:13px;margin-bottom:16px;}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:16px;overflow-x:auto;}
.card h2{font-size:15px;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid var(--border);}
.card h3.sub-h{font-size:13px;margin:16px 0 8px;color:var(--accent);}
.tip{font-size:12.5px;color:var(--muted);background:var(--acc-l);border-radius:6px;padding:8px 10px;margin-bottom:12px;}
.empty{color:var(--muted);padding:18px;text-align:center;font-size:13px;}
table{width:100%;border-collapse:collapse;font-size:13px;}
th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--border);vertical-align:top;}
th{background:var(--acc-l);color:var(--accent);font-weight:600;white-space:nowrap;}
tr:hover{background:#faf5ff;}
.up{color:var(--red);}  /* A股习惯：涨=红 */
.down{color:var(--green);}
.kvs{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px;}
.kv{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:8px 10px;}
.kv .k{display:block;font-size:11.5px;color:var(--muted);}
.kv .v{display:block;font-size:14px;font-weight:600;}
.tag{display:inline-block;border-radius:10px;padding:1px 7px;font-size:11px;font-weight:600;white-space:nowrap;}
.t-red{background:var(--red-l);color:var(--red);}
.t-yel{background:var(--yel-l);color:var(--yel);}
.t-info{background:var(--blue-l);color:var(--blue);}
.t-ok{background:var(--grn-l);color:var(--green);}
.rid{font-size:11px;color:var(--muted);font-weight:400;}
.why{font-size:12px;color:var(--muted);}
.total{font-size:15px;margin-top:12px;padding:10px;background:var(--acc-l);border-radius:8px;}
.open{margin-left:18px;font-size:13px;color:#495057;}
.open li{margin-bottom:4px;}
.concl{border-left:4px solid var(--accent);}
.concl p{font-size:14px;}
/* 输入区 */
.search{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px;}
.search .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;}
input#kw{flex:1;min-width:200px;padding:10px 12px;border:1px solid var(--border);border-radius:8px;font-size:14px;outline:none;}
input#kw:focus{border-color:var(--accent);}
button{padding:10px 20px;border-radius:8px;border:none;font-size:14px;font-weight:600;cursor:pointer;}
.bt-main{background:var(--accent);color:#fff;}
.bt-main:disabled{opacity:.55;cursor:not-allowed;}
.v-red{color:var(--red);font-weight:600;}
.v-yel{color:var(--yel);font-weight:600;}
select{padding:10px 12px;border-radius:10px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:14px;font-family:inherit;}
.bt-ghost{background:var(--card);color:var(--accent);border:1px solid var(--accent);}
.chips{margin-top:10px;display:flex;gap:6px;flex-wrap:wrap;}
.chip{padding:5px 12px;border-radius:16px;font-size:12px;border:1px solid var(--border);background:var(--bg);color:#495057;cursor:pointer;}
.chip:hover{border-color:var(--accent);color:var(--accent);}
.status{margin-top:10px;font-size:12.5px;color:var(--muted);}
.status.err{color:var(--red);}
.copybar{margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;}
.hidden{display:none;}
.note{font-size:12px;color:var(--muted);margin-top:10px;}
@media(max-width:640px){th,td{font-size:12px;padding:6px 7px;}.kvs{grid-template-columns:1fr 1fr;}}
"""

JS_UI = """
(function(){
  var $=function(id){return document.getElementById(id);};
  var state=null;
  function setStatus(t,err){var s=$('status');s.textContent=t;s.className='status'+(err?' err':'');}

  function go(raw){
    var nc=CaiBao.normalizeCode(raw);
    if(!nc){setStatus('请输入 6 位股票代码，例如 600026 / 000001 / 300750',true);return;}
    setStatus('正在拉取 '+nc.code+' 的行情与三张报表…');
    $('out').innerHTML='';$('copybar').classList.add('hidden');
    $('btn').disabled=true;
    Promise.all([CaiBao.fetchQuote(nc), CaiBao.fetchAll(nc), CaiBao.fetchHS300PE()])
      .then(function(r){
        var quote=r[0], raw=r[1], hs=r[2];
        if(!raw.income.length) throw new Error('未获取到财报数据（可能代码有误或为港股/美股）');
        var series=CaiBao.buildSeries(raw);
        if(!series.length) throw new Error('财报数据为空');
        var flags=CaiBao.runRules(series);
        var gC=CaiBao.grade(flags,'C'), gD=CaiBao.grade(flags,'D');
        var A=CaiBao.engineA(series[series.length-1],quote,hs);
        var sc=CaiBao.score(series[series.length-1],flags,gC,gD,A);
        var vatSel=$('vat');var vat=vatSel?parseFloat(vatSel.value):0.13;
        var checks=CaiBao.runChecks(series,vat);
        state={nc:nc,quote:quote,series:series,flags:flags,gC:gC,gD:gD,A:A,score:sc,checks:checks,vat:vat};
        $('out').innerHTML=CaiBao.render(state);
        $('copybar').classList.remove('hidden');
        setStatus('完成：'+quote.name+' · 最新报告期 '+series[series.length-1].label+' · 数据来自东财 F10');
        $('out').scrollIntoView({behavior:'smooth',block:'start'});
      })
      .catch(function(e){
        setStatus('失败：'+(e&&e.message?e.message:e)+'　（若为浏览器拦截，请确认页面为 https 且网络可达东财）',true);
      })
      .then(function(){ $('btn').disabled=false; });
  }

  function copyPrompt(){
    if(!state)return;
    var t=CaiBao.buildPrompt(state);
    if(navigator.clipboard&&navigator.clipboard.writeText){
      navigator.clipboard.writeText(t).then(function(){setStatus('已复制 AI 提示词（'+t.length+' 字），粘贴到任意 AI 即可生成完整报告');},
        function(){fallbackCopy(t);});
    } else fallbackCopy(t);
  }
  function fallbackCopy(t){
    var ta=document.createElement('textarea');ta.value=t;document.body.appendChild(ta);ta.select();
    try{document.execCommand('copy');setStatus('已复制 AI 提示词（'+t.length+' 字）');}
    catch(e){setStatus('复制失败，请手动选择下方文本',true);}
    document.body.removeChild(ta);
  }
  function copyMd(){
    if(!state)return;
    var el=document.createElement('div');el.innerHTML=CaiBao.render(state);
    var md=(el.innerText||el.textContent||'');
    if(navigator.clipboard&&navigator.clipboard.writeText){
      navigator.clipboard.writeText(md).then(function(){setStatus('已复制报告文本');},function(){});
    }
  }

  window.addEventListener('DOMContentLoaded',function(){
    $('btn').addEventListener('click',function(){go($('kw').value);});
    $('kw').addEventListener('keydown',function(e){if(e.key==='Enter')go($('kw').value);});
    $('btCopy').addEventListener('click',copyPrompt);
    $('btMd').addEventListener('click',copyMd);
    Array.prototype.forEach.call(document.querySelectorAll('.chip[data-c]'),function(c){
      c.addEventListener('click',function(){ $('kw').value=c.getAttribute('data-c'); go($('kw').value); });
    });
  });
})();
"""

CHIPS = [
    ("600026", "中远海能"), ("600900", "长江电力"), ("600519", "贵州茅台"),
    ("300750", "宁德时代"), ("000001", "平安银行"), ("601088", "中国神华"),
    ("000858", "五粮液"), ("002594", "比亚迪"),
]


def _page(now: str) -> str:
    try:
        with open(ENGINE, "r", encoding="utf-8") as f:
            engine = f.read()
    except OSError:
        engine = "console.error('engine.js missing');"
    chips = "".join(
        f'<span class="chip" data-c="{c}">{n}</span>' for c, n in CHIPS
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>穿透财报分析 · 小渔点儿</title>
<style>{CSS}</style>
</head>
<body>
<header><div class="inner">
  <span class="brand">小渔点儿 · 穿透财报分析</span>
  <span class="ts">生成时间：{now}</span>
</div></header>
<div class="container">
  <nav class="breadcrumb"><a href="index.html">首页</a></nav>

  <h1>穿透财报分析</h1>
  <p class="sub">输入 A 股代码或名称 → 实时抓取东财 F10 三张报表 + 行情 → 按邹佩轩框架自动出报告</p>

  <div class="search">
    <div class="row">
      <input id="kw" placeholder="输入 6 位代码，如 600026 / 300750 / 000001" autocomplete="off">
      <select id="vat" title="增值税率假设，用于销售收现勾稽（CK-07）。免税行业请选 0%">
        <option value="0.13" selected>增值税 13%</option>
        <option value="0.09">增值税 9%</option>
        <option value="0.06">增值税 6%</option>
        <option value="0">免税 0%</option>
      </select>
      <button class="bt-main" id="btn">生成分析</button>
    </div>
    <div class="chips">{chips}</div>
    <div class="status" id="status">数据全部在浏览器端实时拉取，不经过任何服务器。</div>
    <div class="copybar hidden" id="copybar">
      <button class="bt-ghost" id="btCopy">复制 AI 提示词（交给 AI 深挖）</button>
      <button class="bt-ghost" id="btMd">复制报告文本</button>
    </div>
    <p class="note">框架：「不给财报估值，给叙事估值」双引擎（叙事定位 A + 三表科目验证 B）+ 舞弊/调节交叉检验（C+D）。
    规则蒸馏自邹佩轩《穿透财报》《穿透估值》《穿透叙事》。仅供学习参考，不构成投资建议。</p>
  </div>

  <div id="out"></div>
  <p class="sub" style="text-align:center;margin-top:24px">由 GitHub Actions 生成 · 数据实时取自东方财富 · 仅供学习参考，不构成投资建议</p>
</div>
<script>{engine}</script>
<script>{JS_UI}</script>
</body>
</html>
"""


def render(now: str = "") -> None:
    now = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(DIST, exist_ok=True)
    path = os.path.join(DIST, "caibao.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_page(now))
    print(f"[caibao] 已生成 {path}")


if __name__ == "__main__":
    render()
