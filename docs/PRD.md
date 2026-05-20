# Product Requirements Document
# MCU Regional Competitor Dashboard — China Market
**项目代号**：geo-mcu-competitor  
**版本**：v1.0  
**日期**：2026-05-20  
**负责人**：ST数字营销/数据组  
**Repo**：https://github.com/Yude-Jiang/mcu-regional-competitor-dashboard  

---

## 一、项目背景与目标

### 1.1 背景

意法半导体（ST）MCU产品线需要系统性监控中国本土MCU竞争对手的财务表现与市场地位。当前信息获取方式依赖人工查阅分散的年报、研报和行业报告，效率低、口径不统一、无法形成可追溯的历史数据库。

### 1.2 核心目标

构建一套**内部使用的竞品财务数据库与可视化dashboard**，覆盖11家中国本土上市MCU公司，追踪其MCU收入、毛利率、研发投入等核心指标的历史趋势（2018-2025），为ST产品定价、市场策略、竞争态势判断提供数据支撑。

### 1.3 使用场景

- 季度竞品分析报告制作
- 市场策略会议数据支持
- 领导层汇报材料底稿
- 销售团队竞争情报参考

---

## 二、受众与使用者

**主要使用者**：ST数字营销/数据组（单人维护）  
**最终受众**：销售、产品、市场管理层  
**使用频率**：每季度更新，季报/年报发布后2周内完成录入  
**访问方式**：内部Web应用（Cloud Run部署），邮件白名单鉴权

---

## 三、覆盖范围

### 3.1 竞争对手列表（11家）

| 股票代码 | 中文名 | 英文名 | 交易所 |
|---------|-------|-------|-------|
| 603986 | 兆易创新 | GigaDevice | SSE |
| 300327 | 中颖电子 | SinoWealth | SZSE |
| 688380 | 中微半导 | Cmsemicon | STAR |
| 300077 | 国民技术 | NationZ | SZSE |
| 688279 | 峰岹科技 | Fortior Tech | STAR |
| 002180 | 纳思达（极海微） | Geehy (APM) | SZSE |
| 688385 | 复旦微电子 | FDM | STAR |
| 688766 | 普冉股份 | Puya | STAR |
| 688595 | 芯海科技 | Chipsea | STAR |
| 688391 | 钜泉科技 | Hi-Trend | STAR |
| 688018 | 乐鑫科技 | Espressif | STAR |

### 3.2 时间范围

- 历史数据：2018–2025年（逐年）
- 季度数据：当年各季度（辅助验证，不作为主要分析维度）
- 2025年：已发布年报为actual，未发布公司标注forecast

---

## 四、核心数据字段

### 4.1 公司静态信息（companies_meta.json）

| 字段 | 说明 |
|-----|-----|
| symbol | 股票代码 |
| name_cn / name_en | 中英文名 |
| founded_year / listed_year | 成立/上市年份 |
| core_license | 核心IP架构（ARM Cortex-M系列/RISC-V/8051等） |
| foundry_fab | 代工厂（TSMC/SMIC/HHGrace等） |
| mcu_strategy | MCU收入推算策略（segment_reported/total_proxy/total_revenue/estimated等） |
| mcu_multiplier | 推算系数（total_proxy策略使用） |
| mcu_confidence | 口径置信度（high/medium/low） |
| mcu_note | MCU口径说明（人读） |

### 4.2 财务时序数据（data.json，按公司×年度）

| 字段 | 说明 | 单位 |
|-----|-----|-----|
| total_revenue_yuan | 总营收 | 元CNY |
| total_revenue_musd | 总营收 | M USD |
| revenue_yoy_pct | 总营收同比增长 | % |
| mcu_revenue_yuan | MCU相关收入 | 元CNY |
| mcu_revenue_musd | MCU相关收入 | M USD |
| mcu_yoy_pct | MCU收入同比增长 | % |
| mcu_weight_pct | MCU占总收入比重 | % |
| gross_margin_pct | 毛利率 | % |
| rd_expense_yuan / rd_expense_musd | 研发费用 | 元CNY / M USD |
| rd_pct | 研发占收入比 | % |
| net_income_yuan / net_income_musd | 净利润 | 元CNY / M USD |
| net_income_yoy_pct | 净利润同比增长 | % |
| employee_count | 员工人数 | 人 |
| cagr_pct / cagr_label | 营收CAGR及区间标注 | % |
| mcu_data_type | 数据类型（reported/derived/estimated/unavailable） | — |
| mcu_confidence | 本年口径置信度 | — |
| mcu_source | 数据来源说明 | 文本 |
| filing_status | 报告状态（reported/derived/estimated/pending） | — |
| data_coverage | 字段覆盖率（0–1） | — |

