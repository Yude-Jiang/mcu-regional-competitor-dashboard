#!/usr/bin/env python3
"""check_extraction.py — 验证AI提取的MCU营收数据合理性

Checks:
  1. MCU/total ratio:      0–100%（超出则 MCU > 总营收，逻辑错误）
  2. YoY anomaly:          单年跌幅 > 60% 或涨幅 > 200% 时预警
  3. Gross margin range:   10%–75%（超出则怀疑提取了错误行）
  4. Magnitude consistency:跨年标准差/均值 > 1.5 时预警（量级跳跃）
  5. Low confidence flag:  confidence=low 或 data_type=estimated 列出待人工核对

Usage:
    python check_extraction.py              # all companies
    python check_extraction.py 603986       # single company
    python check_extraction.py --flag-only  # only show flagged rows
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent

# ── thresholds ────────────────────────────────────────────────────────────────
MCU_RATIO_MAX    = 1.05   # allow 5% over (rounding in source data)
YOY_DROP_THRESH  = -0.60  # -60%
YOY_SPIKE_THRESH =  2.00  # +200%
GM_MIN           = 0.10   # 10%
GM_MAX           = 0.75   # 75%
CV_THRESH        = 1.5    # coefficient of variation for magnitude check

WARN  = "⚠ "
OK    = "  "
INFO  = "ℹ "


def load_data() -> dict:
    return json.loads((HERE / "data.json").read_text())


def check_company(symbol: str, company: dict, flag_only: bool) -> list[str]:
    meta      = company.get("meta", {})
    name_cn   = meta.get("name_cn", symbol)
    fins      = company.get("financials", {})
    issues    = []
    any_flag  = False

    sorted_years = sorted(fins.keys())
    mcu_values   = []

    rows_output = []

    for yr in sorted_years:
        row   = fins[yr]
        flags = []

        mcu   = row.get("mcu_revenue_yuan")
        total = row.get("total_revenue_yuan")
        gm    = row.get("gross_margin_pct")     # stored as 43.72 (percentage)
        conf  = row.get("mcu_confidence", "")
        dtype = row.get("mcu_data_type", "")
        yoy   = row.get("mcu_yoy_pct")          # e.g. 9.7 for +9.7%

        # 1. MCU/total ratio
        if mcu is not None and total is not None and total > 0:
            ratio = mcu / total
            if ratio > MCU_RATIO_MAX:
                flags.append(f"MCU/总营收={ratio:.1%} 超过100%（MCU={mcu/1e8:.2f}亿 > 总={total/1e8:.2f}亿）")
            elif ratio < 0:
                flags.append(f"MCU营收为负 ({mcu/1e6:.1f}M)")

        # 2. YoY anomaly
        if yoy is not None:
            yoy_ratio = yoy / 100
            if yoy_ratio < YOY_DROP_THRESH:
                flags.append(f"MCU同比下滑 {yoy:+.1f}%（阈值-60%）")
            elif yoy_ratio > YOY_SPIKE_THRESH:
                flags.append(f"MCU同比激增 {yoy:+.1f}%（阈值+200%）")

        # 3. Gross margin range
        if gm is not None:
            gm_ratio = gm / 100  # convert from percent
            if gm_ratio < GM_MIN:
                flags.append(f"毛利率={gm:.1f}% 低于10%（可能提取了错误行）")
            elif gm_ratio > GM_MAX:
                flags.append(f"毛利率={gm:.1f}% 高于75%（请核查）")

        # 5. Low confidence / estimated
        if conf in ("low",) or dtype in ("estimated",):
            flags.append(f"低置信度: confidence={conf}, data_type={dtype}（需人工核查）")

        if mcu is not None:
            mcu_values.append(mcu)

        tag = WARN if flags else OK
        if not flag_only or flags:
            mcu_str   = f"¥{mcu/1e8:.3f}亿" if mcu is not None else "N/A    "
            total_str = f"¥{total/1e8:.2f}亿" if total is not None else "N/A"
            ratio_str = f"{mcu/total*100:.0f}%" if (mcu is not None and total is not None and total > 0) else "—"
            gm_str    = f"{gm:.1f}%" if gm is not None else "—"
            rows_output.append(
                f"  {tag}{yr}  MCU={mcu_str:12s}  总营收={total_str:10s}  "
                f"占比={ratio_str:5s}  GM={gm_str:6s}  [{dtype}/{conf}]"
            )
            for f in flags:
                rows_output.append(f"       >> {f}")
                any_flag = True

    # 4. Magnitude consistency (coefficient of variation)
    if len(mcu_values) >= 3:
        import statistics
        mean = statistics.mean(mcu_values)
        stdev = statistics.stdev(mcu_values)
        cv = stdev / mean if mean > 0 else 0
        if cv > CV_THRESH:
            cv_note = (
                f"  {WARN}MCU营收跨年变异系数CV={cv:.2f} > {CV_THRESH}（量级波动较大，请检查是否有异常年份）"
            )
            rows_output.insert(0, cv_note)
            any_flag = True

    if rows_output or not flag_only:
        issues.append(f"\n{'━'*72}")
        issues.append(f"  {symbol}  {name_cn}  [{meta.get('mcu_strategy','')}]")
        issues.extend(rows_output)

    return issues, any_flag


def main() -> None:
    args       = sys.argv[1:]
    flag_only  = "--flag-only" in args
    args       = [a for a in args if not a.startswith("--")]
    target     = args[0] if args else None

    data     = load_data()
    companies = data.get("companies", {})

    if target and target not in companies:
        sys.exit(f"Unknown symbol: {target}. Valid: {sorted(companies)}")

    symbols = [target] if target else sorted(companies)

    total_flags = 0
    all_lines   = []
    all_lines.append(f"check_extraction.py — MCU数据质量报告")
    all_lines.append(f"数据生成日期: {data.get('generated_at','?')}  共{len(symbols)}家公司")

    for sym in symbols:
        lines, had_flag = check_company(sym, companies[sym], flag_only)
        all_lines.extend(lines)
        if had_flag:
            total_flags += 1

    all_lines.append(f"\n{'━'*72}")
    if total_flags == 0:
        all_lines.append(f"  {OK}所有检查通过，未发现异常。")
    else:
        all_lines.append(f"  {WARN}{total_flags}/{len(symbols)} 家公司存在需关注项，请逐一核查。")
    all_lines.append("")

    print("\n".join(all_lines))

    # Exit 1 only for hard errors (MCU > total revenue); warnings are informational
    hard_error = False
    for sym in symbols:
        fins = companies[sym].get("financials", {})
        for yr, row in fins.items():
            mcu   = row.get("mcu_revenue_yuan")
            total = row.get("total_revenue_yuan")
            if mcu is not None and total is not None and total > 0 and mcu / total > MCU_RATIO_MAX:
                hard_error = True

    sys.exit(1 if hard_error else 0)


if __name__ == "__main__":
    main()
