"""可转债轮动策略引擎"""
import copy
from datetime import date, datetime


class FilterConfig:
    """统一过滤条件"""
    MAX_PREMIUM = 60.0           # 溢价率<60%
    MIN_PRICE = 100.0            # 价格≥100 (部分策略)
    MAX_PRICE_130 = 130.0        # 130三低价格上限
    MAX_PRICE_150 = 150.0        # 150三低价格上限
    MAX_REMAINING_SIZE = 5.0     # 剩余规模<5亿
    MAX_REMAINING_SIZE_SMALL = 3.0  # 次新规模<3亿
    MAX_REDEMPTION_DAYS = 8    # 强赎满足天数排除阈值
    MIN_REMAINING_YEARS = 1.0    # 剩余年限>=1年
    MIN_STOCK_PRICE = 2.0        # 正股最小股价


def _apply_common_filters(bonds: list[dict]) -> list[dict]:
    """应用通用过滤条件，返回符合条件的转债列表"""
    filtered = []
    for b in bonds:
        # 排除公告强赎（数据由集思录/公告源注入，默认 False 不误杀）
        if b.get("announced_redemption"):
            continue
        # 排除暂未上市的可转债（无上市日期，或上市日期晚于今天）
        ld = b.get("list_date")
        if ld is None:
            continue
        if isinstance(ld, date) and ld > date.today():
            continue
        # 排除强赎满足天数 >= 8 天（数据由公告源注入，默认 0 不误杀）
        if (b.get("redemption_days") or 0) >= FilterConfig.MAX_REDEMPTION_DAYS:
            continue
        # 排除信用等级低（A- 以下）
        VALID_RATINGS = {"AAA", "AA+", "AA", "AA-", "A+", "A", "A-"}
        rating = (b.get("credit_rating") or "").upper().strip()
        if rating and rating not in VALID_RATINGS:
            continue
        # 排除正股 ST
        if b.get("stock_st"):
            continue
        # 排除正股股价 < 2 元
        stock_price = b.get("stock_price") or 0
        if stock_price > 0 and stock_price < FilterConfig.MIN_STOCK_PRICE:
            continue
        # 排除净资产为负
        net_assets = b.get("net_assets")
        if net_assets is not None and net_assets < 0:
            continue
        # 排除剩余年限 < 1 年
        remaining_years = b.get("remaining_years")
        if remaining_years is not None and remaining_years < FilterConfig.MIN_REMAINING_YEARS:
            continue
        # 注：不再因「概念(concept)为空」排除债券。
        # concept 仅是题材标签，与转债是否可交易/可轮动无关；
        # 此前因该字段为空误杀了欧22/武进/盛泰/立高/严牌等正常在市转债。
        filtered.append(b)
    return filtered


def _unified_score(b: dict) -> float:
    """统一打分公式：价格 + 溢价率 + 剩余规模*10（数值越低越优）

    注意：数据库 premium_rate 已是「百分比数值」(如 58.9 表示 58.9%)，
    等同于「溢价率(小数) * 100」。因此这里直接用 premium_rate 参与计算，
    若再 *100 会把溢价项放大 100 倍、完全主导排序（例如 58.9*100=5890，
    远超价格与规模项），导致排序失真。即本函数即实现：
        价格 + (溢价率_小数) * 100 + 剩余规模 * 10
    """
    price = b.get("price") or 999
    premium = b.get("premium_rate") or 999
    size = b.get("remaining_size") or 999
    return price + premium + (size * 10)


def strategy_130_sandi(bonds: list[dict]) -> list[dict]:
    """130三低策略: 价格<130且≥100, 溢价率<60%, 剩余规模<5亿"""
    filtered = _apply_common_filters(bonds)
    candidates = []
    for b in filtered:
        price = b.get("price") or 999
        premium = b.get("premium_rate") or 999
        size = b.get("remaining_size") or 999
        if FilterConfig.MIN_PRICE <= price < FilterConfig.MAX_PRICE_130 \
           and premium < FilterConfig.MAX_PREMIUM \
           and size < FilterConfig.MAX_REMAINING_SIZE:
            b["_score"] = _unified_score(b)
            candidates.append(b)
    candidates.sort(key=lambda x: x["_score"])
    return candidates


def strategy_150_sandi(bonds: list[dict]) -> list[dict]:
    """150三低策略: 价格<150且≥100, 溢价率<60%, 剩余规模<5亿"""
    filtered = _apply_common_filters(bonds)
    candidates = []
    for b in filtered:
        price = b.get("price") or 999
        premium = b.get("premium_rate") or 999
        size = b.get("remaining_size") or 999
        if FilterConfig.MIN_PRICE <= price < FilterConfig.MAX_PRICE_150 \
           and premium < FilterConfig.MAX_PREMIUM \
           and size < FilterConfig.MAX_REMAINING_SIZE:
            b["_score"] = _unified_score(b)
            candidates.append(b)
    candidates.sort(key=lambda x: x["_score"])
    return candidates


