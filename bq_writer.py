#!/usr/bin/env python3
"""bq_writer.py — BigQuery write helpers for the MCU dashboard pipeline.

Gracefully no-ops when BQ is not configured (local dev without GCP creds).
All writes are UPSERT (INSERT OR REPLACE via MERGE) to avoid duplicates.

Environment variables:
    GCP_PROJECT          — required for BQ writes
    BQ_DATASET           — default "mcu"
    MCU_BQ_DISABLED      — set to "1" to skip all BQ writes (offline mode)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_BQ_DISABLED = os.environ.get("MCU_BQ_DISABLED", "").strip() == "1"
_PROJECT     = (os.environ.get("GCP_PROJECT")
                or os.environ.get("GOOGLE_CLOUD_PROJECT")
                or "st-china-ai-force")
_DATASET     = os.environ.get("BQ_DATASET", "mcu")

_client = None   # lazy-init


def _get_client():
    global _client
    if _client is not None:
        return _client
    if _BQ_DISABLED or not _PROJECT:
        return None
    try:
        from google.cloud import bigquery
        import google.auth

        # Explicitly resolve credentials so init errors surface here, not
        # later during the first query (which gives a cryptic metadata error
        # in Cloud Shell when only `gcloud auth login` — not
        # `gcloud auth application-default login` — has been run).
        try:
            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/bigquery"]
            )
        except Exception as cred_exc:
            log.debug("BigQuery credentials unavailable (offline mode): %s", cred_exc)
            return None

        _client = bigquery.Client(project=_PROJECT, credentials=creds)
        return _client
    except Exception as exc:
        log.debug("BigQuery client init failed (offline mode): %s", exc)
        return None


def _table(name: str) -> str:
    return f"`{_PROJECT}.{_DATASET}.{name}`"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merge_financials(bq, row: dict) -> None:
    """UPSERT one row into mcu.financials keyed on (symbol, year, period)."""
    sql = f"""
    MERGE {_table('financials')} T
    USING (SELECT
        @symbol           AS symbol,
        @year             AS year,
        @period           AS period,
        @name_cn          AS name_cn,
        @name_en          AS name_en,
        @market           AS market,
        @total_revenue_yuan   AS total_revenue_yuan,
        @net_income_yuan      AS net_income_yuan,
        @rd_expense_yuan      AS rd_expense_yuan,
        @total_revenue_musd   AS total_revenue_musd,
        @net_income_musd      AS net_income_musd,
        @rd_expense_musd      AS rd_expense_musd,
        @rd_pct               AS rd_pct,
        @revenue_yoy_pct      AS revenue_yoy_pct,
        @cagr_pct             AS cagr_pct,
        @cagr_label           AS cagr_label,
        @gross_margin_pct     AS gross_margin_pct,
        @net_income_yoy_pct   AS net_income_yoy_pct,
        @employee_count       AS employee_count,
        @mcu_revenue_yuan     AS mcu_revenue_yuan,
        @mcu_revenue_musd     AS mcu_revenue_musd,
        @mcu_yoy_pct          AS mcu_yoy_pct,
        @mcu_weight_pct       AS mcu_weight_pct,
        @mcu_data_type        AS mcu_data_type,
        @mcu_confidence       AS mcu_confidence,
        @mcu_source           AS mcu_source,
        @mcu_strategy         AS mcu_strategy,
        @filing_status        AS filing_status,
        @filing_date          AS filing_date,
        @data_coverage        AS data_coverage,
        CAST(@akshare_updated_at AS TIMESTAMP)  AS akshare_updated_at,
        CAST(@pdf_extracted_at  AS TIMESTAMP)  AS pdf_extracted_at,
        CAST(@updated_at AS TIMESTAMP)          AS updated_at
    ) S ON T.symbol = S.symbol AND T.year = S.year AND T.period = S.period
    WHEN MATCHED THEN UPDATE SET
        name_cn = S.name_cn, name_en = S.name_en, market = S.market,
        total_revenue_yuan = COALESCE(S.total_revenue_yuan, T.total_revenue_yuan),
        net_income_yuan    = COALESCE(S.net_income_yuan,    T.net_income_yuan),
        rd_expense_yuan    = COALESCE(S.rd_expense_yuan,    T.rd_expense_yuan),
        total_revenue_musd = COALESCE(S.total_revenue_musd, T.total_revenue_musd),
        net_income_musd    = COALESCE(S.net_income_musd,    T.net_income_musd),
        rd_expense_musd    = COALESCE(S.rd_expense_musd,    T.rd_expense_musd),
        rd_pct             = COALESCE(S.rd_pct,             T.rd_pct),
        revenue_yoy_pct    = COALESCE(S.revenue_yoy_pct,   T.revenue_yoy_pct),
        cagr_pct           = COALESCE(S.cagr_pct,          T.cagr_pct),
        cagr_label         = COALESCE(S.cagr_label,        T.cagr_label),
        gross_margin_pct   = COALESCE(S.gross_margin_pct,  T.gross_margin_pct),
        net_income_yoy_pct = COALESCE(S.net_income_yoy_pct,T.net_income_yoy_pct),
        employee_count     = COALESCE(S.employee_count,    T.employee_count),
        mcu_revenue_yuan   = COALESCE(S.mcu_revenue_yuan,  T.mcu_revenue_yuan),
        mcu_revenue_musd   = COALESCE(S.mcu_revenue_musd,  T.mcu_revenue_musd),
        mcu_yoy_pct        = COALESCE(S.mcu_yoy_pct,       T.mcu_yoy_pct),
        mcu_weight_pct     = COALESCE(S.mcu_weight_pct,    T.mcu_weight_pct),
        mcu_data_type      = COALESCE(S.mcu_data_type,     T.mcu_data_type),
        mcu_confidence     = COALESCE(S.mcu_confidence,    T.mcu_confidence),
        mcu_source         = COALESCE(S.mcu_source,        T.mcu_source),
        mcu_strategy       = COALESCE(S.mcu_strategy,      T.mcu_strategy),
        filing_status      = COALESCE(S.filing_status,     T.filing_status),
        filing_date        = COALESCE(S.filing_date,       T.filing_date),
        data_coverage      = COALESCE(S.data_coverage,     T.data_coverage),
        akshare_updated_at = S.akshare_updated_at,
        pdf_extracted_at   = COALESCE(S.pdf_extracted_at, T.pdf_extracted_at),
        updated_at         = S.updated_at
    WHEN NOT MATCHED THEN INSERT ROW
    """
    from google.cloud.bigquery import ScalarQueryParameter, QueryJobConfig

    params = [
        ScalarQueryParameter("symbol",            "STRING",  row.get("symbol")),
        ScalarQueryParameter("year",              "INT64",   row.get("year")),
        ScalarQueryParameter("period",            "STRING",  row.get("period", "年报")),
        ScalarQueryParameter("name_cn",           "STRING",  row.get("name_cn")),
        ScalarQueryParameter("name_en",           "STRING",  row.get("name_en")),
        ScalarQueryParameter("market",            "STRING",  row.get("market")),
        ScalarQueryParameter("total_revenue_yuan","FLOAT64", row.get("total_revenue_yuan")),
        ScalarQueryParameter("net_income_yuan",   "FLOAT64", row.get("net_income_yuan")),
        ScalarQueryParameter("rd_expense_yuan",   "FLOAT64", row.get("rd_expense_yuan")),
        ScalarQueryParameter("total_revenue_musd","FLOAT64", row.get("total_revenue_musd")),
        ScalarQueryParameter("net_income_musd",   "FLOAT64", row.get("net_income_musd")),
        ScalarQueryParameter("rd_expense_musd",   "FLOAT64", row.get("rd_expense_musd")),
        ScalarQueryParameter("rd_pct",            "FLOAT64", row.get("rd_pct")),
        ScalarQueryParameter("revenue_yoy_pct",   "FLOAT64", row.get("revenue_yoy_pct")),
        ScalarQueryParameter("cagr_pct",          "FLOAT64", row.get("cagr_pct")),
        ScalarQueryParameter("cagr_label",        "STRING",  row.get("cagr_label")),
        ScalarQueryParameter("gross_margin_pct",  "FLOAT64", row.get("gross_margin_pct")),
        ScalarQueryParameter("net_income_yoy_pct","FLOAT64", row.get("net_income_yoy_pct")),
        ScalarQueryParameter("employee_count",    "INT64",   row.get("employee_count")),
        ScalarQueryParameter("mcu_revenue_yuan",  "FLOAT64", row.get("mcu_revenue_yuan")),
        ScalarQueryParameter("mcu_revenue_musd",  "FLOAT64", row.get("mcu_revenue_musd")),
        ScalarQueryParameter("mcu_yoy_pct",       "FLOAT64", row.get("mcu_yoy_pct")),
        ScalarQueryParameter("mcu_weight_pct",    "FLOAT64", row.get("mcu_weight_pct")),
        ScalarQueryParameter("mcu_data_type",     "STRING",  row.get("mcu_data_type")),
        ScalarQueryParameter("mcu_confidence",    "STRING",  row.get("mcu_confidence")),
        ScalarQueryParameter("mcu_source",        "STRING",  row.get("mcu_source")),
        ScalarQueryParameter("mcu_strategy",      "STRING",  row.get("mcu_strategy")),
        ScalarQueryParameter("filing_status",     "STRING",  row.get("filing_status")),
        ScalarQueryParameter("filing_date",       "STRING",  row.get("filing_date")),
        ScalarQueryParameter("data_coverage",     "FLOAT64", row.get("data_coverage")),
        ScalarQueryParameter("akshare_updated_at","STRING",  _now()),
        ScalarQueryParameter("pdf_extracted_at",  "STRING",  row.get("pdf_extracted_at")),
        ScalarQueryParameter("updated_at",        "STRING",  _now()),
    ]
    job = bq.query(sql, job_config=QueryJobConfig(query_parameters=params))
    job.result()


def write_financials(symbol: str, year: int, fin_row: dict, meta: dict,
                     period: str = "年报") -> bool:
    """Write one company-year row from fetch_mcu_data output to BigQuery.

    Args:
        symbol:  stock code e.g. "603986"
        year:    fiscal year e.g. 2024
        fin_row: dict from compute_metrics() — contains all financial fields
        meta:    dict from companies_meta.json
        period:  report type, default "年报"

    Returns True if written, False if skipped (offline mode / no creds).
    """
    bq = _get_client()
    if bq is None:
        return False

    row = {
        "symbol":  symbol,
        "year":    year,
        "period":  period,
        "name_cn": meta.get("name_cn"),
        "name_en": meta.get("name_en"),
        "market":  meta.get("market"),
        "mcu_strategy": meta.get("mcu_strategy"),
        **{k: fin_row.get(k) for k in [
            "total_revenue_yuan", "net_income_yuan", "rd_expense_yuan",
            "total_revenue_musd", "net_income_musd", "rd_expense_musd",
            "rd_pct", "revenue_yoy_pct", "cagr_pct", "cagr_label",
            "gross_margin_pct", "net_income_yoy_pct",
            "employee_count",
            "mcu_revenue_yuan", "mcu_revenue_musd",
            "mcu_yoy_pct", "mcu_weight_pct",
            "mcu_data_type", "mcu_confidence", "mcu_source",
            "filing_status", "filing_date", "data_coverage",
            "pdf_extracted_at",
        ]},
    }
    try:
        _merge_financials(bq, row)
        log.debug("BQ write: %s %s %s", symbol, year, period)
        return True
    except Exception as exc:
        log.warning("BQ write failed for %s %s: %s", symbol, year, exc)
        return False


def write_mcu_segment(symbol: str, year: int, period: str,
                      mcu_revenue_yuan: float | None,
                      source_type: str,
                      source_page: str = "",
                      raw_excerpt: str = "",
                      confidence: float = 1.0,
                      model: str = "") -> bool:
    """Log a PDF-extracted MCU segment result to mcu.mcu_segments."""
    bq = _get_client()
    if bq is None:
        return False

    from google.cloud.bigquery import ScalarQueryParameter, QueryJobConfig

    FX = {2018:6.617,2019:6.899,2020:6.900,2021:6.452,
          2022:6.737,2023:7.075,2024:7.243,2025:7.260}
    musd = (
        round(mcu_revenue_yuan / FX.get(year, 7.2) / 1_000_000, 2)
        if mcu_revenue_yuan is not None else None
    )

    sql = f"""
    INSERT INTO {_table('mcu_segments')}
      (symbol, year, period, mcu_revenue_yuan, mcu_revenue_musd,
       source_type, source_page, raw_text_excerpt,
       confidence_score, extraction_model, created_at)
    VALUES
      (@symbol, @year, @period, @mcu_revenue_yuan, @mcu_revenue_musd,
       @source_type, @source_page, @raw_text_excerpt,
       @confidence_score, @extraction_model, CURRENT_TIMESTAMP())
    """
    params = [
        ScalarQueryParameter("symbol",           "STRING",  symbol),
        ScalarQueryParameter("year",             "INT64",   year),
        ScalarQueryParameter("period",           "STRING",  period),
        ScalarQueryParameter("mcu_revenue_yuan", "FLOAT64", mcu_revenue_yuan),
        ScalarQueryParameter("mcu_revenue_musd", "FLOAT64", musd),
        ScalarQueryParameter("source_type",      "STRING",  source_type),
        ScalarQueryParameter("source_page",      "STRING",  source_page),
        ScalarQueryParameter("raw_text_excerpt", "STRING",  raw_excerpt[:2000]),
        ScalarQueryParameter("confidence_score", "FLOAT64", confidence),
        ScalarQueryParameter("extraction_model", "STRING",  model),
    ]
    try:
        bq.query(sql, job_config=QueryJobConfig(query_parameters=params)).result()
        return True
    except Exception as exc:
        log.warning("BQ mcu_segment write failed %s %s: %s", symbol, year, exc)
        return False


def log_pdf_status(symbol: str, year: int, report_type: str,
                   gcs_path: str = "",
                   file_size_kb: int = 0,
                   download_status: str = "downloaded",
                   title: str = "",
                   announcement_id: str = "") -> bool:
    """Upsert PDF download status into mcu.pdf_index."""
    bq = _get_client()
    if bq is None:
        return False

    from google.cloud.bigquery import ScalarQueryParameter, QueryJobConfig

    sql = f"""
    MERGE {_table('pdf_index')} T
    USING (SELECT
        @symbol AS symbol, @year AS year, @report_type AS report_type
    ) S ON T.symbol=S.symbol AND T.year=S.year AND T.report_type=S.report_type
    WHEN MATCHED THEN UPDATE SET
        gcs_path        = @gcs_path,
        file_size_kb    = @file_size_kb,
        download_status = @download_status,
        title           = @title,
        announcement_id = @announcement_id,
        downloaded_at   = CASE WHEN @download_status='downloaded'
                               THEN CURRENT_TIMESTAMP() ELSE T.downloaded_at END,
        updated_at      = CURRENT_TIMESTAMP()
    WHEN NOT MATCHED THEN INSERT
        (symbol, year, report_type, gcs_path, file_size_kb,
         download_status, title, announcement_id,
         downloaded_at, created_at, updated_at)
    VALUES
        (@symbol, @year, @report_type, @gcs_path, @file_size_kb,
         @download_status, @title, @announcement_id,
         CASE WHEN @download_status='downloaded' THEN CURRENT_TIMESTAMP() ELSE NULL END,
         CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
    """
    params = [
        ScalarQueryParameter("symbol",          "STRING", symbol),
        ScalarQueryParameter("year",            "INT64",  year),
        ScalarQueryParameter("report_type",     "STRING", report_type),
        ScalarQueryParameter("gcs_path",        "STRING", gcs_path),
        ScalarQueryParameter("file_size_kb",    "INT64",  file_size_kb),
        ScalarQueryParameter("download_status", "STRING", download_status),
        ScalarQueryParameter("title",           "STRING", title),
        ScalarQueryParameter("announcement_id", "STRING", announcement_id),
    ]
    try:
        bq.query(sql, job_config=QueryJobConfig(query_parameters=params)).result()
        return True
    except Exception as exc:
        log.warning("BQ pdf_index write failed %s %s %s: %s",
                    symbol, year, report_type, exc)
        return False


def get_doc_status_matrix() -> dict:
    """Return document status for all companies × years.
    Returns dict: {symbol: {year: {report_type: status}}}
    Used by /api/doc-status endpoint.
    """
    bq = _get_client()
    if bq is None:
        return {}

    sql = f"""
    SELECT symbol, year, report_type, download_status, extraction_status,
           gcs_path, file_size_kb, downloaded_at, extracted_at
    FROM {_table('pdf_index')}
    ORDER BY symbol, year, report_type
    """
    try:
        rows = list(bq.query(sql).result())
        result: dict = {}
        for r in rows:
            sym = r["symbol"]
            yr  = str(r["year"])
            rt  = r["report_type"]
            result.setdefault(sym, {}).setdefault(yr, {})[rt] = {
                "download_status":   r["download_status"],
                "extraction_status": r.get("extraction_status"),
                "gcs_path":          r.get("gcs_path"),
                "file_size_kb":      r.get("file_size_kb"),
                "downloaded_at":     r["downloaded_at"].isoformat() if r.get("downloaded_at") else None,
                "extracted_at":      r["extracted_at"].isoformat() if r.get("extracted_at") else None,
            }
        return result
    except Exception as exc:
        log.warning("BQ doc_status query failed: %s", exc)
        return {}


def is_available() -> bool:
    """Return True if BigQuery is reachable."""
    return _get_client() is not None
