# Step 2：Diagnostic Toolkit Construction

## 1. 摘要

本步骤新增最小诊断工具集，用于后续 Step 3/Step 4 的 specs degradation、planning-vs-gold diff 和 oracle substitution。  
本步骤没有执行 MQTT/CoAP 全量实验，没有调用 coder 生成代码，也没有修改 `agent/planning` 或 `agent/coder` 主流程。

新增工具统一递归扫描 `*_spec.json`，按 `KIND` 区分 `PROTOCOL_MODULE_SPEC`、`FILE_SPEC`、`FUNCTION_SPEC`，因此兼容 `specs-example/*_specs` 与 planning `spec_bundle/` 的不同目录深度。

## 2. 工具入口

| tool | role | output |
|---|---|---|
| `tools/specs_optimization/degrade_specs.py` | 从 gold/example specs 生成降级 specs | `<output>/<profile>/specs/`, `manifest.json`, `summary.md` |
| `tools/specs_optimization/compare_specs.py` | 比较 gold specs 与 planning specs 字段覆盖差异 | `comparison.json`, `summary.md` |
| `tools/specs_optimization/oracle_substitute.py` | 生成 oracle substitution skeleton；实现 `P+GoldDependency` | `<output>/<strategy>/specs/`, `substitution_manifest.json`, `summary.md` |
| `tools/specs_optimization/README.md` | 工具说明与示例命令 | markdown |

示例命令：

```bash
python tools/specs_optimization/degrade_specs.py \
  --input specs-example/mqtt_specs \
  --output /tmp/specforge_diag_mqtt \
  --profiles full,no_calls \
  --validate

python tools/specs_optimization/compare_specs.py \
  --gold specs-example/mqtt_specs \
  --planning agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/spec_bundle \
  --output /tmp/specforge_diag_compare

python tools/specs_optimization/oracle_substitute.py \
  --planning agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/spec_bundle \
  --gold specs-example/mqtt_specs \
  --output /tmp/specforge_diag_oracle \
  --strategy P+GoldDependency \
  --validate
```

## 3. Degradation Profiles

所有 profile 都先完整复制输入 specs，再修改输出副本。工具不会向 strict `*_spec.json` 写入 metadata，因为当前 schema 使用 `additionalProperties: false`。所有删除、清空、弱化和 skipped reason 都写入 manifest。

| profile | strategy |
|---|---|
| `full` | baseline copy，不修改 spec |
| `no_behavior_detail` | 弱化 `FUNCTION_SPEC.LOGIC/EVENT` 文本与 `SOURCE.INTERFACE[].CONTRACT` pre/postcondition，保留 schema 必需形状 |
| `no_wire_binding` | 删除 function-level `WIRE_MAPPING`；保留无法安全区分用途的 `ACCESS_PATHS` 并记录 skipped reason |
| `no_calls` | 清空 `FUNCTION_SPEC.RELY.FUNC` 与 function/file `CALL_CONTRACTS`；不改 module/header/source dependency |
| `min_interface` | 保留加载、header/source 生成必需字段；删除或弱化 `DOC_REF`、`PUBLIC_SYMBOLS`、`ACCESS_PATHS`、`CALL_CONTRACTS`、`FORBIDDEN_SYMBOLS`、`TEST_VECTORS`、behavior detail 和 `RELY` |
| `no_test_vectors` | 删除 module/file/function `TEST_VECTORS` |
| `sidecar_only_traceability` | 将 module/file `DOC_REF` 置空，并把原始 doc refs 写入 `traceability_sidecar.json` |

`sidecar_only_traceability` 遇到 strict spec 内非 schema `TRACEABILITY` 字段时 fail-closed，不静默删除。

## 4. Compare Metrics

`compare_specs.py` 输出 `comparison.json`，至少包含：

- `module_coverage`：module name 集合、交集、missing、extra。
- `file_coverage`：按 `TRACE_ID` 与 normalized header/source path 两套统计。
- `function_count`：gold/planning function 总数、public/private 数量。
- `function_family_coverage`：按函数名前缀 family 统计，例如 `mqtt_encode_*`。
- `public_type_coverage`：来自 module artifacts `TYPE` 与 public `HEADER.DATA`。
- `signature_coverage`：按 function name 匹配后的 exact/normalized signature match、missing、extra、mismatch。
- `behavior_field_presence`：`LOGIC/EVENT` 与 `SOURCE.INTERFACE.CONTRACT` 的存在和非空数量。
- `wire_access_field_presence`：`WIRE_MAPPING`、function/file `ACCESS_PATHS` 数量。
- `calls_dependency_presence`：`RELY.FUNC`、`CALL_CONTRACTS`、module/header/source dependency 数量。
- `test_vector_presence`：module/file/function `TEST_VECTORS` 数量。
- `diagnostics`：schema 与 `load_spec_bundle_from_root(validate_rendered_headers=True)` 诊断。

