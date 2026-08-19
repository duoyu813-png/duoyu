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

    # 待发可转债(抢权)页面：按审核进度分组 + 排序 + 搜索（学习 adile.cn 交互）
    with open(ISSUES_TEMPLATE, "r", encoding="utf-8") as f:
        iss_html = f.read()
    iss_html = iss_html.replace(CDN_BLOCK, LOCAL_VUE)
    with open(os.path.join(DIST, "issues.html"), "w", encoding="utf-8") as f:
        f.write(iss_html)

    # 本地 Vue 库（随页面一起部署，避免 CDN 依赖）
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