**货币说明**：所有金额以人民币CNY原始值存储，同时提供按当年平均汇率换算的M USD字段，汇率硬编码于 `fetch_mcu_data.py` 的 `FX` 字典（来源：人民银行年度均价）。

---

## 五、MCU收入口径规则

> 这是本项目最核心的设计决策，各家公司情况不同，统一规则如下：

### 5.1 各公司口径汇总

| 公司 | mcu_strategy | 系数/说明 | 置信度 | 来源 |
|------|------------|---------|-------|-----|
| 兆易创新 | segment_reported | 年报「微控制器」产品线 | high | 年报分产品表 |
| 中颖电子 | total_proxy | ×0.9988（含AMOLED驱动IC） | high | 年报分类近似 |
| 中微半导 | total_proxy | ×0.99（纯MCU，非MCU<1%） | high | 年报总收入 |
| 国民技术 | estimated | 总收入×27%（通用MCU估算） | low | IR活动记录 |
| 峰岹科技 | total_revenue | ×1.0（电机驱动专营） | high | 年报总收入 |
| 纳思达极海 | subsidiary_geehy | 极海微子公司营收 | medium | 年报集成电路分部 |
| 复旦微电子 | segment_estimated | 「智能电表芯片」产品线 | medium | 年报产品线 |
| 普冉股份 | segment_reported | 年报「MCU产品线」 | high | 年报分产品表 |
| 芯海科技 | estimated | 总收入×45%（MCU估算） | low | IR估算系数 |
| 钜泉科技 | total_revenue | ×1.0（电力计量专营） | high | 年报总收入 |
| 乐鑫科技 | segment_estimated | 年报「芯片收入」（不含模组） | medium | 年报分产品 |

### 5.2 口径原则

1. **优先年报分产品表**：有直接披露的用原始数字，不用系数
2. **系数估算需标注**：mcu_data_type=derived/estimated，mcu_source说明系数来源
3. **跨年口径必须一致**：一旦确定，历史年份统一适用
4. **不追求纯MCU**：工业控制（含BMIC）、芯片（含SoC）等近似口径可接受，必须在mcu_note中明确注明
5. **不做净利润拆分**：年报只给收入和毛利率，净利润为公司整体核算，不可拆分到产品线
6. **纳思达特殊处理**：`total_revenue_yuan` 只填极海微分部数据（~4亿/年），**禁止**填入集团总收入（264亿）

---

## 六、可视化需求

### 6.1 已实现图表

**Chart 1 — 按公司横向对比（grouped bar）**
- X轴：11家公司
- Y轴：MCU Revenue（M$），每家展示多年柱状（可勾选年份）
- 毛利率折线叠加（Y轴右）
- 支持CNY/M$切换

**Chart 2 — 按年度堆叠趋势**
- X轴：2018–2025年
- Y轴：11家公司MCU Revenue堆叠柱状
- 每家公司不同颜色，含总量折线

**Pipeline Strip** — 顶部公司状态栏，含MCU策略徽章与置信度颜色边框

**Detail Panel** — 点击行展开公司详情（8年趋势图 + 关键指标 + 口径说明）

**AI Q&A 悬浮面板** — 基于data.json的结构化上下文调用DeepSeek V3 / Gemini 2.0 Flash

### 6.2 待改进

1. **数据置信度可视化**：estimated数据在图表中用虚线/斜线填充区分，与actual数据视觉区分
2. **YoY气泡**：在柱状图上直接显示YoY%，减少在表格和图表之间切换
3. **竞争力雷达图**：可选展示，按收入规模/增速/毛利率/研发强度/车规布局五维度评分
4. **缓存控制**：data.json加版本号参数，避免浏览器缓存旧数据

### 6.3 UI设计要求

