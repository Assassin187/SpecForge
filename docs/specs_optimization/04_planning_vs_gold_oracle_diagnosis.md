# Step 4：Planning-vs-Gold + Oracle Diagnosis

## 1. 输入与非目标

本步骤只做 Step 4 诊断：比较 planning 自动生成 specs 与 MQTT example specs 的差异，并用有限 oracle substitution 定位瓶颈阶段。本步骤没有执行 Step 5，没有写最终优化决策报告，也没有修改 `agent/planning` 或 `agent/coder` 核心 pipeline。

Gold/example specs 是 code-derived oracle，只作为诊断参照，不视为 protocol facts。oracle substitution 的含义是“如果某一类 code-derived gold 字段被替换进去，当前 failure 是否改变”，不能解释为 planning 应直接复制 example implementation detail。

输入：

| item | path | status |
|---|---|---|
| MQTT gold specs | `specs-example/mqtt_specs` | 存在 |
| CoAP gold specs | `specs-example/coap_specs` | 存在 |
| MQTT planning specs | `agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/spec_bundle` | 存在 |
| MQTT planning validation | `agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/_validation_reports/014_planning_validation_report.json` | `success` |
| CoAP planning specs | `agent/planning/out/**/coap/**/spec_bundle` | 未发现 |
| coder compile stderr | `agent/out/mqtt_broker_20260610_102238/_agent_logs/010_compile_stderr_0.txt` | 存在 |

CoAP 本轮只记录 gold specs 存在；仓库中没有可对齐的 CoAP planning `spec_bundle`，因此不做完整 planning-vs-gold diff 或 oracle。

## 2. Planning-vs-Gold 差异总结

运行命令：

```bash
python tools/specs_optimization/compare_specs.py \
  --gold specs-example/mqtt_specs \
  --planning agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/spec_bundle \
  --output experiments/specs_optimization/oracle_substitution/mqtt/planning_vs_gold \
  --match-mode name
```

输出：

- `experiments/specs_optimization/oracle_substitution/mqtt/planning_vs_gold/comparison.json`
- `experiments/specs_optimization/oracle_substitution/mqtt/planning_vs_gold/summary.md`

Comparison status 为 `degraded_input`，原因不是 gold invalid，而是 planning bundle 在 rendered header validation 中已有 3 个 header compile errors。Step 4 仍使用该 diff，因为这些 errors 正是诊断对象。

### 2.1 覆盖指标

| dimension | gold | planning | matched / note |
|---|---:|---:|---|
| modules | 6 | 6 | 4 matched；gold missing from planning: `topic`, `router`；planning extra: `timer`, `topic_router` |
| file paths | 21 path entries | 17 path entries | 1 matched；planning layout 与 gold layout 基本不同 |
| file trace ids | 11 | 9 | 0 matched；trace id 体系不同 |
| functions | 89 | 104 | planning 更多 helper/event functions，但 public functions 更少 |
| public functions | 58 | 35 | public API surface 收缩且命名不同 |
| function families | 31 | 13 | 6 matched；missing includes `mqtt_connection_*`, `mqtt_decoder_*`, `mqtt_packet_*`, `mqtt_tcp_*`, `handle_*`, `decode_*` |
| public types | 19 | 33 | 10 matched；missing includes `mqtt_message_router_t`, `mqtt_session_manager_t`, `mqtt_tcp_callbacks_t`, `mqtt_tcp_server_t`, `mqtt_topic_tree_t` |
| exact/name signature overlap | 7 functions | 7 functions | only 1 normalized signature match |

解释：

- planning 并非简单“规格太少”。它生成了更多 total functions、更多 public types、更多 call contracts，但 architecture、file layout、public API families 与 gold 差异很大。
- `topic_router` 合并了 gold 的 `topic`/`router` concerns，`timer` 是 planning extra module。这个差异会使 file/module oracle 无法安全 graft gold dependencies。
- signature mismatch 集中在 public API shape，例如 `mqtt_broker_create`、`mqtt_broker_run`、`mqtt_session_create`。这指向 `5.4b_function_signatures` 和 upstream architecture/type choices。

