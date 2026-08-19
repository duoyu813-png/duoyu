"""集思录封闭基金 / 定开基金 数据抓取

数据源：https://www.jisilu.cn/data/cf/cf_list/
- 游客（未配置 cookie）仅返回前 20 条（全量共 31 条），配置 JISILU_COOKIE 可解锁全部。
- 返回 JSON：{"page":1, "rows":[{"id":..,"cell":{..}}], "total":20, "warn":"..."}
- cell 关键字段：
    fund_id           基金代码
    fund_nm           名称
    price             现价
    increase_rt       涨跌%
    net_value         单位净值
    discount_rt       折价率(%) —— 定开基金集思录显示为 '-'
    annualize_dscnt_rt 折价率年化(%) —— 定开显示为 '-'
    left_year         剩余年限(年) —— 对定开即"到下次开放"的年数
    maturity_dt       到期日 / 封闭期截止日 (YYYY-MM-DD)
    volume            成交额(万元)
    amount_outstanding 场内份额
    type_cd          类型: D=定开, 其他(如 C)=传统封闭
    issue_dt          发行日
    list_dt           上市日
    notes             备注(如 '2年定开(9月1日)')

说明：集思录对"定开基金"不给出折价率，但 (净值 - 现价)/净值 即真实折价，
本模块自行计算；折价率年化 = 折价率 / 剩余年限。
"""
from datetime import date

import requests


class JisiluScraper:
    CF_LIST_URL = "https://www.jisilu.cn/data/cf/cf_list/"
    CB_LIST_URL = "https://www.jisilu.cn/data/cb/cb_list_new/"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Referer": "https://www.jisilu.cn/data/cf/",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    CB_HEADERS = {
        "User-Agent": HEADERS["User-Agent"],
        "Referer": "https://www.jisilu.cn/data/cb/",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }

    # 游客模式被截断标记（供刷新脚本打印提示）
    _last_guest_truncated = False

    @classmethod
    def _cookie(cls) -> str:
        try:
            from config import Config
            return getattr(Config, "JISILU_COOKIE", "") or ""
        except Exception:
            return ""

    @classmethod
    def fetch_closed_funds(cls) -> list[dict]:
        """返回封基/定开基金列表，字段对齐 _save_fund_data 所需。"""
        from scrapers.data_merger import safe_float, safe_str  # 惰性导入，避免与 data_merger 循环依赖
        cls._last_guest_truncated = False
        headers = dict(cls.HEADERS)
        cookie = cls._cookie()
        if cookie:
            headers["Cookie"] = cookie
        try:
            r = requests.get(cls.CF_LIST_URL, headers=headers, timeout=15)
            r.raise_for_status()
            d = r.json()
        except Exception as e:
            print(f"[Jisilu] 封基接口请求失败: {e}")
            return []

        rows = d.get("rows") or []
        total = d.get("total") or len(rows)
        if len(rows) < total:
            cls._last_guest_truncated = True
            print(f"[Jisilu] 游客模式仅返回 {len(rows)}/{total} 条"
                  f"（配置 config.JISILU_COOKIE 可解锁全部）")

        out = []
        for it in rows:
            c = it.get("cell", {}) if isinstance(it, dict) else {}
            code = safe_str(c.get("fund_id"))
            if not code:
                continue
            name = safe_str(c.get("fund_nm"))
            price = safe_float(c.get("price"))
            nav = safe_float(c.get("net_value"))

            # 折价率：定开基金集思录不显示('-')，自行用 (净值-价)/净值 计算
            disc = safe_float(c.get("discount_rt"))
            if disc is None and nav and price is not None and nav > 0:
                disc = round((nav - price) / nav * 100, 2)

            remaining = safe_float(c.get("left_year"))

            # 折价率年化 = 折价率 / 剩余年限
            ann = safe_float(c.get("annualize_dscnt_rt"))
            if ann is None and disc is not None and remaining and remaining > 0:
                ann = round(disc / remaining, 2)

            # 到期日 / 封闭期截止日
            mdt = c.get("maturity_dt")
            maturity = None
            if mdt and mdt not in ("-", ""):
                try:
                    maturity = date.fromisoformat(str(mdt)[:10])
                except (ValueError, TypeError):
                    maturity = None

            ftype = c.get("type_cd")
            ftype_cn = {"D": "定开", "C": "封闭"}.get(ftype, ftype or "")

            out.append({
                "code": code,
                "name": name,
                "price": price,
                "change_pct": safe_float(c.get("increase_rt")),
                "nav": nav,
                "discount_rate": disc,
                "discount_annual": ann,
                "remaining_years": remaining,
                "maturity_date": maturity,          # date 对象或 None
                "last_volume": safe_float(c.get("volume")),   # 万元
                "total_cap": safe_float(c.get("amount_outstanding")),
                "fund_type": ftype_cn,
                "notes": safe_str(c.get("notes")),
            })
        return out

    @classmethod
    def fetch_cb_coupon_rates(cls) -> dict:
        """返回 {转债代码: 票面利率阶梯字符串}，用于计算税后 YTM。

        - 数据源：集思录 cb_list_new（唯一稳定提供可转债票面利率的免费源）。
        - 游客（未配置 cookie）仅返回前 30 只；配置 config.JISILU_COOKIE 解锁全部 ~300+ 只。
        - coupon_rate 形如 '0.30'（单值）或阶梯式 '0.20,0.40,0.60,1.20,1.80,2.00'（%/年）。
          完整阶梯会原样返回（不取首段），因为可转债票息逐年递增，税后 YTM 必须用各期票息。
        """
        headers = dict(cls.CB_HEADERS)
        cookie = cls._cookie()
        if cookie:
            headers["Cookie"] = cookie
        from scrapers.data_merger import safe_float, safe_str  # 惰性导入，避免与 data_merger 循环依赖
        out: dict[str, str] = {}
        try:
            r = requests.post(cls.CB_LIST_URL, headers=headers, timeout=20, data={"___jb": ""})
            r.raise_for_status()
            d = r.json()
        except Exception as e:
            print(f"[Jisilu] 转债票息接口请求失败: {e}")
            return out
        rows = d.get("rows") or []
        total = d.get("total") or len(rows)
        if len(rows) < total:
            print(f"[Jisilu] 游客模式仅返回 {len(rows)}/{total} 只转债票息"
                  f"（配置 config.JISILU_COOKIE 可解锁全部）")
        for it in rows:
            c = it.get("cell", {}) if isinstance(it, dict) else {}
            code = safe_str(c.get("bond_id"))
            if not code:
                continue
            raw = safe_str(c.get("coupon_rate"))
            if not raw:
                continue
            # 校验：至少含一个可解析的正数票息段
            segs = [p.strip().rstrip("%").strip() for p in raw.split(",")]
            ok = False
            for p in segs:
                try:
                    if float(p) > 0:
                        ok = True
                        break
                except ValueError:
                    continue
            if not ok:
                continue
            out[code] = raw
        return out