- ST企业风格（深蓝色系，专业感），参考色：`#03234b` / `#3cb4e6` / `#ffd200`
- 内部工具定位，信息密度优先于美观
- 必须展示数据来源和最后更新时间
- 响应式布局，兼容1400px宽度桌面
- 支持深色/浅色主题切换

---

## 七、数据更新流程

### 7.1 全量自动化刷新

```bash
# Cloud Shell 推荐命令（--no-bq 跳过BQ写入，--commit 自动提交推送）
python smart_sync.py --no-bq --commit
```

内部步骤：
```
fetch_mcu_data.py        → AKShare利润表 + MCU推算 → data.json
fetch_yjbb_quarterly.py  → 业绩报表Q4快照 → 毛利率/净利润YoY → merge
validate_data.py         → 数据验证，exit 0才算完成
```

### 7.2 手工维护部分（每季度）

- **国民技术、芯海科技**：查巨潮资讯IR活动记录表，更新MCU占比估算，写入 `mcu_known_data.json`
- **纳思达极海**：从年报集成电路分部手工提取极海微营收，写入 `mcu_known_data.json`
- **峰岹科技**：验证MCU/ASIC占比系数是否有变化（年报公布后）
- **普冉股份 / 兆易创新**：年报分产品表MCU收入直接录入 `mcu_known_data.json`（已有2024实际值）
- **2025预测数据**：使用分析师一致预期，标forecast，年报发布后替换为actual

### 7.3 已知数据手工录入待办

| 公司 | 待录入 | 优先级 |
|-----|-------|-------|
| 兆易创新 | 2018–2023年报MCU分产品收入 | P1 |
| 普冉股份 | 2022招股书2018–2021历史MCU数据 | P1 |
| 纳思达极海 | 极海微分部逐年收入 | P2 |

### 7.4 数据质量规则

- 每次更新后必须运行 `validate_data.py`，exit 0才提交
- estimated数据必须附mcu_source说明估算方法
- reported/derived/estimated/unavailable四种mcu_data_type不得混淆

---

## 八、技术架构

### 8.1 技术栈

| 层级 | 技术 |
|-----|-----|
| 后端 | Python Flask（app.py） |
| 前端 | 单文件HTML + Chart.js 4.4 |
| 数据存储 | JSON文件（data.json / companies_meta.json / mcu_known_data.json） |
| 数据获取 | AKShare 1.18+（stock_profit_sheet_by_yearly_em / stock_yjbb_em） |
| PDF年报 | CNINFO API下载 → GCS存储 → DeepSeek V3提取MCU分段 |
| 数据库 | BigQuery（mcu dataset，asia-east1）— 可选，dashboard以data.json为主 |
| 部署 | Google Cloud Run（asia-east1），`--source .` 构建 |
| 鉴权 | 暂无（allow-unauthenticated），计划Firebase Auth邮件白名单 |
| 版本控制 | GitHub |

### 8.2 API端点

| 端点 | 说明 |
|-----|-----|
| GET / | dashboard.html |
| GET /admin | admin.html（文档矩阵 + 新增公司） |
| GET /data.json | 财务时序数据 |
| GET /companies_meta.json | 公司静态信息 |
| GET /api/doc-status | PDF文档状态矩阵（来自BigQuery） |
| POST /api/company/add | 新增公司到companies_meta.json |
| POST /api/ask | AI问答（DeepSeek V3 → Gemini 2.0 Flash fallback） |
| POST /api/refresh | Phase 3 stub（年报下载pipeline，未实现） |

### 8.3 关键文件

| 文件 | 用途 |
|-----|-----|
| data.json | 财务时序数据主库（AKShare自动 + 手工known数据合并） |
| companies_meta.json | 公司静态信息及MCU口径策略配置 |
| mcu_known_data.json | 手工录入的高置信度MCU收入数据（优先级高于自动推算） |
| bigquery_schema.sql | BQ表结构（financials / mcu_segments / pdf_index） |
| smart_sync.py | 完整数据刷新pipeline编排（支持--no-bq --commit） |
| fetch_mcu_data.py | AKShare利润表拉取 + MCU推算 + BQ写入 |
| fetch_yjbb_quarterly.py | 业绩报表Q4快照（毛利率、净利润YoY） |
| download_reports.py | CNINFO年报PDF下载 → GCS |
| extract_mcu_segments.py | DeepSeek V3从PDF提取MCU分段收入 |
| validate_data.py | 数据验证（每次更新必跑） |
| bq_writer.py | BigQuery UPSERT helpers |
| app.py | Flask服务（静态文件 + API路由） |
| dashboard.html | 前端单文件应用 |
| admin.html | 管理后台（文档矩阵 + 新增公司表单） |