### 2.2 语义与依赖指标

| field group | gold | planning | interpretation |
|---|---:|---:|---|
| nonempty behavior functions | 89 | 104 | planning fields 非空，但不保证 actionability |
| source interface contracts | 89 | 104 | contract presence 高，仍需检查是否约束到正确 state/type |
| `WIRE_MAPPING` | 23 | 21 | 数量接近，但 access path coverage 弱 |
| function `ACCESS_PATHS` | 29 | 14 | wire/access binding 不闭合 |
| `RELY.FUNC` | 120 | 219 | planning call hints 很多，但与 dependency graph 脱节 |
| function `CALL_CONTRACTS` | 10 | 219 | planning 生成大量 call contracts，precision 需要怀疑 |
| module dependencies | 8 | 8 | 数量相同，但 target module 不兼容 |
| `HEADER.DEPENDENCY` | 9 | 0 | public header dependency lowering 缺失 |
| `HEADER.SYSTEM_DEPENDENCY` | 0 | 0 | planning 未 lower `ssize_t` 等 system type |
| `SOURCE.DEPENDENCY` | 16 | 8 | source dependency coverage 不足 |
| test vectors | 7 | 21 | planning 有更多 test vectors，不是本轮主要 blocker |

`007_implementation_plan.json` 进一步显示：

| internal field | total entries | nonempty entries | signal |
|---|---:|---:|---|
| `calls_allowed` | 104 | 0 | call graph 输入被清空 |
| `call_contracts` | 104 | 64 | call semantics 存在但未进入 dependency graph |
| `imports_allowed` | 9 | 0 | source/header imports 输入被清空 |
| `signature_dependencies` | 104 | 83 | 只有 signature deps 被用于 final graph |

`008_dependency_validation_report.json` 仍为 `passed`，且 `function_edge_count=0`。这说明 dependency validation 没有检查 “`call_contracts` 非空但 `calls_allowed`/function edges 为空” 的失真情况。

### 2.3 Rendered Header 与 Compile Symptoms

`compare_specs.py` 的 loader diagnostics：

| header | symptom |
|---|---|
| `src/broker_app/broker.h` | unknown `mqtt_connection_t` in callback typedefs and callback struct fields |
| `src/network/network.h` | unknown `ssize_t`; missing system include such as `<sys/types.h>` |
| `src/session/session.h` | unknown `mqtt_timer_t` in public signature |

已有 coder compile stderr 进一步显示 source-level symptoms：

- `src/network/network.c` uses missing `mqtt_network_encode_buffer_t`
- implicit declarations for `mqtt_network_cleanup_failed_io`, `mqtt_network_close_connection`, `mqtt_network_handle_message`
- helpers are called before private prototypes are visible
- generated code treats `struct mqtt_network` as if it had connection fields such as `fd`, `recv_buf`, `send_buf`, `closed`

这些 symptoms 说明问题跨越 public type closure、system type lowering、private/source data availability、function ordering/prototype closure 和 behavior actionability。

## 3. Oracle Substitution 结果

本轮只执行三个 single-factor oracle：

```bash
python tools/specs_optimization/oracle_substitute.py \
  --planning agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/spec_bundle \
  --gold specs-example/mqtt_specs \
  --output experiments/specs_optimization/oracle_substitution/mqtt \
  --strategy <strategy> \
  --validate \
  --overwrite
```

未执行 `P+GoldBehavior`、`P+GoldWire`、`P+GoldCalls`。原因是前三个 oracle 已显示 architecture/type/signature/dependency 之间存在强耦合；继续叠加 behavior/wire/calls 会破坏 single-factor 诊断。

