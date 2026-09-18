/* ============================================================
 * 穿透财报分析 · 前端规则引擎
 * 蒸馏自邹佩轩《穿透财报》《穿透估值》《穿透叙事》
 * 数据：东方财富 F10（datacenter.eastmoney.com）+ 东财/腾讯行情
 * ============================================================ */
(function () {
  'use strict';

  var EM = 'https://datacenter.eastmoney.com/securities/api/data/v1/get';
  var PUSH = 'https://push2delay.eastmoney.com/api/qt/stock/get';
  var PUSH2 = 'https://push2.eastmoney.com/api/qt/stock/get';
  var GTIMG = 'https://qt.gtimg.cn/q=';
  var R = 0.10; // A股权益折现率

  /* ---------- 工具 ---------- */
  function num(v) { var n = parseFloat(v); return isFinite(n) ? n : 0; }
  function yi(v) { return v / 1e8; }
  function f2(v) { if (!isFinite(v)) return '—'; return (Math.round(v * 100) / 100).toFixed(2); }
  function pct(v, d) { if (!isFinite(v)) return '—'; return (Math.round(v * 1000) / 10).toFixed(d === undefined ? 1 : d) + '%'; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function div(a, b) { return (b === 0 || !isFinite(b) || !isFinite(a)) ? null : a / b; }
  function mdOf(d) { return String(d || '').slice(5, 10); } // MM-DD
  function yrOf(d) { return parseInt(String(d || '').slice(0, 4), 10); }

  /* ---------- 代码归一化 ---------- */
  function normalizeCode(raw) {
    var s = String(raw || '').trim().replace(/\s+/g, '');
    var m = s.match(/(\d{6})/);
    if (!m) return null;
    var code = m[1], mk, suffix;
    if (/^(60|68|9|11)/.test(code)) { mk = '1'; suffix = 'SH'; }
    else if (/^(00|30|12|15|16|18)/.test(code)) { mk = '0'; suffix = 'SZ'; }
    else if (/^(4|8|92)/.test(code)) { mk = '0'; suffix = 'BJ'; }
    else { mk = '1'; suffix = 'SH'; }
    return { code: code, secid: mk + '.' + code, secucode: code + '.' + suffix, market: suffix, gt: (suffix === 'SH' ? 'sh' : suffix === 'SZ' ? 'sz' : 'bj') + code };
  }

  /* ---------- 网络 ---------- */
  function jget(url) {
    return fetch(url, { credentials: 'omit' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }
  function gbget(url) {
    return fetch(url, { credentials: 'omit' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.arrayBuffer();
    }).then(function (buf) {
      try { return new TextDecoder('gbk').decode(buf); } catch (e) { return new TextDecoder('utf-8').decode(buf); }
    });
  }

  function emRows(reportName, secucode, pageSize) {
    var q = 'reportName=' + reportName + '&columns=ALL&filter=' + encodeURIComponent('(SECUCODE="' + secucode + '")') +
      '&pageSize=' + (pageSize || 40) + '&pageNumber=1&sortColumns=REPORT_DATE&sortTypes=-1&source=HSF10&client=PC';
    return jget(EM + '?' + q).then(function (d) {
      var node = d && (d.result || d.data);
      var list = (node && (node.data || node.list)) || [];
      return list;
    });
  }

  /* ---------- 行情 ---------- */
  function fetchQuote(nc) {
    var fields = 'f43,f57,f58,f116,f117,f162,f163,f167,f168,f169,f170';
    var url = PUSH + '?secid=' + nc.secid + '&fields=' + fields;
    return jget(url).then(function (d) {
      var o = (d && d.data) || {};
      if (!o.f58) throw new Error('行情返回为空');
      return {
        name: o.f58, price: num(o.f43) / 100, chg: num(o.f170) / 100,
        pe_dyn: num(o.f162) / 100, pe_static: num(o.f163) / 100, pb: num(o.f167) / 100,
        mktcap: num(o.f116), float_cap: num(o.f117), turnover: num(o.f168) / 100,
        pe_ttm: null, src: '东财'
      };
    }).catch(function () {
      return jget(PUSH2 + '?secid=' + nc.secid + '&fields=' + fields).then(function (d) {
        var o = (d && d.data) || {};
        return {
          name: o.f58 || nc.code, price: num(o.f43) / 100, chg: num(o.f170) / 100,
          pe_dyn: num(o.f162) / 100, pe_static: num(o.f163) / 100, pb: num(o.f167) / 100,
          mktcap: num(o.f116), float_cap: num(o.f117), turnover: num(o.f168) / 100,
          pe_ttm: null, src: '东财'
        };
      });
    }).catch(function () {
      // 腾讯兜底（GBK）
      return gbget(GTIMG + nc.gt).then(function (txt) {
        var m = txt.match(/="([^"]+)"/);
        if (!m) throw new Error('行情解析失败');
        var a = m[1].split('~');
        return {
          name: a[1], price: num(a[3]), chg: num(a[32]),
          pe_dyn: null, pe_static: null, pe_ttm: num(a[39]), pb: num(a[46]),
          mktcap: num(a[45]) * 1e8, float_cap: num(a[44]) * 1e8, turnover: num(a[38]),
          src: '腾讯'
        };
      });
    }).then(function (q) { return enrichTTM(q, nc); });
  }

  function fetchHS300PE() {
    return gbget(GTIMG + 'sh000300').then(function (txt) {
      var m = txt.match(/="([^"]+)"/);
      if (!m) return null;
      var a = m[1].split('~');
      return { name: a[1], point: num(a[3]), pe: num(a[39]) };
    }).catch(function () { return null; });
  }

  /* 腾讯行情补 PE(TTM)：东财 push2 只给动态/静态 PE */
  function enrichTTM(q, nc) {
    return gbget(GTIMG + nc.gt).then(function (txt) {
      var m = txt.match(/="([^"]+)"/);
      if (!m) return q;
      var a = m[1].split('~');
      var ttm = num(a[39]);
      if (ttm > 0) q.pe_ttm = ttm;
      if (!q.pb && num(a[46]) > 0) q.pb = num(a[46]);
      if (!q.mktcap && num(a[45]) > 0) q.mktcap = num(a[45]) * 1e8;
      if (!q.float_cap && num(a[44]) > 0) q.float_cap = num(a[44]) * 1e8;
      return q;
    }).catch(function () { return q; });
  }

  /* ---------- 财报抓取 ---------- */
  function fetchAll(nc) {
    return Promise.all([
      emRows('RPT_F10_FINANCE_GINCOME', nc.secucode, 40),
      emRows('RPT_F10_FINANCE_GBALANCE', nc.secucode, 40),
      emRows('RPT_F10_FINANCE_GCASHFLOW', nc.secucode, 40),
      emRows('RPT_F10_FINANCE_MAINFINADATA', nc.secucode, 40)
    ]).then(function (r) {
      return { income: r[0], balance: r[1], cashflow: r[2], main: r[3] };
    });
  }

  /* ---------- 指标计算 ---------- */
  var ANNU = { '年报': 1, '中报': 2, '一季报': 4, '三季报': 4 / 3, '一季': 4, '三季': 4 / 3, '半年': 2 };

  function buildSeries(raw) {
    var balBy = {}, incBy = {}, cfBy = {}, mainBy = {};
    raw.balance.forEach(function (r) { balBy[r.REPORT_DATE] = r; });
    raw.income.forEach(function (r) { incBy[r.REPORT_DATE] = r; });
    raw.cashflow.forEach(function (r) { cfBy[r.REPORT_DATE] = r; });
    raw.main.forEach(function (r) { mainBy[r.REPORT_DATE] = r; });

    var dates = Object.keys(incBy).filter(function (d) { return balBy[d]; }).sort();
    var out = [];
    for (var i = 0; i < dates.length; i++) {
      var d = dates[i], b = balBy[d] || {}, c = incBy[d] || {}, f = cfBy[d] || {}, mi = mainBy[d] || {};
      var annu = ANNU[c.REPORT_TYPE] || 1;
      // 去年同期
      var prev = null, py = yrOf(d) - 1, pmd = mdOf(d);
      for (var j = 0; j < dates.length; j++) {
        if (yrOf(dates[j]) === py && mdOf(dates[j]) === pmd) { prev = out.filter(function (x) { return x.date === dates[j]; })[0] || null; break; }
      }
      var p = prev || {};

      var m = {
        date: d, type: c.REPORT_TYPE, label: c.REPORT_DATE_NAME || String(d).slice(0, 10), annu: annu,
        opinion: b.OPINION_TYPE || c.OPINION_TYPE || f.OPINION_TYPE || '',
        // 利润表
        revenue: num(c.OPERATE_INCOME || c.TOTAL_OPERATE_INCOME),
        cogs: num(c.OPERATE_COST),
        parent_np: num(c.PARENT_NETPROFIT),
        deduct_np: num(c.DEDUCT_PARENT_NETPROFIT),
        netprofit: num(c.NETPROFIT),
        minority_np: num(c.MINORITY_INTEREST),
        total_profit: num(c.TOTAL_PROFIT),
        invest_income: num(c.INVEST_INCOME),
        other_income: num(c.OTHER_INCOME),
        finance_expense: num(c.FINANCE_EXPENSE),
        sale_expense: num(c.SALE_EXPENSE),
        manage_expense: num(c.MANAGE_EXPENSE),
        rd_expense: num(c.RESEARCH_EXPENSE || c.ME_RESEARCH_EXPENSE),
        impair: num(c.ASSET_IMPAIRMENT_LOSS) + num(c.CREDIT_IMPAIRMENT_LOSS),
        fv_change: num(c.FAIRVALUE_CHANGE_INCOME),
        eps: num(c.BASIC_EPS),
        // 资产负债表
        cash: num(b.MONETARYFUNDS),
        ar: num(b.ACCOUNTS_RECE) + num(b.NOTE_RECE) + num(b.CONTRACT_ASSET),
        ar_pure: num(b.ACCOUNTS_RECE),
        note_rece: num(b.NOTE_RECE),
        contract_asset: num(b.CONTRACT_ASSET),
        inventory: num(b.INVENTORY),
        prepay: num(b.PREPAYMENT),
        other_rece: num(b.TOTAL_OTHER_RECE) || num(b.OTHER_RECE),
        other_cur: num(b.OTHER_CURRENT_ASSET),
        other_nc: num(b.OTHER_NONCURRENT_ASSET),
        fixed: num(b.FIXED_ASSET),
        cip: num(b.CIP),
        intang: num(b.INTANGIBLE_ASSET),
        goodwill: num(b.GOODWILL),
        dta: num(b.DEFER_TAX_ASSET),
        dtl: num(b.DEFER_TAX_LIAB),
        total_assets: num(b.TOTAL_ASSETS),
        total_liab: num(b.TOTAL_LIABILITIES),
        parent_equity: num(b.TOTAL_PARENT_EQUITY),
        minority_equity: num(b.MINORITY_EQUITY),
        total_equity: num(b.TOTAL_EQUITY),
        perpetual: num(b.PERPETUAL_BOND) + num(b.PREFERRED_SHARES),
        contract_liab: num(b.CONTRACT_LIAB) + num(b.ADVANCE_RECEIVABLES),
        ap: num(b.ACCOUNTS_PAYABLE) + num(b.NOTE_PAYABLE),
        ibd: num(b.SHORT_LOAN) + num(b.NONCURRENT_LIAB_1YEAR) + num(b.LONG_LOAN) + num(b.BOND_PAYABLE) +
             num(b.LEASE_LIAB) + num(b.SHORT_BOND_PAYABLE) + num(b.SHORT_FIN_PAYABLE),
        short_loan: num(b.SHORT_LOAN),
        long_loan: num(b.LONG_LOAN),
        // 现金流量表
        cfo: num(f.NETCASH_OPERATE),
        cfi: num(f.NETCASH_INVEST),
        cff: num(f.NETCASH_FINANCE),
        sales_cash: num(f.SALES_SERVICES),
        capex: num(f.CONSTRUCT_LONG_ASSET),
        // 主要指标
        roe: num(mi.ROEJQ),
        roe_kf: num(mi.ROEKCJQ),
        np_yoy: num(mi.PARENTNETPROFITTZ),
        rev_yoy: num(mi.OI_YOYRATIO_PK)
      };
      // 派生
      m.gross_margin = div(m.revenue - m.cogs, m.revenue);
      m.net_ibd = m.ibd - m.cash;
      m.fcf = m.cfo - m.capex;
      m.cfo_to_np = div(m.cfo, m.parent_np);
      m.sales_cash_ratio = div(m.sales_cash, m.revenue);
      m.capex_to_cfo = div(m.capex, m.cfo);
      m.deduct_ratio = div(m.deduct_np, m.parent_np);
      m.minority_np_ratio = div(m.minority_np, m.netprofit);
      m.minority_eq_ratio = div(m.minority_equity, m.total_equity);
      m.perpetual_ratio = div(m.perpetual, m.total_equity);
      m.invest_ratio = div(m.invest_income, m.total_profit);
      m.other_income_ratio = div(m.other_income, m.total_profit);
      m.goodwill_ratio = div(m.goodwill, m.parent_equity);
      m.other_asset_ratio = div(m.other_rece + m.other_cur + m.other_nc, m.total_assets);
      m.cip_ratio = div(m.cip, m.total_assets);
      m.ibd_to_assets = div(m.ibd, m.total_assets);
      m.cash_to_ibd = div(m.cash, m.ibd);
      m.cash_ratio = div(m.cash, m.total_assets);
      m.finexp_to_ibd = div(m.finance_expense, m.ibd);
      m.debt_ratio = div(m.total_liab, m.total_assets);
      m.impair_to_pbt = div(m.impair, m.total_profit);
      m.net_ibd = m.ibd - m.cash;
      // 周转率（年化）
      if (p.date) {
        var avgAr = (m.ar + p.ar) / 2, avgInv = (m.inventory + p.inventory) / 2, avgFix = ((m.fixed + m.cip + m.intang) + (p.fixed + p.cip + p.intang)) / 2;
        m.ar_turnover = div(m.revenue * m.annu, avgAr);
        m.inv_turnover = div(m.cogs * m.annu, avgInv);
        m.fixed_turnover = div(m.revenue * m.annu, avgFix);
        m.ar_turnover_prev = p.ar_turnover || null;
        m.inv_turnover_prev = p.inv_turnover || null;
        m.gm_prev = p.gross_margin || null;
        // 递延所得税二阶导
        m.dta_delta_ratio = div(m.dta - p.dta, m.revenue);
        m.dtl_delta_ratio = div(m.dtl - p.dtl, m.revenue);
        m.dta_delta_prev = p.dta_delta_ratio || null;
        m.dtl_delta_prev = p.dtl_delta_ratio || null;
        m.rev_yoy_calc = div(m.revenue - p.revenue, p.revenue);
        m.np_yoy_calc = div(m.parent_np - p.parent_np, p.parent_np);
        m.inv_yoy_calc = div(m.inventory - p.inventory, p.inventory);
        m.prepay_yoy_calc = div(m.prepay - p.prepay, p.prepay);
        m.contract_liab_yoy = div(m.contract_liab - p.contract_liab, p.contract_liab);
        m.ar_turnover_chg = (p.ar_turnover && m.ar_turnover) ? div(m.ar_turnover - p.ar_turnover, p.ar_turnover) : null;
        m.inv_turnover_chg = (p.inv_turnover && m.inv_turnover) ? div(m.inv_turnover - p.inv_turnover, p.inv_turnover) : null;
        m.gm_chg = (p.gross_margin != null && m.gross_margin != null) ? (m.gross_margin - p.gross_margin) : null;
      }
      out.push(m);
    }
    return out;
  }

  /* ---------- 规则库 ---------- */
  function flag(id, level, title, detail, why) { return { id: id, level: level, title: title, detail: detail, why: why }; }

  function runRules(s) {
    var L = s[s.length - 1], out = [];
    if (!L) return out;
    var std = !L.opinion || /标准无保留/.test(L.opinion);

    // ---- 模块 C：舞弊 ----
    if (!std) out.push(flag('C-12', 'red', '审计意见非标', '意见类型：' + (L.opinion || '未知'), '重视审计报告，警惕非标准事项段。非标意见是舞弊的一票否决项。'));

    if (L.ar_turnover_chg != null && L.ar_turnover_chg < -0.30 && L.gm_chg != null && L.gm_chg > 0.01)
      out.push(flag('C-01', 'red', '应收周转率骤降 + 毛利率上升（背离）',
        '应收周转率 ' + f2(L.ar_turnover_prev) + ' → ' + f2(L.ar_turnover) + '（' + pct(L.ar_turnover_chg) + '），毛利率 ' + pct(L.gm_prev) + ' → ' + pct(L.gross_margin),
        '虚增收入→虚增应收→周转率下降；同时收入虚增摊薄成本→毛利率反常上升。二者同时发生是最经典的收入舞弊指纹。'));

    if (L.rev_yoy_calc != null && L.rev_yoy_calc > 0.50 && L.inv_yoy_calc != null && L.inv_yoy_calc < 0.10 && L.inv_turnover_chg != null && L.inv_turnover_chg > 0.40)
      out.push(flag('C-02', 'red', '存货周转率异常上升（收入暴增而存货不增）',
        '营收 ' + pct(L.rev_yoy_calc) + '，存货 ' + pct(L.inv_yoy_calc) + '，存货周转率 ' + pct(L.inv_turnover_chg),
        '虚增收入时公司常同步虚增成本，但忘记虚增存货，导致存货不增、周转率飙升。这是等比造假最容易露馅的地方。'));

    if (L.cash_to_ibd != null && L.cash_to_ibd > 0.5 && L.ibd_to_assets > 0.25 && L.cash_ratio > 0.20)
      out.push(flag('C-03', 'red', '存贷双高',
        '货币资金 ' + f2(yi(L.cash)) + ' 亿，有息负债 ' + f2(yi(L.ibd)) + ' 亿，现金/有息负债 ' + f2(L.cash_to_ibd) + '，有息负债/总资产 ' + pct(L.ibd_to_assets * 100),
        '贷款利率通常大于存款利率，不会一边存大额现金一边借大额有息负债。要么现金有问题，要么融资能力存在重大不确定性，估值需深度折价。'));

    out.push(flag('C-04', 'info', '倒算存款利率（需人工核对年报附注）',
      '东财 F10 不提供利息收入明细。请取年报附注「利息收入」÷ 货币资金季度均值，长期 <1% 为红旗（地产龙头 2%~3% 属合理）；母公司报表倒算更异常，且母公司存贷比应小于合并报表。',
      '货币资金必须函证，造假需内鬼配合。倒算利率是最硬的证据。'));

    if (L.goodwill_ratio != null && L.goodwill_ratio > 0.30)
      out.push(flag('C-05', 'red', '商誉占比过高', '商誉 ' + f2(yi(L.goodwill)) + ' 亿，占归母净资产 ' + pct(L.goodwill_ratio * 100),
        '商誉在原理上并不完全符合资产定义，注水后基本无解。没有十足把握时，谨慎处理＝直接对商誉打折估值。'));

    if (L.invest_ratio != null && L.invest_ratio > 0.30)
      out.push(flag('C-06', 'yellow', '投资收益占利润总额过高', '投资收益 ' + f2(yi(L.invest_income)) + ' 亿，占利润总额 ' + pct(L.invest_ratio * 100),
        '股权投资和投资收益中隐藏着更大的造假黑洞，占比过高说明主营不实或利润来源不可持续。'));

    if (L.other_asset_ratio != null && L.other_asset_ratio > 0.15)
      out.push(flag('C-07', 'yellow', '「其他」类资产占比过高',
        '其他应收+其他流动/非流动资产合计 ' + f2(yi(L.other_rece + L.other_cur + L.other_nc)) + ' 亿，占总资产 ' + pct(L.other_asset_ratio * 100),
        '造假者偏好「存量金额大、科目冷门」的藏匿点，各种「其他」资产是虚假收入的第二级落点。'));

    if (L.cip_ratio != null && L.cip_ratio > 0.10)
      out.push(flag('C-08', 'yellow', '在建工程占比偏高（关注是否长期不转固）', '在建工程 ' + f2(yi(L.cip)) + ' 亿，占总资产 ' + pct(L.cip_ratio * 100),
        '在建工程说值多少钱就值多少钱，审计师难以质疑，是虚增收入/成本的高级落点；长期不转固还可少提折旧。'));

    var recent = s.slice(-3).filter(function (x) { return x.cfo_to_np != null && x.parent_np > 0; });
    if (recent.length >= 2) {
      var avg = recent.reduce(function (a, x) { return a + x.cfo_to_np; }, 0) / recent.length;
      if (avg < 0.7) out.push(flag('C-09', 'red', 'CFO/归母净利长期偏低', '近 ' + recent.length + ' 期均值 ' + f2(avg),
        '利润表是意见，现金流量表是证词。长期收不到钱就是虚增。'));
    }

    if (L.sales_cash_ratio != null && L.sales_cash_ratio < 0.95)
      out.push(flag('C-10', 'yellow', '收现比偏低', '销售收现/营业收入 = ' + f2(L.sales_cash_ratio) + '（含13%增值税时理论约 1.13）',
        '销售收现/营业收入是营收质量最直接的一票否决指标。'));

    if (L.prepay_yoy_calc != null && L.prepay_yoy_calc > 0.80 && L.prepay / L.total_assets > 0.05)
      out.push(flag('C-11', 'yellow', '预付款项骤增', '预付 ' + f2(yi(L.prepay)) + ' 亿，同比 ' + pct(L.prepay_yoy_calc * 100) + '，占总资产 ' + pct(L.prepay / L.total_assets * 100),
        '大额预付要么说明对上游无话语权，要么是虚增收入后谎称已打款。存货异常增加的危险度远高于应收/预付。'));

    // ---- 模块 D：合法调节 ----
    if (L.dta_delta_ratio != null && L.dta_delta_prev != null && L.dta_delta_ratio > L.dta_delta_prev && (L.dta_delta_ratio - L.dta_delta_prev) > 0.005)
      out.push(flag('D-01', 'info', '递延所得税资产二阶导为正（压利润）',
        'Δ递延所得税资产/营收：' + pct(L.dta_delta_prev * 100, 2) + ' → ' + pct(L.dta_delta_ratio * 100, 2),
        '税务局筛出了所有「只有会计报表认可、税务报表不认可」的调节。递延所得税资产加速增加＝超额计提费用压利润——公司压利润的时候，行业格局往往较好。'));

    if (L.dtl_delta_ratio != null && L.dtl_delta_prev != null && L.dtl_delta_ratio > L.dtl_delta_prev && (L.dtl_delta_ratio - L.dtl_delta_prev) > 0.005)
      out.push(flag('D-02', 'yellow', '递延所得税负债二阶导为正（放利润）',
        'Δ递延所得税负债/营收：' + pct(L.dtl_delta_prev * 100, 2) + ' → ' + pct(L.dtl_delta_ratio * 100, 2),
        '冲回费用放利润——虽然利润增速仍在、甚至可能加速，但已是强弩之末。'));

    if (L.deduct_ratio != null && L.deduct_ratio < 0.80)
      out.push(flag('D-03', 'yellow', '扣非占比低（非经常性损益撑利润）', '扣非归母/归母 = ' + pct(L.deduct_ratio * 100),
        '政府补助、资产处置、公允价值变动等一次性收益撑起的利润不可持续。'));

    if (L.minority_np_ratio != null && (L.minority_np_ratio > 0.40 || (L.minority_eq_ratio != null && L.minority_eq_ratio > 0.40)))
      out.push(flag('D-04', 'yellow', '少数股东占比异常（归母口径失真）',
        '少数股东损益占净利润 ' + pct(L.minority_np_ratio * 100) + '，少数股东权益占比 ' + pct((L.minority_eq_ratio || 0) * 100),
        '少数股东分走大部分利润时，「归母」口径的估值倍数会失真。'));

    if (L.perpetual_ratio != null && L.perpetual_ratio > 0.10)
      out.push(flag('D-05', 'red', '永续债/优先股占权益比高（归母口径陷阱）', '永续债+优先股 ' + f2(yi(L.perpetual)) + ' 亿，占所有者权益 ' + pct(L.perpetual_ratio * 100),
        '永续债实质是负债却计入权益，虚增净资产、压低资产负债率、虚降PB。计算真实归母权益与净有息负债时必须扣除。'));

    if (L.other_income_ratio != null && L.other_income_ratio > 0.15)
      out.push(flag('D-06', 'yellow', '其他收益（政府补助）占利润总额高', '其他收益 ' + f2(yi(L.other_income)) + ' 亿，占利润总额 ' + pct(L.other_income_ratio * 100),
        '政府补助确认时点存在不规范空间，占比过高说明主业盈利能力存疑。'));

    // 单季巨额亏损
    var q = s.slice(-8).filter(function (x) { return x.parent_np != null; });
    if (q.length >= 6) {
      var vals = q.map(function (x) { return x.parent_np; });
      var mean = vals.reduce(function (a, v) { return a + v; }, 0) / vals.length;
      var sd = Math.sqrt(vals.reduce(function (a, v) { return a + Math.pow(v - mean, 2); }, 0) / vals.length);
      var worst = null;
      q.forEach(function (x) { if (x.parent_np < mean - 2 * sd && x.parent_np < 0 && (!worst || x.parent_np < worst.parent_np)) worst = x; });
      if (worst) out.push(flag('D-07', 'info', '单季巨额亏损（疑似一把减到底）',
        worst.label + ' 归母 ' + f2(yi(worst.parent_np)) + ' 亿，显著偏离近 ' + q.length + ' 期均值 ' + f2(yi(mean)) + ' 亿',
        '教科书级调节手法：在业绩反转前夜一次性把减值、费用计提到底，留下低折旧基数让后续年份利润轻松高增长。需还原真实盈利中枢。'));
    }

    if (L.ibd_to_assets > 0.30 && L.finexp_to_ibd != null && L.finexp_to_ibd < 0.02)
      out.push(flag('D-08', 'yellow', '有息负债大但财务费用低（利息资本化嫌疑）',
        '有息负债/总资产 ' + pct(L.ibd_to_assets * 100) + '，财务费用/有息负债 ' + pct(L.finexp_to_ibd * 100, 2),
        '利息支出资本化可以把当期费用挪到资产里，虚增当期利润并低估资产负债率。'));

    var gm5 = s.slice(-5).filter(function (x) { return x.gross_margin != null; }).map(function (x) { return x.gross_margin; });
    if (gm5.length >= 5) {
      var m5 = gm5.reduce(function (a, v) { return a + v; }, 0) / 5;
      var sd5 = Math.sqrt(gm5.reduce(function (a, v) { return a + Math.pow(v - m5, 2); }, 0) / 5);
      if (sd5 < 0.008) out.push(flag('D-09', 'info', '毛利率异常平稳', '近5期毛利率标准差仅 ' + pct(sd5 * 100, 2),
        '真实经营的毛利率会随原料、售价、产品结构波动。异常平稳往往意味着收入成本被等比调节。'));
    }

    if (L.contract_liab_yoy != null && Math.abs(L.contract_liab_yoy) > 0.30)
      out.push(flag('D-10', 'info', '合同负债（含预收）异动',
        '合同负债 ' + f2(yi(L.contract_liab)) + ' 亿，同比 ' + pct(L.contract_liab_yoy * 100),
        '合同负债是收入的蓄水池。骤增＝可能延后确认收入；骤降＝可能在加速释放此前蓄的水。'));

    return out;
  }

  function grade(flags, module) {
    var f = flags.filter(function (x) { return x.id.indexOf(module) === 0; });
    var red = f.filter(function (x) { return x.level === 'red'; }).length;
    var yel = f.filter(function (x) { return x.level === 'yellow'; }).length;
    if (module === 'C') {
      if (red >= 4 || flags.some(function (x) { return x.id === 'C-12'; })) return 'D';
      if (red >= 2) return 'C';
      if (red === 1 || yel >= 3) return 'C';
      if (yel >= 1) return 'B';
      return 'A';
    }
    if (yel >= 5) return 'D';
    if (yel >= 3 || f.some(function (x) { return x.id === 'D-05'; })) return 'C';
    if (yel >= 1) return 'B';
    return 'A';
  }

  /* ---------- 引擎 A：叙事定位 ---------- */
  function peToM(pe) {
    // r=10%, n=10 年，反查隐含天花板倍数 m（分段线性插值）
    var tbl = [[6.1, 1], [15.3, 2], [20, 3], [28.8, 5], [38.6, 7.5], [48.6, 10], [66.3, 12.5]];
    if (pe <= tbl[0][0]) return Math.max(pe / 8, 0.3);
    for (var i = 1; i < tbl.length; i++) {
      if (pe <= tbl[i][0]) {
        var t = (pe - tbl[i - 1][0]) / (tbl[i][0] - tbl[i - 1][0]);
        return tbl[i - 1][1] + t * (tbl[i][1] - tbl[i - 1][1]);
      }
    }
    return 12.5 + (pe - 66.3) / 12;
  }

  function engineA(L, quote, hs) {
    var pe = quote.pe_ttm || quote.pe_dyn || quote.pe_static || null;
    var m = pe ? peToM(pe) : null;
    var annNp = L.parent_np * L.annu;      // 年化归母
    var Lstar = m ? m * annNp : null;      // 隐含稳态利润
    var rel = (pe && hs && hs.pe) ? pe / hs.pe : null;
    var peLabel = quote.pe_ttm ? 'PE(TTM)' : (quote.pe_dyn ? 'PE(动态)' : (quote.pe_static ? 'PE(静态)' : 'PE'));
    return { pe: pe, peLabel: peLabel, m: m, annNp: annNp, Lstar: Lstar, rel: rel, hs: hs, pb: quote.pb };
  }

  /* ---------- 打分 ---------- */
  function score(L, flags, gC, gD, A) {
    var gm = [0.20, 0.25, 0.30, 0.15, 0.10];
    var s = [0, 0, 0, 0, 0];
    // 1 叙事清晰度：能否测算出隐含 L
    s[0] = A.Lstar ? 75 : 45;
    if (A.Lstar && L.rev_yoy_calc != null) s[0] += Math.min(15, Math.abs(L.rev_yoy_calc) * 20);
    // 2 财报兑现度
    var v = 50;
    if (L.rev_yoy_calc != null) v += Math.max(-25, Math.min(25, L.rev_yoy_calc * 40));
    if (L.np_yoy_calc != null) v += Math.max(-20, Math.min(20, L.np_yoy_calc * 30));
    if (L.cfo_to_np != null) v += Math.max(-15, Math.min(20, (L.cfo_to_np - 1) * 15));
    if (L.sales_cash_ratio != null) v += Math.max(-10, Math.min(10, (L.sales_cash_ratio - 1) * 40));
    s[1] = Math.max(0, Math.min(100, v));
    // 3 财务可信度
    var base = { A: 92, B: 74, C: 52, D: 12 };
    s[2] = (base[gC] * 0.6 + base[gD] * 0.4);
    // 4 估值安全边际：隐含 L 相对当前年化利润的倍数越接近 1~3 越合理
    if (A.Lstar && A.annNp > 0) {
      var ratio = A.Lstar / A.annNp;
      s[3] = Math.max(10, Math.min(95, 95 - Math.max(0, ratio - 2) * 14));
    } else s[3] = 40;
    // 5 叙事可持续性
    var sus = 65;
    if (flags.some(function (x) { return x.id === 'D-02'; })) sus -= 20;
    if (flags.some(function (x) { return x.id === 'D-03'; })) sus -= 10;
    if (L.fcf != null && L.fcf > 0 && L.parent_np > 0) sus += 15;
    if (L.rev_yoy_calc != null && L.rev_yoy_calc < -0.10) sus -= 15;
    s[4] = Math.max(0, Math.min(100, sus));
    var total = s.reduce(function (a, v, i) { return a + v * gm[i]; }, 0);
    return { parts: s, total: Math.round(total) };
  }

  function verdict(score, gC, gD) {
    if (gC === 'D' || gD === 'D') return '叙事已被证伪或财报不可信 → 远离';
    if (score >= 80) return '叙事成立且被财报兑现，可跟踪';
    if (score >= 60) return '叙事成立但有瑕疵，需盯开放项';
    if (score >= 40) return '叙事与财报背离，谨慎';
    return '叙事已被证伪或财报不可信 → 远离';
  }

  /* ---------- 渲染 ---------- */
  function tbl(head, rows) {
    var h = '<table><thead><tr>' + head.map(function (x) { return '<th>' + x + '</th>'; }).join('') + '</tr></thead><tbody>';
    rows.forEach(function (r) {
      h += '<tr>' + r.map(function (c) {
        var o = c && typeof c === 'object' ? c : { v: c };
        return '<td' + (o.cls ? ' class="' + o.cls + '"' : '') + '>' + o.v + '</td>';
      }).join('') + '</tr>';
    });
    return h + '</tbody></table>';
  }
  function sec(title, body) { return '<div class="card"><h2>' + title + '</h2>' + body + '</div>'; }
  function kv(k, v) { return '<div class="kv"><span class="k">' + k + '</span><span class="v">' + v + '</span></div>'; }

  var LV = { red: '<span class="tag t-red">红旗</span>', yellow: '<span class="tag t-yel">关注</span>', info: '<span class="tag t-info">提示</span>' };
  var GC = { A: '<span class="tag t-ok">A 低嫌疑</span>', B: '<span class="tag t-info">B 中</span>', C: '<span class="tag t-yel">C 高</span>', D: '<span class="tag t-red">D 重大</span>' };

  function render(ctx) {
    var s = ctx.series, L = s[s.length - 1], q = ctx.quote, A = ctx.A, flags = ctx.flags;
    var gC = ctx.gC, gD = ctx.gD, sc = ctx.score;
    var html = '';

    /* 一句话结论 */
    var one = '';
    if (A.Lstar && A.annNp > 0) {
      one = '市场为 <b>' + esc(q.name) + '</b> 付的钱，隐含的是「稳态归母利润能从当前年化的 <b>' + f2(yi(A.annNp)) +
        ' 亿</b> 走到 <b>' + f2(yi(A.Lstar)) + ' 亿</b>」（隐含天花板倍数 m≈' + f2(A.m) + '×）这个故事的钱。';
    } else {
      one = '当前口径下无法稳定测算隐含稳态利润（PE 缺失或利润为负），需改用 PB / PS / 市值速算倍数路径。';
    }
    one += ' 财报端：营收同比 ' + (L.rev_yoy_calc != null ? pct(L.rev_yoy_calc * 100) : '—') +
      '，归母同比 ' + (L.np_yoy_calc != null ? pct(L.np_yoy_calc * 100) : '—') +
      '，CFO/归母 ' + (L.cfo_to_np != null ? f2(L.cfo_to_np) : '—') +
      '，收现比 ' + (L.sales_cash_ratio != null ? f2(L.sales_cash_ratio) : '—') + '。' +
      ' 舞弊评级 ' + GC[gC] + '，调节评级 ' + GC[gD] + '，<b>叙事匹配度 ' + sc.total + ' 分 → ' + verdict(sc.total, gC, gD) + '</b>。';
    html += '<div class="card concl"><h2>一句话结论</h2><p>' + one + '</p></div>';

    /* 第 0 步 */
    var h0 = '<div class="kvs">' +
      kv('公司', esc(q.name) + ' · ' + ctx.nc.code + '.' + ctx.nc.market) +
      kv('最新报告期', esc(L.label) + '（' + esc(L.date ? String(L.date).slice(0, 10) : '') + '）') +
      kv('股价', f2(q.price) + ' 元 <span class="' + (q.chg >= 0 ? 'up' : 'down') + '">' + (q.chg >= 0 ? '+' : '') + f2(q.chg) + '%</span>') +
      kv('总市值 / 流通市值', f2(yi(q.mktcap)) + ' 亿 / ' + f2(yi(q.float_cap)) + ' 亿') +
      kv('PE(TTM) / 动态 / 静态', (q.pe_ttm ? f2(q.pe_ttm) : '—') + ' / ' + (q.pe_dyn ? f2(q.pe_dyn) : '—') + ' / ' + (q.pe_static ? f2(q.pe_static) : '—')) +
      kv('PB', f2(q.pb)) +
      kv('ROE(加权)', L.roe ? pct(L.roe) : '—') +
      kv('资产负债率', pct((L.debt_ratio || 0) * 100)) +
      kv('审计意见', L.opinion ? esc(L.opinion) : '—') +
      kv('数据源', '东财 F10 · ' + esc(q.src) + '行情') +
      '</div>';
    html += sec('第 0 步 · 基础事实卡', h0);

    /* 第 1 步 */
    var relTxt = A.rel ? f2(A.rel) + '（个股PE / 沪深300 PE ' + f2(A.hs.pe) + '）' : '—';
    var h1 = '<p class="tip">不给财报估值，给叙事估值。财报的作用是<b>证伪或证实</b>叙事。以下按 r=10%（A股权益口径）、n=10 年反查。</p>';
    h1 += '<div class="kvs">' +
      kv('当前 ' + A.peLabel, f2(A.pe)) +
      kv('反查隐含天花板 m', f2(A.m) + ' 倍') +
      kv('年化归母利润', f2(yi(A.annNp)) + ' 亿') +
      kv('<b>隐含稳态利润 L</b>', '<b>' + (A.Lstar ? f2(yi(A.Lstar)) + ' 亿' : '—') + '</b>') +
      kv('相对天花板（剥离分母）', relTxt) +
      kv('PB', f2(A.pb)) +
      '</div>';
    h1 += '<p class="tip"><b>口径说明</b>：L = 隐含天花板倍数 m × 年化归母利润，这是《穿透估值》「路径 1」——' +
      '固定 r=10%、n=10 年由当前 PE 反查 m，再乘以当前利润。它衡量的是「市场把这个公司当成未来能长到多大的公司」，' +
      '<b>不是</b>三阶段 DCF 逐年折现算出的稳态利润（后者需要一致预期，本工具不取）。' +
      '相对天花板 = 个股PE / 沪深300 PE，用来剥离分母端（折现率）变化，剩下的才是分子端（叙事空间）的变动。</p>';
    var hist = s.filter(function (x) { return x.type === '年报' && x.parent_np != null; }).slice(-6);
    if (hist.length) {
      h1 += '<h3 class="sub-h">历史归母利润对照（年报口径）</h3>' +
        tbl(['报告期', '营收(亿)', '归母(亿)', '同比', 'ROE'], hist.map(function (x) {
          return [x.label, f2(yi(x.revenue)), f2(yi(x.parent_np)),
            { v: x.np_yoy_calc != null ? (x.np_yoy_calc >= 0 ? '+' : '') + pct(x.np_yoy_calc * 100) : (x.np_yoy ? pct(x.np_yoy) : '—'), cls: (x.np_yoy_calc != null ? (x.np_yoy_calc >= 0 ? 'up' : 'down') : '') },
            x.roe ? pct(x.roe) : '—'];
        }));
      h1 += '<p class="tip">请把上面反推出的 L 放进这张表里看它落在历史什么位置——「隐含假设处在合理区间的相对位置，是比 PE/PB 历史分位数更有效的判断指标」。</p>';
    }
    html += sec('第 1 步 · 引擎 A：叙事定位（股价里埋的是什么故事）', h1);

    /* 第 2 步 */
    var h2 = '<p class="tip">利润表是<b>意见</b>，现金流量表是<b>证词</b>，资产负债表才是<b>事实</b>。判断真实性的切入点永远是资产负债表。</p>';
    h2 += '<h3 class="sub-h">2.1 关键比率</h3>' + tbl(['指标', '最新值', '判读'], [
      ['毛利率', pct((L.gross_margin || 0) * 100), L.gm_chg != null ? ('同比 ' + pct(L.gm_chg * 100) + ' pct') : '—'],
      ['应收周转率（年化）', f2(L.ar_turnover), L.ar_turnover_chg != null ? ('同比 ' + pct(L.ar_turnover_chg * 100)) : '—'],
      ['存货周转率（年化）', f2(L.inv_turnover), L.inv_turnover_chg != null ? ('同比 ' + pct(L.inv_turnover_chg * 100)) : '—'],
      ['固定资产+在建+无形周转率', f2(L.fixed_turnover), '趋势性下降 = 重大负面'],
      ['CFO / 归母净利', f2(L.cfo_to_np), L.cfo_to_np >= 1 ? '现金含量良好' : (L.cfo_to_np >= 0.7 ? '尚可' : '偏弱')],
      ['收现比（销售收现/营收）', f2(L.sales_cash_ratio), L.sales_cash_ratio >= 1.0 ? '良好' : '偏低'],
      ['自由现金流 CFO−资本开支', f2(yi(L.fcf)) + ' 亿', L.fcf > 0 ? '正' : '负'],
      ['资本开支 / CFO', L.capex_to_cfo != null ? pct(L.capex_to_cfo * 100) : '—', '>100% 说明在烧钱扩张'],
      ['扣非归母 / 归母', L.deduct_ratio != null ? pct(L.deduct_ratio * 100) : '—', '低于 80% 需警惕'],
      ['有息负债率', pct((L.ibd_to_assets || 0) * 100), '—'],
      ['货币资金 / 有息负债', f2(L.cash_to_ibd), L.cash_to_ibd > 0.5 ? '偏高，注意存贷双高' : '正常']
    ]);

    h2 += '<h3 class="sub-h">2.2 资产负债表扫描（单位：亿元）</h3>' + tbl(['科目', '金额', '占总资产', '判读'], [
      ['货币资金', f2(yi(L.cash)), pct((L.cash_ratio || 0) * 100), '存贷双高检测'],
      ['应收账款+票据+合同资产', f2(yi(L.ar)), pct(L.ar / L.total_assets * 100), '最简陋的造假地'],
      ['存货', f2(yi(L.inventory)), pct(L.inventory / L.total_assets * 100), '异常增加比应收更危险'],
      ['预付款项', f2(yi(L.prepay)), pct(L.prepay / L.total_assets * 100), '无话语权 或 谎称打款'],
      ['其他应收+其他流动/非流动', f2(yi(L.other_rece + L.other_cur + L.other_nc)), pct((L.other_asset_ratio || 0) * 100), '冷门科目，造假第二落点'],
      ['固定资产', f2(yi(L.fixed)), pct(L.fixed / L.total_assets * 100), '—'],
      ['在建工程', f2(yi(L.cip)), pct((L.cip_ratio || 0) * 100), '长期不转固可少提折旧'],
      ['无形资产+商誉', f2(yi(L.intang + L.goodwill)), pct((L.intang + L.goodwill) / L.total_assets * 100), '最虚的部分'],
      ['递延所得税资产', f2(yi(L.dta)), pct(L.dta / L.total_assets * 100), '万能探测器'],
      ['递延所得税负债', f2(yi(L.dtl)), pct(L.dtl / L.total_assets * 100), '万能探测器'],
      ['合同负债+预收', f2(yi(L.contract_liab)), pct(L.contract_liab / L.total_assets * 100), '收入蓄水池'],
      ['有息负债合计', f2(yi(L.ibd)), pct((L.ibd_to_assets || 0) * 100), '含短借/长借/债券/租赁/一年内到期'],
      ['永续债+优先股', f2(yi(L.perpetual)), pct((L.perpetual_ratio || 0) * 100), '归母口径陷阱'],
      ['少数股东权益', f2(yi(L.minority_equity)), pct((L.minority_eq_ratio || 0) * 100), '判断归母是否失真']
    ]);

    h2 += '<h3 class="sub-h">2.3 利润表 / 现金流（单位：亿元）</h3>' + tbl(['科目', '金额', '占营收/利润', '判读'], [
      ['营业收入', f2(yi(L.revenue)), '100%', '—'],
      ['营业成本', f2(yi(L.cogs)), pct(L.cogs / L.revenue * 100), '—'],
      ['毛利', f2(yi(L.revenue - L.cogs)), pct((L.gross_margin || 0) * 100), '毛利率'],
      ['销售/管理/研发费用', f2(yi(L.sale_expense + L.manage_expense + L.rd_expense)), pct((L.sale_expense + L.manage_expense + L.rd_expense) / L.revenue * 100), '费用率突变需查'],
      ['财务费用', f2(yi(L.finance_expense)), pct((L.finexp_to_ibd || 0) * 100) + ' of 有息负债', '过低＝利息资本化嫌疑'],
      ['投资收益', f2(yi(L.invest_income)), pct((L.invest_ratio || 0) * 100) + ' of 利润总额', '>30% 需警惕'],
      ['其他收益（政府补助）', f2(yi(L.other_income)), pct((L.other_income_ratio || 0) * 100) + ' of 利润总额', '>15% 需警惕'],
      ['资产+信用减值损失', f2(yi(L.impair)), pct((L.impair_to_pbt || 0) * 100) + ' of 利润总额', '一把减到底检测'],
      ['归母净利润', f2(yi(L.parent_np)), L.np_yoy_calc != null ? pct(L.np_yoy_calc * 100) : '—', '—'],
      ['扣非归母', f2(yi(L.deduct_np)), pct((L.deduct_ratio || 0) * 100) + ' of 归母', '—'],
      ['经营现金流净额 CFO', f2(yi(L.cfo)), 'CFO/归母=' + f2(L.cfo_to_np), '—'],
      ['投资现金流净额', f2(yi(L.cfi)), '—', '资本开支节奏'],
      ['筹资现金流净额', f2(yi(L.cff)), '—', '定增/分红/还债'],
      ['自由现金流', f2(yi(L.fcf)), '—', 'CFO − 资本开支']
    ]);

    var recent = s.slice(-8);
    if (recent.length >= 2) {
      h2 += '<h3 class="sub-h">2.4 最近 ' + recent.length + ' 期趋势（单位：亿元）</h3>' +
        tbl(['报告期', '营收', '归母', 'CFO', 'CFO/归母', '毛利率', 'ROE'], recent.map(function (x) {
          return [x.label, f2(yi(x.revenue)), f2(yi(x.parent_np)), f2(yi(x.cfo)), f2(x.cfo_to_np), pct((x.gross_margin || 0) * 100), x.roe ? pct(x.roe) : '—'];
        }));
    }
    html += sec('第 2 步 · 引擎 B：三表科目级验证', h2);

    /* 第 3/4 步 */
    function flagTable(mod) {
      var f = flags.filter(function (x) { return x.id.indexOf(mod) === 0; });
      if (!f.length) return '<p class="empty">未触发该模块的任何规则。</p>';
      return tbl(['级别', '规则', '读数', '为什么'], f.map(function (x) {
        return [{ v: LV[x.level] }, '<b>' + esc(x.title) + '</b><br><span class="rid">' + x.id + '</span>', esc(x.detail), '<span class="why">' + esc(x.why) + '</span>'];
      }));
    }
    html += sec('第 3 步 · 模块 C：舞弊识别（合理怀疑 + 有罪推定）' + '　评级 ' + GC[gC], flagTable('C'));
    html += sec('第 4 步 · 模块 D：合法调节识别（小心公司预判你的预判）' + '　评级 ' + GC[gD], flagTable('D'));

    /* 第 5 步 */
    var names = ['叙事清晰度', '财报兑现度', '财务可信度', '估值安全边际', '叙事可持续性'];
    var w = [20, 25, 30, 15, 10];
    var h5 = tbl(['维度', '权重', '得分'], names.map(function (n, i) {
      return [n, w[i] + '%', '<b>' + Math.round(sc.parts[i]) + '</b>'];
    }));
    h5 += '<p class="total">叙事匹配度总分：<b class="' + (sc.total >= 60 ? 'up' : 'down') + '">' + sc.total + '</b> / 100　→　<b>' + verdict(sc.total, gC, gD) + '</b></p>';
    h5 += '<h3 class="sub-h">需要继续跟踪的开放项</h3><ul class="open">' +
      '<li>倒算存款利率：取年报附注「利息收入」÷ 货币资金季度均值，长期 &lt;1% 为红旗（本工具无法自动获取，需人工）。</li>' +
      '<li>递延所得税明细：剔除未弥补亏损、会计直线/税务加速折旧、未实现内部交易损益后再看二阶导。</li>' +
      '<li>母公司报表：存贷比应小于合并报表；利润主要来自子公司时需看母公司口径。</li>' +
      '<li>关联交易：外部投资者很难判断实质，关联收入骤升 + 应收挂账需警惕。</li>' +
      '<li>叙事空间代理变量：行业专属（白酒批价/出厂价、光伏渗透率、周期股 PE 反查），需人工填。</li>' +
      '</ul>';
    html += sec('第 5 步 · 叙事匹配度总结', h5);

    return html;
  }

  /* ---------- AI 提示词 ---------- */
  function buildPrompt(ctx) {
    var s = ctx.series, L = s[s.length - 1], q = ctx.quote, A = ctx.A, flags = ctx.flags;
    var lines = [];
    lines.push('你是资深 A 股分析师。请严格按「邹佩轩穿透分析框架」为下面的公司写一份财报分析报告。');
    lines.push('');
    lines.push('## 框架要求（必须逐条执行）');
    lines.push('1. 世界观：不给财报估值，给叙事估值。利润表是意见，现金流量表是证词，资产负债表才是事实。');
    lines.push('2. 第 0 步：基础事实卡（股本/市值/行业定位/当前行业景气/关键驱动）。');
    lines.push('3. 第 1 步 引擎A：叙事定位。列估值体系矩阵（PE/PB/PS/EV-EBITDA/DCF）并给出各自失效条件；用 r=10%、n=10 年由 PE 反查隐含天花板 m，算出市场隐含稳态利润 L；把 L 放进历史归母序列里看它落在什么位置；指出三个预期差。');
    lines.push('4. 第 2 步 引擎B：三表科目级验证。资产负债表按 9 项扫描（生产类资产/营运资本/货币资金/商誉无形/递延所得税/少数股东/有息负债/永续债/合同负债）；利润表拆季度；现金流做 CFO-净利、收现比、自由现金流。');
    lines.push('5. 第 3 步 模块C：舞弊识别（合理怀疑+有罪推定），逐条给结论。');
    lines.push('6. 第 4 步 模块D：合法调节识别，重点用递延所得税二阶导做万能探测器。');
    lines.push('7. 第 5 步：叙事匹配度打分卡 + 开放项 + 一句话给老钱。');
    lines.push('8. 纪律：业绩的增长弥补不了估值的下跌；ROE 不影响未来股价走势（分母是沉没成本）；公司压利润时行业格局往往较好，放利润时已是强弩之末。');
    lines.push('');
    lines.push('## 公司数据（东方财富 F10，报告期 ' + L.label + '）');
    lines.push('- ' + q.name + '（' + ctx.nc.code + '.' + ctx.nc.market + '）：股价 ' + f2(q.price) + ' 元，涨跌 ' + f2(q.chg) + '%');
    lines.push('- 总市值 ' + f2(yi(q.mktcap)) + ' 亿 / 流通市值 ' + f2(yi(q.float_cap)) + ' 亿；PE(TTM) ' + (q.pe_ttm || '—') + ' / 动态 ' + (q.pe_dyn || '—') + ' / PB ' + q.pb);
    lines.push('- 隐含天花板 m≈' + f2(A.m) + '；年化归母 ' + f2(yi(A.annNp)) + ' 亿；隐含稳态利润 L≈' + (A.Lstar ? f2(yi(A.Lstar)) : '—') + ' 亿');
    if (A.rel) lines.push('- 相对天花板（个股PE/沪深300PE）= ' + f2(A.rel) + '（沪深300 PE ' + f2(A.hs.pe) + '）');
    lines.push('- 营收 ' + f2(yi(L.revenue)) + ' 亿（同比 ' + pct((L.rev_yoy_calc || 0) * 100) + '），归母 ' + f2(yi(L.parent_np)) + ' 亿（同比 ' + pct((L.np_yoy_calc || 0) * 100) + '），扣非 ' + f2(yi(L.deduct_np)) + ' 亿');
    lines.push('- 毛利率 ' + pct((L.gross_margin || 0) * 100) + '，ROE(加权) ' + pct(L.roe) + '，资产负债率 ' + pct((L.debt_ratio || 0) * 100));
    lines.push('- CFO ' + f2(yi(L.cfo)) + ' 亿（CFO/归母 ' + f2(L.cfo_to_np) + '），收现比 ' + f2(L.sales_cash_ratio) + '，资本开支 ' + f2(yi(L.capex)) + ' 亿，自由现金流 ' + f2(yi(L.fcf)) + ' 亿');
    lines.push('- 货币资金 ' + f2(yi(L.cash)) + ' 亿 / 有息负债 ' + f2(yi(L.ibd)) + ' 亿（现金/有息负债 ' + f2(L.cash_to_ibd) + '）；净有息负债 ' + f2(yi(L.net_ibd)) + ' 亿');
    lines.push('- 应收类 ' + f2(yi(L.ar)) + ' 亿，存货 ' + f2(yi(L.inventory)) + ' 亿，预付 ' + f2(yi(L.prepay)) + ' 亿，商誉 ' + f2(yi(L.goodwill)) + ' 亿');
    lines.push('- 递延所得税资产 ' + f2(yi(L.dta)) + ' 亿，递延所得税负债 ' + f2(yi(L.dtl)) + ' 亿；合同负债 ' + f2(yi(L.contract_liab)) + ' 亿');
    lines.push('- 少数股东损益占净利 ' + pct((L.minority_np_ratio || 0) * 100) + '；永续债+优先股占权益 ' + pct((L.perpetual_ratio || 0) * 100));
    lines.push('- 审计意见：' + (L.opinion || '—'));
    lines.push('');
    lines.push('## 已自动触发的规则');
    if (!flags.length) lines.push('-（无）');
    flags.forEach(function (x) { lines.push('- [' + x.level + '] ' + x.id + ' ' + x.title + '：' + x.detail); });
    lines.push('');
    lines.push('## 最近报告期序列（营收/归母/CFO，单位亿元）');
    s.slice(-8).forEach(function (x) { lines.push('- ' + x.label + '：营收 ' + f2(yi(x.revenue)) + '，归母 ' + f2(yi(x.parent_np)) + '，CFO ' + f2(yi(x.cfo))); });
    lines.push('');
    lines.push('请输出完整 Markdown 报告。对无法从数据判断的项目，明确写「数据不足，无法判断」，不要编造。');
    return lines.join('\n');
  }

  window.CaiBao = {
    normalizeCode: normalizeCode, fetchQuote: fetchQuote, fetchHS300PE: fetchHS300PE,
    fetchAll: fetchAll, buildSeries: buildSeries, runRules: runRules, grade: grade,
    engineA: engineA, score: score, verdict: verdict, render: render, buildPrompt: buildPrompt,
    util: { f2: f2, pct: pct, yi: yi, esc: esc }
  };
})();
