# MCU Regional Competitor Dashboard

面向半导体行业分析师的中国大陆 MCU 上市公司竞争情报仪表盘。

**GCP 项目：** `st-china-ai-force` · **BQ Dataset：** `mcu` (asia-east1) · **GCS：** `st-finance-reports`

---

## 快速开始

### 本地 / Cloud Shell

```bash
git clone https://github.com/Yude-Jiang/mcu-regional-competitor-dashboard
cd mcu-regional-competitor-dashboard
git checkout claude/code-review-hlYFP

pip install -r requirements_cloudrun.txt
python app.py          # → http://localhost:8080
```

### 数据更新（每季报季）

```bash
# 1. 同步 AKShare 财务数据（需能访问 eastmoney.com）
python smart_sync.py

# 2. 下载新季报 PDF（需 CNINFO Cookie，在 Colab/本地跑）
export CNINFO_COOKIE="routeId=.uc2; ..."
python download_reports.py --years 2025 2026

# 3. 上传 PDF 到 GCS + 写入 BQ pdf_index
python upload_pdfs.py /content/finance_reports

# 4. LLM 提取 MCU 分段营收（需 DeepSeek API Key）
python extract_mcu_segments.py
```

---

## 架构一览

```
数据采集层 (离线/Colab/Cloud Shell)
  AKShare (免认证)              CNINFO (需Cookie)
  ├── fetch_mcu_data.py         download_reports.py
  ├── fetch_yjbb_quarterly.py   upload_pdfs.py
  └── smart_sync.py (编排)      extract_mcu_segments.py (LLM)
         ↓                              ↓
    data.json (本地缓存)    BigQuery: st-china-ai-force.mcu
                            ├── financials
                            ├── mcu_segments
                            ├── pdf_index
                            └── qa_cache
                                    +
                            GCS: gs://st-finance-reports/reports/

服务层 (Cloud Run, asia-east1)
  app.py (Flask)
  ├── GET  /              → dashboard.html
  ├── GET  /data.json     → 静态财务缓存
  ├── GET  /api/doc-status → BQ pdf_index 矩阵
  ├── POST /api/refresh   → [Phase 3] 触发下载+提取
  └── POST /api/ask       → [Phase 5] AI问答
```

详细 PRD 见 [docs/PRD.md](docs/PRD.md)

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `dashboard.html` | 前端（Chart.js · 可排序表格 · Detail Panel） |
| `app.py` | Flask 服务 + API stub |
| `smart_sync.py` | 3步编排：AKShare → data.json → validate |
| `fetch_mcu_data.py` | AKShare 利润表 + MCU推算 + BQ同步 |
| `fetch_yjbb_quarterly.py` | 毛利率/净利润YoY + BQ同步 |
| `download_reports.py` | CNINFO PDF 下载（Colab/本地） |
| `upload_pdfs.py` | PDF → GCS + BQ pdf_index |
| `extract_mcu_segments.py` | LLM MCU分段提取（DeepSeek/Gemini） |
| `bq_writer.py` | BigQuery UPSERT 工具库 |
| `validate_data.py` | data.json schema 检查 |
| `companies_meta.json` | 11家公司静态元数据 |
| `mcu_known_data.json` | 手工/LLM 高置信MCU营收（优先级最高） |
| `data.json` | 财务缓存（smart_sync.py 生成） |
| `bigquery_schema.sql` | 4张表 DDL |
| `setup_gcp.py` | GCP 一次性初始化 |
| `cloudbuild.yaml` | Cloud Run 部署 (asia-east1) |
| `requirements_cloudrun.txt` | 生产依赖 |
