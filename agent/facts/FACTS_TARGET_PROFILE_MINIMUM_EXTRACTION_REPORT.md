# Facts Target-Profile Minimum Extraction 决策报告

## 结论

本轮 facts-only 优化达到 correctness 与 stability 的 Go 条件：facts agent 能从 `MQTT_3.1.1.txt` 和 facts-owned `mqtt_min` target profile 推导出稳定的 8-surface 最小 broker/QoS0 闭包，三次 fresh extraction 均通过 verifier、dependency closure、gold structural comparator 和 critical semantic rubric。

候选 facts 与 `gold_facts/mqtt_min/protocol_facts.json` 在 public JSON structure、minimum surface、paired response、状态/路由/资源骨架、错误与限制、deferred features、implementation assumptions 和 `planning_inputs` 上语义结构相当。它不是 gold 的文本复制，证据来自 MQTT technical document 与可识别的 `profile_*` scope directives。

本结论不包含 planning 验证；按本轮边界，只比较 facts candidate 与现有 gold facts。

## 实施范围

所有实现和产物都位于 `agent/facts/`：

- facts-local target profile loader、validator、semantic projection 和 profile evidence；
- MQTT minimum target profile；
- 正文 heading 修复、document-level surface index 和 capability resolution；
- typed dependency closure、feature-constraint pruning 和 scoped retrieval；
- scoped prompts、evidence closure、gold-compatible normalization；
- profile/scope/evidence verifier gates；
- facts-local structural/semantic comparator 与 JSON/Markdown reports。

未修改或运行 planning、coder、evaluation 模块。

## 运行结果

| Run | Verifier | Closure | Structural | Critical semantic | Semantic gaps | Forbidden scope | Tokens |
| --- | --- | --- | --- | --- | ---: | ---: | ---: |
| 1 | PASS | PASS | PASS | PASS | 0 | 0 | 297620 |
| 2 | PASS | PASS | PASS | PASS | 0 | 0 | 296272 |
| 3 | PASS | PASS | PASS | PASS | 0 | 0 | 299941 |

三次均固定得到：

- included surface：CONNECT、CONNACK、PUBLISH、SUBSCRIBE、SUBACK、PINGREQ、PINGRESP、DISCONNECT；
- unresolved capability：0；
- unknown evidence refs：0；
- main state/resource 中的 QoS1/2 transaction leakage：0；
- public top-level/category structure 与 gold contract 一致。

## 与原 baseline 的变化

原 fresh baseline 使用 104 个错误切分的 chunks，verifier 有 36 个 errors，其中 29 个 traceability gaps、5 个 planning insufficiency、2 个 coverage gaps。优化后正文 heading 能识别 MQTT packet sections，形成 209 个 chunks；三次 verifier errors 都为 0。

关键改善来自 deterministic scope control，而不是让 LLM 猜测 minimum：

- profile capabilities 先映射 document-backed surface seeds；
- request/mandatory response 做 fixed-point closure；
- `delivery_qos=[0]` 阻止 PUBACK/PUBREC/PUBREL/PUBCOMP transaction branch；
- category extraction 共享同一 scope envelope；
- normalization 和 verifier 强制 public shape、scope 与 evidence closure。

## 与 gold facts 的差异分类

- `acceptable_variation`：具体 prose、evidence IDs、list ordering、各类事实数量和 LLM 提取细节不同；
- `gold_only_implementation_detail`：example implementation 的 C module/API、特定 decoder 行为不计入 technical-document factual score；
- `candidate_only_supported_fact`：候选可包含 technical document 支持、但 gold example 未显式物化的规范边界；
- `critical_scope_mismatch`：0；
- `structural_mismatch`：0；
- `semantic_gap`：0。

## Remaining gap：成本

correctness 达标，但 token cost 目标未达到：三次中位数 297620，比 290347-token baseline 高约 2.5%，也明显高于 203243 的目标。原因是完整 surface discovery、每个 category 的 rerank/extract 和 reconciliation 仍产生约 27 次模型调用；新增 scope control 当前主要提高正确性，没有减少调用数。

后续若单独开启 cost tranche，应优先合并 rerank、复用 deterministic surface index、减少 category prompt 重复上下文；不得通过删除 required evidence 或放宽 verifier 来降 token。

## 最终决策

Correctness/structure/stability：**GO**。

Cost optimization：**NO-GO，留作独立后续任务**。

因此，本轮目标“让 facts agent 根据 technical document 和 target profile 抽取语义结构与 MQTT minimum gold 相当的最小实现协议事实”已完成；本 tranche 在 facts/gold comparison 处停止。
