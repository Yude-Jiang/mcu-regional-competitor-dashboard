# CLAUDE.md — MCU竞品数据库操作指南 v2.1
# 以 review 分支架构为主，字段命名：金额用 _yuan，M$用 _musd

## 文件职责
- data.json：财务时序数据主库（financials字段，年度记录）
- companies_meta.json：公司配置，mcu_strategy/mcu_multiplier/mcu_confidence
- profiles_xq.json：公司静态信息，auto_mcu_status/mcu_revenue_scope
- fx_rates.json：汇率表，禁止修改（除非明确要求更新汇率）
- mcu_known_data.json：手工录入MCU数据，优先级高于自动推算，单位CNY元
- validate_data.py：每次编辑后必须运行，exit 0才算完成
- extract_mcu_segments.py：PDF→AI提取分产品MCU数据，写入BQ+mcu_known_data.json
- fetch_mcu_data.py：自动拉取总收入+应用mcu_strategy，生成data.json

## 字段命名规范（严格遵守）
- 金额CNY原始值：total_revenue_yuan / mcu_revenue_yuan / rd_expense_yuan
- 金额M$换算值：total_revenue_musd / mcu_revenue_musd / rd_expense_musd
- 禁止使用 _cny 后缀（已废弃）

## MCU口径规则（修改前必读 docs/DATA_DICTIONARY.md）
- 603986 兆易创新：segment_reported，年报「微控制器及模拟产品线」
- 300327 中颖电子：segment_industrial，年报「工业控制芯片」（含BMIC）
- 688380 中微半导：total_proxy × 0.99，总收入≈MCU
- 300077 国民技术：estimated，IR问答×0.27，低置信度
- 688279 峰岹科技：total_proxy × 0.67，MCU/ASIC系数
- 002180 纳思达极海：subsidiary_geehy，极海微子公司营收（非集团264亿）
- 688385 复旦微电子：segment_estimated，「智能电表芯片产品线」
- 688766 普冉股份：segment_reported，年报「MCU产品线」
- 688595 芯海科技：estimated × 0.45，低置信度
- 688391 钜泉科技：total_proxy × 0.34，计量芯片系数
- 688018 乐鑫科技：segment_estimated，年报「芯片收入」

## mcu_known_data.json 使用规则
- 存储单位：CNY元（非M$）
- 字段：mcu_revenue_yuan / mcu_gross_margin / data_type / confidence / source
- 此文件中的数据优先级高于 fetch_mcu_data.py 自动推算
- extract_mcu_segments.py 自动写入，人工审核后确认

## 数据更新标准流程
1. 运行 python fetch_mcu_data.py（自动）
2. 运行 python extract_mcu_segments.py --year YYYY（自动，需GCS+API Key）
3. 人工审核 mcu_known_data.json 中的异常flag
4. 运行 python validate_data.py → exit 0
5. git add / commit / push
6. commit message格式：feat(data): update {symbol} {year} from {来源}

## 禁止操作
- 禁止将纳思达集团总收入（264亿量级）填入002180的total_revenue_yuan
- 禁止使用688694（已废弃，普冉正确代码688766）
- 禁止在mcu_known_data.json中用M$存储（必须用CNY元）
- 禁止删除任何已有null字段（骨架必须保留）
- 禁止修改fx_rates.json的历史汇率（只能添加新年份）
- 修改任何文件后必须运行validate_data.py，exit 0才能提交
