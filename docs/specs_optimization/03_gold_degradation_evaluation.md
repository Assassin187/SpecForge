# Step 3：Gold Specs Degradation Evaluation

## 1. 摘要

本步骤使用 Step 2 的 `degrade_specs.py` 对 MQTT、CoAP example specs 生成 7 组 gold degradation profiles，并对每组执行：

- schema validation；
- coder loader validation；
- rendered header validation；
- bounded coder generation + compile + repair；
- compile 后的非阻断 smoke/behavior verification。

本步骤没有执行 Step 4，没有做 planning-vs-gold oracle diagnosis，没有修改 `agent/planning` 或 `agent/coder` 核心 pipeline。实验输入为 code-derived example specs，不能解释为 protocol facts。

核心结果：

1. MQTT、CoAP 的 `Gold-Full` 当前仍可通过完整 coder run：MQTT 在 1 轮 source repair 后 compile 通过且 4/4 smoke 通过；CoAP initial compile 通过且 4/4 smoke 通过。
2. 所有 protocol/profile 都通过 schema、spec load 和 rendered header validation；删除 behavior/wire/calls/test vectors 后最先出现的问题不在 loader/header 层，而在 generated source compile 或 smoke behavior 层。
3. `Gold-Sidecar-Only-Traceability` 在 MQTT、CoAP 均 compile + smoke 通过，支持 module/file `DOC_REF` 更适合 sidecar，不是 coder-facing strict specs 的硬必需字段。
4. `Gold-No-Wire-Binding` 在 MQTT、CoAP 均 compile + smoke 通过；在当前工具保留 `ACCESS_PATHS` 的前提下，function-level `WIRE_MAPPING` 未显示为 coder 硬必需字段。
5. behavior detail、test vectors、calls 的证据是混合的：它们不是 loader/header 硬必需，但会影响 source compile、interop 或 smoke behavior，不能直接移出 strict specs。
6. 当前已有证据说明 strict specs 中部分 traceability/wire 细节可能过重；但还没有证据支持整体压缩 strict specs，尤其 behavior/test-vector 类字段仍体现出 coder usability 价值。

## 2. 输入、Profiles 与命令

输入：

| protocol | input path | spec files | module count | role |
|---|---:|---:|---:|---|
| MQTT | `specs-example/mqtt_specs` | 101 | 6 | `BROKER` |
| CoAP | `specs-example/coap_specs` | 67 | 4 | `SERVER` |

生成命令：

```bash
python tools/specs_optimization/degrade_specs.py \
  --input specs-example/mqtt_specs \
  --output experiments/specs_optimization/gold_degradation/mqtt \
  --profiles full,no_behavior_detail,no_wire_binding,no_calls,min_interface,no_test_vectors,sidecar_only_traceability \
  --validate \
  --overwrite

python tools/specs_optimization/degrade_specs.py \
  --input specs-example/coap_specs \
  --output experiments/specs_optimization/gold_degradation/coap \
  --profiles full,no_behavior_detail,no_wire_binding,no_calls,min_interface,no_test_vectors,sidecar_only_traceability \
  --validate \
  --overwrite
```

Coder run template：

```bash
timeout 45m python -m agent.coder \
  --api-key-env ALI_API_2 \
  --spec-root experiments/specs_optimization/gold_degradation/<protocol>/<profile>/specs \
  --output-dir experiments/specs_optimization/gold_degradation/coder_runs/<protocol>/<profile> \
  --max-repair-rounds 3 \
  generate
```

完整结果：

- `experiments/specs_optimization/gold_degradation/results.json`
- `experiments/specs_optimization/gold_degradation/results.md`
- per-profile degradation manifests under `experiments/specs_optimization/gold_degradation/{mqtt,coap}/`
- per-profile coder manifests under `experiments/specs_optimization/gold_degradation/coder_runs/`
- command logs under `experiments/specs_optimization/gold_degradation/run_logs/`

指标口径：

- `spec load` 和 `rendered header` 来自每个 profile 的 `manifest.json` 中 `validation` 结果。
- `compile`、`repair iterations`、`smoke` 来自每个 coder run 的 `_agent_logs/run_manifest.json`。
- `compile errors`、`missing symbol/type`、`dependency/header errors`、`hallucinated symbol count` 来自 `_agent_logs/*compile_stderr*.txt` 的正则归类；它们是诊断性指标，不是新的 validator。
- 每行原始 evidence 可通过 `results.json` 的 `coder_run_manifest` 字段回溯到对应 run；profile mutation 则回溯到 `experiments/specs_optimization/gold_degradation/<protocol>/<profile>/manifest.json`。

## 3. Results Matrix

