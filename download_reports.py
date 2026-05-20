#!/usr/bin/env python3
"""download_reports.py — Download annual/quarterly reports from CNINFO (巨潮资讯) for 11 MCU companies.

Designed to run in Google Colab (where CNINFO cookies can be obtained interactively).

Usage in Colab:
    !python download_reports.py                  # download using COOKIE below
    !python download_reports.py --dry-run        # show what would be downloaded
    !python download_reports.py --symbols 603986 688766  # specific companies

After downloading, run:
    !python upload_pdfs.py /content/finance_reports

Setup:
    1. Open https://www.cninfo.com.cn in browser and log in (or just visit any page)
    2. Open DevTools → Network → find any XHR request
    3. Copy the Cookie header value and paste it as COOKIE below
"""

import argparse
import os
import re
import time
from pathlib import Path

import requests

# ── User Config ───────────────────────────────────────────────────────────────

# Paste your browser Cookie from cninfo.com.cn here:
COOKIE = os.environ.get("CNINFO_COOKIE", "YOUR_CNINFO_COOKIE_HERE")

OUTPUT_DIR = Path(os.environ.get("LOCAL_REPORTS_DIR", "/content/finance_reports"))

# ── CNINFO Org Codes ──────────────────────────────────────────────────────────
# orgId is CNINFO's internal identifier for each listed company.
# Obtain via: GET https://www.cninfo.com.cn/new/information/topSearch/query
#             ?keyWord=603986&maxNum=5

COMPANY_INFO = {
    "603986": {"name": "兆易创新", "orgId": "9900016927", "sse": "sh"},
    "300327": {"name": "中颖电子", "orgId": "9900008973", "sse": "sz"},
    "688380": {"name": "中微半导", "orgId": "9900034278", "sse": "sh"},
    "300077": {"name": "国民技术", "orgId": "9900004862", "sse": "sz"},
    "688279": {"name": "峰岹科技", "orgId": "9900031897", "sse": "sh"},
    "002180": {"name": "纳思达",   "orgId": "9900003561", "sse": "sz"},
    "688385": {"name": "复旦微电", "orgId": "9900029477", "sse": "sh"},
    "688766": {"name": "普冉股份", "orgId": "9900037014", "sse": "sh"},
    "688595": {"name": "芯海科技", "orgId": "9900030716", "sse": "sh"},
    "688391": {"name": "钜泉科技", "orgId": "9900030963", "sse": "sh"},
    "688018": {"name": "乐鑫科技", "orgId": "9900025958", "sse": "sh"},
}

# Report type categories on CNINFO
CATEGORY_ANNUAL    = "category_ndbg_szsh"   # 年度报告
CATEGORY_Q1        = "category_yjdbg_szsh"  # 一季度报告
CATEGORY_SEMI      = "category_bndbg_szsh"  # 半年度报告
CATEGORY_Q3        = "category_sjdbg_szsh"  # 三季度报告

# Which report types to download (modify as needed per run)
TARGET_CATEGORIES = [CATEGORY_ANNUAL]

# Year range to fetch
YEAR_START = 2018
YEAR_END   = 2026

# ── CNINFO API ────────────────────────────────────────────────────────────────

QUERY_URL  = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
DETAIL_URL = "https://static.cninfo.com.cn/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer":    "https://www.cninfo.com.cn/",
    "Origin":     "https://www.cninfo.com.cn",
}

EXCLUDE_KEYWORDS = [
    "摘要", "提示性", "英文版", "English", "补充", "更正",
    "取消", "撤销", "终止",
]

PERIOD_MAP = {
    CATEGORY_ANNUAL: "年报",
    CATEGORY_Q1:     "一季报",
    CATEGORY_SEMI:   "半年报",
    CATEGORY_Q3:     "三季报",
}


def build_session(cookie: str) -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    if cookie and cookie != "YOUR_CNINFO_COOKIE_HERE":
        s.headers["Cookie"] = cookie
    return s


