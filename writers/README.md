# Writers

按 `quanta_data/configs/schemas` 写入结构化产物。

Writer 负责：

- 生成稳定 ID。
- 计算或保存 hash。
- 写相对 `GJ_QUANTA_DATA_ROOT` 的路径。
- 校验 schema。
- 避免 Agent 直接写入 `gold`。
