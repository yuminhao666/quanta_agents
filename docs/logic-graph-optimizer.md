# Quanta Logic Graph Optimizer

`logic_graph_optimizer` 是 Quanta 逻辑图谱的实验、评估和迭代层。它不直接替代
`research_logic_graph`，也不直接修改 active framework 或 `gold` 数据，而是在候选区建立一套可复现的
“评估 -> 找错 -> 小实验 -> 回归 -> compare -> review/promote/reject”闭环。

这份文档先沉淀框架思路，后续再从简单任务开始逐步梳理代码和产出。

## 背景

当前 Quanta 已经有三类重要基础：

- 各品种的分析框架维度：相当于逻辑图谱的初始骨架。
- 结构化研报、新闻摘要、Polymarket 日报、数据简报等中间产物：相当于触发图谱节点的证据流。
- `research_logic_graph/latest` 产物：已经能从结构化研报生成候选逻辑节点、边、driver trigger 和人类报告。

下一步不是一次性重写系统，而是把图谱算法变成一个可持续优化的工程过程。每次只选一个问题，生成一个
Challenger，用固定 benchmark 回归，确认它确实更好，再进入 review。

## 核心原则

- 先审计真实实现，再讨论理想架构。
- 所有实验只写 candidate 区，不直接改 `gold`、active framework 或线上图谱。
- 节点聚类目标是稳定概念 ID，不是研报里的自然语言名称。
- 方向、强弱、时间、冲突状态不进入节点名称，必须放入 `state_snapshot`。
- 所有 signal、driver 变化和 causal path 都必须能追溯到来源证据。
- 先做小 benchmark 和 review queue，再做大规模自动 promote。

## 边界

- 只读当前研报逻辑图谱、结构化研报目录、active framework 目录和已有 concept index。
- 不写 `gold`，不改 active framework，不回写 `research_logic_graph/latest`。
- 第一个 Challenger 只处理一个问题：把带方向/状态的节点标签拆成稳定概念身份和状态快照。
- 共振 Alpha 前端展示不是本阶段第一优先级；本阶段优先保证算法闭环、产物可读、结果可回归。

## 总体链路

```text
Raw Data
  -> 中间结构化产物
  -> Event / Evidence / Signal Candidates
  -> Framework Dimension Mapping
  -> Logic Concept Node Candidate
  -> Causal / Driver Edge Candidate
  -> Driver Trigger Candidate
  -> Human Review Queue
  -> Stable Registry / Champion
  -> Report / UI
```

这条链路里，`logic_graph_optimizer` 只负责从“已有候选图谱”开始做评估和优化，不负责爬虫、LLM 抽取、
正式入库和最终 UI。

## 三类输入

### 1. 稳定框架骨架

来源主要是各品种 active framework：

```text
/Users/miniquanta/Documents/quanta_research_group/data_lake/active_knowledge/research_frameworks/commodities/*.json
```

它定义相对稳定的分析维度，比如供给、需求、库存、成本、宏观、政策、地缘、价差结构等。它不应该频繁被
新闻或单篇研报直接改写。

### 2. 中间结构化产物

这些产物是后续触发图谱节点的证据来源：

- 结构化研报 profile
- 研报 evidence 汇总
- 新闻摘要 / 新闻逻辑映射
- Polymarket 日报
- 基本面数据简报
- 人工输入或 Agent 输入

这些产物要保留来源路径、时间、原文摘要和证据 ID，不能只保留最终判断。

### 3. 当前候选图谱

当前 baseline 主要读取：

```text
agent_workspace/candidates/research_logic_graph/latest/
  logic-nodes.json
  logic-edges.json
  temporal-episodes.json
  driver-trigger-candidates.json
  concept_index/
```

这些是优化器建立 baseline、benchmark 和 Challenger 的起点。

## 节点模型

逻辑图谱节点要分成“稳定身份”和“动态状态”两部分。

### 稳定身份

```json
{
  "concept_id": "asset.fut_cu.supply.concept_xxx",
  "preferred_label_zh": "海外铜矿",
  "alt_labels_zh": ["铜矿扰动", "海外矿端供应"],
  "asset": "铜",
  "asset_id": "FUT-CU",
  "dimension_type": "supply",
  "dimension_label": "基本面/供给/海外铜矿"
}
```

稳定身份解决的是“这是什么概念”。它不应该包含“偏多”“偏空”“增强”“削弱”“本周”“短期”等状态信息。

### 动态状态

```json
{
  "state_snapshot": {
    "state": "conflict",
    "direction": "bullish",
    "strength": 0.18,
    "confidence": 0.8,
    "support_evidence_count": 30,
    "conflict_evidence_count": 8,
    "first_seen": "2026-04-01",
    "last_seen": "2026-06-21"
  }
}
```

动态状态解决的是“这个概念现在怎么变化”。同一个 `concept_id` 可以经历多次状态变化，这些变化应该通过事件、
signal 和 state history 追踪。

## 优化闭环

每轮优化都按同一个流程走：

