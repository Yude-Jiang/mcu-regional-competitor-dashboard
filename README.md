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
AUTH_DISABLED=1 python app.py   # local dev — skip login gate
python app.py                  # production-like (requires @st.com email on /login)
```

### 访问门禁（Access Token）

部署后所有页面需先登录。用户在 `/login` 输入 **ST 邮箱** 作为 Access Token；只要地址以 `@st.com` 结尾即可进入（可用环境变量 `AUTH_EMAIL_DOMAIN` 修改）。

| 变量 | 说明 |
|------|------|
| `AUTH_EMAIL_DOMAIN` | 允许的后缀，默认 `@st.com` |
| `FLASK_SECRET_KEY` | Session 签名密钥（Secret Manager，deploy.sh 自动创建） |
| `AUTH_DISABLED=1` | 本地开发关闭门禁 |

```bash
./deploy.sh   # 已配置 AUTH_EMAIL_DOMAIN 与 FLASK_SECRET_KEY
```

**说明**：这是域名门禁，不验证邮箱真实性；仅防止公开 URL 被随意访问。登录成功/失败会写入 Cloud Run 日志（`auth_audit` JSON：邮箱、IP、UTC 时间）。

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
| `extract_employee_counts.py` | Gemini REST API 批量提取年报员工总数 |
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

### 2026-05-26 — UI 优化 + 部署流程规范化 + 数据持久化修复

#### 完成内容

1. **User Guide HTML 重构**
   - 字体放大、stats bar 宽度对齐正文、正文区域加宽
   - 新增 AI 竞情问答章节（含可拖拽放大答案框、建议问题 chips）
   - 各图表 description 补充异常原因说明（国民技术/芯海科技亏损背景）
   - 删除各公司 MCU 口径详情卡片（已在 dashboard 详情面板展示，避免重复）
   - 联系邮箱更新：jania.jiang@gmail.com → yude.jiang@st.com

2. **Dashboard 工具栏合并**
   - 将独立的「年份下拉 + 搜索」控件条合并进图表筛选栏，两行合一

3. **Favicon**
   - dashboard.html 和 user_guide.html 均添加内联 SVG favicon（深蓝芯片图标）

4. **员工数持久化 Bug 修复（fetch_mcu_data.py）**
   - 根本原因：`employee_known_data.json` 回填逻辑在 `apply_mcu_strategy()` **之前**执行
   - AKShare 断网时 `fin` 为空，员工数无行可写
   - 修复：回填逻辑移至 `apply_mcu_strategy()` + `compute_metrics()` **之后**
   - 同步手动将 66 条员工数据从 `employee_known_data.json` 回填至 `data.json`

5. **companies_meta.json MCU 口径说明全面更新**
   - 11 家公司 `mcu_note` 重写为准确表述，删除所有「需手工录入」等历史遗留错误
   - 修正 300077 国民技术和 688595 芯海科技的 `mcu_strategy` 字段（`segment_reported` → `estimated`）

#### 经验与教训

**【高频复现】data.json 本地改动阻塞 git checkout**
- 根本原因：fetch 脚本每次运行都修改 `data.json`，产生未提交改动
- `git checkout -- data.json` 无法解决（恢复到当前 HEAD，但 HEAD 与目标分支仍不同）
- **正确做法**：`git stash` 再 checkout；已封装进 `deploy` alias
- **规律**：每次切换到 main 之前必须先 stash，这是固定动作

**Cloud Run 部署必须两步，缺一不可**
- `gcloud builds submit` = 构建镜像推送至 Artifact Registry（**Cloud Run 不变**）
- `gcloud run deploy` = 用新镜像替换运行实例（**这步才真正生效**）
- 只跑第一步时页面无变化，排查困难；`deploy` alias 已将两步合并

```bash
# ~/.bashrc 中的 deploy alias
alias deploy="git stash && git checkout main && git pull origin main && \
  gcloud builds submit --project st-china-ai-force && \
  gcloud run deploy mcu-regional-competitor-dashboard \
  --image asia-east1-docker.pkg.dev/st-china-ai-force/mcu/mcu-regional-competitor-dashboard \
  --region asia-east1 --platform managed --allow-unauthenticated \
  --set-env-vars GCP_PROJECT=st-china-ai-force,BQ_DATASET=mcu,GCS_BUCKET=st-finance-reports \
  --set-secrets VITE_DEEPSEEK_API_KEY=VITE_DEEPSEEK_API_KEY:latest \
  --project st-china-ai-force"
