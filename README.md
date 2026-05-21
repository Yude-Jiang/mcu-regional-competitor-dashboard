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
| `fetch_ir_records.py` | CNINFO IR记录 → LLM MCU数据补全（002180/300077等） |
| `static_chartjs.js` | Chart.js 4.4.0 本地打包（CDN 沙盒不可用） |

---

## 运维日志

### 2026-05-21 — IR记录提取 + Gemini SDK迁移 + Vertex AI GCS路径

#### 背景
部分公司（002180纳思达、300077国民技术、688385复旦微电、688018乐鑫、688595芯海）
年报未直接披露 MCU 口径，尝试通过 CNINFO 投资者关系活动记录补全。

#### 完成内容
1. 新建 `fetch_ir_records.py` — 从巨潮查询 IR 公告、下载 PDF、LLM 提取 MCU 数字
2. 将 `extract_mcu_segments.py` 的 Gemini SDK 从废弃的 `google-generativeai` 迁移到 `google-genai`
3. 新增 Vertex AI GCS URI 路径，避免下载超大 PDF

#### 经验与教训

**CNINFO API 行为（重要）**
- `category_iractivty_szsh` **不只**返回IR记录，而是返回公司**所有**公告（担保/法律意见书/董事会决议全混在里面）
- `searchkey` 只支持单个关键词的子串匹配，**不支持 OR / AND 语法**（`"MCU OR 微控制器"` 被当作完整字符串匹配）
- `announcementTime` 字段返回的是 **Unix 毫秒时间戳（int）**，不是字符串；直接用 `re.search(r"20\\d{2}", ...)` 会 TypeError
- `seDate` 格式：`"YYYY-MM-DD~YYYY-MM-DD"`（波浪号前后**无空格**）
- 正确策略：逐个关键词单独请求 + 客户端标题过滤；CATEGORY_IR 通道仍需标题过滤（类别不可信）
- "关于举办/举行...说明会的**公告**" 是会前通知（1-2页，无财务数据），和"投资者关系活动**记录表**"是不同文件

**002180纳思达 / 300077国民技术 IR 记录**
- 两家公司在巨潮上**没有**上传独立的投资者关系活动记录 PDF
- 纳思达极海MCU数据应从年报子公司披露章节提取（`extract_mcu_segments.py`）
- 国民技术MCU比例需人工访问[互动易](https://irm.cninfo.com.cn/)查询

**Gemini SDK 迁移：google-generativeai → google-genai**
- 旧包 `google-generativeai` 已停止维护，新包是 `google-genai`
- import 路径变化：`import google.generativeai as genai` → `from google import genai`
- 初始化：`genai.configure(api_key=)` → `client = genai.Client(api_key=)`
- 文件上传参数：`genai.upload_file(pdf_path, mime_type=...)` → `client.files.upload(file=pdf_path, config={"mime_type":...})`（注意参数名是 `file=` 不是 `path=`）
- 生成：`model.generate_content(...)` → `client.models.generate_content(model=..., contents=..., config=...)`
- fetch_ir_records.py 的 `call_llm_gemini()` 同步迁移，用 `GenerateContentConfig(system_instruction=...)`

**Vertex AI GCS URI 路径（针对超大PDF）**
- 目标：不下载PDF，直接让 Gemini 读 GCS URI（`gs://bucket/path.pdf`）
- 纳思达2024年报超过200MB，`gsutil cp` 600秒超时，Python SDK 120秒超时
- `google.genai(vertexai=True)` 使用 **v1beta1** 端点，该项目中全部模型 404
- 改用官方 `vertexai` SDK（`pip install google-cloud-aiplatform`）使用 **v1** 端点
- ADC 问题：Cloud Shell 元数据服务有时缺少 service account `email` 字段
  → 解决：`gcloud auth application-default login`
- 多模型/多 region 自动 fallback 策略：6个模型 × 4个region（us-central1 / us-east4 / asia-east1 / asia-northeast1）遍历，遇 404 跳过
- 下载顺序改为：`gcloud storage cp` → `gsutil cp` → Python SDK（timeout=None）

**文件下载超时**
- 纳思达年报 PDF 文件名含中文（`002180_纳思达/2024_年报_...`），subprocess 列表传参不存在转义问题
- 真正原因：文件过大，非网络或编码问题
- `gcloud storage cp` 比 `gsutil cp` 速度更快（推荐优先）
- Python SDK 设 `timeout=None` 作为最终兜底
