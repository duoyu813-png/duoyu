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
        // 勾稽校验所需字段
        cce_add: num(f.CCE_ADD), begin_cce: num(f.BEGIN_CCE), end_cce: num(f.END_CCE),
        fx_effect: num(f.RATE_CHANGE_EFFECT),
        pay_tax: num(f.PAY_ALL_TAX), tax_refund: num(f.RECEIVE_TAX_REFUND),
        tax_surcharge: num(c.OPERATE_TAX_ADD), income_tax: num(c.INCOME_TAX),
        cfo_indirect: num(f.NETCASH_OPERATENOTE),
        cf_depr: num(f.FA_IR_DEPR) + num(f.IR_DEPR) + num(f.IA_AMORTIZE) + num(f.USERIGHT_ASSET_AMORTIZE),
        div_paid: num(f.ASSIGN_DIVIDEND_PORFIT),
        ar_tr: num(b.ACCOUNTS_RECE) + num(b.NOTE_RECE),
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

  /* ---------- 勾稽校验（先于一切比率分析） ---------- */
  // 硬勾稽 = 会计恒等式，不平说明报表本身有错；软勾稽 = 含估算假设，仅作提示。
  var CHECKS = [
    { id: 'CK-01', name: '资产 = 负债 + 所有者权益', kind: 'hard', tol: 0.01 },
    { id: 'CK-02', name: '所有者权益 = 归母权益 + 少数股东权益', kind: 'hard', tol: 0.01 },
    { id: 'CK-03', name: '期末现金 = 期初现金 + 现金净增加额', kind: 'hard', tol: 0.01 },
    { id: 'CK-04', name: '现金净增加额 = 经营 + 投资 + 筹资 + 汇率影响', kind: 'hard', tol: 0.01 },
    { id: 'CK-05', name: '间接法经营现金流 = 直接法经营现金流', kind: 'hard', tol: 0.02 },
    { id: 'CK-06', name: '利润总额 - 所得税费用 = 净利润', kind: 'hard', tol: 0.02 },
    { id: 'CK-07', name: '销售收现 ≈ 营收×(1+税率) - 应收增加 + 合同负债增加', kind: 'soft', tol: 0.20 },
    { id: 'CK-08', name: '支付税费(净退税后) ≈ 所得税费用 + 税金及附加', kind: 'soft', tol: 0.80 },
    { id: 'CK-09', name: '购建长期资产现金 ≈ 资产增加 + 折旧摊销', kind: 'soft', tol: 0.50 },
    { id: 'CK-10', name: '归母权益变动 ≈ 净利润 - 分红', kind: 'soft', tol: 0.30 }
  ];

  function gapOf(actual, expected) {
    if (!isFinite(actual) || !isFinite(expected) || Math.abs(expected) < 1e-6) return null;
    return Math.abs(actual - expected) / Math.abs(expected);
  }

  function runChecks(s, vat) {
    vat = (vat == null) ? 0.13 : vat;
    var out = [], hist = {};
    for (var i = 0; i < s.length; i++) {
      var m = s[i], p = null;
      // 优先用去年同期（避开季节性），否则用上一期
      for (var j = i - 1; j >= 0; j--) {
        if (mdOf(s[j].date) === mdOf(m.date) && yrOf(s[j].date) === yrOf(m.date) - 1) { p = s[j]; break; }
      }
      if (!p && i > 0) p = s[i - 1];
      var g = {};
      g['CK-01'] = gapOf(m.total_assets, m.total_liab + m.total_equity);
      g['CK-02'] = gapOf(m.total_equity, m.parent_equity + m.minority_equity);
      g['CK-03'] = gapOf(m.end_cce, m.begin_cce + m.cce_add);
      g['CK-04'] = gapOf(m.cce_add, m.cfo + m.cfi + m.cff + m.fx_effect);
      g['CK-05'] = m.cfo_indirect ? gapOf(m.cfo, m.cfo_indirect) : null;
      g['CK-06'] = gapOf(m.netprofit, m.total_profit - m.income_tax);
      if (p && p.date) {
        var dAr = m.ar_tr - p.ar_tr, dCl = m.contract_liab - p.contract_liab;
        g['CK-07'] = gapOf(m.sales_cash, m.revenue * (1 + vat) - dAr + dCl);
        g['CK-08'] = gapOf(m.pay_tax - m.tax_refund, m.income_tax + m.tax_surcharge);
        var exp9 = ((m.fixed + m.cip + m.intang) - (p.fixed + p.cip + p.intang)) + m.cf_depr;
        g['CK-09'] = Math.abs(exp9) > 1e7 ? gapOf(m.capex, exp9) : null;
        g['CK-10'] = gapOf(m.parent_equity, p.parent_equity + m.parent_np - m.div_paid);
      }
      var warn = [], hard = [];
      for (var k = 0; k < CHECKS.length; k++) {
        var v = g[CHECKS[k].id];
        if (v == null) continue;
        if (v > CHECKS[k].tol) { warn.push(CHECKS[k].id); if (CHECKS[k].kind === 'hard') hard.push(CHECKS[k].id); }
      }
      for (var h = 0; h < hard.length; h++) hist[hard[h]] = (hist[hard[h]] || 0) + 1;
      out.push({ date: m.date, label: m.label, gaps: g, warn: warn, hardFail: hard });
    }
    var chronic = [];
    Object.keys(hist).forEach(function (id) { if (hist[id] >= 2) chronic.push(id); });
    return { rows: out, chronic: chronic, vat: vat };
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

  /* ---------- 模板化叙事段落：对齐《穿透财报》报告格式（估值矩阵 / 三预期差 / 一句话锁定叙事） ---------- */
  function estEBITDA(L) {
    // 毛估：营收 − 营业成本 − 销售/管理/研发费用 + 投资收益 + 其他收益 + 折旧摊销
    return (L.revenue - L.cogs - L.sale_expense - L.manage_expense - L.rd_expense) + L.invest_income + L.other_income + L.cf_depr;
  }

  function valuationMatrix(A, L, quote) {
    var ev = quote.mktcap + L.net_ibd - L.minority_equity;
    var ebitda = estEBITDA(L);
    var rows = [
      ['PE(TTM)', qpe(A.pe),
        '市场预期当前盈利水平能持续',
        '强周期/重资产：利润高峰的 PE 是「骗人的低」'],
      ['PE 静态', qpe(quote.pe_static),
        '用去年年报 EPS 定价',
        '同上，滞后一个报告周期'],
      ['PB', qpe(quote.pb),
        '净资产×稳态 ROE 决定合理 PB',
        '轻资产/高营运资本行业净资产失真'],
      ['EV/EBITDA（毛估）', (ev > 0 && ebitda > 0 ? f2(ev / ebitda) + '×' : '—'),
        '行业倍数适配',
        'EBITDA 若是周期高峰的 EBITDA，同样是「骗人的低」；新租赁准则下使用权资产折旧不能加回'],
      ['前向 PE', '—（需一致预期，交 AI 深挖）',
        '一致预期 EPS',
        '一致预期一旦下修，估值快速回吐'],
      ['DCF 反推（隐含 L）', A.Lstar ? '<b>' + f2(yi(A.Lstar)) + ' 亿/年</b>' : '—',
        '稳态利润永久上抬',
        '稳态假设一旦被证伪，PB 支撑立刻被抽掉']
    ];
    return tbl(['估值体系', '当前读数', '隐含假设', '失效条件'], rows.map(function (r) {
      return [r[0], r[1], r[2], r[3]];
    }));
  }
  function qpe(v) { return (v != null && isFinite(v) && v > 0) ? f2(v) + '×' : '—'; }

  function expectationsGap(A, L, quote, hs, meanHist) {
    var gaps = [];
    // 增速预期差
    if (L.np_yoy_calc != null) {
      gaps.push('<li><b>增速预期差</b>：本期归母同比 ' + (L.np_yoy_calc >= 0 ? '+' : '') + pct(L.np_yoy_calc * 100) +
        '、营收同比 ' + (L.rev_yoy_calc != null ? (L.rev_yoy_calc >= 0 ? '+' : '') + pct(L.rev_yoy_calc * 100) : '—') +
        '——股价当前定价的是「这个增速能持续」，下一报告期若增速锐减，第一波下修就会来。</li>');
    }
    // 增速持续时间预期差
    if (A.Lstar && meanHist != null && meanHist > 0) {
      var k = A.Lstar / meanHist;
      gaps.push('<li><b>增速持续时间预期差（最致命）</b>：隐含稳态利润 ' + f2(yi(A.Lstar)) +
        ' 亿是历史年均 ' + f2(yi(meanHist)) + ' 亿的 <b>' + f2(k) + ' 倍</b>——市场赌高盈利「永久上台阶」。' +
        (k > 2 ? '若市场开始相信高盈利只有 1~2 年（如景气回落），估值对应大幅压缩。' : '若市场进一步相信高盈利延续多年，估值还有上修空间。') + '</li>');
    }
    // 折现率预期差
    if (A.rel != null && hs && hs.pe) {
      gaps.push('<li><b>折现率预期差</b>：相对天花板（个股PE/沪深300PE）=' + f2(A.rel) +
        '（沪深300 ' + f2(hs.pe) + '×）。该值显著偏高说明市场用低折现率+高叙事定价，一旦市场要求更高折现率（周期股定价回归），股价有回撤压力。</li>');
    }
    return '<h3 class="sub-h">三个预期差的手电筒（超额收益只来自预期差）</h3><ul class="open">' + gaps.join('') +
      (gaps.length ? '' : '<li>数据不足，暂无法给出预期差量化。</li>') + '</ul>';
  }

  function oneLineNarrative(A, L, quote, meanHist) {
    var base = '市场为 <b>' + esc(quote.name) + '</b> 付的钱，不是当期这份报表的钱，而是「稳态归母利润从当前年化 <b>' +
      f2(yi(A.annNp)) + ' 亿</b> 永久性上抬到 <b>' + (A.Lstar ? f2(yi(A.Lstar)) + ' 亿' : '—') + '</b>' +
      (A.m != null ? '（隐含天花板 m≈' + f2(A.m) + '×）' : '') + '」这个故事的钱。';
    var fail = '「稳态利润中枢重新跌回历史均值（' + (meanHist != null ? f2(yi(meanHist)) + ' 亿' : '更低水平') + '）」——一旦市场开始相信这一点，高 PB 的估值支撑会被立刻抽掉。';
    return '<p class="tip"><b>一句话锁定叙事</b>：' + base + ' 这个叙事只有一个失效条件：' + fail + '</p>';
  }

  /* ---------- 投资·跟踪结论（一句话总结） ---------- */
  function conclusionHTML(ctx) {
    var s = ctx.series, L = s[s.length - 1], q = ctx.quote, A = ctx.A;
    var sc = ctx.score, gC = ctx.gC, gD = ctx.gD, flags = ctx.flags;
    var v = verdict(sc.total, gC, gD);
    var reds = flags.filter(function (x) { return x.level === 'red'; });
    var yels = flags.filter(function (x) { return x.level === 'yellow'; });
    var annuals = s.filter(function (x) { return x.type === '年报' && x.revenue > 0 && x.parent_np > 0; }).slice(-5);
    var meanHist = null;
    if (annuals.length) {
      var sum = 0; annuals.forEach(function (x) { sum += x.parent_np; });
      meanHist = sum / annuals.length;
    }
    var ratio = (A.Lstar && A.annNp > 0) ? A.Lstar / A.annNp : null;
    var avoid = (gC === 'D' || gD === 'D');
    var verdictTag = '<span class="tag ' + (avoid ? 't-red' : (sc.total >= 60 ? 't-ok' : 't-yel')) + '">' + v + '</span>';

    var sentence = '';
    if (avoid) {
      sentence = '一句话总结：<b>' + esc(q.name) + '</b> 财报可信度亮红灯（舞弊 ' + GC[gC] + ' / 调节 ' + GC[gD] + '）。按纪律「D 级直接远离、不估值」——<b>宁可错过，不可买错</b>。';
    } else {
      var confirm = sc.total >= 80 ? '财报在高分兑现这个叙事' : (sc.total >= 60 ? '财报基本兑现了叙事、但存在瑕疵' : '财报与股价隐含叙事出现背离');
      var risk = '';
      if (ratio != null) {
        risk = '当前隐含稳态利润 ' + f2(yi(A.Lstar)) + ' 亿，是年化利润的 <b>' + f2(ratio) + ' 倍</b>' +
          (meanHist != null ? '、是历史均值 ' + f2(yi(meanHist)) + ' 亿的 ' + f2(A.Lstar / meanHist) + ' 倍' : '') +
          '。它赌的是「利润中枢永久性上台阶」，一旦叙事证伪，估值弹性向下。';
      }
      var hold = sc.total >= 80 ? '<b>可跟踪</b>：叙事成立且被财报验证' : (sc.total >= 60 ? '<b>跟踪为主、仓位从轻</b>：叙事成立但有瑕疵，需盯开放项' : '<b>谨慎</b>：叙事与财报背离，等证伪信号或估值回到安全边际');
      sentence = '一句话总结：市场为 <b>' + esc(q.name) + '</b> 付的钱，埋的是一个「稳态利润从 ' + f2(yi(A.annNp)) +
        ' 亿走到 ' + (A.Lstar ? f2(yi(A.Lstar)) : '—') + ' 亿」的故事，' + risk + ' 这份财报' + confirm + '。操作上：' + hold + '。';
    }

    var html = '<p class="tip"><b>投资决策的分水岭</b>：先问自己相信「稳态利润永久上台阶」还是「这只是一次周期高峰」。前者对应' +
      (A.Lstar && meanHist ? f2(yi(A.Lstar)) + ' 亿稳态中枢的价格' : '高估值') + '，后者对应回到历史均值 ' +
      (meanHist != null ? f2(yi(meanHist)) + ' 亿' : '——') + ' 的价格。两者之间的价差，就是这只股票当前真正的风险和机会。</p>';

    html += '<div class="total">' + sentence + '</div>';

    html += '<h3 class="sub-h">结论依据</h3><ul class="open">' +
      '<li>叙事匹配度总分 <b>' + sc.total + '</b> / 100（' + ['叙事清晰度', '财报兑现度', '财务可信度', '估值安全边际', '叙事可持续性'].map(function (n, i) {
        return n + ' <b>' + Math.round(sc.parts[i]) + '</b>';
      }).join(' · ') + '） → ' + verdictTag + '</li>' +
      '<li>舞弊评级 ' + GC[gC] + '，调节评级 ' + GC[gD] + '（C/D 为 D 级则一票否决）</li>' +
      '<li>CFO/归母 ' + f2(L.cfo_to_np) + '，收现比 ' + f2(L.sales_cash_ratio) +
        (A.Lstar ? '，隐含稳态利润 L≈' + f2(yi(A.Lstar)) + ' 亿' : '') +
        (meanHist ? '，历史年均归母 ' + f2(yi(meanHist)) + ' 亿' : '') + '</li>' +
      (reds.length ? '<li>触发红旗 ' + reds.length + ' 条：' + reds.map(function (x) { return esc(x.title); }).join('；') + '</li>' : '') +
      (yels.length ? '<li>触发关注 ' + yels.length + ' 条：' + yels.map(function (x) { return esc(x.title); }).join('；') + '</li>' : '') +
      '</ul>';

    html += '<h3 class="sub-h">跟踪信号（开放项看什么、什么情况下改变结论）</h3><ul class="open">' +
      '<li>倒算存款利率（货币资金造假最硬证据）：取年报附注「利息收入」÷ 货币资金季度均值，长期 &lt;1% 为红旗。</li>' +
      '<li>递延所得税二阶导：剔除税务加速折旧/未弥补亏损/未实现内部交易后，若加速扩大＝压利润（行业格局好），若冲回＝放利润（强弩之末）。</li>' +
      '<li>折旧/减值政策：折旧年限或残值率若变更、或出现「单季一把减到底」，是合法调节利润的最重要手法，需还原真实盈利中枢。</li>' +
      '<li>相对天花板（' + (A.peLabel || 'PE') + ' / 沪深300 PE）= ' + (A.rel ? f2(A.rel) : '—') + '，持续抬升＝叙事在上修，回落＝叙事在动摇。</li>' +
      '<li>关联交易与审计意见：非标意见一票否决；关联交易占比 &gt;30% 或定价偏离公允 20% 以上需警惕。</li>' +
      '</ul>';

    return '<div class="card concl"><h2>投资·跟踪结论（一句话总结）</h2>' + html + '</div>';
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

    /* 第 0.5 步 · 勾稽校验 */
    var CK = ctx.checks || runChecks(s, ctx.vat);
    var Lc = CK.rows[CK.rows.length - 1] || { gaps: {}, warn: [], hardFail: [] };
    var hc = '<p class="tip">这一步不需要判断，只需要计算。<b>三张表之间对不上，后面的比率分析就是空中楼阁。</b>' +
      '硬勾稽 = 会计恒等式（不平说明报表本身编错，别往下算）；软勾稽 = 含税率/口径/汇率的估算假设（仅作提示，需按行业解释）。</p>';
    hc += tbl(['代码', '校验项', '类型', '容忍', '本期缺口', '判定'], CHECKS.map(function (c) {
      var v = Lc.gaps[c.id], sTxt = '', cls = '';
      if (v == null) { sTxt = '数据不足'; }
      else if (v <= c.tol) { sTxt = '✅ 通过'; }
      else if (c.kind === 'hard') { sTxt = '🔴 不平'; cls = 't-red'; }
      else { sTxt = '⚠️ 超容忍'; cls = 't-yel'; }
      return [c.id, c.name, c.kind === 'hard' ? '硬' : '软', pct(c.tol * 100, 0),
        v == null ? '—' : pct(v * 100),
        cls ? '<span class="tag ' + cls + '">' + sTxt + '</span>' : sTxt];
    }));
    var hardN = (Lc.hardFail || []).length;
    var softN = (Lc.warn || []).length - hardN;
    if (hardN) {
      hc += '<p class="verdict v-red">🔴 硬勾稽不平：' + Lc.hardFail.join('、') +
        ' —— 报表内部矛盾，请先核验数据源或报表本身，再谈分析。</p>';
    } else if (CK.chronic.length) {
      hc += '<p class="verdict v-yel">⚠️ 以下项在历史上连续多期不平：' + CK.chronic.join('、') +
        ' —— 单期波动多为口径噪音，连续出现才值得警惕。</p>';
    } else {
      hc += '<p class="tip">硬勾稽 6 项全部通过，说明这张表内部自洽。' +
        (softN ? '另有 ' + softN + ' 项软勾稽超容忍，多由行业结算模式、免税/退税、外币折算、合并范围变动引起，需人工核对口径。' : '') +
        '</p>';
    }
    hc += '<p class="tip"><b>已知边界</b>：本工具只能验证"表内自洽"，验证不了"业务真实"。' +
      '以下必须人工翻年报附注：① 关联方交易与资金往来 ② 对外担保及未决诉讼 ③ 会计政策/估计变更（折旧年限、坏账计提、研发资本化）。' +
      '另外「倒算存款利率」（货币资金造假最硬的证据）需要利息收入明细，东财不提供。</p>';
    html += sec('第 0.5 步 · 勾稽校验（这张表本身可信吗）', hc);

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

    /* 模板 1.1 估值体系矩阵 */
    h1 += '<h3 class="sub-h">1.1 当前估值体系（每一种都有失效条件）</h3>' + valuationMatrix(A, L, q);

    /* 模板 1.3 历史归母利润对照 */
    var hist = s.filter(function (x) { return x.type === '年报' && x.parent_np != null; }).slice(-6);
    if (hist.length) {
      var yrHist = s.filter(function (x) { return x.type === '年报' && x.parent_np > 0; }).slice(-5);
      var meanHist = null;
      if (yrHist.length) { var sumH = 0; yrHist.forEach(function (x) { sumH += x.parent_np; }); meanHist = sumH / yrHist.length; }
      h1 += '<h3 class="sub-h">1.2 历史归母利润对照 + 隐含 L 落位（年报口径）</h3>' +
        tbl(['报告期', '营收(亿)', '归母(亿)', '同比', 'ROE'], hist.map(function (x) {
          return [x.label, f2(yi(x.revenue)), f2(yi(x.parent_np)),
            { v: x.np_yoy_calc != null ? (x.np_yoy_calc >= 0 ? '+' : '') + pct(x.np_yoy_calc * 100) : (x.np_yoy ? pct(x.np_yoy) : '—'), cls: (x.np_yoy_calc != null ? (x.np_yoy_calc >= 0 ? 'up' : 'down') : '') },
            x.roe ? pct(x.roe) : '—'];
        }));
      h1 += '<p class="tip">把上面反推出的 L 放进这张表看它落在历史什么位置' +
        (meanHist != null ? '：历史年均利润约 <b>' + f2(yi(meanHist)) + ' 亿</b>，隐含稳态 L 约为其 ' + (A.Lstar ? f2(A.Lstar / meanHist) + ' 倍' : '—') : '') +
        '。「隐含假设处在合理区间的相对位置，是比 PE/PB 历史分位数更有效的判断指标」。</p>';
    }

    /* 模板 1.4 三个预期差 + 1.5 一句话锁定叙事 */
    h1 += expectationsGap(A, L, q, A.hs, meanHist);
    h1 += oneLineNarrative(A, L, q, meanHist);
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

    /* 第 5.3 节 · 投资/跟踪结论（一句话总结）—— 模板必须项，永不缺省 */
    html += conclusionHTML(ctx);

    return html;
  }

  /* ---------- AI 提示词 ---------- */
  function buildPrompt(ctx) {
    var s = ctx.series, L = s[s.length - 1], q = ctx.quote, A = ctx.A, flags = ctx.flags;
    var lines = [];
    lines.push('你是资深 A 股分析师。请严格按「邹佩轩穿透分析框架」为下面的公司写一份财报分析报告。');
    lines.push('');
    lines.push('## 输出格式（必须严格执行，格式对齐下面的模板范例）');
    lines.push('请用 Markdown 输出，章节标题与顺序一字不差地照下面这套结构（这是《中远海能·穿透财报分析》的模板）：');
    lines.push('');
    lines.push('1. 第一行标题：# {公司名} · 穿透财报分析（{代码} · {最新报告期}窗口）');
    lines.push('2. 三个引用块（> 开头）：报告口径 / 股价基点（现价、市值、PE_TTM、PB）/ 框架（不给财报估值，给叙事估值，双引擎 A+B + 舞弊/调节交叉检验 C+D）。');
    lines.push('3. `## 一句话结论`：2-4 句浓缩「市场付的到底是什么钱 + 财报兑现了什么 + 真正的风险在哪」。');
    lines.push('4. `## 第 0 步 · 基础事实卡`：股本与市值 / 行业定位 / 当前行业景气与运价类数字 / 关键驱动。');
    lines.push('5. `## 第 1 步 · 引擎 A：叙事定位`：');
    lines.push('   - 1.1 估值体系矩阵（PE/PE静态/PB/EV-EBITDA/DCF，每行含「当前读数 + 隐含假设 + 失效条件」）；');
    lines.push('   - 1.2 DCF 反推（r=10%）市场隐含稳态利润 L，写清输入与倒算过程；');
    lines.push('   - 1.3 把 L 放进历史归母序列看它落在什么位置（给历史年份表）；');
    lines.push('   - 1.4 三个预期差的手电筒（增速 / 增速持续时间 / 折现率）；');
    lines.push('   - 1.5 一句话锁定叙事（一句话 + 它的唯一失效条件）。');
    lines.push('6. `## 第 2 步 · 引擎 B：三表科目级验证`：三表定位与主线勾稽 → 资产负债表按 9 项科目扫描（生产类资产/营运资本/货币资金/商誉无形/递延所得税/少数股东/有息负债/永续债/合同负债）→ 利润表季度拆分与增速真实性 → 现金流量表（CFO/净利、收现比、自由现金流、资本开支）。');
    lines.push('7. `## 第 3 步 · 模块 C：舞弊识别`：合理怀疑 + 有罪推定，七类红旗逐条给结论（收入/存货/成本/货币资金/商誉/投资收益/关联交易/审计信号），并给综合评级 A/B/C/D。');
    lines.push('8. `## 第 4 步 · 模块 D：合法调节识别`：递延所得税二阶导万能探测器 + 逐科目手法清单（收入/成本/折旧/费用/财务费用/政府补助/投资收益/减值），给综合评级 A/B/C/D。');
    lines.push('9. `## 第 5 步 · 叙事匹配度总结`：');
    lines.push('   - 5.1 打分卡（股价隐含叙事 vs 财报现实，逐项「兑现/证伪」+ 偏差含义）；');
    lines.push('   - 5.2 需要继续跟踪的开放项（带具体触发阈值）；');
    lines.push('   - 5.3 【必须写，不得省略】投资/跟踪结论 · 一句话总结：先给一句鲜明结论，再分「若你相信 X / 若你认为 Y」两种情景给出不同决策与区间，最后给出你自己的判断和跟踪触发条件。这是全报告最重要的段落，禁止用「仅供参考、注意风险」一类的废话搪塞。');
    lines.push('10. 附录：数据源 / 关键公告 / 框架金句在本案例的映射。');
    lines.push('');
    lines.push('## 分析纪律（金句即规则）');
    lines.push('- 世界观：不给财报估值，给叙事估值。利润表是意见，现金流量表是证词，资产负债表才是事实。');
    lines.push('- 虚增利润归根结底都是虚增资产；利润表造假必然在资产端留下痕迹。');
    lines.push('- 业绩的增长弥补不了估值的下跌；ROE 不影响未来股价走势（分母是沉没成本）。');
    lines.push('- 公司压利润的时候，行业格局往往较好；放利润的时候已是强弩之末。');
    lines.push('- 每一项风险结论都必须能回到具体科目和具体数字（证据闭环原则），禁止无出处的推论。');
    lines.push('- 勾稽校验先行：先报告硬勾稽（会计恒等式 6 条）通过情况，不平就说「报表本身有错，以下分析仅供参考」；再解释软勾稽超容忍项的行业口径（税率/结算模式/外币折算/合并范围）。');
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
    lines.push('## 三表勾稽校验结果（增值税率假设 ' + pct(((ctx.checks || runChecks(s, ctx.vat)).vat) * 100, 0) + '）');
    var CKx = ctx.checks || runChecks(s, ctx.vat);
    var Lcx = CKx.rows[CKx.rows.length - 1] || { gaps: {} };
    CHECKS.forEach(function (c) {
      var v = Lcx.gaps[c.id];
      var verdict = v == null ? '数据不足' : (v <= c.tol ? '通过' : (c.kind === 'hard' ? '❌不平' : '⚠️超容忍'));
      lines.push('- ' + c.id + ' [' + (c.kind === 'hard' ? '硬' : '软') + '] ' + c.name +
        '：容忍 ' + pct(c.tol * 100, 0) + '，实测缺口 ' + (v == null ? '—' : pct(v * 100)) + ' → ' + verdict);
    });
    if (CKx.chronic.length) lines.push('- 注意：以下项目历史上连续多期不平 → ' + CKx.chronic.join('、'));
    lines.push('');
    lines.push('## 已自动触发的规则');
    if (!flags.length) lines.push('-（无）');
    flags.forEach(function (x) { lines.push('- [' + x.level + '] ' + x.id + ' ' + x.title + '：' + x.detail); });
    lines.push('');
    lines.push('## 最近报告期序列（营收/归母/CFO，单位亿元）');
    s.slice(-8).forEach(function (x) { lines.push('- ' + x.label + '：营收 ' + f2(yi(x.revenue)) + '，归母 ' + f2(yi(x.parent_np)) + '，CFO ' + f2(yi(x.cfo))); });
    lines.push('');
    lines.push('请输出完整 Markdown 报告，章节严格对齐上面 10 条模板结构。报告的「5.3 投资/跟踪结论 · 一句话总结」必须存在且有明确投资立场与触发条件。对无法从数据判断的项目，明确写「数据不足，无法判断」，不要编造。');
    return lines.join('\n');
  }

  window.CaiBao = {
    normalizeCode: normalizeCode, fetchQuote: fetchQuote, fetchHS300PE: fetchHS300PE,
    fetchAll: fetchAll, buildSeries: buildSeries, runRules: runRules, grade: grade,
    runChecks: runChecks, CHECKS: CHECKS,
    engineA: engineA, score: score, verdict: verdict, render: render, buildPrompt: buildPrompt,
    util: { f2: f2, pct: pct, yi: yi, esc: esc }
  };
})();
