# Futures Daily Agent

生成期货市场日报、品种分析、主题生命周期和审核包。

默认写入：

```text
agent_workspace/runs
agent_workspace/prompt_packs
agent_workspace/candidates/market_brief
agent_workspace/candidates/asset_analysis
agent_workspace/review_packages
```

自动结果可以是 `candidate` 或 `machine_published`，但不能直接进入 `gold`。
