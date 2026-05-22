# MCU竞品数据库 用户手册

**版本：** v1.0 · **更新：** 2026-05-22  
**受众：** 接手季度维护的同事，需能独立完成数据更新和核验  
**配套文件：** CLAUDE.md（操作规范）· README.md（运维日志）

---

## 目录

1. [数据架构一览](#1-数据架构一览)
2. [各公司MCU数据来源说明](#2-各公司mcu数据来源说明)
3. [季度更新标准流程](#3-季度更新标准流程)
4. [数据质检方法](#4-数据质检方法)
5. [常见问题排查](#5-常见问题排查)

---

## 1. 数据架构一览

```
mcu_known_data.json     ← 手工/LLM提取的MCU分段数据（最高优先级）
        ↓
fetch_mcu_data.py       ← 应用mcu_strategy推算，生成data.json
        ↓
data.json               ← 财务时序主库，供dashboard读取
        ↓
dashboard.html          ← 前端可视化（Flask via app.py）
```

**字段单位规范：**
- `_yuan` 后缀：CNY 元（原始值）
- `_musd` 后缀：百万美元（按年度汇率换算）
- 禁止使用 `_cny` 后缀（已废弃）
- `mcu_known_data.json` 必须用 CNY 元存储，禁止用 M$

---

## 2. 各公司MCU数据来源说明

### 603986 兆易创新（GigaDevice）

| 项目 | 内容 |
|------|------|
| **口径** | 年报「主营业务分产品情况」表「微控制器」行（部分年份为「MCU及模拟产品」合并披露，已内部拆分） |
| **获取方式** | 手工录入（mcu_known_data.json），2018-2024数据来自年报PDF人工提取 |
| **置信度** | high |
| **历史覆盖** | 2018-2025（8年完整） |
| **局限性** | 2024年年报将MCU与模拟产品合并披露（¥17.06亿），内部拆分后纯MCU为¥16.90亿，有估算成分 |
| **更新方法** | 每年4月年报发布后，从巨潮PDF提取分产品表，写入mcu_known_data.json |

---

### 300327 中颖电子（SinoWealth）

| 项目 | 内容 |
|------|------|
| **口径** | 年报「主营业务分产品情况」表「工业控制芯片」行（含BMIC电池管理芯片，非纯MCU，为官方披露口径） |
| **获取方式** | 2019-2022：Gemini从GCS年报PDF提取；2023-2025：手工录入 |
| **置信度** | medium（工业控制口径包含非MCU产品） |
| **历史覆盖** | 2019-2025（7年，2018年无GCS PDF） |
| **局限性** | 「工业控制芯片」含BMIC，高于纯MCU口径约10-15%；2018年数据缺失 |
| **更新方法** | `python extract_mcu_segments.py 300327` |

---

### 688380 中微半导（Cmsemicon）

| 项目 | 内容 |
|------|------|
| **口径** | 总营收 × 0.99（纯MCU公司，非MCU收入<1%） |
| **获取方式** | fetch_mcu_data.py自动推算（total_proxy策略） |
| **置信度** | high |
| **历史覆盖** | 2018-2025（8年完整） |
| **局限性** | 极少量非MCU收入（技术服务等）未扣除 |
| **更新方法** | `python fetch_mcu_data.py 688380`（AKShare自动拉取总收入） |

---

### 300077 国民技术（NationZ）

| 项目 | 内容 |
|------|------|
| **口径** | 港股招股书「芯片产品」收入（含通用MCU + 专业市场芯片 + BMS + RF，非纯MCU） |
| **获取方式** | 手工录入，来源：《国民技术股份有限公司全球发售招股章程》2026年3月（港交所披露编号2026031300050） |
| **置信度** | medium（芯片产品口径含安全芯片等，非纯MCU）|
| **历史覆盖** | 2022-2025（4年，招股书覆盖范围） |
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
| **历史覆盖** | 2018-2025（8年完整） |
| **局限性** | 系数0.67为静态估算，实际MCU占比随产品结构变化；年报未拆分MCU vs ASIC vs HVIC |
| **更新方法** | `python fetch_mcu_data.py 688279` |

---

### 002180 纳思达（Nasda / Geehy）

| 项目 | 内容 |
|------|------|
| **口径** | 年报「营业收入构成」表「芯片」产品行（极海微电子子公司MCU产品线代理口径） |
| **获取方式** | Gemini从GCS年报PDF提取（mcu_known_data.json） |
| **置信度** | medium（年报不单独披露极海MCU，「芯片」产品行为最佳可得口径） |
| **历史覆盖** | 2018-2025（7年，2020年提取失败缺失） |
| **局限性** | 纳思达集团总收入约264亿（含打印耗材业务），**严禁**将集团总收入写入total_revenue_yuan；「芯片」行包含极海全部芯片产品，不等于纯MCU |
| **更新方法** | `python extract_mcu_segments.py 002180`；注意SYSTEM_PROMPT有专项提示 |

> ⚠ **重要**：data.json中002180的total_revenue_yuan字段必须为null（集团264亿收入对MCU分析无意义）。validate_data.py有护栏：total_revenue_yuan > 200亿时报FAIL。

---

### 688385 复旦微电子（FDM）

| 项目 | 内容 |
|------|------|
| **口径** | 年报「主营业务分产品情况」表「智能电表芯片」行（含智能电表MCU、通用低功耗MCU、车规MCU） |
| **获取方式** | Gemini从GCS年报PDF提取（mcu_known_data.json） |
| **置信度** | high（年报直接披露，口径稳定） |
| **历史覆盖** | 2021-2025（5年，2021年上市，上市前数据不追溯） |
| **局限性** | 「智能电表芯片」口径自2021年起稳定；2018-2020年上市前数据不可获取 |
| **更新方法** | `python extract_mcu_segments.py 688385` |

---

### 688766 普冉股份（Puya）

| 项目 | 内容 |
|------|------|
| **口径** | 年报「主营业务分产品情况」表「微控制器」行（独立披露，区别于NOR Flash和SRAM） |
| **获取方式** | Gemini从GCS年报PDF提取（mcu_known_data.json） |
| **置信度** | high（2022起年报单独披露，数据清晰）；2023年为estimated/low（MCU+VCM Driver合并披露，无法拆分） |
| **历史覆盖** | 2022-2025（4年，2022年上市，上市前数据不追溯） |
| **局限性** | 2023年报将MCU与VCM Driver合并为「存储+系列」，无法拆分纯MCU口径 |
| **更新方法** | `python extract_mcu_segments.py 688766` |

---

### 688595 芯海科技（Chipsea）

| 项目 | 内容 |
|------|------|
| **口径** | 年报管理层讨论章节「MCU芯片」行（不含AIoT芯片、不含模拟信号链芯片） |
| **获取方式** | Gemini从GCS年报PDF提取 + YoY反推核验（mcu_known_data.json） |
| **置信度** | high（2020-2025全部经年报原文或YoY反推核验，Gemini提取误差<0.01%） |
| **历史覆盖** | 2020-2025（6年） |
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
| **历史覆盖** | 2018-2025（8年完整） |
| **局限性** | 系数0.34为静态估算，应用场景单一（智能电表计量），实际占比相对稳定 |
| **更新方法** | `python fetch_mcu_data.py 688391` |

---

### 688018 乐鑫科技（Espressif）

| 项目 | 内容 |
|------|------|
| **口径** | 年报「主营业务分产品情况」表「芯片」行（区别于「模组」收入） |
| **获取方式** | Gemini从GCS年报PDF提取（mcu_known_data.json） |
| **置信度** | high（年报直接披露芯片/模组两段，口径清晰） |
| **历史覆盖** | 2019-2025（7年，2019年上市，2018年无数据） |
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

# Step 4: 人工核验mcu_known_data.json中新写入的条目
#   - 对照年报原文确认数值
#   - 核验方法见第4节

# Step 5: 更新AKShare总收入数据（需能访问eastmoney.com，建议在Cloud Shell/本地跑）
python fetch_mcu_data.py

# Step 6: 数据质检
python validate_data.py          # 必须exit 0
python check_extraction.py       # 查看异常flag

# Step 7: 提交
git add data.json mcu_known_data.json
git commit -m "feat(data): update YYYY annual report data"
git push
```

### Colab使用注意事项

1. **Git推送**：Colab无法直接push GitHub（需PAT）。替代方案：`print(open("mcu_known_data.json").read())` 复制内容给Claude Code合并
2. **Git冲突**：`git pull --rebase`前先`git stash`；rebase产生冲突且commit已在远端时用`git rebase --skip`
3. **JSON损坏**：冲突标记会导致JSON解析失败；用`git checkout HEAD -- mcu_known_data.json`还原，或从GitHub raw URL拉取

### fetch_mcu_data.py在隔离环境中的风险

Claude Code远端环境无法访问AKShare，运行fetch_mcu_data.py会**清空data.json的financials字段**。
- 规避方法：用`git checkout HEAD -- data.json`还原，再用Python脚本手动patch字段
- 不要在Claude Code session中运行`python fetch_mcu_data.py`

---

## 4. 数据质检方法

### 自动质检

```bash
python check_extraction.py              # 全部公司
python check_extraction.py 688595       # 单家
python check_extraction.py --flag-only  # 只显示异常
```

检验项目：
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

## 5. 常见问题排查

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| `validate_data.py` FAIL: mcu_strategy invalid | 新增了VALID_MCU_STRATEGY白名单之外的策略值 | 在validate_data.py第23行添加新策略名 |
| `validate_data.py` FAIL: 002180 total_revenue_yuan > 200亿 | 误将纳思达集团总收入写入 | 将002180的total_revenue_yuan设为null |
| mcu_known_data.json JSONDecodeError | 合并冲突留下`<<<<`标记，或trailing comma | `git checkout HEAD -- mcu_known_data.json` |
| extract_mcu_segments.py "No eligible PDFs" | GCS路径不匹配或年份过滤 | 检查GCS文件夹命名（`{symbol}_{name_cn}/`） |
| Gemini提取值与年报不符 | 提取了错误产品行（常见：漏取某行，或取了合计行） | 用`--debug`模式查看原始响应，对照年报确认行名 |
| fetch_mcu_data.py清空data.json | AKShare在隔离环境不可用，返回空dict | `git checkout HEAD -- data.json` 还原 |
| BQ write failed: Unrecognized name | bq_writer.py SQL字段与BQ表schema不符 | 检查bq_writer.py的`_merge_financials()`，对照bigquery_schema.sql |

---

*最后更新：2026-05-22 | 维护人：参见git log*
