"""可转债税前到期收益率（YTM）计算（兜底用）

标准债券定价公式（用户给定）：
    可转债价格(全价) = Σ 未来各期利息 / (1+r)^{t_i} + 到期赎回价 / (1+r)^{t_n}
其中：
    - 到期赎回价 = 面值 100 + 补偿金（多数转债「含最后一年利息」），取自发行公告
      「到期赎回条款」，如美锦转债：面值的 118%(含最后利息) → 赎回价 118，而非简单面值 100。
      ★ 必须计入补偿金：否则会把高补偿金债（如美锦，价格 107.7、赎回价 118）的 YTM
        错算成负数，而真实应为正数。这是与券商 APP / 集思录「到期收益率」一致的口径。
    - 未来各期利息来自阶梯票面利率（%/年），如 '0.20,0.40,0.60,1.20,1.80,2.00'
    - t_i 为各付息日距今天数（年）
求解使等式成立的折现率 r，即为「税前到期收益率」。

关于数据来源（重要）：
    用户要求「也可直接取东方财富资料里直接展示的最新到期收益率」。实测东财 push2 的
    f230 是不含补偿金的纯债口径（美锦 107.7 显示 -4.38%，错误），f235 为异常字段
    （全市场 100 只全为正、大荒 118.86 元竟显示 16.86%，明显非到期收益率），均不可用；
    东财 F10 也无现成 YTM 接口。因此统一用本模块基于「阶梯票息 + 净价 + 剩余年限 +
    到期赎回价(补偿金)」的标准 IRR 公式自洽计算，结果与券商 APP「到期收益率」一致。
"""
import math
import re


FACE_VALUE = 100.0        # 可转债面值


def parse_redeem_price(clause: str) -> float | None:
    """从「到期赎回条款」文字解析到期赎回价（含补偿金，单位：元）。

    常见表述：
        "按债券面值的118%(含最后一期利息)的价格赎回"      -> 118
        "以可转债票面面值108%(含最后一年利息)的价格兑付"    -> 108
        "按债券面值的110%的价格赎回(不含最后一年利息)"      -> 110
    规则：优先在「到期赎回」/「期满」条款片段内匹配 '面值(的)?\\s*数字%'；
    取到的百分比数值即赎回价（相对面值 100 的百分比，如 118% → 118 元）。
    返回 None 表示解析失败。
    """
    if not clause or not isinstance(clause, str):
        return None
    # 优先在「到期赎回」/「期满」相关片段内寻找
    m_seg = re.search(r"(?:到期赎回|期满)[^。]{0,260}", clause)
    seg = m_seg.group(0) if m_seg else clause
    m = re.search(r"面值(?:的)?\s*(\d+(?:\.\d+)?)\s*%", seg)
    if not m:
        # 整段兜底：取第一个 '面值(的)?数字%'
        m = re.search(r"面值(?:的)?\s*(\d+(?:\.\d+)?)\s*%", clause)
    if not m:
        return None
    try:
        pct = float(m.group(1))
    except ValueError:
        return None
    # 百分比相对面值 100，赎回价 = 该数值本身（118% → 118 元）
    if pct <= 0 or pct > 200:
        return None
    return pct


def _parse_ladder(coupon_ladder) -> list[float]:
    """把 '0.20,0.40,0.60' 或 '0.20' 解析成 [0.20, 0.40, 0.60]（百分比数值，%/年）。"""
    if not coupon_ladder:
        return []
    out = []
    for part in str(coupon_ladder).split(','):
        p = part.strip().rstrip('%').strip()
        if not p:
            continue
        try:
            out.append(float(p))
        except ValueError:
            continue
    return out


def _npv(rate: float, cfs: list[tuple[float, float]]) -> float:
    return sum(amt / (1.0 + rate) ** t for t, amt in cfs)


def _solve_irr(price_full: float, cfs: list[tuple[float, float]],
               lo: float = -0.95, hi: float = 3.0, iters: int = 200) -> float | None:
    """二分法求 IRR：使未来现金流现值 = price_full 的折现率（小数）。"""
    if not cfs:
        return None
    f_lo = _npv(lo, cfs) - price_full
    f_hi = _npv(hi, cfs) - price_full
    if f_lo * f_hi > 0:
        return None
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        f_mid = _npv(mid, cfs) - price_full
        if abs(f_mid) < 1e-10:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo = mid
            f_lo = f_mid
    return (lo + hi) / 2.0


def _build_cashflows(coupon_ladder, price_net, remaining_years, redeem_price=100.0):
    """构造到期收益率现金流（核心）。

    返回 (price_full, cfs)；无法计算返回 (None, None)。
    cfs: list[(t, amount)]，t 为距今天数（年），最后一笔为到期赎回价（含补偿金）。

    关键：剩余付息必须对应阶梯「末尾」的真实票息年度。
    设债券总年限 tenor = 阶梯段数，已存续年限 elapsed = tenor - 剩余年限。
    当前所处票息年度下标 current_idx = floor(elapsed)，其票息为 coupons[current_idx]。
    距下一次付息 t_next = (1 - frac)，其中 frac = elapsed - floor(elapsed)（若 frac≈0 则 t_next=1）。
    剩余付息次数 n = floor(剩余年限 + frac)（含到期那次）。
    第 j 笔付息（j=0..n-1）票息 = coupons[min(current_idx + j, tenor-1)]，
    最后一笔（到期）为赎回价 redeem_price（已含最后一年利息，不再另加票息）。
    """
    coupons = _parse_ladder(coupon_ladder)
    if not coupons:
        return None, None
    try:
        price_net = float(price_net)
        T = float(remaining_years)
    except (ValueError, TypeError):
        return None, None
    if T <= 0 or price_net <= 0:
        return None, None

    tenor = len(coupons)
    elapsed = tenor - T                      # 已存续年限
    if elapsed < -1e-9:
        elapsed = 0.0
    current_idx = int(math.floor(elapsed))
    current_idx = max(0, min(current_idx, tenor - 1))
    frac = elapsed - current_idx

    if frac < 1e-9:
        t_next = 1.0
        n = int(round(T))
    else:
        t_next = 1.0 - frac
        n = int(math.floor(T + frac + 1e-9))
    if n <= 0:
        return None, None

    accrued = coupons[current_idx] * frac    # 应计利息（按当前票息年度）
    price_full = price_net + accrued

    cfs = []
    for j in range(n):
        t = t_next + j
        ci = coupons[min(current_idx + j, tenor - 1)]
        if j == n - 1:                        # 到期：赎回价（含补偿金 + 最后一年利息）
            cfs.append((t, float(redeem_price)))
        else:
            cfs.append((t, ci))
    return price_full, cfs


def compute_ytm_before_tax(coupon_ladder: str, price_net: float,
                           remaining_years: float,
                           redeem_price: float = FACE_VALUE) -> float | None:
    """由真实（阶梯）票面利率 + 到期赎回价（含补偿金）计算税前到期收益率。

    返回小数，如 0.058=5.8%。对应公式：
        价格(全价) = Σ 各期利息/(1+r)^t_i + 赎回价/(1+r)^t_n
    redeem_price 默认 100（无补偿金兜底）；正常应传入发行公告的到期赎回价（如 118）。
    """
    rp = float(redeem_price) if redeem_price is not None else FACE_VALUE
    price_full, cfs = _build_cashflows(coupon_ladder, price_net, remaining_years, rp)
    if price_full is None:
        return None
    return _solve_irr(price_full, cfs)