| strategy | status | replacements | unmatched | validation errors | interpretation |
|---|---|---:|---:|---|---|
| `P+GoldDependency` | `validation_failed` | 11 | 7 | `unknown_module_dependency`: `broker_app` depends on missing `router` | gold dependency 无法直接 graft 到 planning architecture；`5.5a_file_layout` 与 `5.6_dependency_closure` 不兼容 |
| `P+GoldType` | `validation_failed` | 7 | 7 | `broker.h`、`network.h`、`session.h` 仍有 rendered header errors | type oracle 单独无法修复 stale signatures/dependencies，同时暴露 signature/type projection drift |
| `P+GoldSignature` | `validation_failed` | 20 | 223 | `broker.h`、`network.h`、`session.h` 仍有 rendered header errors | 只有 7 个 exact function name overlap；signature oracle 太稀疏，仍需要 type/dependency closure |

### 3.1 P+GoldDependency

Manifest：

- `experiments/specs_optimization/oracle_substitution/mqtt/P+GoldDependency/substitution_manifest.json`
- `experiments/specs_optimization/oracle_substitution/mqtt/P+GoldDependency/summary.md`

关键 replacements:

- `broker_app` dependency changed from `network, protocol_codec, session, timer, topic_router` to gold `network, protocol_codec, session, router`
- `main` `SOURCE.DEPENDENCY` changed from empty to `../broker/broker.h`

关键 unmatched:

- no gold module named `timer`
- no gold module named `topic_router`
- gold `network` and `protocol_codec` each have multiple files, so file-level dependency replacement refused to guess

Validation 含义:

- `unknown_module_dependency` for `router` is a useful failure. It shows planning architecture cannot accept gold module dependencies without an explicit architecture/file-layout mapping.
- 临时 rendered-header compile 还显示 `network/tcp_server.h`、`network/connection.h` 等 fatal missing includes，因为 gold include paths 不存在于 planning layout 中。这说明 dependency oracle 被 architecture divergence 阻塞，不只是边数量不足。

### 3.2 P+GoldType

Manifest：

- `experiments/specs_optimization/oracle_substitution/mqtt/P+GoldType/substitution_manifest.json`
- `experiments/specs_optimization/oracle_substitution/mqtt/P+GoldType/summary.md`

已实现替换范围：

- exact matched modules: replace TYPE artifacts only
- matched files: replace public `HEADER.DATA` `KIND=TYPE` entries only
- no changes to signatures, dependencies, behavior, wire, calls, or source data

关键结果:

- `broker.h` changed to gold public type set for `mqtt_broker_t`, but planning signatures still referenced planning-only callback types such as `mqtt_broker_app_callbacks_t`, `mqtt_broker_app_on_accept_fn`, and `mqtt_broker_app_t`.
- `network.h` still failed on `ssize_t`.
- `session.h` still failed on `mqtt_timer_t`.

解释：

- `5.3_type_data` has real public type coverage gaps, especially callback and manager/tree types.
- However, type replacement alone is not a cure because `5.4b_function_signatures` and `5.6_dependency_closure` still refer to stale or incompatible types.

### 3.3 P+GoldSignature

Manifest：

- `experiments/specs_optimization/oracle_substitution/mqtt/P+GoldSignature/substitution_manifest.json`
- `experiments/specs_optimization/oracle_substitution/mqtt/P+GoldSignature/summary.md`

已实现替换范围：

- exact function name match only
- replace `FUNCTION_SPEC.SIGNATURE`
- synchronize matched `HEADER.INTERFACE[].SIGNATURE` and `SOURCE.INTERFACE[].SIGNATURE`
- no changes to call contracts, behavior, wire, type data, or dependencies

关键结果：

- 只完成 20 个 signature/interface replacements，另有 223 个 unmatched interface/function records。
- `session.h` changed from `mqtt_timer_t* timer` toward gold `mqtt_connection_t* conn`, but the header still had no dependency that made `mqtt_connection_t` visible.
- `broker.h` and `network.h` retained their original type/system dependency failures.

解释：

