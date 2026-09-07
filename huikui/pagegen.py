"""股东回馈活动 · 静态看板页生成器

生成 dist/huikui.html：自包含原生 JS 页面（排序/年份过滤/搜索），数据内嵌。
收录当年全部活动：排序 / 公司(代码) / 发布公告时间 / 股数要求 /
回馈内容 / 股东要求 / 公告链接。
"""
import json
import os
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(BASE, "dist")

CSS = """<style>
:root{--bg:#f8f9fa;--card:#fff;--text:#212529;--muted:#6c757d;--border:#dee2e6;--accent:#9333ea;--blue-l:#f3e8ff;--red:#dc2626;--green:#16a34a;}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.6}
.container{max-width:1280px;margin:0 auto;padding:14px 16px 48px}
header{background:var(--card);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:10}
header .inner{max-width:1280px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;padding:0 16px;height:52px}
header .brand{font-weight:700;font-size:16px;color:var(--text)}
header .ts{color:var(--muted);font-size:12px}
.breadcrumb{margin:10px 0 6px}
.breadcrumb a{font-size:13px;color:var(--accent);text-decoration:none;font-weight:500}
h1{font-size:20px;margin:4px 0}
.sub{color:var(--muted);font-size:13px;margin-bottom:12px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:16px;overflow-x:auto}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:12px}
.bar .search{margin-left:auto;padding:7px 12px;border:1px solid var(--border);border-radius:8px;font-size:13px;outline:none;width:170px}
.bar .search:focus{border-color:var(--accent)}
.chip{padding:7px 14px;border-radius:20px;font-size:13px;cursor:pointer;border:1px solid var(--border);background:var(--card);color:#495057;transition:all .15s;user-select:none}
.chip:hover{border-color:var(--accent);color:var(--accent)}
.chip.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.chip .n{font-size:11px;opacity:.8;margin-left:4px}
table{width:100%;border-collapse:collapse;font-size:13px;min-width:820px}
th,td{padding:8px 10px;text-align:center;border-bottom:1px solid var(--border);vertical-align:middle}
th{background:var(--blue-l);color:var(--accent);font-weight:600;cursor:pointer;user-select:none;white-space:nowrap}
th:hover{opacity:.8}
th .ic{font-size:10px;margin-left:2px}
tr:hover td{background:#faf5ff}
.td-l{text-align:left}
.seq{display:inline-block;min-width:22px;padding:1px 7px;border-radius:999px;background:var(--blue-l);color:var(--accent);font-weight:700;font-size:12px}
.name{font-weight:600}
.code{color:var(--muted);font-size:11px;font-weight:400}
.act-title{font-size:11px;color:var(--muted);max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cell{display:block;max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
a.golink{display:inline-block;padding:3px 10px;border-radius:6px;font-size:12px;text-decoration:none;font-weight:600}
a.golink.on{background:var(--blue-l);color:var(--accent)}
a.golink.pdf{background:#f1f5f9;color:#334155;margin-left:4px}
.empty{color:var(--muted);padding:40px;text-align:center}
@media(max-width:768px){.container{padding:10px 8px 40px}.card{padding:8px}th,td{padding:6px 6px;font-size:12px}.bar .search{width:120px;margin-left:0}.cell{max-width:140px}.act-title{max-width:150px}}
</style>"""


def _clean_event(e: dict) -> dict:
    """选取/规范一条记录：输出纯 JSON（转义统一放前端 esc() 处理）。"""
    return {
        "id": str(e.get("id") or ""),
        "code": str(e.get("code") or ""),
        "name": str(e.get("name") or ""),
        "title": str(e.get("title") or ""),
        "notice_date": str(e.get("notice_date") or ""),
        "shares": str(e.get("shares") or ""),
        "reward": str(e.get("reward") or ""),
        "requirement": str(e.get("requirement") or ""),
        "link": str(e.get("link") or "#"),
        "pdf": str(e.get("pdf") or ""),
    }


def build_page(events: list[dict], now: str, year: str, total_all: int) -> str:
    """events：当年且已排好序并带 seq 的记录（纯 dict，未转义）。"""
    data_json = json.dumps(events, ensure_ascii=False, default=str)
    data_json = data_json.replace("</", "<\\/")
    n = len(events)
    sub = (f"全市场公告自动扫描 · 命中即收录 · 当前 {year} 年共 {n} 条"
           f"（含手动补充 {max(0, n - (n - (len([e for e in events if e['source'] == 'auto'])))) if False else ''}".strip())
    sub = (f"当前展示：{year} 年 · 共 {n} 条 · 数据源：东方财富全市场公告 · "
           f"字段为自动解析（尽力而为，仅供参考，不构成投资建议）")
    ts = now
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>股东回馈活动 · 小渔点儿</title>
__CSS__
</head>
<body>
<header>
  <div class="inner"><a href="index.html" style="text-decoration:none"><span class="brand">小渔点儿</span></a><span class="ts">数据时间：__TS__</span></div>
</header>
<div class="container">
  <nav class="breadcrumb"><a href="index.html">← 返回首页</a></nav>
  <h1>股东回馈活动</h1>
  <p class="sub">__SUB__</p>
  <div class="bar">
    <span id="years"></span>
    <input class="search" id="q" placeholder="搜公司/代码/标题">
  </div>
  <div class="card"><div id="wrap"></div></div>
  <p class="sub" style="text-align:center;margin-top:14px">发现方式：自动扫描东方财富全市场公告，标题含 股东回馈/回馈股东/股东福利/股东专享/感恩股东 等关键词即收录并推送微信「股东回馈活动」</p>