def strategy_double_low(bonds: list[dict]) -> list[dict]:
    """双低策略: 价格≥100, 溢价率<60%, 剩余规模<5亿, 按 价格+溢价率(百分比) 排序（经典双低，不含规模项）

    说明：数据库 premium_rate 已是百分比数值(如 58.9)，故「价格+溢价率*100」(溢价率取小数)
    实际落地为 price + premium_rate，即经典双低值，不含剩余规模项。
    """
    filtered = _apply_common_filters(bonds)
    candidates = []
    for b in filtered:
        price = b.get("price") or 999
        premium = b.get("premium_rate") or 999
        size = b.get("remaining_size") or 999
        if price >= FilterConfig.MIN_PRICE \
           and premium < FilterConfig.MAX_PREMIUM \
           and size < FilterConfig.MAX_REMAINING_SIZE:
            b["_score"] = price + premium   # 经典双低值：价格 + 溢价率(百分比)，不含规模项
            candidates.append(b)
    candidates.sort(key=lambda x: x["_score"])
    return candidates


def strategy_low_price(bonds: list[dict]) -> list[dict]:
    """低价格策略: 单纯按价格升序排序（债性强优先）"""
    filtered = _apply_common_filters(bonds)
    candidates = [b for b in filtered if b.get("price") and b["price"] > 0]
    for b in candidates:
        b["_score"] = b.get("price") or 999
    candidates.sort(key=lambda x: x["_score"])
    return candidates


def strategy_low_premium(bonds: list[dict]) -> list[dict]:
    """低溢价策略: 价格≥100, 按溢价率升序排序（跟正股更紧优先）"""
    filtered = _apply_common_filters(bonds)
    candidates = []
    for b in filtered:
        price = b.get("price") or 0
        premium = b.get("premium_rate")
        if price >= FilterConfig.MIN_PRICE and premium is not None:
            b["_score"] = premium
            candidates.append(b)
    candidates.sort(key=lambda x: x["_score"])
    return candidates


def _apply_ytm_filters(bonds: list[dict]) -> list[dict]:
    """高到期收益率(持有到期)策略专用过滤：仅剔除「不可交易/无意义」的标的，不过度过滤质量。

    与轮动策略不同，高收益债往往伴随低评级/正股ST/净资产为负等「风险信号」，
    这些正是其高 YTM 的来由，若按轮动质量过滤会把最该看的标的藏起来。故此处只剔除：
      - 暂未上市（list_date 为空或晚于今天）
      - 已公告/临近强赎（会被提前赎回，持有到期逻辑失效）
      - 剩余年限过短（<0.5 年，YTM 无意义）
    """
    filtered = []
    for b in bonds:
        ld = b.get("list_date")
        if ld is None:
            continue
        if isinstance(ld, date) and ld > date.today():
            continue
        if b.get("announced_redemption"):
            continue
        if (b.get("redemption_days") or 0) >= FilterConfig.MAX_REDEMPTION_DAYS:
            continue
        ry = b.get("remaining_years")
        if ry is not None and ry < 0.5:
            continue
        filtered.append(b)
    return filtered


def strategy_high_ytm(bonds: list[dict]) -> list[dict]:
    """高到期收益率策略: 按税前年化到期收益率(ytm_before_tax，券商 APP 口径/东财最新到期收益率)从高到低排序。
    使用轻量过滤（见 _apply_ytm_filters），保留低评级/正股ST等高风险高收益标的。"""
    filtered = _apply_ytm_filters(bonds)
    candidates = [b for b in filtered if b.get("ytm_before_tax") is not None]
    for b in candidates:
        b["_score"] = b.get("ytm_before_tax") or 0
    candidates.sort(key=lambda x: x["_score"], reverse=True)
    return candidates


def strategy_cixin_sandi(bonds: list[dict]) -> list[dict]:
    """次新三低策略: 价格<150, 溢价率<60%, 流通规模<3亿, 未到转股期"""
    filtered = _apply_common_filters(bonds)
    today = date.today()
    candidates = []
    for b in filtered:
        price = b.get("price") or 999
        premium = b.get("premium_rate") or 999
        size = b.get("remaining_size") or 999
        # 未到转股期
        conv_start = b.get("conversion_period_start")
        if conv_start and isinstance(conv_start, date):
            if conv_start <= today:
                continue
        if price < FilterConfig.MAX_PRICE_150 \
           and premium < FilterConfig.MAX_PREMIUM \
           and size < FilterConfig.MAX_REMAINING_SIZE_SMALL:
            b["_score"] = _unified_score(b)
            candidates.append(b)
    candidates.sort(key=lambda x: x["_score"])
    return candidates


ALL_STRATEGIES = {
    "130_sandi":        ("130三低", strategy_130_sandi),
    "150_sandi":        ("150三低", strategy_150_sandi),
    "double_low":       ("双低", strategy_double_low),
    "low_price":        ("低价格", strategy_low_price),
    "low_premium":      ("低溢价", strategy_low_premium),
    "high_ytm":         ("高到期收益率", strategy_high_ytm),
    "cixin_sandi":      ("次新三低", strategy_cixin_sandi),
}


def run_all_strategies(bonds: list[dict]) -> dict:
    """运行所有策略，返回 {策略key: [债券列表]}

    每个策略都拿到 bonds 的一份独立深拷贝，避免各策略直接在原 dict 上写
    _score 时互相覆盖（否则后跑的策略会污染先跑策略的排序分数）。
    """
    results = {}
    for key, (name, func) in ALL_STRATEGIES.items():
        results[key] = {"name": name, "bonds": func(copy.deepcopy(bonds))}
    return results