- Signature oracle 确认 `5.4b_function_signatures` 是瓶颈之一，但 exact-name overlap 太小，单因素 signature replacement 无法救回整个 bundle。
- Signature 正确性依赖 type owner/header closure 和 module/file architecture；这是 closure problem，不是孤立的文本不匹配。

## 4. Symptom-to-Stage 诊断矩阵

| symptom | likely stage | validator gap | possible fix | risk |
|---|---|---|---|---|
| missing public type：planning public type coverage 缺少 `mqtt_message_router_t`、`mqtt_session_manager_t`、`mqtt_tcp_server_t`、`mqtt_topic_tree_t` | `5.3_type_data`, `5.5a_file_layout` | type validation 只检查局部形状，不检查 implementation obligations 要求的 module-family public type coverage | 增加 deterministic public type obligation coverage 与 module-family required-type checks | 如果把 gold names 当成 protocol facts，容易 overfit |
| unknown type ref：`broker.h` 引用 `mqtt_connection_t`，但没有 include/declaration | `5.3_type_data`, `5.6_dependency_closure`, `specs_compiler` | callback signatures 与 struct fields 没有 public type owner closure | 从 callback signatures、struct fields、aliases、function pointer params、public signatures 派生 header deps | 会暴露当前 fallback 掩盖的 include cycles |
| unknown type ref：`session.h` 引用 `mqtt_timer_t`；signature oracle 后又引用未闭合的 `mqtt_connection_t` | `5.4b_function_signatures`, `5.6_dependency_closure` | signature validation 不要求所有 public type refs 在 rendered header 中可解析 | 校验 public signature type refs 是否来自 same-header declarations、header deps 或 system type registry | 更严格 validation 会让许多现有 planning runs 失败 |
| header include cycle / incompatible include path risk：gold deps 包含 `network/tcp_server.h`，planning layout 只有 `src/network/network.h` | `5.5a_file_layout`, `5.6_dependency_closure` | dependency validator 不验证 include path 是否能在 planning layout 中解析 | success 前用 generated header path index 校验 quoted includes | oracle 复用前可能需要显式 architecture mapping |
| source dependency promoted to header：compiler 历史上用同一 `imports_allowed` 生成 `SOURCE.DEPENDENCY` 和 `HEADER.DEPENDENCY` | `specs_compiler`, `5.6_dependency_closure` | source call dependency 与 public header dependency 没有分离 | 拆分 `header_public_deps` 与 `source_call_deps`，并用不同 deterministic evidence lowering | 错误拆分可能隐藏 source includes 或造成 header under-include |
| missing callback type dependency：`broker.h` 的 callback typedefs 依赖 `mqtt_connection_t` | `5.3_type_data`, `specs_compiler` | callback `CALLBACK_SIGNATURE` 未参与 header dependency closure | 解析 callback signatures 中的 non-system type refs 并补充 public header deps | C type parsing 需要避免 primitive/system types false positives |
| missing struct field dependency：callback struct fields 引用 callback typedefs，并在 typedef 失败后连带失败 | `5.3_type_data`, `specs_compiler` | struct field dependency closure 没有独立检查 | 将 field type refs 纳入 deterministic public type dependency graph | 可能需要 forward declaration policy 处理 cycles |
| weak behavior actionability：behavior fields 非空，但 generated code 调用缺失 helpers 并使用错误 struct fields | `5.4c_function_behavior_contract`, `5.3_type_data` | validator 只检查 presence，不检查 behavior actions 是否绑定到已声明 state/resources/functions | 将 behavior actions 与 declared access paths、source data、callable functions 做一致性校验 | semantic validator 可能需要 structured behavior IR，而不是自由文本 |
| missing parser/serializer/handler family：function family coverage 只有 6/31，缺少 `mqtt_decoder_*`、`mqtt_packet_*`、`mqtt_connection_*`、`handle_*` | `5.4a_function_inventory`, `5.5a_file_layout` | function inventory validation 不强制 required protocol roles 的 target family coverage | 增加 codec、network、session、router、broker 的 role-driven family coverage obligations | family names 必须从 target profile 派生，不能直接复制 gold implementation names |
| wire field not bound：planning 有 21 个 `WIRE_MAPPING`，但 function access paths 只有 14 个，gold 为 29 个 | `5.4d_wire_access_binding` | wire mapping 数量可通过，但 access-path closure 不闭合 | 要求每个 wire field strategy 绑定到 valid access path 或显式 skip/reject action | 过严可能拒绝 intentionally ignored fields |
| `calls_allowed` empty while `call_contracts` nonempty | `5.4e_call_contracts`, `5.6_dependency_closure` | dependency validation 不比较 `call_contracts`、`calls_allowed`、`RELY.FUNC` 和 function edges | 当 call contracts 存在但 allowed-call graph 为空或不一致时 fail closed | 可能迫使 specs compilation 前先完成 dependency repair |
| `calls_allowed` over-broad signal：planning 有 219 个 `RELY.FUNC` 与 219 个 function call contracts，显著高于 gold 120/10 | `5.4e_call_contracts` | 缺少 call contracts precision 与 allowed call target scope 检查 | 校验 callee existence、module boundary、contract-to-signature consistency | precision checks 可能需要 protocol-specific roles |
| `imports_allowed` empty across 9 files while `signature_dependencies` nonempty | `5.6_dependency_closure` | fallback 可清空 imports，并仍生成 passed dependency report | 将 nonempty signature deps + empty imports 视为 blocking diagnostic | 会阻塞 dependency repair 质量不足的 runs |
| system type not lowered：`network.h` 出现 `ssize_t`，但缺少 `HEADER.SYSTEM_DEPENDENCY` | `specs_compiler`, `5.7_spec_readiness` | final planning compatibility 当前跳过 rendered header compile | 增加 system type registry，并在 final readiness 启用 rendered header validation | 跨 C 环境 portability 需要显式 policy |
| public type closure not closed：planning `HEADER.DEPENDENCY` 为 0，但 gold 为 9 | `specs_compiler`, `5.6_dependency_closure` | schema/loader 允许空 header deps，即使 public refs 需要外部类型 | 从 public type/signature closure 派生 `HEADER.DEPENDENCY`，不要从 source imports 复用 | 会暴露需要 architecture changes 的 include cycles |
| file layout consistency gap：planning 有 17 个 path entries，但只有 1 个 gold path match | `5.5a_file_layout` | file layout validation 只检查内部一致性，不检查 downstream coder/example comparability | 要求 role-to-file mapping explanations 与 generated header path resolvability | 不应把 example layout 强制当成 protocol fact |
| runtime entrypoint mismatch risk：planning 有 `broker_app` 和 `main`，dependency oracle 难以映射 gold `broker` concepts | `5.5b_runtime_entrypoint` | entrypoint validation 不验证 runtime module 到 protocol modules 的 dependency closure | 校验 entrypoint module dependencies 与 public startup API signature compatibility | 可能需要 target-profile-specific runtime policies |
| planning validation passes but coder loading/rendered header compile fails | `5.7_spec_readiness`, `coder_compat validator` | `014_planning_validation_report.json` 显示 coder compatibility passed，但 `compare_specs.py` 的 rendered header validation 发现 errors | 在 final planning validation 中启用 `validate_rendered_headers=True` 或 dummy translation unit compile | 更严格 readiness 会把当前 “success” runs 转为 failed runs |

