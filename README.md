# AI信用风险仪表盘

这是一个面向美国 AI 产业链信用风险的本地仪表盘。它把公开信用利差、手工/付费数据字段、SEC 财务数据和 private credit proxy 统一到一个 `AI Credit Stress Index` 中，便于每天或每周跟踪 AI capex 融资链是否开始收紧。

## 首版核心指标

仪表盘包含 16 个指标，按权重合成为 0-100 的 AI Credit Stress Index：

| 模块 | 指标 | 首选数据源 | 免费/可复现 proxy |
| --- | --- | --- | --- |
| Market credit | Oracle 5Y CDS | Bloomberg/Markit/ICE CDS | `data/manual/oracle_cds.csv` 手工填入；或用 ORCL 债券 OAS 替代 |
| Market credit | Hyperscaler Bond OAS | ICE/BofA、Bloomberg BVAL/OAS、TRACE 曲线 | `data/manual/issuer_oas.csv`；缺失时用 FRED IG/BBB OAS |
| Relative credit | Oracle Spread vs BBB IG | ORCL OAS - BBB IG OAS | 手工 ORCL OAS - FRED `BAMLC0A4CBBB` |
| Relative credit | AI Bond Basket vs IG | 自定义 AI 债券篮子 OAS - IG OAS | `data/manual/ai_bond_basket_oas.csv` - FRED `BAMLC0A0CM` |
| Market credit | US BBB Corporate OAS | ICE/BofA BBB OAS | FRED `BAMLC0A4CBBB` |
| Market credit | US High Yield OAS | ICE/BofA HY OAS | FRED `BAMLH0A0HYM2` |
| Primary market | Hyperscaler New-Issue Volume, 4W | Bloomberg DCM、Dealogic、IFR | `data/manual/new_issues.csv` |
| Primary market | New-Issue Orderbook Cover | 发行簿/承销商/IFR | `data/manual/new_issues.csv` |
| Primary market | New-Issue Concession | Bloomberg/IFR/TRACE 曲线 | `data/manual/new_issues.csv` |
| Fundamentals | Capex / FCF | SEC XBRL、10-Q/10-K | SEC companyfacts API |
| Fundamentals | Debt / Capex | SEC XBRL、10-Q/10-K | SEC companyfacts API |
| Fundamentals | Net Debt / EBITDA | SEC XBRL、10-Q/10-K | SEC companyfacts API |
| Fundamentals | Interest Coverage | SEC XBRL、10-Q/10-K | SEC companyfacts API |
| Off-balance sheet | Purchase + Lease Obligations / Revenue | 10-Q/10-K 承诺和租赁脚注 | `data/manual/off_balance_sheet.csv` |
| Private credit | Private Credit / BDC Stress Proxy | BDC NAV 折价、PIK、non-accrual、赎回 | `data/manual/private_credit.csv`；可接 BIZD/BDC ETF 与 HY OAS |
| Private credit | Data Center / Neocloud Financing Spread | 私募信贷条款、数据中心贷款/ABS | `data/manual/private_credit.csv` |

## 红黄绿阈值

阈值在 `config/metrics.json` 中集中维护。每个指标都有：

- `green`：正常区边界
- `yellow`：关注区边界
- `red`：压力区边界
- `direction`：`high_is_stress` 或 `low_is_stress`
- `weight`：综合指数权重

脚本会把每个指标映射成 0-100 压力分数，再按权重合成：

```text
AI Credit Stress Index = sum(metric_score * weight) / sum(weight)
```

默认区间：

- `0-34`：正常
- `35-64`：关注
- `65-100`：压力

## 界面阅读

综合指数以紧凑摘要显示，核心区域把空间留给 16 个单项指标。每张卡片同时展示最新值、较前值变化、0-100 压力评分、历史压力分位、样本数量和历史区间。

- 指标图带横向日期轴和纵向数值轴；鼠标在图中移动时会吸附到最近的数据点，并显示日期、原始值和当期压力评分。
- 鼠标停留在指标名称或旁边的 `i` 标记时，会显示该指标的定义和风险含义。
- 图表获得键盘焦点后，可以用左右方向键逐点查看，`Home`/`End` 跳到首尾数据点。
- 单一历史观测的财务指标仍显示坐标轴和数据点；需要连续趋势时，应在后续季度保留历史缓存。

