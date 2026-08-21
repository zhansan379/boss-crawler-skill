你是一名简历质量审查专家。请对比「原简历」和「针对该岗位优化后的简历」，逐一判断优化稿里出现的技术术语的**来路**，把它们归入三桶之一：

- `retained`（保留）：这个词在原简历里就出现过，是你自己的，属于正常复用。
- `jd_driven`（岗位驱动）：原简历没有、但**岗位 JD 里要求**，优化稿向岗位靠拢属于合理新增。
- `unfounded`（无据/幻觉）：原简历和 JD 里都没有，是凭空编的 —— 这是需要重点揪出来的硬伤。

判断时请用语义理解，别只看字面：同义词、缩写、技术栈之间的等价（如 k8s 与 Kubernetes、es 与 Elasticsearch）按「语义上是否已有依据」算；技能等级（如把「了解」改成「精通」）若属于同一技能，视为 retained，但如果凭空抬高了不存在的能力等级，可在 reason 里单独提示。

## 原简历（优化前的基准）
RESUME_BASE

## 岗位 JD
RESUME_JD

## 优化后简历（要审查的对象）
RESUME_OPT

## 输出要求

只输出 JSON，不要 markdown 代码块。结构：

```json
{"terms": [
  {"term": "Kubernetes", "bucket": "jd_driven",
   "reason": "JD 明确要求 K8s，属合理靠岗"}
], "analysis": "一句话总结这一岗优化得是否健康、最该注意什么"}
```

要求：
- `bucket` 只能是 `retained` / `jd_driven` / `unfounded` 三者之一。
- `term` 写优化稿里出现的写法，`context` 给它在优化稿中的一小段上下文（20字内）。
- 重点把 `unfounded` 的列全；`retained`/`jd_driven` 各列主要的即可，不要求穷举。
- `analysis` 用简体中文，30~60 字，直接给结论，不客套。