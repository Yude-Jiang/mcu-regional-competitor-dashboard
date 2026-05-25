# MCU竞品数据库 用户手册

**版本：** v1.1 · **更新：** 2026-05-25  
**受众：** 接手季度维护的同事，需能独立完成数据更新和核验  
**配套文件：** CLAUDE.md（操作规范）· README.md（运维日志）

---

## 目录

1. [数据架构一览](#1-数据架构一览)
2. [各公司MCU数据来源说明](#2-各公司mcu数据来源说明)
3. [季度更新标准流程](#3-季度更新标准流程)
4. [数据质检方法](#4-数据质检方法)
5. [Dashboard 使用指南](#5-dashboard-使用指南)
6. [常见问题排查](#6-常见问题排查)

---

## 1. 数据架构一览

```
mcu_known_data.json          ← 手工/LLM提取的MCU分段数据（最高优先级）
        ↓
fetch_mcu_data.py            ← 应用mcu_strategy推算，生成data.json
        ↓
data.json                    ← 财务时序主库，供dashboard读取
  ├── financials[sym][year]  ← 每家公司每年：营收/MCU/毛利率/研发/净利润/员工数
  └── companies[sym].meta    ← 静态元数据：上市年份/策略/置信度
        ↓
dashboard.html               ← 前端可视化（Flask via app.py）
```

**附属数据文件：**

| 文件 | 说明 |
|------|------|
| `companies_meta.json` | mcu_strategy / mcu_multiplier / mcu_confidence |
| `profiles_xq.json` | auto_mcu_status / mcu_revenue_scope（雪球口径注记） |
| `fx_rates.json` | CNY/USD 年度均值汇率（禁止修改历史值） |
| `mcu_known_data.json` | MCU分段数据（CNY元），优先级高于推算 |

**字段单位规范：**
- `_yuan` 后缀：CNY 元（原始值）
- `_musd` 后缀：百万美元（按年度汇率换算）
- 禁止使用 `_cny` 后缀（已废弃）
- `mcu_known_data.json` 必须用 CNY 元存储，禁止用 M$

### 上市前数据来源（招股书）

7家公司在上市前有历史财务数据，来源为 **IPO招股说明书**（非年度报告）：

| 公司 | 上市年份 | 招股书覆盖年份 | 备注 |
|------|---------|--------------|------|
| 688380 中微半导 | 2022 | 2018–2021 | 总收入+MCU推算，无毛利率/员工数 |
| 688279 峰岹科技 | 2021 | 2018–2020 | 总收入+MCU推算，无毛利率/员工数 |
| 688385 复旦微电子 | 2021 | 2018–2020 | 总收入+MCU推算，无毛利率/员工数 |
| 688766 普冉股份 | 2022 | 2018–2021 | 总收入+MCU推算，无毛利率/员工数 |
| 688595 芯海科技 | 2020 | 2018–2019 | 总收入+MCU推算，无毛利率/员工数 |
| 688391 钜泉科技 | 2021 | 2018–2020 | 总收入+MCU推算，无毛利率/员工数 |
| 688018 乐鑫科技 | 2019 | 2018 | 总收入+MCU推算，无毛利率/员工数 |

Dashboard 中这些行的 FY 列会显示紫色 **招股书** 徽章，表格下方有自动脚注说明。

### 毛利率数据覆盖现状（截至2026-05-25）

| 状态 | 公司 | 原因 |
|------|------|------|
| ✅ 全覆盖 | 603986 兆易创新、002180 纳思达 | AKShare完整覆盖 |
| ⚡ 近年有 | 300327 中颖(2024-25)、688385 复旦微(2024)、688766 普冉(2024)、300077 国民(2022-24) | API从近年开始覆盖 |
| ❌ 全缺失 | 688380 中微半导、688279 峰岹、688595 芯海、688391 钜泉、688018 乐鑫 | AKShare不提供分产品毛利率，需PDF提取 |

### 员工数数据来源

`employee_count` 字段由两条路径填充：
- **2025年**：AKShare/雪球 API（`fetch_mcu_data.py` 自动拉取）
- **2018–2024年**：`extract_employee_counts.py` 从GCS年报PDF提取（Gemini REST API）

上市前年份（year < listed_year）无员工数，因为招股书员工数未纳入自动提取。

---

## 2. 各公司MCU数据来源说明

### 603986 兆易创新（GigaDevice）

| 项目 | 内容 |
|------|------|
| **口径** | 年报「主营业务分产品情况」表「微控制器」行（部分年份为「MCU及模拟产品」合并披露，已内部拆分） |
| **获取方式** | 手工录入（mcu_known_data.json），2018-2024数据来自年报PDF人工提取 |
| **置信度** | high |
| **MCU历史覆盖** | 2018–2025（8年完整） |
| **员工数覆盖** | 2018–2025（8年，2018-2024来自年报PDF，2025来自AKShare） |
| **毛利率覆盖** | 2018–2025（AKShare完整覆盖） |
| **局限性** | 2024年年报将MCU与模拟产品合并披露（¥17.06亿），内部拆分后纯MCU为¥16.90亿，有估算成分 |
| **更新方法** | 每年4月年报发布后，从巨潮PDF提取分产品表，写入mcu_known_data.json |

---

### 300327 中颖电子（SinoWealth）

| 项目 | 内容 |
|------|------|
| **口径** | 年报「主营业务分产品情况」表「工业控制芯片」行（含BMIC电池管理芯片，非纯MCU，为官方披露口径） |
| **获取方式** | 2019-2022：Gemini从GCS年报PDF提取；2023-2025：手工录入 |
| **置信度** | medium（工业控制口径包含非MCU产品） |
| **MCU历史覆盖** | 2019–2025（7年，2018年无GCS PDF） |
| **员工数覆盖** | 2018–2025（8年，2018-2024来自年报PDF，2025来自AKShare） |
| **毛利率覆盖** | 2024–2025（仅近2年，历史年份待PDF提取） |
| **局限性** | 「工业控制芯片」含BMIC，高于纯MCU口径约10-15%；2018年MCU数据缺失 |
| **更新方法** | `python extract_mcu_segments.py 300327` |

---

### 688380 中微半导（Cmsemicon）

| 项目 | 内容 |
|------|------|
| **口径** | 总营收 × 0.99（纯MCU公司，非MCU收入<1%） |
| **获取方式** | fetch_mcu_data.py自动推算（total_proxy策略） |
| **置信度** | high |
| **MCU历史覆盖** | 2018–2025（8年完整，2018–2021来自招股书） |
| **员工数覆盖** | 2022–2025（上市后年报），2018–2021无（招股书未提取） |
| **毛利率覆盖** | ❌ 全部缺失（AKShare不覆盖，待PDF提取） |
| **局限性** | 2018–2021营收来自招股书，毛利率和员工数未提取 |
| **更新方法** | `python fetch_mcu_data.py 688380`（AKShare自动拉取总收入） |

---

### 300077 国民技术（NationZ）

| 项目 | 内容 |
|------|------|
| **口径** | 港股招股书「芯片产品」收入（含通用MCU + 专业市场芯片 + BMS + RF，非纯MCU） |
| **获取方式** | 手工录入，来源：《国民技术股份有限公司全球发售招股章程》2026年3月（港交所披露编号2026031300050） |
| **置信度** | medium（芯片产品口径含安全芯片等，非纯MCU）|
| **MCU历史覆盖** | 2022–2025（4年，招股书覆盖范围） |
| **员工数覆盖** | 2018–2025（8年，2018-2024来自年报PDF，2025来自AKShare） |
| **毛利率覆盖** | 2022–2024（AKShare覆盖2022起） |
| **局限性** | A股年报始终将所有IC产品合并为「芯片类产品」，永不单独披露MCU；2018-2021数据不可获取；灼识咨询估算2024年纯通用MCU约5亿元≈芯片产品的90% |
| **更新方法** | 年报仍无法提取，需关注港股年报（股票代码2701）是否单独披露MCU分段 |

> ⚠ **口径提醒（来自2026年H股招股说明书）**：图表中MCU Revenue列为「芯片产品」收入，含安全MCU/BMS/RF，非纯通用MCU口径。

---

### 688279 峰岹科技（Fortior Tech）

| 项目 | 内容 |
|------|------|
| **口径** | 总营收 × 0.67（MCU/ASIC系数，其余为HVIC/MOSFET/IPM约30-35%） |
| **获取方式** | fetch_mcu_data.py自动推算（total_proxy策略） |
| **置信度** | medium（系数基于产品结构估算） |
| **MCU历史覆盖** | 2018–2025（8年完整，2018–2020来自招股书） |
| **员工数覆盖** | 2022–2025（上市后年报），2018–2020无（招股书未提取） |
| **毛利率覆盖** | ❌ 全部缺失（AKShare不覆盖，待PDF提取） |
| **局限性** | 系数0.67为静态估算，实际MCU占比随产品结构变化；年报未拆分MCU vs ASIC vs HVIC |
| **更新方法** | `python fetch_mcu_data.py 688279` |

---

### 002180 纳思达（Nasda / Geehy）

| 项目 | 内容 |
|------|------|
| **口径** | 年报「营业收入构成」表「芯片」产品行（极海微电子子公司MCU产品线代理口径） |
| **获取方式** | Gemini从GCS年报PDF提取（mcu_known_data.json） |
| **置信度** | medium（年报不单独披露极海MCU，「芯片」产品行为最佳可得口径） |
| **MCU历史覆盖** | 2018–2025（7年，2020年提取失败缺失） |
| **员工数覆盖** | 2018–2024（7年来自年报PDF），2025年缺失（集团员工数≠极海微员工数，无法填入） |
| **毛利率覆盖** | 2018–2025（AKShare完整覆盖） |
| **局限性** | 纳思达集团总收入约264亿（含打印耗材业务），**严禁**将集团总收入写入total_revenue_yuan；「芯片」行包含极海全部芯片产品，不等于纯MCU；员工数为集团合并数，2025年因子公司口径不统一暂缺 |
| **更新方法** | `python extract_mcu_segments.py 002180`；注意SYSTEM_PROMPT有专项提示 |

> ⚠ **重要**：data.json中002180的total_revenue_yuan字段必须为null（集团264亿收入对MCU分析无意义）。validate_data.py有护栏：total_revenue_yuan > 200亿时报FAIL。

---

### 688385 复旦微电子（FDM）

| 项目 | 内容 |
|------|------|
| **口径** | 年报「主营业务分产品情况」表「智能电表芯片」行（含智能电表MCU、通用低功耗MCU、车规MCU） |
| **获取方式** | Gemini从GCS年报PDF提取（mcu_known_data.json） |
| **置信度** | high（年报直接披露，口径稳定） |
| **MCU历史覆盖** | 2021–2025（5年，上市前数据来自招股书，MCU使用total_proxy推算） |
| **员工数覆盖** | 2021–2025（上市后年报），2018–2020无（招股书未提取） |
| **毛利率覆盖** | 2024（仅1年，历史待PDF提取） |
| **局限性** | 「智能电表芯片」口径自2021年起稳定；2018-2020上市前数据为推算值 |
| **更新方法** | `python extract_mcu_segments.py 688385` |

---

### 688766 普冉股份（Puya）

| 项目 | 内容 |
|------|------|
| **口径** | 年报「主营业务分产品情况」表「微控制器」行（独立披露，区别于NOR Flash和SRAM） |
| **获取方式** | Gemini从GCS年报PDF提取（mcu_known_data.json） |
| **置信度** | high（2022起年报单独披露，数据清晰）；2023年为estimated/low（MCU+VCM Driver合并披露，无法拆分） |
| **MCU历史覆盖** | 2022–2025（4年，上市前数据来自招股书，MCU使用total_proxy推算） |
| **员工数覆盖** | 2021–2025（上市后年报），2018–2021无（招股书未提取） |
| **毛利率覆盖** | 2024（仅1年，历史待PDF提取） |
| **局限性** | 2023年报将MCU与VCM Driver合并，无法拆分纯MCU口径 |
| **更新方法** | `python extract_mcu_segments.py 688766` |

---

### 688595 芯海科技（Chipsea）

| 项目 | 内容 |
|------|------|
| **口径** | 年报管理层讨论章节「MCU芯片」行（不含AIoT芯片、不含模拟信号链芯片） |
| **获取方式** | Gemini从GCS年报PDF提取 + YoY反推核验（mcu_known_data.json） |
| **置信度** | high（2020-2025全部经年报原文或YoY反推核验，Gemini提取误差<0.01%） |
| **MCU历史覆盖** | 2020–2025（6年，上市前2018–2019来自招股书，MCU使用total_proxy推算） |
| **员工数覆盖** | 2020–2025（上市后年报），2018–2019无（招股书未提取） |
| **毛利率覆盖** | ❌ 全部缺失（AKShare不覆盖，待PDF提取） |
| **局限性** | 年报同时披露「MCU和AIoT芯片合计」和「MCU芯片单行」——本库使用单行MCU口径；2019年及以前无GCS PDF |
| **更新方法** | `python extract_mcu_segments.py 688595`；注意SYSTEM_PROMPT已指定只取MCU芯片单行 |

> ⚠ **核验注意**：管理层讨论有时先披露「MCU和AIoT芯片合计X万元」，再分别披露「MCU芯片Y万元」「AIoT芯片Z万元」。核验时须对比单行MCU数字，不要用合计数比较。

---

### 688391 钜泉科技（Hi-Trend）

| 项目 | 内容 |
|------|------|
| **口径** | 总营收 × 0.34（计量MCU/SoC系数，其余为计量模块和方案服务） |
| **获取方式** | fetch_mcu_data.py自动推算（total_proxy策略） |
| **置信度** | medium（系数基于产品结构估算） |
| **MCU历史覆盖** | 2018–2025（8年完整，2018–2020来自招股书） |
| **员工数覆盖** | 2022–2025（上市后年报），2018–2020无（招股书未提取） |
| **毛利率覆盖** | ❌ 全部缺失（AKShare不覆盖，待PDF提取） |
| **局限性** | 系数0.34为静态估算，应用场景单一（智能电表计量），实际占比相对稳定 |
| **更新方法** | `python fetch_mcu_data.py 688391` |

---

### 688018 乐鑫科技（Espressif）

| 项目 | 内容 |
|------|------|
| **口径** | 年报「主营业务分产品情况」表「芯片」行（区别于「模组」收入） |
| **获取方式** | Gemini从GCS年报PDF提取（mcu_known_data.json） |
| **置信度** | high（年报直接披露芯片/模组两段，口径清晰） |
| **MCU历史覆盖** | 2019–2025（7年，2018年来自招股书，MCU使用total_proxy推算） |
| **员工数覆盖** | 2019–2025（上市后年报），2018无（招股书未提取） |
| **毛利率覆盖** | ❌ 全部缺失（AKShare不覆盖，待PDF提取） |
| **局限性** | 「芯片」行含Wi-Fi/BT SoC和RISC-V MCU，属于广义MCU口径；2018年无GCS PDF |
| **更新方法** | `python extract_mcu_segments.py 688018` |

---

## 3. 季度更新标准流程

### 年报季（每年4月）

```bash
# Step 1: 下载新年报PDF（在Colab或本地，需CNINFO Cookie）
python download_reports.py --years 2026

# Step 2: 上传到GCS
python upload_pdfs.py /content/finance_reports

# Step 3: LLM提取MCU分段数据（需Gemini API Key）
python extract_mcu_segments.py   # 全部公司

# Step 4: 提取员工数（需Gemini API Key + GCS访问权限，建议在Colab运行）
python extract_employee_counts.py
# 单公司单年：python extract_employee_counts.py --symbol 603986 --year 2025
# Dry run：   python extract_employee_counts.py --dry-run

# Step 5: 人工核验mcu_known_data.json和data.json中新写入的条目
#   - 对照年报原文确认MCU数值（核验方法见第4节）
#   - 员工数核对年报「员工情况」章节「报告期末在职员工总数」

# Step 6: 更新AKShare总收入数据（需能访问eastmoney.com，建议在Cloud Shell/本地跑）
python fetch_mcu_data.py

# Step 7: 数据质检
python validate_data.py          # 必须exit 0
python check_extraction.py       # 查看异常flag

# Step 8: 提交
git add data.json mcu_known_data.json
git commit -m "feat(data): update YYYY annual report data"
git push
```

### extract_employee_counts.py 使用说明

该脚本通过 Gemini REST API 直读 GCS 中的年报 PDF，提取「报告期末在职员工总数」。

```bash
# 全量（11家公司，跳过已有数据）
python extract_employee_counts.py

# 单公司
python extract_employee_counts.py --symbol 603986

# 强制重跑（覆盖已有数据）
python extract_employee_counts.py --force

# 环境变量
export VITE_GEMINI_API_KEY="your-key"
export GCS_BUCKET="st-finance-reports"     # 默认值
export GCP_PROJECT="st-china-ai-force"     # 默认值
```

**注意**：上市前年份（招股书数据）的员工数 **不会** 由此脚本填入，因为脚本过滤了招股书PDF（文件名含「招股书」的跳过）。

### Colab使用注意事项

1. **Git推送**：Colab无法直接push GitHub（需PAT）。替代方案：`print(open("mcu_known_data.json").read())` 复制内容给Claude Code合并，或将结果JSON粘贴给Claude Code手动写入
2. **Git冲突**：`git pull --rebase`前先`git stash`；rebase产生冲突且commit已在远端时用`git rebase --skip`
3. **JSON损坏**：冲突标记会导致JSON解析失败；用`git checkout HEAD -- mcu_known_data.json`还原

### fetch_mcu_data.py在隔离环境中的风险

Claude Code远端环境无法访问AKShare，运行fetch_mcu_data.py会**清空data.json的financials字段**。
- 规避方法：用`git checkout HEAD -- data.json`还原，再用Python脚本手动patch字段
- 不要在Claude Code session中运行`python fetch_mcu_data.py`

---

## 4. 数据质检方法

### 自动质检

```bash
python validate_data.py                 # schema检查，必须exit 0才能提交
python check_extraction.py              # 全部公司
python check_extraction.py 688595       # 单家
python check_extraction.py --flag-only  # 只显示异常
```

`check_extraction.py` 检验项目：
- MCU/总营收占比超100%（硬错误，exit 1）
- 同比异常（跌幅>60%或涨幅>200%）
- 毛利率范围（10-75%）
- 跨年量级变异系数（CV>1.5）
- 低置信度条目（confidence=low）

### 人工核验方法

**YoY反推法**（推荐，最可靠）
```
已知今年值 A 和同比增速 r → 去年值 = A / (1 + r)
例：688595 2022年28,899.59万，YoY-2.09% → 2021年 = 28899.59/0.9791 = 29,516万
```

**单位验证法**（防止千元/万元/元混淆）
```
将yuan值 / 汇率 / 1,000,000 → M$值，与已知M$数字比对
若相差10倍，说明单位处理有误（常见：千元误作万元）
```

**跨行比较陷阱（688595特有）**
- 管理层讨论有「MCU和AIoT合计」和「MCU单行」两个数字
- 核验时必须用相同口径的数字比较，不能用合计数验证单行提取值

---

## 5. Dashboard 使用指南

### 5.1 语言切换（中/英）

页面右上角有 **EN / 中** 切换按钮，点击后：
- 界面所有文字（标签、图表标题、图例、说明文字、详情面板）切换为英文
- 图表中公司名称切换为英文简称（如「兆易创新」→「GigaDevice」）
- 偏好保存在浏览器 localStorage，刷新后保持
- 适合对外演示或英文报告截图

### 5.2 数据置信度颜色说明

主表格和图表中 MCU 营收数据点旁的彩色圆点：

| 颜色 | 含义 | 对应数据来源 |
|------|------|-------------|
| 🟢 绿（dc） | 年报直接披露 | segment_reported / segment_industrial / segment_estimated 策略，有年报原文依据 |
| 🔵 青（dd） | 推算（高置信） | total_proxy × k 策略，总收入 × 稳定系数 |
| 🟠 橙（dp） | 估算（低置信） | estimated 策略，基于IR/招股书/第三方估算 |
| 🔴 红（du） | 数据缺失 | 该年份无可用数据 |

YoY 同比颜色：**绿色**为增长，**红色**为下滑。

### 5.3 主表格使用

- **点击任意行**：打开右侧详情面板，显示该公司完整8年历史数据
- **点击列标题**：按该列排序（再次点击切换升/降序）
- **搜索框**：支持公司名、股票代码、代工厂名称搜索
- **年份选择**：切换表格中「当年」指标列（YoY、MCU营收等）的参考年份
- **货币切换（USD/CNY）**：影响金额列显示单位（M$ ↔ 亿元），不影响百分比列

### 5.4 详情面板（右侧展开）

点击公司行后展开，包含：

| 区域 | 内容 |
|------|------|
| **MCU口径策略** | 策略标签（SEG·RPT / TOTAL×k 等）+ 说明文字 + 数据覆盖警告 |
| **8年趋势图** | 总营收（蓝色柱）vs MCU营收（深色柱），叠加毛利率折线（如有） |
| **关键指标** | 当年7项KPI；每项右上角的 **?** 按钮可查看计算公式和数据说明 |
| **公司画像** | 核心架构、代工厂、策略、置信度 |
| **年报历史** | 8年逐年表格；上市前年份标注 **招股书** 徽章；表格下方有数据来源脚注 |

**数据来源脚注**（自动显示）：
- 若存在上市前年份：说明哪些年份数据来自招股书，为何毛利率/员工数为「—」
- 若毛利率全部缺失：注明 AKShare 未覆盖，需PDF提取
- 若毛利率部分缺失：列出具体缺失年份

### 5.5 MCU 11家合计汇总条

MCU营收图表下方显示所选公司和年份范围的：
- 逐年合计 M$ 值
- 最新年 YoY 同比
- 整体 CAGR

可通过图表控制栏的"公司"多选或取消公司来调整合计范围。

### 5.6 YoY热力图

展示11家公司 × 8年的营收同比增速：
- 上行（总营收YoY）/ 下行（MCU YoY）双排
- 颜色深浅反映增速幅度（绿=增长，红=下滑）
- 空白格表示该年份无数据或无法计算同比

---

## 6. 常见问题排查

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| `validate_data.py` FAIL: mcu_strategy invalid | 新增了VALID_MCU_STRATEGY白名单之外的策略值 | 在validate_data.py第23行添加新策略名 |
| `validate_data.py` FAIL: 002180 total_revenue_yuan > 200亿 | 误将纳思达集团总收入写入 | 将002180的total_revenue_yuan设为null |
| `mcu_known_data.json` JSONDecodeError | 合并冲突留下`<<<<`标记，或trailing comma | `git checkout HEAD -- mcu_known_data.json` |
| `extract_mcu_segments.py` "No eligible PDFs" | GCS路径不匹配或年份过滤 | 检查GCS文件夹命名（`{symbol}_{name_cn}/`） |
| `extract_employee_counts.py` UnicodeEncodeError | google.genai SDK 在HTTP header中对中文编码失败 | 脚本已改为直接调用Gemini REST API（requests.post），无需修改 |
| `extract_employee_counts.py` SKIP "Here is the JSON requested" | Gemini responseMimeType截断响应 | 已移除responseMimeType参数，当前版本无此问题 |
| `extract_employee_counts.py` JSON parse error | Gemini输出千分位逗号如`1,781` | 脚本已内置清洗逻辑，若仍失败检查maxOutputTokens是否≥1000 |
| Gemini提取值与年报不符 | 提取了错误产品行（常见：漏取某行，或取了合计行） | 用`--debug`模式查看原始响应，对照年报确认行名 |
| `fetch_mcu_data.py`清空data.json | AKShare在隔离环境不可用，返回空dict | `git checkout HEAD -- data.json` 还原 |
| BQ write failed: Unrecognized name | bq_writer.py SQL字段与BQ表schema不符 | 检查bq_writer.py的`_merge_financials()`，对照bigquery_schema.sql |
| Dashboard语言切换后图表未更新 | 切换时setLang()调用了rerenderCharts()，但图表DOM尚未就绪 | 刷新页面，或等待图表初始化完成后再切换语言 |
| 招股书年份的员工数显示「—」 | extract_employee_counts.py只处理年报PDF，不处理招股书 | 如需填入上市前员工数，需单独从招股书PDF提取并手动写入data.json |

---

*最后更新：2026-05-25 | 维护人：参见git log*
