# MCU Regional Competitor Dashboard — PRD

> 最后更新：2026-05-20

---

## 一、产品目标

构建面向半导体行业分析师的中国大陆 MCU 上市公司竞争情报仪表盘，整合年报财务数据、LLM提取的MCU分段营收、AI问答功能，通过 Cloud Run 部署，中国大陆可稳定访问。

**核心价值：**
- 自动获取11家A股MCU公司2018–2026年的财务数据（营收/利润/研发/员工）
- 补全年报中MCU分段营收数据（AKShare无法直接获取）
- 可视化呈现竞争格局（图表/表格/sparkline）
- 支持季报更新和AI问答

---

## 二、GCP 配置

| 资源 | 值 |
|------|----|
| Project | `st-china-ai-force` |
| BigQuery Dataset | `mcu` (location: asia-east1) |
| GCS Bucket | `st-finance-reports` (location: asia-east1) |
| Cloud Run Region | `asia-east1` |
| Secret Manager | `VITE_DEEPSEEK_API_KEY`, `VITE_GEMINI_API_KEY` |

---

## 三、11家目标公司

| 代码 | 公司 | MCU策略 | 置信度 |
|------|------|---------|--------|
| 603986 | 兆易创新 GigaDevice | segment_reported | high |
| 300327 | 中颖电子 SinoWealth | total_proxy ×0.9988 | high |
| 688380 | 中微半导 Cmsemicon | total_proxy ×0.99 | high |
| 300077 | 国民技术 NationZ | estimated | medium |
| 688279 | 峰岹科技 Fortior | total_revenue ×1.0 | high |
| 002180 | 纳思达 Nasda/Geehy | subsidiary_geehy | medium |
| 688385 | 复旦微电子 FDM | segment_estimated | medium |
| 688766 | 普冉股份 Puya | segment_reported | high |
| 688595 | 芯海科技 Chipsea | estimated | low |
| 688391 | 钜泉科技 Hi-Trend | total_revenue ×1.0 | high |
| 688018 | 乐鑫科技 Espressif | segment_estimated | medium |

> 注：普冉股份正确代码为 **688766**（已全项目修正）

---

## 四、架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     数据采集层 (Offline)                         │
│                                                                 │
│  AKShare API (无需认证)         CNINFO (需Cookie, 手动触发)      │
│  股票利润表/yjbb_em/员工数       年报/季报 PDF                   │
│       ↓                              ↓                          │
│  fetch_mcu_data.py             download_reports.py              │
│  fetch_yjbb_quarterly.py       (Colab / 本地)                   │
│       ↓                              ↓                          │
│            BigQuery: st-china-ai-force.mcu                      │
│            ├── financials (主财务表)                             │
│            ├── mcu_segments (LLM提取的MCU分段)                  │
│            ├── pdf_index (文档状态追踪)                          │
│            └── qa_cache (AI问答缓存)                            │
│                    +                                            │
│            GCS: gs://st-finance-reports/reports/               │
│            (PDF原件存档)                                         │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                     服务层 (Cloud Run, asia-east1)               │
│                                                                 │
│  app.py (Flask + Gunicorn)                                      │
│  ├── GET  /                → dashboard.html                     │
│  ├── GET  /data.json       → 静态财务缓存                        │
│  ├── GET  /api/doc-status  → BQ pdf_index 文档状态矩阵           │
│  ├── POST /api/refresh     → [Phase 3] 触发下载+提取             │
│  └── POST /api/ask         → [Phase 5] AI问答                   │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                     前端层 (dashboard.html)                      │
│                                                                 │
│  原生 JS + Chart.js 4.x (CDN)                                   │
│  ├── CSS设计系统 + Dark Mode (localStorage)                      │
│  ├── Filing Pipeline 状态条                                     │
│  ├── 图表区 (MCU堆叠 + 营收/研发)                               │
│  ├── 可排序/搜索表格 + SVG Sparkline                             │
│  └── Detail Panel (点击行展开公司详情)                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 五、数据管道

### 管道A：AKShare 结构化数据（自动）

```
smart_sync.py
  Step 1: fetch_mcu_data.py
    → ak.stock_profit_sheet_by_yearly_em(symbol=...) → 利润表
    → ak.stock_employee_em(symbol=...) → 员工数
    → apply_mcu_strategy() → MCU分段推算
    → compute_metrics() → YoY/CAGR/USD换算
    → 写入 data.json
    → 同步写入 BigQuery mcu.financials

  Step 2: fetch_yjbb_quarterly.py
    → ak.stock_yjbb_em(date=20221231/20231231/20241231)
    → 提取 gross_margin_pct / net_income_yoy_pct
    → merge 到 data.json
    → 同步写入 BigQuery

  Step 3: validate_data.py → schema检查
```