如果输入 validation 有 error，工具仍输出 diff 文件，但将 top-level `status` 标记为 `degraded_input`。

## 5. Oracle Substitution

已实现：

- `P+GoldDependency`
  - 替换 matched module 的 `MODULES[].DEPENDENCIES`。
  - 替换 matched file 的 `HEADER.DEPENDENCY`、`HEADER.SYSTEM_DEPENDENCY`、`SOURCE.DEPENDENCY`。
  - file 匹配优先级：normalized source/header path exact，file basename，module name single-file fallback。
  - unmatched file/module 不猜测、不清空、不补造 dependency。

Skeleton：

- `P+GoldType`
  - 预留字段：`HEADER.DATA` public `TYPE` 与相关 `TYPE_SPEC`。
  - Step 2 默认 skipped，因为 type oracle 需要 public type closure 与 compatibility 判断。
- `P+GoldSignature`
  - 预留字段：`HEADER.INTERFACE[].SIGNATURE`、`SOURCE.INTERFACE[].SIGNATURE`、`FUNCTION_SPEC.SIGNATURE`。
  - Step 2 默认 skipped，避免只替换 raw signature 后留下 structured params 漂移。

oracle manifest 明确写入：

```json
{
  "source_kind": "gold_spec_code_derived_oracle",
  "not_protocol_fact": true
}
```

因此 oracle substitution 不能被解释为 protocol facts，也不能用于声称 code-derived detail 来自 technical documents。

## 6. Fail-Closed 行为

三个工具均对以下情况 fail-closed：

- 输入根目录不存在；
- JSON 读取失败或 spec 不是 object；
- `KIND` 缺失或不是三类 strict spec；
- 没有或存在多个 `PROTOCOL_MODULE_SPEC`；
- 输出目录已存在且未传 `--overwrite`；
- `sidecar_only_traceability` 遇到非 schema traceability 字段。

`oracle_substitute.py --validate` 在输出 bundle validation failed 时返回 non-zero，并将错误写入 manifest，避免后续实验误用失败 oracle bundle。

## 7. 轻量验证结果

已运行：

```text
python -m compileall tools/specs_optimization
python tools/specs_optimization/degrade_specs.py --input specs-example/mqtt_specs --output /tmp/specforge_diag_mqtt --profiles full,no_calls --validate
python tools/specs_optimization/degrade_specs.py --input specs-example/mqtt_specs --output /tmp/specforge_diag_all_profiles --validate
python tools/specs_optimization/compare_specs.py --gold specs-example/mqtt_specs --planning agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/spec_bundle --output /tmp/specforge_diag_compare
python tools/specs_optimization/oracle_substitute.py --planning agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/spec_bundle --gold specs-example/mqtt_specs --output /tmp/specforge_diag_oracle --strategy P+GoldDependency --validate
python tools/specs_optimization/oracle_substitute.py --planning agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/spec_bundle --gold specs-example/mqtt_specs --output /tmp/specforge_diag_oracle_type --strategy P+GoldType
```

结果：

- 三个 CLI 均支持 `--help`。
- MQTT example specs 的全部 7 个 degradation profiles 均生成成功，并通过 schema + loader/header validation。
- `compare_specs.py` 对 MQTT gold vs planning sample 输出 `status=degraded_input`；该状态来自 planning rendered header diagnostics，不阻塞 diff 报告生成。
- `P+GoldDependency --validate` 输出 `validation_failed`，schema error 数量为 0，loader error 为 `unknown_module_dependency`：`broker_app` 引用了当前 planning bundle 中不存在的 gold dependency `router`。这说明工具没有通过清空或伪造 dependency graph 制造成功。
- `P+GoldType` skeleton 正常输出 `status=skipped` 与 skipped reason。

以上验证只证明工具可运行和接口清晰，不构成 Step 3/Step 4 的实验结论。

## 8. 已知限制

- diff 的 semantic matching 仍是轻量级：主要使用 names、paths、normalized signatures 和 function family，不做 deep semantic equivalence。
- `P+GoldDependency` 不重写 architecture/module set；gold dependency 指向 planning 中不存在的 module 时会暴露 validation failure。
- `P+GoldType` 与 `P+GoldSignature` 仅提供 skeleton，后续需要 closure-aware 替换策略。
- 工具不会调用 coder generation、compile 或 smoke test；后续 Step 3/4 可用这些输出作为实验输入。
