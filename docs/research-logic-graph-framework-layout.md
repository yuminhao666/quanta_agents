# 研报驱动逻辑图谱的框架与目录设计

本文定义“稳定分析框架、指标/事件 catalog、研报逻辑图谱”三者的边界。目标是用四月以来的结构化研报持续更新逻辑图谱，同时避免把短期事件、长句研报观点和临时指标直接写入长期 framework。

## 当前检查结论

截至 2026-06-21，本地可见的 active commodity framework 位于：

```text
/Users/miniquanta/Documents/quanta_research_group/data_lake/active_knowledge/research_frameworks/commodities
```

抽样和结构统计显示：

- active framework 共有 86 个品种文件、516 个 core dimensions，且每个品种都是 6 个核心维度。
- 这些 6 维框架适合作为稳定坐标系，但粒度不足以直接承载“矿端扰动强化”“美联储降息路径”“OPEC+减产纪律”这类逻辑节点。
- `analysis-framework-registry.json` 当前包含 79 个资产、1768 个维度，`indicator-event-catalog.json` 包含 49184 个 term。这里已经有更细的候选维度和词条，但 supply/inventory 占比偏高，部分 event term 是完整研报句子，不适合作为长期框架字段。
- `wechat-research-evidence.json` 当前有 3442 条 evidence，其中有大量中性跟踪证据，也有 383 条落到“未归类”。这说明需要用 catalog 增强召回，但不能把所有未归类内容都升格为新维度。
- active framework 存在少量模板串味，例如棉花/棉纱/苹果/红枣里混入“食糖、淀粉糖、化纤”，黄金框架里混入“光伏银浆、白银工业需求”。这类内容应进入 framework audit candidate，由人工审核后修正。

## 分层原则

稳定 framework 只回答四个问题：

1. 这个品种长期看哪些维度？
2. 维度之间有哪些稳定传导关系？
3. 每个维度如何影响 driver state？
4. 哪些 catalog 文件负责补充指标、事件和 claim 词条？

它不应该长期存放：

- 高频新闻事件；
- 研报原句；
- 某一天的库存、产量、价差数值；
- 临时主题名；
- 未经审核的指标别名；
- 直接投资结论。

具体事件/指标/claim 应单独进入 catalog 文件。研报和新闻只生成 evidence、research signal、logic node candidate 和 graph mutation，不直接覆盖 active framework。

## 目标目录结构

推荐把长期知识和运行产物分开：

```text
quanta_data/
  gold/
    reference_data/
      assets/
        futures_assets.v1.json

  knowledge_base/
    frameworks/
      commodity_dimension_taxonomy.v1.json
      commodities/
        FUT-CU/
          framework-core.v1.json
        FUT-SC/
          framework-core.v1.json
        FUT-AU/
          framework-core.v1.json

    catalogs/
      shared/
        macro-indicators.v1.json
        macro-events.v1.json
        geopolitics-events.v1.json
      commodities/
        FUT-CU/
          indicators.v1.json
          event-triggers.v1.json
          claim-patterns.v1.json
          aliases.v1.json
        FUT-SC/
          indicators.v1.json
          event-triggers.v1.json
          claim-patterns.v1.json
          aliases.v1.json

  agent_workspace/
    candidates/
      analysis_framework/
        latest/
          analysis-framework-registry.json
          indicator-event-catalog.json
      research_logic_graph/
        latest/
          logic-nodes.json
          logic-edges.json
          temporal-episodes.json
          driver-trigger-candidates.json
          human-report.md
    runs/
      research_logic_graph/
        yyyy/mm/dd/RUN-*/
          run_manifest.json
          input_refs.json
          evidence-signals.jsonl
          logic-node-updates.jsonl
          graph-mutations.jsonl
          validation.md
```

现阶段不必一次性迁移所有历史文件。可以先保持当前 active framework 位置不变，由 `quanta-analysis-framework-registry` 生成 candidate registry；后续再把稳定框架升级到 `knowledge_base/frameworks/commodities/{asset_id}/framework-core.v1.json`。

## 稳定 Framework 文件

`framework-core.v1.json` 应保持短、稳、可审核：