```

**git checkout 失败时 gcloud builds submit 打包的是旧代码**
- `&&` 短路：checkout 失败则 pull 不执行，builds submit 打包当前目录（旧代码）
- 表现：STATUS: SUCCESS 但部署内容是旧版本，极难排查
- **验证**：部署前先 `git log --oneline -3` 确认 HEAD 是最新 commit

**计算结果未持久化至 mcu_known_data.json → 下次 fetch 丢失**
- `fetch_mcu_data.py` 将 AKShare 计算的 `gross_margin_pct`、`employee_count` 写入 `data.json`
- 断网环境（AKShare 不可用）重跑 fetch 后，这些计算值全部丢失
- **正确做法**：运行完 fetch 后，手动将需持久化字段同步到 `mcu_known_data.json`
- `employee_count` 已通过 Bug 修复解决（移后执行）；`gross_margin_pct` 仍需在 Cloud Shell fetch 后手动同步

**浏览器缓存导致部署后页面无变化**
- `companies_meta.json`、`data.json` 等文件被浏览器缓存，部署后刷新无效
- **解决**：`Ctrl+Shift+R`（Mac: `Cmd+Shift+R`）强制刷新，或无痕模式打开

**Cloud Shell 会话重置后项目配置丢失**
- 长时间不操作后新实例的 hostname 从 `cloudshell` 变为 `cs-xxxxxxxxx-default`
- **恢复**：`gcloud config set project st-china-ai-force && source ~/.bashrc`

---

### 2026-05-25 — Dashboard 可读性大升级 + 历史员工数补录（56条）

#### 完成内容

1. **历史员工数提取（extract_employee_counts.py）**
   - 新建脚本，使用 Gemini REST API（绕过 google.genai SDK 编码问题）批量读取 GCS 年报 PDF
   - 在 Colab 运行，提取 11 家公司 2018–2024 年「报告期末在职员工总数」
   - 成功率 56/56，写入 `data.json` 的 `employee_count` 字段
   - 覆盖明细：002180(7年) · 300077(7年) · 300327(7年) · 603986(7年) · 688018(6年) · 688279(3年) · 688380(3年) · 688385(4年) · 688391(3年) · 688595(5年) · 688766(4年)

2. **Dashboard 图表精简**
   - 删除「MCU市场份额饼图」和「毛利率对比」双图：数据覆盖率不足50%，展示误导性强
   - 删除 `renderMcuShareChart()` / `renderGrossMarginChart()` 及其所有引用

3. **Detail Panel 宽度升级**
   - 固定宽度 600px → 响应式 `width:50vw; min-width:440px; max-width:900px`
   - `body.panel-open .table-wrap` padding-right 随之调整为 50vw

4. **Pipeline 导航优化**
   - 增加高度至 56px，确保股票代码完整可见
   - 代码字体改为 `var(--fz-xs)`，颜色改为 `var(--text-dim)`

5. **MCU 11家合计汇总条**
   - 在「MCU按公司分组图」下方添加 `#mcu-summary`，镜像总营收汇总条逻辑
   - 实现 `_updateMcuSummary()`，按选中年份动态计算、显示 MCU 合计 M$ 及 YoY

6. **KPI 指标 `?` 悬浮解释**
   - 所有 7 个指标（总营收/MCU营收/MCU占比/毛利率/研发费用/净利润/员工人数）右上角添加圆形 `?` 按钮
   - 点击弹出 `.metric-popup` 展示计算公式 + 数据说明；点击任意处关闭
   - 毛利率 tooltip 特别列出 5 家历史数据缺失的公司及原因