---

## 九、已知问题与待解决项

### 9.1 P0（需立即核实）

| 问题 | 现状 | 正确值 |
|-----|-----|-------|
| 纳思达total_revenue | 需确认data.json填的是集团还是极海微分部 | 只能填极海微~4亿/年 |
| 峰岹科技口径描述 | companies_meta当前为「总收入=MCU全量×1.0」 | 应为×0.67系数（motor ctrl MCU+ASIC） |
| 钜泉科技口径描述 | 同上「总收入=MCU全量×1.0」 | 应为×0.34系数（计量MCU/SoC） |

### 9.2 P1（下季度前修正）

| 问题 | 说明 |
|-----|-----|
| 中颖电子MCU口径 | 当前×0.9988近似总收入，应改为年报「工业控制芯片」分类（含BMIC） |
| 兆易MCU毛利率 | 需从分产品表核实是MCU口径还是整体毛利率 |
| 芯海科技2024总收入 | 确认是否用全年值（7.02亿）而非三季报（6.12亿） |
| 峰岹科技mcu_confidence | 当前为high，改为medium（系数估算） |
| 钜泉科技mcu_confidence | 同上改为medium |

### 9.3 数据天花板（结构性限制，无法通过下载年报解决）

| 公司 | 限制 |
|-----|-----|
| 国民技术 | 年报无通用MCU vs 安全芯片拆分，永远是估算 |
| 芯海科技 | 年报无MCU分类，永远是系数估算 |
| 复旦微电子 | 智能电表芯片含模组，纯MCU无法剥离 |
| 中颖电子 | 工规MCU无单独披露行，BMIC无法从工业控制中剥离 |
| 纳思达极海 | 极海微子公司数据需手工从集团年报分部注释中提取 |

---

## 十、汽车MCU专项追踪

> 汽车MCU是ST的重要战略方向，需要单独追踪各竞品进展。

由于所有11家公司均未在财报中单独披露汽车MCU收入，汽车MCU采用**状态追踪**而非金额追踪：

| 公司 | 状态 | 说明 |
|-----|-----|-----|
| 峰岹科技 | volume（规模量产） | 2024车规占营收7.35%≈0.3亿 |
| 纳思达极海 | volume | BMS/车灯控制批量出货 |
| 兆易创新 | ramping | 与头部Tier1合作导入 |
| 中微半导 | ramping | 车规产品客户导入阶段 |
| 复旦微电子 | ramping | 智能电表产品线含车规MCU |
| 普冉股份 | pilot | 256MB产品AEC-Q100认证中 |
| 国民技术 | pilot | N32G45x系列小批量试产 |
| 芯海科技 | pilot | 有车规产品，认证阶段 |
| 中颖电子 | pilot | 首颗车规MCU 2023年实现小量销售 |
| 乐鑫科技 | na | 主营AIoT无线MCU，不涉及车规 |
| 钜泉科技 | na | 专注电力计量，不涉及车规 |

---

## 十一、验收标准

| 维度 | 标准 |
|-----|-----|
| 数据完整性 | validate_data.py exit 0，无FAIL |
| 口径一致性 | 11家公司mcu_note全部填写，无错误口径描述 |
| 代码正确性 | 普冉股份代码688766（非688694） |
| 图表正确性 | Chart 1显示MCU收入（非总收入），estimated数据有视觉区分 |
| 可追溯性 | 每条estimated/derived数据有mcu_source说明 |
| 部署 | Cloud Run asia-east1可正常访问 |
| AI问答 | /api/ask返回包含实际财务数字的中文回答 |

---

*本PRD基于2026年5月的完整需求讨论整理，涵盖数据口径验证、技术架构、HTML改进方向。每季度财报季后更新数据，半年回顾一次口径设定是否需要调整。*