```json
{
  "schema_version": "framework_core.v1",
  "asset_id": "FUT-CU",
  "asset_name": "铜",
  "status": "active",
  "dimensions": [
    {
      "dimension_id": "FUT-CU:supply",
      "label": "供给",
      "dimension_type": "supply",
      "role": "primary_driver",
      "default_weight": 0.18,
      "driver_mapping": {
        "driver_id": "DRV:FUT-CU:supply",
        "signal_vector_keys": ["supply", "price"]
      },
      "catalog_refs": [
        "knowledge_base/catalogs/commodities/FUT-CU/indicators.v1.json",
        "knowledge_base/catalogs/commodities/FUT-CU/event-triggers.v1.json"
      ]
    }
  ],
  "causal_templates": [
    {
      "from_dimension": "FUT-CU:supply",
      "to_dimension": "FUT-CU:price",
      "polarity": 1,
      "delay": "days",
      "confidence": 0.7
    }
  ],
  "governance": {
    "write_back": "human_review_required",
    "runtime_evidence_write_forbidden": true
  }
}
```

建议统一的 `dimension_type`：

```text
supply / demand / supply_demand / inventory / cost / policy / macro /
geopolitics / spread / valuation / substitution / weather / logistics /
funding / seasonality / disease / capacity_cycle / other
```

## Catalog 文件

Catalog 是可增长、可审计的词条层。每条词条可以映射到多个 framework dimension，但不能直接成为 driver。

```json
{
  "schema_version": "framework_term_catalog.v1",
  "asset_id": "FUT-CU",
  "catalog_type": "indicators",
  "terms": [
    {
      "term_id": "TERM:FUT-CU:TC",
      "term_type": "indicator",
      "name": "铜精矿 TC",
      "aliases": ["TC/RC", "加工费"],
      "framework_node_refs": ["FUT-CU:supply", "FUT-CU:cost"],
      "unit": "美元/吨",
      "frequency": "weekly",
      "direction_hint": {
        "lower": "supply_tightening",
        "higher": "supply_easing"
      },
      "review_status": "active"
    }
  ]
}
```

`event-triggers.v1.json` 存事件触发词和事件定义：

```json
{
  "term_id": "EVTERM:FUT-SC:OPEC_CUT",
  "term_type": "event_trigger",
  "name": "OPEC+减产纪律",
  "aliases": ["OPEC+减产", "减产执行率", "产量配额"],
  "framework_node_refs": ["FUT-SC:supply", "FUT-SC:geopolitics"],
  "event_definition": "OPEC+成员国减产、增产、执行率变化或会议指引改变",
  "effective_time_rule": "meeting_date_or_report_publication",
  "review_status": "active"
}
```

`claim-patterns.v1.json` 存研报中常见判断句式，但它仍是候选映射辅助，不是事实：

```json
{
  "term_id": "CLAIM:FUT-AU:FED_CUT_SUPPORT",
  "term_type": "claim_pattern",
  "name": "降息预期支撑贵金属",
  "framework_node_refs": ["FUT-AU:macro", "FUT-AU:funding"],
  "polarity_hint": "bullish",
  "confidence_default": 0.55,
  "review_status": "active"
}
```

## 研报进入逻辑图谱的算法

输入范围：

```text
canonical_documents/research_reports/structured_profiles/hzzhqx_wechat/2026/04/01 至今
agent_workspace/candidates/research_reports/wechat_evidence/latest/wechat-research-evidence.json
agent_workspace/candidates/analysis_framework/latest/analysis-framework-registry.json
agent_workspace/candidates/analysis_framework/latest/indicator-event-catalog.json
```

处理流：

```text
structured profile / wechat evidence
  -> evidence normalization
  -> framework dimension mapping
  -> catalog term matching
  -> research_signal.v1
  -> logic node candidate
  -> temporal episode update
  -> causal edge candidate
  -> driver trigger candidate
  -> human readable graph/report
```

### 1. Evidence Normalization

每条研报 evidence 先规范为统一 capsule：

```json
{
  "evidence_id": "WREP-...",
  "asset_id": "FUT-CU",
  "source_role": "research_report",
  "published_at": "2026-06-18T09:00:00+08:00",
  "text": "矿端扰动导致 TC 下行，铜精矿偏紧格局延续。",
  "direction": "bullish",
  "direction_score": 0.7,
  "source_ref": {
    "article_id": "...",
    "profile_id": "...",
    "path": "canonical_documents/..."
  }
}
```

### 2. Dimension Mapping

先用稳定 framework 判断“属于哪个长期维度”，再用 catalog 判断“命中了哪些具体指标/事件”。

评分建议：

```text
mapping_score =
0.35 * asset_match
+ 0.25 * framework_dimension_match
+ 0.25 * catalog_term_match
+ 0.10 * source_section_quality
+ 0.05 * direction_confidence
```