7. **页脚更新**
   - 由 `AKShare...` 改为 `MCU Regional Competitor Analysis | 数据来源:AKShare·东方财富·年报·IPO招股书 | 创建: yude.jiang@st.com | 动态日期`

8. **首页各 Section 说明文字**
   - 总营收板块：说明数据来源（AKShare/东方财富），AI 解读年化增长与2024景气度
   - MCU营收板块：说明口径差异（年报分部/总收入系数推算），提示置信度分层含义

9. **公司详情表头 + 图例**
   - 表格上方加「公司详情」section header 及数据来源说明
   - 彩色点图例（绿=年报直接披露/青=推算高置信/橙=估算低置信/红=数据缺失）
   - YoY 颜色说明（+绿/-红）

10. **数据来源透明化（招股书标注）**
    - 历史表格中上市前年份的 FY 列显示紫色 `招股书` 徽章（7家公司有上市前数据）
    - 表格下方动态脚注：自动说明招股书来源年份范围、毛利率缺失原因
    - 毛利率全缺失（688380/688279/688595/688391/688018）：注明 AKShare 未覆盖，需 PDF 提取
    - 毛利率部分缺失：列出具体年份

**data.json 数据点变化：员工数 0 → 56（11家公司全覆盖2018–2024）**

#### 经验与教训

**google.genai SDK 中文编码 Bug（关键）**
- `google.genai` SDK 内部使用 `httpx`，在设置 HTTP headers 时调用 `value.encode("ascii")`
- Prompt 含中文字符时（`EMPLOYEE_PROMPT` 使用中文），httpx 在 header 序列化时崩溃
- 错误信息：`UnicodeEncodeError: 'ascii' codec can't encode characters`，堆栈指向 `httpx/_models.py line 82`
- **根本修复**：绕过 SDK，直接用 `requests.post()` 调用 Gemini REST API
  ```python
  url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={api_key}"
  requests.post(url, json=payload, timeout=120)
  ```
- 适用场景：只要 Prompt 或系统指令含非 ASCII 字符，就应用 REST API 直调

**gemini-3.5-flash `responseMimeType` 截断问题**
- 设置 `responseMimeType: "application/json"` 导致响应被截断为 "Here is the JSON requested"（不含实际 JSON）
- **修复**：移除 `responseMimeType`，改为纯文本响应，再用正则从文本中提取 JSON 块
- JSON 解析使用三级 fallback：① markdown 代码块 ② `{...employee_count...}` ③ 任意 `{}`

**JSON 解析：千分位逗号**
- Gemini 有时输出 `"employee_count": 1,781`（含千分位逗号，非合法 JSON）
- 在 `json.loads()` 前先 `re.sub(r'(\d),(\d{3})', r'\1\2', raw)` 清洗

**maxOutputTokens 设置**
- 中文 JSON 响应中每个汉字占 2–3 tokens；300 tokens 不足以输出完整 JSON（末尾 `}` 会截断）
- **推荐值**：`1000`（约 300 中文字 + JSON 结构开销）

**Colab 无法直接 push GitHub**
- Colab 无 GitHub 写权限；提取结果以 JSON 格式 `print()` 输出 → 粘贴给 Claude Code → 从远端环境推送
- 适用于所有「Colab 计算 + Claude Code 提交」场景

**招股书数据覆盖局限**
- 上市前年份（year < listed_year）的总营收来自 IPO 招股说明书
- 招股书中毛利率格式不规范（不在标准财务附注位置），未做自动提取 → 显示「—」
- 员工数在招股书中通常有，本次已通过 `extract_employee_counts.py` 统一提取（含招股书年份）
- 7家存在招股书数据的公司：688380(2018-21) · 688279(2018-20) · 688385(2018-20) · 688766(2018-21) · 688595(2018-19) · 688391(2018-20) · 688018(2018)