## 在线访问

公开仪表盘：<https://jerryfanfanfanfan.github.io/ai-credit-risk-dashboard/>

`.github/workflows/pages.yml` 会在 `dashboard/` 目录或部署工作流变化时自动重新发布 GitHub Pages。每周数据更新提交包含 `dashboard/data/metrics.json`，因此也会触发页面重发。

## 安装与运行

在项目目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/update_data.py
python -m http.server 8000 -d dashboard
```

然后打开：

```text
http://localhost:8000
```

如果不想建虚拟环境，也可以直接运行：

```bash
python3 scripts/update_data.py
python3 -m http.server 8000 -d dashboard
```

## 数据更新流程

每日建议：

1. 更新 `data/manual/oracle_cds.csv`：填 Oracle 5Y CDS，或填入你的 CDS proxy。
2. 更新 `data/manual/issuer_oas.csv`：填 MSFT/AMZN/GOOGL/META/ORCL 可比债券 OAS。
3. 运行 `python scripts/update_data.py`：自动抓 FRED 信用利差，并尝试抓 SEC companyfacts。
4. 刷新浏览器。

每周建议：

1. 更新 `data/manual/new_issues.csv`：新债金额、认购倍数、new issue concession。
2. 更新 `data/manual/private_credit.csv`：BDC/private credit stress proxy 与 neocloud financing spread。
3. 检查 `dashboard/data/metrics.json` 中的 `warnings`，确认哪些字段仍在用样例或 proxy。

每季建议：

1. 更新 `data/manual/off_balance_sheet.csv`：purchase commitments、lease obligations、revenue。
2. 复核 SEC 自动抽取的 capex、FCF、debt、EBITDA、interest expense 标签是否符合你的口径。

## 自动化

仓库内置 `.github/workflows/weekly-update.yml`。GitHub Actions 会在每周日 20:00（Asia/Shanghai，对应 12:00 UTC）自动：

- 抓取 FRED 与 SEC 公开数据；
- 重新计算指标、历史分位和综合压力指数；
- 更新 `dashboard/data/metrics.json` 与 `data/cache/latest_metrics.json`；
- 仅在数据文件变化时由 `github-actions[bot]` 提交到默认分支。

自动任务使用 `--strict-public` 模式。FRED 或 SEC 暂时不可用时，任务会保留该来源最近一次成功的公开缓存，同时继续更新其他可用来源；只有关键来源不可用且没有有效缓存时才会失败。CDS、发行簿、commitments、private credit 和 neocloud 等手工 CSV 不会被自动改写。

也可以在 GitHub 的 **Actions → Weekly data refresh → Run workflow** 手工触发一次。

## 字段说明

### 为什么 Oracle 5Y CDS 单独列出

Oracle 是 AI capex 融资链里更敏感的公开信用节点之一。相比 Microsoft、Alphabet、Amazon，它的传统业务现金流缓冲更弱，债务市场对其 AI 数据中心扩张的定价变化更容易提前暴露风险。

### 为什么新债认购倍数重要

信用利差是价格，认购倍数是边际融资需求。如果利差还没有明显扩，但 orderbook cover 已经从高位掉到接近 1-2 倍，说明一级市场承接能力可能已经变弱。

### 为什么 commitments 要手工维护

Purchase commitments、云采购承诺、GPU/数据中心租赁义务在 SEC XBRL 里标签不稳定，自动抓取容易误读。首版选择透明的手工 CSV，保证你知道每个数来自哪份 10-Q/10-K 脚注。

## 目录结构

```text
config/metrics.json              指标定义、权重、阈值、数据源
data/manual/                     付费字段和手工字段模板
data/cache/latest_metrics.json   最近一次生成的数据缓存
dashboard/                       本地仪表盘页面
dashboard/data/metrics.json      页面读取的数据文件
scripts/update_data.py           数据抓取、计算、指数生成脚本
.github/workflows/               每周数据更新与 GitHub Pages 部署
```

## 注意

仓库自带的 `data/manual/*.csv` 是结构样例，不应当视为真实历史数据。运行脚本时，FRED 与 SEC 能抓到的字段会使用公开数据；CDS、issuer-level OAS、新债认购倍数、private credit 和 neocloud 融资条件需要你用自己的数据源或手工表替换。
