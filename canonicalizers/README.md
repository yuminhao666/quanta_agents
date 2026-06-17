# Canonicalizers

把 raw object 转成标准化文档、chunk、table 和 metadata。

写入目标：

```text
canonical_documents
```

输入必须有 raw manifest，输出必须保留 source、time、hash 和 lineage。