| protocol | profile | spec load | rendered header | compile | repair iterations | smoke | LoC | compile errors | missing symbol/type | dependency/header errors | hallucinated symbol count | key errors | interpretation |
|---|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---|---|
| MQTT | Gold-Full | pass | pass | pass | 1 | 4/4 pass | 2454 | 1 | 2 | 2 | 2 | initial source compile error repaired | baseline stable, though source repair is still needed |
| MQTT | Gold-No-Behavior-Detail | pass | pass | fail | 3 | n/a | 2327 | 3 | 3 | 0 | 3 | `network/tcp_server.c` used undeclared `accept4`; repair made no progress | behavior weakening correlates with source-level implementation drift |
| MQTT | Gold-No-Wire-Binding | pass | pass | pass | 1 | 4/4 pass | 2426 | 6 | 6 | 0 | 2 | prior source compile errors repaired | `WIRE_MAPPING` removal did not hurt final compile/smoke in this run |
| MQTT | Gold-No-Calls | pass | pass | pass | 0 | 3/4 fail | 2421 | 0 | 0 | 0 | 0 | mosquitto interop timed out | calls are not compile-hard, but may anchor interop behavior |
| MQTT | Gold-Min-Interface | pass | pass | fail | 3 | n/a | 2195 | 4 | 3 | 0 | 3 | repeated undeclared `accept4`; max repair rounds exhausted | aggressive minimization removes too much source guidance |
| MQTT | Gold-No-TestVectors | pass | pass | pass | 1 | 4/4 pass | 2483 | 1 | 1 | 0 | 1 | initial source compile error repaired | MQTT smoke did not depend on test vectors in this single run |
| MQTT | Gold-Sidecar-Only-Traceability | pass | pass | pass | 0 | 4/4 pass | 2449 | 0 | 0 | 0 | 0 | none | `DOC_REF`/traceability is safe to sidecar for coder usability |
| CoAP | Gold-Full | pass | pass | pass | 0 | 4/4 pass | 1639 | 0 | 0 | 0 | 0 | none | baseline stable with no repair |
| CoAP | Gold-No-Behavior-Detail | pass | pass | pass | 0 | 2/4 fail | 1646 | 0 | 0 | 0 | 0 | `coap_extended_option` timed out | behavior detail affects deeper protocol scenarios |
| CoAP | Gold-No-Wire-Binding | pass | pass | pass | 1 | 4/4 pass | 1633 | 1 | 0 | 0 | 0 | prior source compile error repaired | `WIRE_MAPPING` removal did not hurt final compile/smoke in this run |
| CoAP | Gold-No-Calls | pass | pass | pass | 1 | 4/4 pass | 1676 | 3 | 6 | 3 | 6 | missing stdlib/stdio includes repaired | calls/rely are useful quality hints but not final-pass requirements here |
| CoAP | Gold-Min-Interface | pass | pass | pass | 0 | 0/4 fail | 1719 | 0 | 0 | 0 | 0 | `/hello` response behavior failed | compile alone is insufficient; minimized specs lose behavior anchoring |
| CoAP | Gold-No-TestVectors | pass | pass | pass | 0 | 0/4 fail | 1641 | 0 | 0 | 0 | 0 | `/hello` response envelope or `2.05` code invalid | test vectors appear behavior-critical for CoAP |
| CoAP | Gold-Sidecar-Only-Traceability | pass | pass | pass | 0 | 4/4 pass | 1664 | 0 | 0 | 0 | 0 | none | `DOC_REF`/traceability is safe to sidecar for coder usability |

## 4. Failure Pattern Analysis

### 4.1 Gold-Full 是否仍然稳定通过

是。当前仓库状态下：

- MQTT `Gold-Full`：spec load/header validation pass；full coder run compile pass；1 轮 source repair；4/4 smoke pass。
- CoAP `Gold-Full`：spec load/header validation pass；initial compile pass；4/4 smoke pass。

这支持 Step 3 的基线假设：example specs 仍可驱动 coder 生成可编译且行为可验证的 implementation。

### 4.2 删除字段后最先出现的问题

最先出现的问题不在 schema、loader 或 rendered header 层。14 个 protocol/profile 组合全部通过这些低成本验证。

按 profile 看：

- `no_behavior_detail`：MQTT 触发 source compile failure，表现为 `network/tcp_server.c` repeatedly hallucinated/used undeclared `accept4`；CoAP compile pass 但 extended-option smoke timeout。这说明 behavior detail 的作用不只是“解释性文字”，它会影响生成出的 concrete implementation choice。
- `no_wire_binding`：MQTT、CoAP 均 compile + smoke pass；只有可修复 source compile warning/error。由于 `ACCESS_PATHS` 被保留，本结果只能说明 function-level `WIRE_MAPPING` 不是本轮 hard requirement，不能推出 wire/access 信息整体可删除。
- `no_calls`：MQTT compile pass 但 mosquitto interop timeout；CoAP compile + smoke pass，但 initial compile 有 missing include / implicit declaration，被 repair 修复。calls/rely 信息更像 implementation quality guard，而不是 loader/header hard gate。
- `min_interface`：MQTT compile failed after 3 repair rounds；CoAP compile pass 但 0/4 smoke pass。
- `no_test_vectors`：MQTT compile + smoke pass；CoAP compile pass 但 0/4 smoke pass。协议差异明显，说明 test vectors 是否可移入 sidecar 需要按协议和 coder prompt 消费方式验证。
- `sidecar_only_traceability`：MQTT、CoAP 均 compile + smoke pass。