`mapping_score < 0.45` 进入 `unmapped_evidence_candidates`，不能自动生成新维度。

### 3. Research Signal

每条有效 evidence 生成或合并到 `research_signal.v1`：

```text
source_role = research_report
signal_kind = thesis_support / thesis_conflict / framework_mapping
asset_refs = [asset]
framework_node_refs = [dimension]
evidence_refs = [evidence_id]
direction / strength / confidence = 规则层计算，LLM 只可辅助摘要
```

### 4. Logic Node Candidate

逻辑节点不是 framework dimension，也不是单条事件。它是同一资产、同一维度、同一谓词在一段时间里的稳定表达。

聚类 key：

```text
asset_id
dimension_id
normalized_predicate
direction
event_definition_hash
```

示例：

```json
{
  "logic_node_id": "LGN-FUT-CU-MINE-SUPPLY-TIGHT",
  "human_label": "矿端扰动强化铜供应偏紧",
  "asset_id": "FUT-CU",
  "framework_node_refs": ["FUT-CU:supply"],
  "catalog_term_refs": ["TERM:FUT-CU:TC", "EVTERM:FUT-CU:MINE_DISRUPTION"],
  "direction": "bullish",
  "support_evidence_count": 18,
  "conflict_evidence_count": 4,
  "first_seen": "2026-04-08",
  "last_seen": "2026-06-18",
  "review_status": "candidate"
}
```

### 5. Temporal Episode

每个 logic node 按日或周滚动更新：

```text
new -> tracking -> strengthening -> weakening -> conflict -> reversal -> dormant
```

状态计算：

```text
recent_support = time_decay(sum(support_signal_strength))
recent_conflict = time_decay(sum(conflict_signal_strength))
net = recent_support - recent_conflict

strengthening: net 上升且 support 来源数增加
weakening: net 下降但方向未反转
conflict: support 和 conflict 同时高
reversal: net 符号改变且置信度超过阈值
dormant: 超过半衰期无新增证据
```

### 6. Causal Edge Candidate

因果边不能只靠文本相似生成。候选边来自三类证据：

1. framework causal templates；
2. industry graph 的上下游/替代/成本传导；
3. 研报中多次共现且有时间先后关系的 logic node。

边置信度：

```text
edge_confidence =
0.35 * template_prior
+ 0.25 * repeated_cooccurrence
+ 0.20 * temporal_order_score
+ 0.10 * multi_source_support
+ 0.10 * conflict_penalty_adjusted
```

输出仍为 `candidate`，不直接写 active causal graph。

### 7. Driver Trigger Candidate

只有当 logic node 的时间状态发生显著变化时，才触发 driver：

```text
trigger_score =
0.40 * logic_node_strength_delta
+ 0.25 * confidence
+ 0.20 * source_diversity
+ 0.15 * recency
```

`trigger_score >= 0.65` 进入 `asset_event_state`，由 canonical event / signal update / driver state machine 继续处理。

## 输出给前端的图谱读模型

前端图谱不要直接显示机器 ID。节点必须有 `human_label`：

```json
{
  "node_id": "LGN-FUT-CU-MINE-SUPPLY-TIGHT",
  "human_label": "矿端扰动强化铜供应偏紧",
  "node_type": "logic_node",
  "asset_label": "铜",
  "dimension_label": "供给",
  "state": "strengthening",
  "evidence_count": 18,
  "latest_evidence_summary": "多篇研报继续提到 TC 下行和海外矿扰动。"
}
```

ID 只放在 hover、详情面板和溯源抽屉中：

```text
human graph: 资产 -> 维度 -> 逻辑节点 -> 时间状态 -> driver
trace panel: logic_node_id / signal_id / evidence_id / article_id / source_path
```

## 第一阶段验收

第一阶段不追求自动生成“完美因果图”，只验收四件事：

1. 稳定 framework 和指标/事件 catalog 已经分层。
2. 四月以来研报可以稳定映射到 framework dimension 和 catalog term。
3. 高频研报观点可以聚成可读的 logic node，并有时间状态。
4. 每个 logic node、edge、driver trigger 都能追溯到 evidence_id 和 source_ref。

推荐先跑铜、原油、黄金、螺纹、豆粕、铝、碳酸锂、集运欧线 8 个品种，覆盖有色、能源、贵金属、黑色、农产品、新能源和航运。