```text
1. Audit
   读取真实代码、真实产物、真实数据目录，记录当前系统能做什么。

2. Baseline
   对当前 Champion 或 latest 产物打指标，得到当前水平。

3. Benchmark
   固定一小组可复现样本，先用 2-3 个有共享节点的品种。

4. Error Taxonomy
   把错误分成可行动类别，比如节点身份状态混用、泛化过度、合并不足、边缺证据。

5. Challenger
   每次只改一个最重要的问题，生成候选结果。

6. Regression
   检查节点数、边数、证据追溯、holdout、driver link、无生产写入等硬门禁。

7. Compare
   和 baseline 对比指标，只看这个 Challenger 是否真的改善目标问题。

8. Decision
   输出 promote / reject / review。第一阶段以 review 为主。
```

## 第一阶段 Benchmark

第一版 benchmark 先选有共享节点且业务上重要的商品：

- 原油
- 铜
- 铝

采样方式：

- 每个品种 40 条样本。
- 按 `holdout / tune / dev` 分层。
- 优先选择证据量高、置信度高的节点。
- 保留 evidence refs、driver edge、状态字段和候选概念 ID。

第一版 benchmark 是弱标注，不是专家 gold set。它适合做回归和发现明显退化，不适合直接证明模型正确。

## 第一版错误分类

当前先维护这些错误类别：

- `stateful_node_identity`：节点名称里混入方向、强弱或时间状态。
- `generic_cross_asset_family_needs_review`：跨品种共享 family 过泛，需要人工判断是共性概念还是误合并。
- `possible_under_merge_within_asset`：同品种同维度里有相近概念，没有合并。
- `edge_traceability_gap`：边缺证据计数、端点或来源路径。
- `derived_concept_id_pending_registry`：概念 ID 可复现但尚未进入稳定 registry。

第一轮最重要的问题是 `stateful_node_identity`，因为它直接影响图谱可读性、聚类稳定性和状态回放。

## 产物组织

优化器输出应按“运行产物 + latest 指针 + review queue”组织，避免所有内容塞在一个大 JSON 里。

## 输出目录

```text
agent_workspace/candidates/logic_graph_optimizer/
  runs/YYYY/MM/DD/RUN-LOGIC-GRAPH-OPT-*/
    current_state_audit.md
    data_inventory.json
    reusable_modules.json
    known_gaps.json
    proposed_minimal_change.md
    benchmark/benchmark_manifest.json
    benchmark/benchmark_samples.jsonl
    baseline_metrics.json
    challenger_metrics.json
    error_taxonomy.json
    experiment_manifest.json
    metrics_comparison.json
    regression_report.md
    review_queue/node_review_queue.jsonl
    review_queue/edge_review_queue.jsonl
    optimization_report.md
    run_manifest.json
  benchmark/latest/
  champion/current.json
  experiments/EXP-*/
  review_queue/
  reports/latest/optimization_report.md
```

## Promote 层级

```text
candidate
  候选运行产物，只表示算法生成，不代表被接受。

review
  进入人工 review queue，等待确认概念、边、driver 关系。

champion
  当前可作为 baseline 的最佳候选版本。

gold
  经过正式审核和迁移后的稳定知识，不由 optimizer 直接写入。
```

第一阶段优化器只允许写 `candidate` 和 review queue。`champion` 只能保存指针或 baseline 信息，不能冒充正式图谱。

## 从简单任务开始的整理顺序

后续可以按下面的小任务一点点推进：

1. 梳理输入目录：确认 structured profiles、news brief、polymarket daily、data brief 的路径和字段。
2. 梳理当前产物：列出 `research_logic_graph/latest` 每个文件的用途、字段和下游消费方。
3. 固化 benchmark：把原油、铜、铝 120 条样本作为第一版回归集，并补人工备注字段。
4. 清理节点命名：把所有 UI 和报告里的节点展示统一切到 `preferred_label_zh`。
5. 建立 review queue 页面：先只 review concept identity，不 review 边权重。
6. 增加错误分类统计：按品种、维度、错误类型看最常见问题。
7. 增加第二个 Challenger：优先处理同品种 under-merge 或泛化 family 过度问题。
8. 再考虑接入新闻、Polymarket、数据简报，让它们触发同一套 concept node。

每个任务都应该有：

- 输入路径
- 输出路径
- 复现命令
- 人类可读报告
- 是否写生产数据
- rollback / review 方式

## 复现命令

```bash
quanta-logic-graph-optimizer optimize \
  --quanta-root "${GJ_QUANTA_DATA_ROOT:-/Volumes/数字大脑/quanta_data}" \
  --assets 原油 铜 铝 \
  --benchmark-size-per-asset 40
```

只做审计：

```bash
quanta-logic-graph-optimizer audit \
  --quanta-root "${GJ_QUANTA_DATA_ROOT:-/Volumes/数字大脑/quanta_data}"
```

比较两份指标：

```bash
quanta-logic-graph-optimizer compare \
  --baseline /path/to/baseline_metrics.json \
  --challenger /path/to/challenger_metrics.json
```

## Promote 规则

第一版优化器的 `promote`、`rollback`、`replay` 命令保持非变更行为。原因是当前
Challenger 生成的 `concept_id` 仍是 `derived_pending_registry`，需要人工 review
后才能进入官方 registry 或影响线上图谱。

## 当前已知下一步

短期最值得做的是把 `review_queue/node_review_queue.jsonl` 做成人能看的 review surface。先只问三个问题：

- 这个 `preferred_label_zh` 是否是稳定概念？
- 是否应该和同品种其他概念合并？
- 是否应该进入跨品种共享 family？

这一步做好以后，再推进边、权重、delay、多跳传播和 driver state 更新。