### 4.3 硬必需字段

本实验支持以下字段类别仍是 coder 硬必需或准硬必需：

- identity/routing/header 基础字段：`KIND`、trace ids、module/file names、paths、`MODULES[].FILES`、`GENERATION_ORDER`、`HEADER.DATA`、`HEADER.INTERFACE`、`HEADER/SOURCE.DEPENDENCY`。本轮没有删除这些字段，且它们是所有 profile 能加载和生成的基础。
- behavior/action detail：不是 loader/header 必需，但 MQTT `no_behavior_detail` compile failure、CoAP `no_behavior_detail` smoke degradation 说明其对 source generation 和 protocol behavior 有实际作用。
- test vectors：不是 compile 必需；但 CoAP `no_test_vectors` 0/4 smoke 说明它们对 behavior anchoring 有价值，至少不能只凭当前 verifier 不直接读取而移出 strict specs。
- calls：不是 compile 硬必需；但 MQTT `no_calls` interop timeout 和 CoAP `no_calls` initial missing include repair 表明 calls/rely 信息有助于 interop quality 和 implementation hygiene。

### 4.4 更适合 validator / traceability / sidecar 的字段

本实验支持以下初步判断：

- module/file `DOC_REF`：`sidecar_only_traceability` 在两个协议均通过；`DOC_REF` 可进入 sidecar，strict specs 中不需要为 coder generation 保留。
- `PROTOCOL.SCOPE`：只在 `min_interface` 中被删除，不能单独归因；结合 Step 1 consumer audit，它更像 manifest/sidecar 信息。
- function-level `WIRE_MAPPING`：当前 `no_wire_binding` 删除 `WIRE_MAPPING` 后两个协议均通过；在 `ACCESS_PATHS` 保留的前提下，它更像 validator/traceability 或 optional prompt detail，而不是当前 coder 硬字段。

### 4.5 strict coder specs 是否过重

有局部证据，但不足以支持整体 schema 压缩：

- 支持“过重”的证据：`DOC_REF` sidecar-only 无损；`WIRE_MAPPING` 删除无损；部分 calls/test-vector 字段不影响 compile。
- 反对“一刀切压缩”的证据：behavior detail 删除会造成 MQTT compile failure 和 CoAP smoke failure；CoAP test vectors 删除会造成 smoke 0/4；`min_interface` 对 MQTT/CoAP 都明显退化。

因此 Step 3 的结论是：strict specs 中确有 traceability/wire 细节可下沉候选，但 behavior/test-vector/call 类字段仍有 coder usability 价值。是否移动到 sidecar 或保留为 strict fields 需要 Step 4 结合 planning-vs-gold 和 oracle diagnosis 进一步验证。

更具体地说，当前证据支持一种分层方向：`DOC_REF` 这类 traceability 字段优先 sidecar；`WIRE_MAPPING` 可考虑作为 validator/sidecar 候选；behavior、test vectors、calls/rely 暂不适合直接删除，而应在 Step 4 中检查 planning 是否能稳定生成同等质量的信息。

## 5. Blockers 与工具记录

Blockers:

- 无输入缺失：MQTT、CoAP example specs 都存在并可运行。
- 无 API/timeout blocker：14 个 full coder runs 均完成。
- 无 Step 2 工具 bug 修复：本步骤未修改 `tools/specs_optimization/*`。

限制：

- 每个 profile 只做单次 LLM generation，结果受 LLM nondeterminism 影响；尤其 MQTT `accept4` compile failure 不能单独解释为 protocol semantics failure。
- `no_wire_binding` profile 保留 `ACCESS_PATHS`，因此只能说明 function-level `WIRE_MAPPING` 在当前条件下非硬必需，不能证明 wire/access 约束整体可删除。
- smoke verifier 覆盖有限；compile pass 不等于协议完整正确。

## 6. Step 4 仍需验证的问题

Step 4 应继续验证：

1. planning specs 与 gold specs 在 behavior/action detail、test vectors、call contracts、wire/access binding 上的真实覆盖差距；
2. MQTT `no_behavior_detail` / `min_interface` 的 `accept4` failure 是否来自缺失 implementation constraint、LLM nondeterminism，还是 dependency/system include 表达不足；
3. CoAP `no_test_vectors` 0/4 smoke 是否说明 test vectors 应留在 strict specs，还是可以移入被 coder prompt 消费的 sidecar；
4. `WIRE_MAPPING` 是否可以下沉到 validator-sidecar，同时保留 `ACCESS_PATHS` 或更稳定的 wire/access projection；
5. calls/rely 字段对 interop 与 repair iteration 的影响是否能通过 oracle substitution 或 repeated runs 稳定复现。
