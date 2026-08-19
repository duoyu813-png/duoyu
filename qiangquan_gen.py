"""可转债抢权操作 · 评分看板生成器（静态版）

迁移自 localhost:8099 / fly.io 的抢权评分看板：
  集思录 pre_list 待发债 -> 腾讯行情(股本/市值) + 东财财务(业绩/PE) -> scorer 评分

输出到 dist/：
  qiangquan.json   评分数据快照（Vue 页面读取）
  qiangquan.html   评分看板页面（交互：排序/过滤/评分明细弹窗）

兜底：若线上抓取失败（网络/集思录限制），回退到仓库内置快照
      hanquan/last_qiangquan.json，保证页面始终有数据。
"""
import asyncio
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hanquan.fetcher import fetch_all_pending_detailed

BASE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(BASE, "dist")
FALLBACK = os.path.join(BASE, "hanquan", "last_qiangquan.json")
TEMPLATE = os.path.join(BASE, "hanquan", "board_template.html")
ISSUES_TEMPLATE = os.path.join(BASE, "hanquan", "issues_template.html")
VUE_LIB = os.path.join(BASE, "hanquan", "vue.global.prod.js")

CDN_BLOCK = """<script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
<script>
window.Vue || document.write('<script src="https://cdn.jsdelivr.net/npm/vue@3/dist/vue.global.prod.js"><\\/script>');
</script>"""
LOCAL_VUE = """<script src="vue.global.prod.js"></script>"""

# 只展示"待发行/审批中"，剔除已上市
EXCLUDE_PROGRESS = {"已上市"}


def _date_str(v):
    if not v:
        return ""
    return str(v)[:10]


def _normalize(bond: dict) -> dict:
    """去掉 datetime 等不可序列化字段，统一日期格式"""
    out = {}
    for k, v in bond.items():
        if isinstance(v, (dict, list, int, float, str, bool)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)
    return out


def _esc(v):
    """HTML 转义，防止特殊字符破坏页面"""
    if v is None:
        return ""
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# 进度展示顺序（含"全部"）
PROG_ORDER = ["发行中", "同意注册", "上市委通过", "交易所通过", "交易所受理", "股东大会审议", "董事会预案"]