**gross_margin 覆盖现状（截至2026-05-25）**
- ✅ 全覆盖：603986兆易创新 · 002180纳思达
- ⚡ 部分覆盖：300327中颖(仅2024-25) · 688385复旦微(仅2024) · 688766普冉(仅2024) · 300077国民(2022-24)
- 🔄 代码已支持从利润表计算总公司毛利率（见下方2026-05-25续）：688380中微半导 / 688279峰岹 / 688595芯海 / 688391钜泉 / 688018乐鑫；在本地/Cloud Shell 运行 `fetch_mcu_data.py` 后自动填充
- 分产品精确毛利率仍需对上述5家跑 `extract_mcu_segments.py` 提取年报毛利率（688380精度已足够，其余公司视需求）

---

### 2026-05-25（续）— fetch_mcu_data.py 新增利润表毛利率计算

#### 完成内容

**`fetch_mcu_data.py` 扩展：从利润表营业成本自动计算公司整体毛利率**

东方财富利润表 API 响应 JSON 包含 `OPERATE_COST`（营业成本），但原始代码只解析了3个字段（营业收入/净利润/研发）。在 `_parse_long_df()` 中新增该字段提取，计算：

```
gross_margin_pct = (营业收入 - 营业成本) / 营业收入 × 100
```

同步修复：`subsidiary_geehy` 清空块增加 `gross_margin_pct = None`，防止纳思达集团合并毛利率污染极海微行数据。

**优先级不变**：`mcu_known_data.json` 中手工录入的 `mcu_gross_margin` 仍高于 API 计算值（在 `apply_mcu_strategy` 中后写覆盖）。

覆盖5家原本全缺失公司（在本地/Cloud Shell 运行 `python fetch_mcu_data.py` 后生效）：

| 代码 | 公司 | MCU占比 | 毛利率精度说明 |
|------|------|---------|--------------| 
| 688380 | 中微半导 | ≈99% | 总公司毛利率≈MCU毛利率，误差<1% |
| 688018 | 乐鑫科技 | 芯片≈85% | 芯片业务主导，为较优代理 |
| 688279 | 峰岹科技 | ≈67% | 含非MCU产品，偏差约10-15% |
| 688595 | 芯海科技 | MCU≈45% | 含模拟信号链，总毛利率为粗略代理 |
| 688391 | 钜泉科技 | ≈34% | MCU仅占1/3，代理误差较大 |

> **注意**：云端 Claude Code 环境 AKShare API 被 403 封锁，数据填充需在本地/Cloud Shell 运行。

---

### 2026-05-22 — 数据覆盖扩张 + 质检脚本 + 300077港股招股书录入

#### 完成内容

1. **check_extraction.py**：新增自动数据质检脚本，五项检验（MCU/总营收比值、同比异常、毛利率范围、跨年变异系数、低置信度标记），exit 0=通过，exit 1=硬错误（MCU>总营收）
2. **002180 纳思达**：从 Colab 提取 2018/2019/2021/2022/2023/2025 六年极海芯片产品线数据（2020年提取失败，暂缺）
3. **300327 中颖电子**：从 Colab 提取 2019-2022 四年工业控制产品数据（2023/2024年提取失败，但已有手工录入，无影响）
4. **688766 普冉股份**：确认2022年上市，放弃追溯上市前数据
5. **688595 芯海科技**：SYSTEM_PROMPT 加专项提示，待下次 Colab 运行提取
6. **300077 国民技术 MCU数据**：
   - 8年年报全部确认无MCU独立分产品表（「芯片类产品」始终合并披露）
   - 从港股招股书（2026年3月）录入2022-2025年芯片产品收入，置信度 low → medium
   - Dashboard 口径提醒加橙色警告标注，注明来自2026年H股招股说明书
7. **extract_mcu_segments.py 两处修复**：
   - `--local` 支持单文件（之前只接受目录）
   - `list_gcs_pdfs` 支持自动识别「招募书/招股书/IPO」类PDF（不过滤年份前缀）

**MCU数据点总数：52 → 66（+14）**

#### 经验与教训