</div>
<script>
var DATA = __DATA__;
var curYear = "__YEAR__";
var sortField = "notice_date";
var sortDesc = true;
function esc(v){ v = (v==null?"":String(v)); return v.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function fmt(v){ return v==null||v===""?"-":v; }
function yearsHtml(){
  var ym = {};
  DATA.forEach(function(e){ var y=(e.notice_date||"").slice(0,4); if(y) ym[y]=(ym[y]||0)+1; });
  var keys = Object.keys(ym).sort().reverse();
  if(!keys.length) keys=[curYear];
  var h = "";
  keys.forEach(function(y){
    h += "<span class='chip"+(y===curYear?" active":"")+"' data-y='"+y+"'>"+esc(y)+" 年<span class='n'>"+(ym[y]||0)+"</span></span>";
  });
  document.getElementById("years").innerHTML = h;
}
function view(){
  var list = DATA.filter(function(e){ return (e.notice_date||"").slice(0,4)===curYear; });
  var q = (document.getElementById("q").value||"").toLowerCase().trim();
  if(q) list = list.filter(function(e){
    return String(e.code).toLowerCase().indexOf(q)>=0 || String(e.name).toLowerCase().indexOf(q)>=0 || String(e.title).toLowerCase().indexOf(q)>=0;
  });
  list.sort(function(a,b){
    var r=0;
    if(sortField==="notice_date") r=String(a.notice_date).localeCompare(String(b.notice_date));
    else if(sortField==="seq") r=(Number(a.seq)||0)-(Number(b.seq)||0);
    else if(sortField==="name") r=String(a.name).localeCompare(String(b.name),"zh");
    else r=String(a[sortField]||"").localeCompare(String(b[sortField]||""),"zh");
    return sortDesc?-r:r;
  });
  if(!list.length){ document.getElementById("wrap").innerHTML="<div class='empty'>今年暂无股东回馈活动</div>"; return; }
  var cols = [["seq","排序"],["name","公司（代码）"],["notice_date","发布公告时间"],["shares","股数要求"],["reward","回馈内容"],["requirement","股东要求"],["","公告链接"]];
  var h = "<table><thead><tr>";
  cols.forEach(function(c){
    var k=c[0], l=c[1], arrow = k===sortField?("<span class='ic'>"+(sortDesc?"↓":"↑")+"</span>"):"";
    h += "<th"+(k?" onclick=window.sb('"+k+"')":"")+">"+l+arrow+"</th>";
  });
  h += "</tr></thead><tbody>";
  list.forEach(function(e){
    var pdf = e.pdf ? "<a class='golink pdf' target='_blank' href='"+esc(e.pdf)+"'>PDF</a>" : "";
    h += "<tr>"+
      "<td><span class='seq'>"+(Number(e.seq)||"-")+"</span></td>"+
      "<td class='td-l'><div class='name'>"+esc(e.name)+"</div><div class='code'>"+esc(e.code)+"</div>"+
        "<div class='act-title' title='"+esc(e.title)+"'>"+esc(e.title)+"</div></td>"+
      "<td>"+fmt(esc(e.notice_date))+"</td>"+
      "<td title='"+esc(e.shares)+"'><span class='cell td-l' style='max-width:140px'>"+fmt(esc(e.shares))+"</span></td>"+
      "<td title='"+esc(e.reward)+"'><span class='cell td-l'>"+fmt(esc(e.reward))+"</span></td>"+
      "<td title='"+esc(e.requirement)+"'><span class='cell td-l'>"+fmt(esc(e.requirement))+"</span></td>"+
      "<td><a class='golink on' target='_blank' href='"+esc(e.link)+"'>公告</a>"+pdf+"</td>"+
    "</tr>";
  });
  h += "</tbody></table>";
  document.getElementById("wrap").innerHTML = h;
}
window.sb = function(f){ if(sortField===f) sortDesc=!sortDesc; else { sortField=f; sortDesc=(f==="seq")?false:true; } view(); };
document.addEventListener("click", function(ev){
  var t = ev.target.closest ? ev.target.closest(".chip") : null;
  if(t && t.getAttribute("data-y")){
    curYear = t.getAttribute("data-y");
    var cs = document.querySelectorAll(".chip");
    for(var i=0;i<cs.length;i++) cs[i].classList.remove("active");
    t.classList.add("active");
    view();
  }
});
document.getElementById("q").addEventListener("input", view);
yearsHtml(); view();
</script>
</body>
</html>"""
    return (html.replace("__CSS__", CSS)
            .replace("__TS__", _esc_attr(ts))
            .replace("__SUB__", _esc_html(sub))
            .replace("__DATA__", data_json)
            .replace("__YEAR__", _esc_attr(year)))


def _esc_html(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _esc_attr(v):
    return _esc_html(v)


def render(events_all: list[dict], now: str = "") -> None:
    """只挑当年活动、去重、按时间编号后生成页面写盘。

    兼容：入参每条可含 source 字段（auto/seed），不传亦可。
    """
    now = now or time.strftime("%Y-%m-%d %H:%M:%S")
    this_year = now[:4]
    rows = []
    seen = set()
    for e in events_all:
        date = (e.get("notice_date") or "")[:10]
        if date[:4] != this_year:
            continue
        key = e.get("id") or (e.get("code") + "|" + date)
        if not key or key in seen:
            continue
        seen.add(key)
        row = _clean_event(e)
        row["source"] = str(e.get("source") or "auto")
        rows.append(row)
    rows.sort(key=lambda x: (x["notice_date"], x["id"]))
    for i, r in enumerate(rows, 1):
        r["seq"] = i
    os.makedirs(DIST, exist_ok=True)
    html = build_page(rows, now, this_year, len(events_all))
    with open(os.path.join(DIST, "huikui.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[huikui] 已生成 {os.path.join(DIST, 'huikui.html')}（{this_year} 年 {len(rows)} 条）")