def query_announcements(session: requests.Session, stock_id: str, org_id: str,
                        category: str, page: int = 1) -> dict:
    payload = {
        "stock":      f"{stock_id},{org_id}",
        "tabName":    "fulltext",
        "pageSize":   30,
        "pageNum":    page,
        "column":     "sse" if stock_id.startswith("6") else "szse",
        "category":   category,
        "plate":      "",
        "seDate":     "",
        "searchkey":  "",
        "secid":      "",
        "sortName":   "",
        "sortType":   "",
        "isHLtitle":  True,
    }
    resp = session.post(QUERY_URL, data=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def detect_year(title: str, filename: str) -> int | None:
    for text in (title, filename):
        m = re.search(r"(20\d{2})", text)
        if m:
            return int(m.group(1))
    return None


def is_excluded(title: str) -> bool:
    return any(kw in title for kw in EXCLUDE_KEYWORDS)


def select_best_pdf(announcements: list[dict], year: int) -> dict | None:
    """Pick the main annual report: correct year, not a summary, largest file."""
    candidates = []
    for ann in announcements:
        title = ann.get("announcementTitle", "")
        if is_excluded(title):
            continue
        ann_year = detect_year(title, ann.get("adjunctUrl", ""))
        if ann_year != year:
            continue
        if ann.get("adjunctType") != "PDF":
            continue
        size = int(ann.get("adjunctSize", 0))
        candidates.append((size, ann))

    if not candidates:
        return None
    # Largest file first (摘要 is usually smaller)
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def download_pdf(session: requests.Session, ann: dict,
                 dest_dir: Path, dry_run: bool) -> Path | None:
    url_path = ann.get("adjunctUrl", "")
    if not url_path:
        return None
    url = DETAIL_URL + url_path.lstrip("/")

    # Build a clean filename
    title = ann.get("announcementTitle", "report").strip()
    title_safe = re.sub(r'[\\/:*?"<>|]', "_", title)[:80]
    filename = f"{title_safe}.pdf"
    dest_path = dest_dir / filename

    if dest_path.exists():
        print(f"    ↷ already exists: {filename}")
        return dest_path

    if dry_run:
        size_kb = int(ann.get("adjunctSize", 0)) // 1024
        print(f"    [dry-run] would download {size_kb:,} KB → {filename}")
        return dest_path

    try:
        r = session.get(url, stream=True, timeout=120)
        r.raise_for_status()
        dest_dir.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        size_kb = dest_path.stat().st_size // 1024
        print(f"    ✓ {size_kb:,} KB → {filename}")
        return dest_path
    except Exception as exc:
        print(f"    ✗ download failed: {exc}")
        return None


def fetch_company(session: requests.Session, stock_id: str, info: dict,
                  category: str, years: list[int], dry_run: bool) -> dict:
    """Fetch all matching reports for one company/category combo."""
    org_id = info["orgId"]
    name   = info["name"]
    folder = OUTPUT_DIR / f"{stock_id}_{name}"
    period_label = PERIOD_MAP.get(category, category)
    results = {"downloaded": [], "skipped": [], "failed": []}

    print(f"\n  [{stock_id}] {name}  ({period_label})")

    # Paginate until we've seen all years or run out of pages
    seen_years: set[int] = set()
    page = 1
    while True:
        try:
            data = query_announcements(session, stock_id, org_id, category, page)
        except Exception as exc:
            print(f"    API error page {page}: {exc}")
            break

        announcements = data.get("announcements") or []
        if not announcements:
            break

        for ann in announcements:
            title = ann.get("announcementTitle", "")
            yr = detect_year(title, ann.get("adjunctUrl", ""))
            if yr is not None and yr in years:
                seen_years.add(yr)

        # Try to download for each requested year from this page batch
        for year in [y for y in years if y not in seen_years or True]:
            best = select_best_pdf(announcements, year)
            if best is None:
                continue
            yr = detect_year(best.get("announcementTitle", ""), "")
            if yr not in years:
                continue
            path = download_pdf(session, best, folder, dry_run)
            if path:
                results["downloaded"].append(f"{yr}{period_label}")
            else:
                results["failed"].append(f"{yr}{period_label}")

        total_pages = data.get("totalPages", 1)
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.5)  # polite rate-limit

    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download CNINFO annual reports for 11 MCU companies"
    )
    parser.add_argument("--symbols", nargs="*",
                        help="Stock codes to process (default: all 11)")
    parser.add_argument("--years", nargs="*", type=int,
                        help=f"Years to fetch (default: {YEAR_START}–{YEAR_END})")
    parser.add_argument("--categories", nargs="*",
                        default=TARGET_CATEGORIES,
                        help="CNINFO category codes (default: annual reports)")
    parser.add_argument("--out", default=str(OUTPUT_DIR),
                        help=f"Output directory (default: {OUTPUT_DIR})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be downloaded without doing it")
    args = parser.parse_args()

    global OUTPUT_DIR
    OUTPUT_DIR = Path(args.out)

    symbols = args.symbols or list(COMPANY_INFO.keys())
    years   = args.years   or list(range(YEAR_START, YEAR_END + 1))

    if COOKIE == "YOUR_CNINFO_COOKIE_HERE":
        print("⚠  No COOKIE set. Some reports may require authentication.")
        print("   Set CNINFO_COOKIE env var or edit COOKIE at the top of this file.\n")

    session = build_session(COOKIE)

    print(f"{'='*65}")
    print(f"CNINFO Report Downloader {'[DRY RUN] ' if args.dry_run else ''}")
    print(f"Output : {OUTPUT_DIR}")
    print(f"Years  : {min(years)}–{max(years)}")
    print(f"Types  : {[PERIOD_MAP.get(c, c) for c in args.categories]}")
    print(f"{'='*65}")

    total_dl, total_fail = 0, 0

    for stock_id in symbols:
        if stock_id not in COMPANY_INFO:
            print(f"Unknown symbol: {stock_id}")
            continue
        info = COMPANY_INFO[stock_id]
        for cat in args.categories:
            res = fetch_company(session, stock_id, info, cat, years, args.dry_run)
            total_dl   += len(res["downloaded"])
            total_fail += len(res["failed"])

    print(f"\n{'='*65}")
    action = "Would download" if args.dry_run else "Downloaded"
    print(f"{action}: {total_dl}  |  Failed: {total_fail}")
    print(f"{'='*65}")

    if not args.dry_run and total_dl:
        print(f"\nNext step: python upload_pdfs.py {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
