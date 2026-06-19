# 自进化商品分析框架

## 核心原则

分析框架是长期知识，研报、新闻、数据库是进入框架的证据流。任何模型生成的新维度、新指标、新逻辑模板，都先写 candidate，经人工审核后再晋级到 gold/active framework。

## 分层结构

1. 标准资产 taxonomy
   - 维护标准品种名、别名、板块、合约代码。
   - 当前路径：`gold/reference_data/assets/futures_assets.v1.json`

2. 分析框架注册表
   - 从现有品种 framework 归一化出标准维度。
   - 维护维度 ID、名称、路径、类型、默认权重、激活规则、评分规则。
   - 当前 candidate 路径：`agent_workspace/candidates/analysis_framework/latest/analysis-framework-registry.json`

3. 指标/事件词条目录
   - 指标、事件、claim 不再散落在代码或 prompt 里，独立维护。
   - 每个词条关联到资产、framework、dimension。
   - 当前 candidate 路径：`agent_workspace/candidates/analysis_framework/latest/indicator-event-catalog.json`

4. 证据对齐层
   - 研报、新闻、数据库记录进入系统后，先识别资产，再映射到框架维度。
   - 映射失败或低置信内容进入 `framework_update_candidates`，作为框架补充候选。

5. 维度合成层
   - 同一品种、同一维度下的重复证据要合并。
   - 输出维度方向、重要性、权重贡献、证据组、冲突点和跟踪点。

6. 逻辑时间线层
   - 每个资产/维度形成稳定 logic node。
   - 新日报、新闻和数据库更新进入后，判断逻辑是新增、继续验证、弱化、消失还是反转。

7. 自我优化层
   - 第二天日报和前一天模型结果比较。
   - 检查主线是否被验证、哪些维度权重应调整、哪些新增因素应进入框架候选。
   - 输出仍为 candidate，不自动覆盖长期知识。

## 当前已落地命令

生成框架注册表和指标事件目录：

```bash
quanta-analysis-framework-registry --quanta-root /Volumes/数字大脑/quanta_data
```

比较两期日报逻辑演化：

```bash
quanta-futures-daily-logic-evolution \
  --previous-run /path/to/RUN-YYYYMMDD-...-RAW-DAILY \
  --current-run /path/to/RUN-YYYYMMDD-...-RAW-DAILY
```

如需模型写品种级演化总结：

```bash
quanta-futures-daily-logic-evolution \
  --previous-run /path/to/previous \
  --current-run /path/to/current \
  --llm
```

生成框架权重优化候选：

```bash
quanta-framework-weight-optimize \
  --quanta-root /Volumes/数字大脑/quanta_data \
  --current-run /path/to/RUN-YYYYMMDD-...-RAW-DAILY \
  --history-limit 20
```

使用 m3/MiniMax-M3 复核候选：

```bash
QUANTA_FRAMEWORK_OPTIMIZER_PROVIDER=m3 \
M3_API_KEY=... \
quanta-framework-weight-optimize \
  --quanta-root /Volumes/数字大脑/quanta_data \
  --current-run /path/to/RUN-YYYYMMDD-...-RAW-DAILY \
  --history-limit 20 \
  --llm \
  --provider m3
```

产物路径：

```text
agent_workspace/candidates/framework_optimization/{yyyy}/{mm}/{dd}/framework-weight-optimization-{HHMMSS}.json
agent_workspace/candidates/framework_optimization/latest/framework-weight-optimization.json
```

该产物只生成候选，不直接写回 active framework。前端可展示 `action`、`proposal_summary`、`current_metrics`、`historical_experience`、`recommended_adjustment` 和 `llm_review`。

生成微信公众号研报证据池，并刷新投研结论候选：

```bash
quanta-wechat-research-evidence \
  --quanta-root /Volumes/数字大脑/quanta_data \
  --date 20260618
```

加入资产级 LLM 结论更新：

```bash
quanta-wechat-research-evidence \
  --quanta-root /Volumes/数字大脑/quanta_data \
  --date 20260618 \
  --llm-assessment \
  --llm-provider m3 \
  --llm-asset-limit 30
```

如果 m3 key 未配置，也可以临时用 DeepSeek：

```bash
quanta-wechat-research-evidence \
  --quanta-root /Volumes/数字大脑/quanta_data \
  --date 20260618 \
  --llm-assessment \
  --llm-provider deepseek \
  --llm-asset-limit 8
```

产物路径：

```text
agent_workspace/candidates/research_reports/wechat_evidence/{yyyy}/{mm}/{dd}/wechat-research-evidence-{HHMMSS}.json
agent_workspace/candidates/research_reports/wechat_evidence/latest/wechat-research-evidence.json
```

该产物把微信研报正文转成 `article -> evidence -> asset -> dimension -> research_conclusion -> llm_assessment -> framework_update_candidates`。前端可以直接展示：

- `stats`：文章数、证据数、品种数、LLM 总结数、框架候选数。
- `assets[*].research_conclusion`：规则层的 prior/evidence/combined 分数和结论关系。
- `assets[*].llm_assessment`：模型合并后的主线更新、维度状态、跟踪点和质量备注。
- `assets[*].dimensions`：每个维度的方向、动态权重、证据组、跨来源验证情况。
- `framework_update_candidates`：未归类、低置信、逻辑反转或权重需要复核的候选项。

## 运行时输出关系

```text
wechat research report/news/mysql
  -> asset taxonomy
  -> analysis framework registry
  -> indicator/event catalog
  -> framework alignment
  -> dimension synthesis
  -> trade thesis
  -> logic evolution timeline
  -> framework update candidates
```

日报和新闻输出中会携带 `analysis_framework_ref`，用于追踪当次分析对齐的是哪一版框架注册表和词条目录。

## 下一步

- 将新闻快讯的 asset/dimension 映射切换到 registry/catalog 优先，framework 文件作为 fallback。
- 给 `framework_update_candidates` 增加人工审核状态机。
- 将 MySQL 关键指标接入同一维度合成层。
- 建立评估集：用 T+1 日报验证 T 日模型主线，记录验证/反转/遗漏原因。
