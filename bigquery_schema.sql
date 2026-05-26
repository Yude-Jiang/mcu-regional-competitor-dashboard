-- BigQuery schema for MCU Regional Competitor Dashboard
-- Dataset: mcu  (create with: bq mk --location=asia-east2 ${PROJECT_ID}:mcu)
-- Run: python setup_gcp.py  to create all tables and GCS bucket

-- ── 主财务表（AKShare聚合数据 + MCU分段数据）──────────────────────────────

CREATE TABLE IF NOT EXISTS `{project}.mcu.financials` (
  -- 主键
  symbol          STRING  NOT NULL,   -- e.g. "603986"
  year            INT64   NOT NULL,   -- e.g. 2024
  period          STRING  NOT NULL,   -- "年报" | "一季报" | "半年报" | "三季报"

  -- 公司基础信息（冗余存储，方便查询）
  name_cn         STRING,
  name_en         STRING,
  market          STRING,             -- SH / SZ / STAR

  -- AKShare 利润表字段（元）
  total_revenue_yuan    FLOAT64,
  net_income_yuan       FLOAT64,
  rd_expense_yuan       FLOAT64,

  -- USD换算（M$）
  total_revenue_musd    FLOAT64,
  net_income_musd       FLOAT64,
  rd_expense_musd       FLOAT64,

  -- 衍生指标
  rd_pct                FLOAT64,      -- 研发费用/营收 %
  revenue_yoy_pct       FLOAT64,      -- 营收同比 %
  cagr_pct              FLOAT64,      -- 营收CAGR %
  cagr_label            STRING,       -- e.g. "CAGR 2018–2024"

  -- AKShare yjbb_em 字段（季报快照）
  gross_margin_pct      FLOAT64,      -- 毛利率 %
  net_income_yoy_pct    FLOAT64,      -- 净利润同比 %

  -- 员工数
  employee_count        INT64,

  -- MCU 分段数据（PDF提取 或 推算）
  mcu_revenue_yuan      FLOAT64,
  mcu_revenue_musd      FLOAT64,
  mcu_yoy_pct           FLOAT64,
  mcu_weight_pct        FLOAT64,      -- MCU占总营收 %
  mcu_data_type         STRING,       -- reported | derived | estimated | unavailable
  mcu_confidence        STRING,       -- high | medium | low | na
  mcu_source            STRING,       -- 数据来源描述
  mcu_strategy          STRING,       -- total_revenue | total_proxy | segment_reported | ...

  -- Filing pipeline 字段
  filing_status         STRING,       -- reported | derived | estimated | pending
  filing_date           STRING,       -- e.g. "2024-04-30"
  data_coverage         FLOAT64,      -- 0.0–1.0，有多少字段有值

  -- 元数据
  akshare_updated_at    TIMESTAMP,    -- AKShare 数据最后更新时间
  pdf_extracted_at      TIMESTAMP,    -- PDF提取完成时间（NULL=未提取）
  updated_at            TIMESTAMP     -- 本行最后写入时间
)
PARTITION BY RANGE_BUCKET(year, GENERATE_ARRAY(2015, 2030, 1))
CLUSTER BY symbol, period
OPTIONS (description = "MCU公司财务数据，AKShare + PDF提取合并");

-- ── PDF 文档索引表（文档状态追踪）──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS `{project}.mcu.pdf_index` (
  symbol              STRING  NOT NULL,
  year                INT64   NOT NULL,
  report_type         STRING  NOT NULL,  -- 年报 | 一季报 | 半年报 | 三季报

  -- 文档元数据
  title               STRING,            -- 原始公告标题
  announcement_id     STRING,            -- CNINFO announcement ID
  gcs_path            STRING,            -- gs://bucket/reports/603986/2024_年报.pdf
  file_size_kb        INT64,

  -- 下载状态
  download_status     STRING,            -- downloaded | failed | pending | skipped
  download_error      STRING,            -- 失败原因

  -- 提取状态
  extraction_status   STRING,            -- extracted | failed | pending | not_applicable
  extraction_model    STRING,            -- deepseek-chat | gemini-2.0-flash
  extraction_error    STRING,

  -- 时间戳
  downloaded_at       TIMESTAMP,
  extracted_at        TIMESTAMP,
  created_at          TIMESTAMP,
  updated_at          TIMESTAMP
)
PARTITION BY RANGE_BUCKET(year, GENERATE_ARRAY(2015, 2030, 1))
CLUSTER BY symbol, report_type
OPTIONS (description = "CNINFO PDF文档下载和提取状态索引");

-- ── MCU 分段提取明细表（PDF原始提取结果留档）────────────────────────────────

CREATE TABLE IF NOT EXISTS `{project}.mcu.mcu_segments` (
  symbol              STRING  NOT NULL,
  year                INT64   NOT NULL,
  period              STRING  NOT NULL,

  -- 提取的MCU分段数据
  mcu_revenue_yuan    FLOAT64,
  mcu_revenue_musd    FLOAT64,
  mcu_gross_margin    FLOAT64,          -- MCU产品毛利率（如有）

  -- 其他产品线（视公司而定）
  flash_revenue_yuan  FLOAT64,          -- 兆易创新 Flash营收
  other_revenue_yuan  FLOAT64,

  -- 提取来源
  source_type         STRING,           -- annual_report | ipo_prospectus | quarterly_report
  source_page         STRING,           -- 年报第X页（方便人工核验）
  raw_text_excerpt    STRING,           -- LLM提取时引用的原文片段
  confidence_score    FLOAT64,          -- LLM给出的置信度 0.0–1.0
  extraction_model    STRING,
  extraction_prompt_v STRING,           -- prompt版本号，方便复现

  created_at          TIMESTAMP
)
OPTIONS (description = "PDF提取的MCU分段营收明细，保留原始提取结果用于审计");

-- ── AI 问答缓存表（可选）──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS `{project}.mcu.qa_cache` (
  cache_key           STRING  NOT NULL,  -- hash(question + context_params)
  question            STRING,
  context_symbols     STRING,            -- JSON array
  context_years       STRING,            -- JSON array
  answer              STRING,
  model               STRING,
  prompt_tokens       INT64,
  completion_tokens   INT64,
  created_at          TIMESTAMP,
  expires_at          TIMESTAMP          -- 缓存过期时间（7天）
)
OPTIONS (description = "AI问答结果缓存，减少重复LLM调用");
