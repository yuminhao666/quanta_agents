# Assistant QA Agent

为平台问答助手提供受控召回和证据回答。

输出应符合 `quanta_data/configs/schemas/assistant_qa_run.v1.schema.json`：

- retrieval plan
- evidence capsule refs
- prompt pack ref
- claims with evidence refs
- missing evidence flags
- candidate refs when new knowledge is proposed