## 5. Step 4 瓶颈排序

以下只是 Step 4 诊断排序，不是 Step 5 的最终优化决策。

1. **Closure and validation gap: `5.6_dependency_closure`, `specs_compiler`, `5.7_spec_readiness`, `coder_compat validator`**
   - 最强证据：planning reports success，但 rendered headers 因 missing public/system types 失败。
   - `calls_allowed` and `imports_allowed` are empty despite nonempty call contracts and signature dependencies.
   - `HEADER.DEPENDENCY` and `HEADER.SYSTEM_DEPENDENCY` are not closed.

2. **Architecture/file-layout mismatch: `5.5a_file_layout` with upstream architecture choices**
   - `topic_router` and `timer` do not map cleanly to gold `topic`/`router`.
   - `P+GoldDependency` 因 missing `router` 和 incompatible include paths 失败。
   - 这使 direct oracle grafting 按设计 fail closed。

3. **Type and signature quality: `5.3_type_data` and `5.4b_function_signatures`**
   - Public type coverage misses key manager/router/tcp callback types.
   - Signature overlap 只有 7 个 functions，且只有 1 个 normalized match。
   - `P+GoldType` and `P+GoldSignature` each fail because they depend on the other plus header deps.

4. **Semantic density/actionability: `5.4a` through `5.4e`**
   - Planning 有大量 behavior/call/test fields，因此问题不是 raw field absence。
   - 更核心的问题是 actionability 与 precision：required function families 缺失、access binding 弱、allowed-call graph 为空、behavior 没有绑定到 declared data/resources。