### 管道B：CNINFO PDF（手动，需Cookie）

```
download_reports.py (本地/Colab)
  → CNINFO hisAnnouncement/query API
  → 按 adjunctSize 降序，过滤摘要，取最大文件
  → 下载到 ./downloaded_reports/{symbol}_{name}/

upload_pdfs.py
  → 上传到 gs://st-finance-reports/reports/{symbol}_{name}/
  → 写入 BigQuery mcu.pdf_index
```

### 管道C：LLM MCU分段提取（Phase 4）

```
extract_mcu_segments.py
  → 从 GCS 列出目标年报PDF
  → pdfplumber 提取文本（关键词评分选页）
  → DeepSeek V3 API (主) / Gemini 2.0 Flash (备)
  → 写入 BigQuery mcu.mcu_segments
  → 更新 mcu_known_data.json（高置信数据覆盖推算值）
```

---

## 六、MCU营收推算方法论

| 策略 | 公司 | 方法 |
|------|------|------|
| `total_revenue` | 峰岹/钜泉 | MCU=总营收（纯MCU公司）|
| `total_proxy` | 中颖/中微 | MCU=总营收×系数 |
| `segment_reported` | 兆易/普冉 | 年报分产品表直接读取 |
| `segment_estimated` | 复旦微/乐鑫 | 年报分段提取 |
| `subsidiary_geehy` | 纳思达 | 极海子公司数据汇总 |
| `estimated` | 国民/芯海 | 估算，置信度低 |

优先级：`mcu_known_data.json` (手工/LLM) > AKShare 推算

**FX 汇率字典：**
```python
FX = {2018:6.617, 2019:6.899, 2020:6.900, 2021:6.452,
      2022:6.737, 2023:7.075, 2024:7.243, 2025:7.260, 2026:7.260}
```

---

## 七、Dashboard 功能（已完成 P0–P2）

- **CSS设计系统**：Brand palette（navy/cyan/yellow/magenta）+ Dark mode
- **Filing Pipeline 条**：11家公司卡片，置信度边框颜色
- **图表**：MCU堆叠柱状图 + 营收/研发并排（Chart.js 4.x）
- **表格**：14列，可排序/搜索，SVG Sparkline
- **Detail Panel**：点击行展开右侧抽屉，8年趋势 mini chart

**待实现：**
- Phase 3：刷新面板（Cookie输入 + SSE进度 + CNINFO触发）
- Phase 5：AI问答（POST /api/ask → DeepSeek/Gemini）

---

## 八、已上传PDF清单（74个）

`gs://st-finance-reports/reports/`

| 公司 | 年份覆盖 |
|------|---------|
| 002180_纳思达 | 2018–2025年报 (8个) |
| 300077_国民技术 | 2018–2025年报 (8个) |
| 300327_中颖电子 | 2018–2025年报 (8个) |
| 603986_兆易创新 | 2018–2025年报 + 2026Q1 (9个) |
| 688018_乐鑫科技 | 2019–2025年报 (7个) |
| 688279_峰岹科技 | 2022–2025年报 + 2026Q1 (5个) |
| 688380_中微半导 | 2022–2025年报 + 2026Q1 (5个) |
| 688385_复旦微电 | 2021–2025年报 + 2026Q1 (6个) |
| 688391_钜泉科技 | 2022–2025年报 + 2026Q1 (5个) |
| 688595_芯海科技 | 2020–2025年报 + 2026Q1 (7个) |
| 688766_普冉股份 | 2021–2025年报 + 2026Q1 (6个) |

---

## 九、待办事项

### 高优先级
- [ ] 运行 `smart_sync.py`（Cloud Shell，AKShare Step 1 已修复）
- [ ] 运行 `extract_mcu_segments.py`（Cloud Shell，读 Secret Manager）
- [ ] 手工录入普冉 2018–2021 IPO招股书数据到 `mcu_known_data.json`

### 中优先级
- [ ] Phase 3：刷新面板（Dashboard 内 Cookie输入 + SSE进度）
- [ ] Phase 5：AI问答（/api/ask endpoint）

### 低优先级
- [ ] Cloud Run 部署（`cloudbuild.yaml`，asia-east1）

---

## 十、已知问题

1. **AKShare `stock_profit_sheet_by_yearly_em`**：新版参数名从 `stock=` 改为 `symbol=`，已修复（commit cdadc4b）
2. **AKShare 防火墙**：在 Claude Code remote sandbox 中被拦，需在 Cloud Shell / 本地跑
3. **BQ location**：必须在 asia-east1 创建，US location 无法访问
4. **Colab shell 命令**：需加 `!` 前缀（`!git clone ...`，`!python ...`）
5. **CNINFO Cookie TTL 短**：每次下载前从浏览器 DevTools 重新复制