def _build_issues_static(bonds: list[dict]) -> str:
    """纯静态待发可转债页面（原生 JS，不依赖任何框架）"""
    progress_counts = {p: 0 for p in PROG_ORDER}
    rows = []
    for idx, b in enumerate(bonds):
        p = b.get("progress") or "其他"
        progress_counts[p] = progress_counts.get(p, 0) + 1
        rows.append([
            idx,
            _esc(b.get("stock_code")),
            b.get("stock_name") or "",
            p,
            b.get("total_scale"), b.get("per_share_amount"),
            b.get("circ_mv"), b.get("one_hand_shares"), b.get("price"),
            b.get("industry") or "", b.get("rating") or "", b.get("action") or "",
            b.get("board") or "",
        ])
    import json as _json
    data_json = _json.dumps(bonds, ensure_ascii=False, default=str)
    chips = ["<button class='chip active' data-progress=''>全部<span class='n'>" + str(len(bonds)) + "</span></button>"]
    for p in PROG_ORDER:
        n = progress_counts.get(p, 0)
        if n > 0:
            chips.append(f"<button class='chip' data-progress='{_esc(p)}'>{p}<span class='n'>{n}</span></button>")
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>待发可转债(抢权) · 小渔点儿</title>
<style>
:root{--bg:#f8f9fa;--card:#fff;--text:#212529;--muted:#6c757d;--border:#dee2e6;--accent:#2563eb;--blue-l:#dbeafe;--red:#dc2626;--green:#16a34a;}
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
.sub{color:var(--muted);font-size:13px;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:16px}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:14px}
.bar .search{margin-left:auto;padding:7px 12px;border:1px solid var(--border);border-radius:8px;font-size:13px;outline:none;width:150px}
.bar .search:focus{border-color:var(--accent)}
.chip{padding:7px 14px;border-radius:20px;font-size:13px;cursor:pointer;border:1px solid var(--border);background:var(--card);color:#495057;transition:all .15s}
.chip:hover{border-color:var(--accent);color:var(--accent)}
.chip.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.chip .n{font-size:11px;opacity:.8;margin-left:4px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;text-align:center;border-bottom:1px solid var(--border);white-space:nowrap}
th{background:var(--blue-l);color:var(--accent);font-weight:600;cursor:pointer;user-select:none}
th:hover{opacity:.8}
tr:hover td{background:#f1f5f9}
.num-pos{color:var(--green);font-weight:600}
.tag{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600}
.tag-green{background:rgba(22,163,74,.15);color:var(--green)}
.tag-blue{background:var(--blue-l);color:var(--accent)}
.tag-orange{background:#ffedd5;color:#ea580c}
.tag-gray{background:#eef0f2;color:#6b7280}
.rating{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700}
.rating-A{background:linear-gradient(135deg,#16a34a,#15803d);color:#fff}
.rating-B{background:#5ec97f;color:#fff}
.rating-C{background:#c3d4c9;color:#3d5c4c}
.rating-D{background:#dbe6df;color:#5d7267}
.score{font-weight:800;font-size:15px}
.score-high{color:var(--green)}
.score-mid{color:#2bc46a}
.score-low{color:#9aa9a2}
.empty{color:var(--muted);padding:40px;text-align:center}
.stock-link{color:var(--accent);text-decoration:none;font-weight:600}
.actionsel{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600}
.action-pos{background:rgba(22,163,74,.15);color:var(--green)}
.action-watch{background:rgba(34,197,94,.1);color:#15803d}
.action-caution{background:#eef3ef;color:#64796e}
.action-avoid{background:#e8ede9;color:#8a9990}
.detail-btn{background:var(--blue-l);color:var(--accent);border:none;padding:4px 12px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer}
.detail-btn:active{opacity:.7}
.overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.45);z-index:999;display:flex;align-items:center;justify-content:center;padding:16px}
.dlg{background:#fff;border-radius:14px;max-width:560px;width:100%;max-height:86vh;overflow-y:auto;padding:18px 20px;box-shadow:0 20px 60px rgba(0,0,0,.2)}
.dlg-h{display:flex;align-items:center;justify-content:space-between;font-size:17px;font-weight:700;margin-bottom:4px}
.dlg-h .x{background:#f1f5f9;border:1px solid var(--border);width:30px;height:30px;border-radius:8px;font-size:16px;cursor:pointer;color:var(--muted)}
.dlg-sub{color:var(--muted);font-size:12px;margin-bottom:14px}
.blk{margin-bottom:14px}
.blk h4{font-size:13px;color:var(--accent);margin-bottom:8px;border-left:3px solid var(--accent);padding-left:8px}
.dlg-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.dlg-grid.d3{grid-template-columns:repeat(3,1fr)}
.dit{background:#f8fafc;border:1px solid var(--border);border-radius:8px;padding:8px 10px}
.dl{font-size:11px;color:var(--muted);margin-bottom:3px}
.dv{font-size:14px;font-weight:700}
.df{font-size:11px;color:var(--muted);margin-top:3px}
.grey{color:var(--muted);font-weight:400;font-size:12px}
@media(max-width:768px){th,td{padding:6px 6px;font-size:12px}.bar .search{width:110px}.c-mh{display:none!important}th{white-space:normal;word-break:break-all}.dlg{padding:14px}.dlg-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <div class="inner"><a href="index.html" style="text-decoration:none;color:var(--text);"><span class="brand">小渔点儿</span></a><span class="ts">数据时间：__TIME__</span></div>
</header>
<div class="container">
  <nav class="breadcrumb"><a href="index.html">← 返回首页</a></nav>
  <h1>待发可转债 · 抢权</h1>
  <p class="sub">按审核进度分组 · 点击表头排序 · 数据源：集思录/东财/腾讯 · 共 __TOTAL__ 条</p>
  <div class="bar">
    __CHIPS__
    <input class="search" id="q" placeholder="搜代码/名称">
  </div>
  <div class="card"><div id="table-wrap"></div></div>
  <p class="sub" style="text-align:center;margin-top:16px">仅供学习参考，不构成投资建议</p>
</div>
<script>
var DATA = __DATA__;
var PROG_ORDER = ["发行中","同意注册","上市委通过","交易所通过","交易所受理","股东大会审议","董事会预案","其他"];
var ACTION_ORDER = {"积极配债":0,"关注建仓":1,"小仓关注":2,"关注":3,"观望":4,"回避":5};
var cur = "";
var sortField = "total_score";
var sortDesc = true;
function fmt(v){ if(v===null||v==="")return "-"; var n=Number(v); if(isNaN(n))return v; return n%1===0?String(n):n.toFixed(2); }
function ratingCls(r){ return "rating-" + String(r||"D").replace("+","-plus").replace("A","A"); }
function actionCls(a){ if(a&&(a.indexOf("积极配债")>=0||a.indexOf("关注建仓")>=0))return "action-pos"; if(a&&(a.indexOf("小仓关注")>=0||a.indexOf("关注")>=0))return "action-watch"; if(a&&a.indexOf("观望")>=0)return "action-caution"; return "action-avoid"; }
function progressCls(p){ if(p==="发行中"||p==="同意注册")return "tag-green"; if(p==="上市委通过"||p==="交易所通过")return "tag-blue"; if(p==="交易所受理")return "tag-orange"; return "tag-gray"; }
function scoreCls(s){ s=Number(s)||0; if(s>=80)return "score-high"; if(s>=60)return "score-mid"; return "score-low"; }
function emUrl(c){ return "https://quote.eastmoney.com/" + (String(c).charAt(0)==="6"?"sh":"sz") + c + ".html"; }
function render(){
  var list = DATA.slice();
  if(cur) list = list.filter(function(b){ return (b.progress||"其他")===cur; });
  var q = (document.getElementById("q").value||"").toLowerCase().trim();
  if(q) list = list.filter(function(b){ return String(b.stock_code||"").toLowerCase().indexOf(q)>=0 || String(b.stock_name||"").indexOf(q)>=0; });
  list.sort(function(a,b){
    var va,vb,r=0;
    if(sortField==="progress"){ va=PROG_ORDER.indexOf(a.progress); vb=PROG_ORDER.indexOf(b.progress); r=(va<0?99:va)-(vb<0?99:vb); }
    else if(sortField==="action"){ va=ACTION_ORDER[a.action]!==undefined?ACTION_ORDER[a.action]:9; vb=ACTION_ORDER[b.action]!==undefined?ACTION_ORDER[b.action]:9; r=va-vb; }
    else { va=Number(a[sortField])||0; vb=Number(b[sortField])||0; r=va-vb; }
    return sortDesc?-r:r;
  });
  var h = "<table><thead><tr>";
  var cols = [["total_score","评分",1],["stock_code","代码/名称",1],["progress","进度",1],["total_scale","规模(亿)",0],["per_share_amount","百元含权",1],["circ_mv","预估流通(亿)",0],["one_hand_shares","一手党(股)",0],["price","正股价",0],["industry","行业",0],["","评级",1],["action","操作建议",1],["","明细",1]];
  for(var i=0;i<cols.length;i++){ h += "<th"+(cols[i][2]===0?" class='c-mh'":"")+" onclick=window.sortB('"+cols[i][0]+"')>"+cols[i][1]+(sortField===cols[i][0]?(sortDesc?" ↓":" ↑"):"")+"</th>"; }
  h += "</tr></thead><tbody>";
  for(var i=0;i<list.length;i++){
    var b=list[i];
    var cells = [
      "<td><span class='score "+scoreCls(b.total_score)+"'>"+(b.total_score||0)+"</span></td>",
      "<td><a class='stock-link' target='_blank' href='"+emUrl(b.stock_code)+"'>"+b.stock_code+"</a><div style='font-size:11px;color:var(--muted)'>"+b.stock_name+"</div></td>",
      "<td><span class='tag "+progressCls(b.progress)+"'>"+(b.progress||"-")+"</span></td>",
      "<td class='c-mh'>"+fmt(b.total_scale)+"</td>",
      "<td>"+fmt(b.per_share_amount)+"</td>",
      "<td class='c-mh'>"+fmt(b.circ_mv)+"</td>",
      "<td class='c-mh'>"+(b.one_hand_shares||"-")+"</td>",
      "<td class='c-mh'>"+fmt(b.price)+"</td>",
      "<td class='c-mh' style='font-size:12px'>"+(b.industry||"-")+"</td>",
      "<td><span class='rating "+ratingCls(b.rating)+"'>"+(b.rating||"-")+"</span></td>",
      "<td><span class='actionsel "+actionCls(b.action)+"'>"+(b.action||"-")+"</span></td>",
      "<td><button class='detail-btn' onclick=window.showD('"+b.stock_code+"')>明细</button></td>"
    ];
    h += "<tr>"+cells.join("")+"</tr>";
  }
  if(!list.length) h = "<div class='empty'>暂无数据</div>"; else h += "</tbody></table>";
  document.getElementById("table-wrap").innerHTML = h;
}
function oneHandDetail(b){
  var mkt = b.market || (String(b.stock_code).charAt(0)==="6"?"沪":String(b.stock_code).charAt(0)==="4"||String(b.stock_code).charAt(0)==="8"?"北":"深");
  if(mkt==="沪" && b.one_hand_shares){
    var minS = Number(b.one_hand_shares)||0;
    var real = Number(b.per_hand_shares)||0;
    var cap = Math.round(real/100)*100; cap = cap<real? cap+100: cap;
    var line = "沪市一手党最低<b>"+minS+"</b>股 · 实际配售约<b>"+cap+"</b>股";
    if(b.price){ line += "<br><span class='grey'>按现价 "+fmt(b.price)+" 元，约需 <b>"+Math.round(cap*b.price)+"</b> 元</span>"; }
    return line;
  }
  if(b.one_hand_shares){
    var line = "一手党<b>"+Number(b.one_hand_shares)+"</b>股";
    if(b.price){ line += " · 按现价 "+fmt(b.price)+" 元，约需 <b>"+Math.round(Number(b.one_hand_shares)*b.price)+"</b> 元"; }
    return line;
  }
  return "-";
}
window.showD = function(code){
  var b = null;
  for(var i=0;i<DATA.length;i++){ if(String(DATA[i].stock_code)===code){ b=DATA[i]; break; } }
  if(!b) return;
  var mkt = b.market || (String(b.stock_code).charAt(0)==="6"?"沪":String(b.stock_code).charAt(0)==="4"||String(b.stock_code).charAt(0)==="8"?"北":"深");
  var sd = b.score_details || {};
  var dRows = "";
  var keys = Object.keys(sd);
  for(var i=0;i<keys.length;i++){
    var k=keys[i], v=sd[k]||{};
    dRows += "<div class='dit'><div class='dl'>"+k+"</div><div class='dv'>"+(v.score>0?"+":"")+(v.score||0)+(v.max>0?"/"+v.max:"")+"</div><div class='df'>"+(v.factor||"")+"</div></div>";
  }
  var ov = document.createElement("div");
  ov.className="overlay"; ov.id="dlg";
  ov.innerHTML =
  "<div class='dlg'>"+
    "<div class='dlg-h'><span>"+b.stock_name+" ("+b.stock_code+")</span><button class='x' onclick='window.closeD()'>×</button></div>"+
    "<div class='dlg-sub'>"+b.bond_name+" · "+(b.board||"-")+" · "+b.industry+" · "+b.progress+" · 评级 <span class='rating "+ratingCls(b.rating)+"'>"+(b.rating||"-")+"</span></div>"+
    "<div class='blk'><h4>抢权核心</h4><div class='dlg-grid'>"+
      "<div class='dit'><div class='dl'>总分</div><div class='dv' style='font-size:20px'>"+ (b.total_score||0) +"</div></div>"+
      "<div class='dit'><div class='dl'>操作建议</div><div class='dv' style='font-size:12px'>"+actionselHtml(b.action)+"</div></div>"+
      "<div class='dit'><div class='dl'>总发行规模</div><div class='dv'>"+fmt(b.total_scale)+" 亿</div></div>"+
      "<div class='dit'><div class='dl'>百元含权</div><div class='dv'>"+fmt(b.per_share_amount)+"</div></div>"+
      "<div class='dit'><div class='dl'>预估流通</div><div class='dv'>"+fmt(b.circ_mv)+" 亿</div></div>"+
      "<div class='dit'><div class='dl'>当前价</div><div class='dv'>"+fmt(b.price)+" 元</div></div>"+
    "</div>"+
    "<div class='dit' style='margin-top:10px'><div class='dl'>一手党</div><div class='dv'>"+oneHandDetail(b)+"</div></div>"+
    (b.action_reason?"<div class='dit' style='margin-top:8px'><div class='dl'>建议理由</div><div class='dv'>"+b.action_reason+"</div></div>":"")+
    (b.action_price?"<div class='dit' style='margin-top:8px'><div class='dl'>买卖参考</div><div class='dv'>"+b.action_price+"</div></div>":"")+
    "</div>"+
    "<div class='blk'><h4>进度日期</h4><div class='dlg-grid'>"+
      "<div class='dit'><div class='dl'>进度</div><div class='dv'>"+(b.progress||"-")+"</div></div>"+
      "<div class='dit'><div class='dl'>受理日期</div><div class='dv'>"+(b.registration_date||"-")+"</div></div>"+
      "<div class='dit'><div class='dl'>股权登记日</div><div class='dv'>"+(b.record_dt||"-")+"</div></div>"+
      "<div class='dit'><div class='dl'>通过日期</div><div class='dv'>"+(b.approval_date||"-")+"</div></div>"+
    "</div></div>"+
    "<div class='blk'><h4>评分明细</h4><div class='dlg-grid'>"+dRows+"</div></div>"+
  "</div>";
  document.body.appendChild(ov);
};
var actionselHtml = function(a){
  return "<span class='actionsel "+actionCls(a)+"'>"+(a||"-")+"</span>";
};
window.closeD = function(){ var e=document.getElementById("dlg"); if(e) e.remove(); };
window.sortB = function(f){ if(sortField===f) sortDesc=!sortDesc; else { sortField=f; sortDesc=true; } render(); };
document.addEventListener("click", function(e){
  var t = e.target.closest ? e.target.closest(".chip") : null;
  if(t && t.dataset.progress!==undefined){
    var all = document.querySelectorAll(".chip");
    for(var i=0;i<all.length;i++) all[i].classList.remove("active");
    t.classList.add("active");
    cur = t.dataset.progress;
    render();
  }
});
document.getElementById("q").addEventListener("input", render);
render();
</script>
</body>
</html>"""
    return (html
            .replace("__CHIPS__", "".join(chips))
            .replace("__TOTAL__", str(len(bonds)))
            .replace("__TIME__", time.strftime("%Y-%m-%d %H:%M:%S"))
            .replace("__DATA__", data_json))


def main() -> int:
    t0 = time.time()
    os.makedirs(DIST, exist_ok=True)
    print("[qiangquan] 开始抓取抢权评分数据...")

    bonds = []
    source = "live"
    try:
        bonds = asyncio.run(fetch_all_pending_detailed([]))
    except Exception as e:
        print(f"[qiangquan] 抓取失败: {e}")

    if bonds:
        bonds = [b for b in bonds if b.get("progress") not in EXCLUDE_PROGRESS]
        print(f"[qiangquan] 实时抓取 {len(bonds)} 条")
    elif os.path.exists(FALLBACK):
        try:
            with open(FALLBACK, "r", encoding="utf-8") as f:
                bonds = json.load(f)
            source = "fallback"
            print(f"[qiangquan] 使用仓库缓存 {len(bonds)} 条")
        except Exception as e:
            print(f"[qiangquan] 缓存读取失败: {e}")
            bonds = []
    else:
        print("[qiangquan] 无可用数据")

    if not bonds:
        print("[qiangquan] 无数据可生成")
        return 1

    # 排序：按总分降序（默认 view 排序亦是 total_score desc）
    bonds.sort(key=lambda x: -(x.get("total_score") or 0))

    payload = {
        "total": len(bonds),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "bonds": [_normalize(b) for b in bonds],
    }

    data_path = os.path.join(DIST, "qiangquan.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # 复制页面模板（Vue 页面 fetch 同目录 qiangquan.json，Vue 用本地库避免 CDN 加载失败）
    with open(TEMPLATE, "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace(CDN_BLOCK, LOCAL_VUE)
    html = html.replace('qiangquan.json?refresh=1', 'qiangquan.json')
    html = html.replace("./qiangquan.json", "qiangquan.json")
    with open(os.path.join(DIST, "qiangquan.html"), "w", encoding="utf-8") as f:
        f.write(html)

    # 待发可转债(抢权)页面：纯静态生成（原生 JS + 数据内嵌，零框架依赖，杜绝 {{ }} 乱码）
    iss_html = _build_issues_static(bonds)
    with open(os.path.join(DIST, "issues.html"), "w", encoding="utf-8") as f:
        f.write(iss_html)

    # 本地 Vue 库（qiangquan.html 评分看板仍用 Vue，随页面一起部署避免 CDN 依赖）
    shutil.copyfile(VUE_LIB, os.path.join(DIST, "vue.global.prod.js"))

    # 更新仓库内置兜底快照（供下次失败时回退）
    try:
        shutil.copyfile(data_path, FALLBACK)
        print("[qiangquan] 已更新兜底缓存")
    except Exception as e:
        print(f"[qiangquan] 更新兜底缓存失败: {e}")

    print(f"[qiangquan] 完成: {len(bonds)} 条，耗时 {time.time()-t0:.0f}s, 来源={source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())