5. **Specs structure over-weight**
   - Step 4 没有提供 strict specs 整体过重的强证据。
   - 更强信号是 rich fields 生成不一致，且没有被 validated/lowered 成闭合的 coder-compatible bundle。

## 6. 限制与缺失输入

- CoAP planning output is missing, so Step 4 complete analysis is MQTT-only.
- Gold specs 是 implementation-derived oracle references。planning 缺少 gold names 是 coder usability/comparability 诊断，不是缺失 protocol facts 的直接证明。
- `P+GoldBehavior`, `P+GoldWire`, and `P+GoldCalls` were not run to preserve single-factor interpretation.
- The oracle strategies are intentionally conservative. They do not synthesize dependency graphs, do not map `topic_router` to `topic/router`, and do not make unsafe guesses for ambiguous files.
- Validation 只包含 deterministic schema/loader/rendered-header validation。本步骤没有对 oracle bundles 运行 full coder generation、compile repair 或 smoke tests。

## 7. 产物

| artifact | path |
|---|---|
| planning-vs-gold comparison JSON | `experiments/specs_optimization/oracle_substitution/mqtt/planning_vs_gold/comparison.json` |
| planning-vs-gold summary | `experiments/specs_optimization/oracle_substitution/mqtt/planning_vs_gold/summary.md` |
| dependency oracle manifest | `experiments/specs_optimization/oracle_substitution/mqtt/P+GoldDependency/substitution_manifest.json` |
| dependency oracle summary | `experiments/specs_optimization/oracle_substitution/mqtt/P+GoldDependency/summary.md` |
| type oracle manifest | `experiments/specs_optimization/oracle_substitution/mqtt/P+GoldType/substitution_manifest.json` |
| type oracle summary | `experiments/specs_optimization/oracle_substitution/mqtt/P+GoldType/summary.md` |
| signature oracle manifest | `experiments/specs_optimization/oracle_substitution/mqtt/P+GoldSignature/substitution_manifest.json` |
| signature oracle summary | `experiments/specs_optimization/oracle_substitution/mqtt/P+GoldSignature/summary.md` |
| planning validation report | `agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/_validation_reports/014_planning_validation_report.json` |
| dependency validation report | `agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/_validation_reports/008_dependency_validation_report.json` |
| implementation plan | `agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/_step_logs/007_implementation_plan.json` |
| coder compile stderr | `agent/out/mqtt_broker_20260610_102238/_agent_logs/010_compile_stderr_0.txt` |

## 8. Step 4 完成判断

Step 4 is complete for MQTT and partially limited for CoAP due missing planning output. The main diagnosis is:

- current failure is primarily a closure/validator problem plus architecture/file-layout mismatch;
- planning semantic density is not simply “too sparse”, but semantic/action fields are not precise enough or not connected to deterministic closure;
- no evidence in this step supports executing broad specs schema compression before fixing dependency/type/signature/readiness closure;
- oracle substitution should remain diagnostic only and must not be used as protocol fact evidence.
