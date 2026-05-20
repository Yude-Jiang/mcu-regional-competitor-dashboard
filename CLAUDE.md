# CLAUDE.md — MCU竞品数据库操作指南

## 项目概述
本项目维护11家中国MCU上市公司的竞品财务数据库，包含公司档案、2018-2025财务时序、汇率换算表。数据通过AkShare自动提取+手工年报校对维护。

## 文件职责

| 文件 | 角色 | 编辑规则 |
|---|---|---|
| `data.json` | 财务时序数据（核心） | **主要编辑目标**，所有数值字段可更新 |
| `profiles_xq.json` | 公司静态信息 | 每季度末检查更新（成立/上市年份不变） |
| `fx_rates.json` | CNY/USD年度平均汇率 | 每年1月更新上一年人行年均汇率 |
| `validate_data.py` | 数据完整性校验 | **每次编辑后必须运行，exit 0才算完成** |
| `fetch_data_akshare.py` | 自动提取 total_revenue_cny | 只能自动填总营收，其余字段手工维护 |
| `DATA_DICTIONARY.md` | 口径声明 | 修改MCU收入口径前必须先读此文件 |
| `dashboard.html` | 前端仪表盘 | 不要修改，除非明确要求 |
| `app.py` | Flask静态文件服务 | 不要修改，除非新增数据文件路由 |

## data.json 顶层结构（必须保留）

```json
{
  "meta": { "dataset": "...", "last_updated": "...", ... },
  "companies": { "SYMBOL": { "symbol": "...", "currency": "CNY", "years": {...}, "cagr_2018_2025": {...} } }
}
```

`meta` 和 `companies` 两个顶层key缺一不可，validate_data.py 会检查。

## 数据更新标准流程

1. 编辑 `data.json` 或 `profiles_xq.json`
2. 运行 `python validate_data.py`
3. 确认输出 `RESULT: PASS`（exit 0）后再commit
4. commit message 格式：`feat(data): update {symbol} {year} {field} from {source}`
   - 示例：`feat(data): update 603986 2024 MCU revenue from annual report`

## 口径规则（重要）

MCU收入口径定义见 `DATA_DICTIONARY.md`，修改数据前必须先读该文件中的"各公司MCU收入口径说明"表。

关键规则：
- **乐鑫科技 (688018)**：只用"芯片收入"分类，不含模组
- **复旦微电子 (688385)**：用"智能电表芯片产品线"（含智能电表MCU、通用MCU、车规MCU），模组一并计入
- **纳思达 (002180)**：只用极海微子公司数据，禁止使用纳思达合并报表（含大量打印耗材）
- **中颖电子 (300327)**：总营收即MCU（99.88%）
- **峰岹科技 (688279)**：总营收即MCU（电机驱动控制芯片）
- **钜泉科技 (688391)**：总营收即MCU（电力计量SoC）

不得自行修改任何公司的MCU收入口径。

## 数据置信度标记

填充数据时必须标注 source 和 source_date：
- `source: "annual_report_2024"` — 年报直接披露
- `source: "akshare_stock_financial_abstract"` — AkShare自动提取
- `source: "estimated_from_total_revenue"` — 从总收入推算（需在note中注明方法）

data_type 字段：
- `"actual"` — 已发布年报数据
- `"forecast"` — 预测值（仅无实际数据时使用）
- `"estimated"` — 估算值（需在note中说明估算方法）

## 禁止操作

- 不得删除任何已有 null 字段（骨架字段必须保留，validate 依赖）
- 不得修改 `KNOWN_SYMBOLS` 列表（`validate_data.py` 硬编码了11个symbol）
- 不得在 `data.json` 里自行估算填入 MCU 字段而没有标注来源
- 不得修改 `dashboard.html` 或 `app.py` 除非明确要求
- 不得修改 `profiles_xq.json` 中的 `mcu_revenue_scope` 字段（口径定义）