**Colab Git 操作注意事项**
- Colab 无法直接 `git push` GitHub（HTTPS需PAT，SSH未配置）；最稳妥方式：在 Colab 里 `print(open("mcu_known_data.json").read())` 把内容复制给 Claude Code 手动合并
- `git pull --rebase` 前必须先 `git stash`，否则报 "unstaged changes"
- Colab 有未提交本地 commit 时执行 `git pull`，遇到分叉（divergent branches）用 `git pull --rebase origin <branch>`；若 rebase 产生冲突且该 commit 已在远端合并，用 `git rebase --skip` 跳过
- `git checkout HEAD -- <file>` 可快速还原有冲突标记的文件

**fetch_mcu_data.py 在隔离环境中的风险**
- 远端 Claude Code 环境无法访问 AKShare（东方财富服务），`fetch_profit_sheet` 返回空 dict
- 空结果时脚本会**清空 data.json 中的 financials**（overwrite 而非 merge）
- 规避方法：隔离环境里用 `git checkout HEAD -- data.json` 还原，再用 Python 脚本手动 patch 字段，不要跑完整的 `fetch_mcu_data.py`

**GCS 路径约定（重要）**
- 实际文件夹命名为 `{symbol}_{company_cn}/`（如 `300077_国民技术/`），不是只有6位代码
- 招募书文件名不以年份（`20XX`）开头，旧版 `list_gcs_pdfs` 会跳过；已修复

**招股书数据单位陷阱**
- 港股招股书附录财务数据通常以**千元（RMB thousands）**列示
- 本次指令文件将"555,724千元"误处理为"5,557,240元×1000 = 5,557,240,000元"，多了10倍
- **验证方法**：将 yuan 值除以汇率除以1,000,000，对比已知 M$ 数字；若相差10倍即为单位错误
- AKShare 的 `total_revenue_yuan` 可作为总收入的交叉验证基准

**validate_data.py 的 mcu_strategy 白名单**
- `VALID_MCU_STRATEGY` 在 validate_data.py 第23行硬编码，新增策略必须同步加入白名单
- 当前合法值：`segment_reported / segment_industrial / segment_estimated / total_proxy / estimated / subsidiary_geehy / na`
- 建议：新口径优先复用现有策略名（如招股书数据用 `segment_reported`），避免白名单漏加导致 FAIL

**688595 芯海科技 口径确认经过（避免重蹈）**
- 年报管理层讨论同时披露「MCU和AIoT芯片合计52,303万」和「MCU芯片单行33,986万」
- Gemini 提取的是 MCU芯片单行（正确），但用合计数比较导致误判为"提取错误"
- 两次错误"修正"（改为MCU+AIoT合并值）后，经年报原文核实还原
- **正确做法**：先确认口径边界（纯MCU or MCU+AIoT），再比较数字，不要直接用管理层讨论里的合并总数去验证分行提取值

**300077 国民技术 MCU口径（历史结论存档）**
- A股年报2018-2025：始终将MCU、安全芯片、BMS、RF合并为「芯片类产品」，永远不拆分
- 2019年年报明确说明通用MCU"处于验证与测试阶段，尚未开始贡献经济效益"（即2019年MCU收入实际为零）
- 港股招股书（2026年3月）为首次审计级披露，口径仍为「芯片产品」（非纯MCU）
- 灼识咨询：2024年纯通用MCU约5亿元≈芯片产品收入的90%（这是目前唯一可引用的纯MCU估算）

---

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

**Gemini 模型版本（2026-05 更新）**
- Gemini 3 系列已 GA：`gemini-3.5-flash`（2026-05-20）、`gemini-3.1-pro-preview`（2026-02-13）、`gemini-3-flash-preview`（2025-12-18）
- 代码中所有 `gemini-2.0-flash` 已统一替换为 `gemini-3.5-flash`
- Vertex AI 的 fallback 列表：`gemini-3.5-flash` → `gemini-3.1-pro-preview` → `gemini-3-flash-preview` → `gemini-3.1-flash-lite` → `gemini-2.0-flash-001`（兜底）
- 判断依据：截图确认 Google AI Studio 模型列表

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
