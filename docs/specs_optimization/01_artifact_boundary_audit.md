# Step 1：Artifact Boundary Audit

## 1. 审计范围与结论摘要

本报告对当前 coder-facing strict specs、planning compiler、coder loader、header renderer、source generation prompt、planning validators 和已有 planning 输出做静态审计。审计采用：

```text
schema
  -> planning producer/lowering
  -> coder loader/model
  -> header renderer/source prompt/repair prompt
  -> coder/planning validator
  -> existing artifact and failure evidence
```

本步骤没有运行 LLM、coder generation、大规模实验、compile 或 smoke test，也没有修改核心 pipeline。

核心结论：

1. 当前 strict coder specs 是 `spec_bundle/` 下的 `PROTOCOL_MODULE_SPEC`、`FILE_SPEC` 和 `FUNCTION_SPEC`。coder loader 通过 `KIND` 自动发现并加载它们；`coder_manifest.json` 不是 loader 输入。
2. 字段不能只按“是否进入 dataclass”划分。`PUBLIC_SYMBOLS`、`ACCESS_PATHS`、`CALL_CONTRACTS`、`FORBIDDEN_SYMBOLS`、`WIRE_MAPPING`、`TEST_VECTORS` 等字段主要通过 `raw` 被 prompt 或 validator 二次消费。
3. header/dependency 失败直接关联的 strict 字段是 `MODULES.DEPENDENCIES`、`HEADER.DEPENDENCY`、`HEADER.SYSTEM_DEPENDENCY`、`HEADER.DATA.TYPE_SPEC`、`HEADER.INTERFACE.SIGNATURE`、`SOURCE.DEPENDENCY`、`CALL_CONTRACTS` 和 `RELY.FUNC`。问题主要在 lowering 和 validation 边界，而不是这些字段本身“太多”。
4. 当前 compiler 从同一 `imports_allowed` 同时生成 `SOURCE.DEPENDENCY` 和 `HEADER.DEPENDENCY`，没有区分 source call dependency 与 public header/type dependency。它也没有生成 `HEADER.SYSTEM_DEPENDENCY`。
5. 当前 dependency fallback 会清空全部 `calls_allowed` 和 `imports_allowed`，之后重新派生一个缺边 dependency graph。已有 planning 样例中 `call_contracts` 非空但 `calls_allowed` 全空，dependency report 仍为 passed。
6. planning final coder compatibility validation 调用 loader 时使用 `validate_rendered_headers=False`。`coder_semantics` 只检查 renderer 是否输出公共名字，不编译 rendered headers，因此可出现 planning success、coder header compile failure。
7. `planning_decisions.json` 和 `planning_ir_refs.json` 是 **validator-sidecars**：不进入 coder generation，但被 `coder_semantics` 消费。`planning_traceability.json` 当前是纯 sidecar。`_step_logs/` 和 `_validation_reports/` 属于 planning internal/validator-only artifacts。
8. `DOC_REF`、`PROTOCOL.SCOPE`、部分嵌套 `ROLE`、`FUNCTION_SPEC.PUBLIC_SYMBOLS` 是优先 sidecar 候选；但 `SOURCE.DATA`、structured signature、`SOURCE.INTERFACE.CONTRACT` 虽然当前消费不足，更可能需要强化 consumer，不能仅凭“当前未读”移出 strict specs。

注意：`specs-example/mqtt_specs/` 和 `specs-example/coap_specs/` 用于核对 schema 与 coder usability。它们包含 code-derived implementation details，不能作为 protocol facts。

### 1.1 Evidence Path 索引

字段矩阵为控制宽度使用以下简称；所有简称均指向具体源码或 artifact：

| short name | concrete path |
|---|---|
| module/file/function schema | `specs-example/specs_schema/module_spec_schema.json`、`file_spec_schema.json`、`function_spec_schema.json` |
| `specs_compiler.py` | `agent/planning/stages/specs_compiler.py` |
| `coder_spec_lowering.py` | `agent/planning/stages/coder_spec_lowering.py` |
| `implementation_plan_merger.py` | `agent/planning/stages/implementation_plan_merger.py` |
| `dependencies.py` | `agent/planning/stages/dependencies.py` |
| `coder/specs.py` | `agent/coder/specs.py` |
| `coder/models.py` | `agent/coder/models.py` |
| `coder/generation.py` | `agent/coder/generation.py` |
| `header_recipes.py` | `agent/coder/header_recipes.py` |
| `coder/prompts.py` | `agent/coder/prompts.py` |
| `coder/verifier.py` | `agent/coder/verifier.py` |
| `coder_semantics.py` | `agent/planning/validators/coder_semantics.py` |
| `coder_compat.py` | `agent/planning/validators/coder_compat.py` |
| planning sample | `agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/` |
| planning network spec | planning sample 下 `spec_bundle/src/network/network/network_spec.json` |
| existing compile log | `agent/out/mqtt_broker_20260610_102238/_agent_logs/010_compile_stderr_0.txt` |

## 2. Artifact 分类与当前边界

| artifact class | current artifacts | current producer | current consumer | boundary judgment |
|---|---|---|---|---|
| coder-facing strict specs | `spec_bundle/**/*_spec.json` 中三种 `KIND` | `agent/planning/stages/specs_compiler.py::compile_spec_bundle`；example specs 由人工/现有代码派生 | coder schema validator、`agent.coder.specs.load_spec_bundle_from_root`、header renderer、source/repair prompt、coder semantic validator | 必须保持 coder compatibility；只能经实验决定删减 |
| coder manifest | `coder_manifest.json` | `compile_spec_bundle` | planning orchestrator/外部调用方 | orchestration artifact，不由 coder loader 读取 |
| validator-sidecar | `planning_decisions.json`、`planning_ir_refs.json` | `coder_spec_lowering.py::sidecar_payload` | `planning/validators/coder_semantics.py::_planning_intent` | 不应进入 generation prompt；保留 planning intent 和 unresolved lowering validation |
| pure sidecar | `planning_traceability.json` | `sidecar_payload` | 当前无核心 consumer | 适合保存 evidence/traceability，不应膨胀 strict specs |
| planning internal IR | `_step_logs/003_planning_ir.json`、`007_implementation_plan.json`、candidate/patch artifacts、`dependency_graph` | planning stages/mergers | 后续 planning stages、compiler、planning validators | 不应直接暴露给 coder；应由 deterministic lowering 投影为 strict specs |
| validator-only artifact | `_validation_reports/*.json`、dependency validation report、repair statistics | planning validators/orchestrator | 人工诊断、resume/verification | 不属于 coder-facing specs |
| mixed-boundary strict fields | `DOC_REF`、`PUBLIC_SYMBOLS`、`ACCESS_PATHS`、`CALL_CONTRACTS`、`FORBIDDEN_SYMBOLS`、`TEST_VECTORS` 等 | compiler/example specs | 一部分经 `raw` 进入 prompt/validation，一部分仅 schema-required | 必须逐字段判断，不能按“loader dataclass 未建模”直接移除 |

边界证据：

- coder 仅扫描 `*_spec.json` 并按 `KIND` 加载：`agent/coder/specs.py:473-555`。
- compiler 分别写 strict specs、三个 sidecars 和 manifest：`agent/planning/stages/specs_compiler.py:574-783`。
- `coder_semantics` 读取 `planning_decisions.json` 与 `planning_ir_refs.json`，不读取 `planning_traceability.json`：`agent/planning/validators/coder_semantics.py:41-72`。
- 完整样例：`agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/`。

## 3. 当前 Strict Specs 字段清单

以下字段清单以 `specs-example/specs_schema/` 为准。`required` 表示 schema required，不等同于当前 generation required。

### 3.1 `PROTOCOL_MODULE_SPEC`

| field/category | required? | nested fields |
|---|---|---|
| `KIND` | yes | 固定为 `PROTOCOL_MODULE_SPEC` |
| `PROTOCOL` | yes | required: `NAME`, `SPEC_VERSION`, `ROLES`; optional: `DEFAULT_PORT`, `SCOPE` |
| `MODULES[]` | yes | `NAME`, `ROLE`, `DEPENDENCIES`, `ARTIFACTS`, `FILES`, `DOC_REF` |
| `MODULES[].ARTIFACTS[]` | yes within module entry | `NAME`, `KIND`, `ROLE`; `KIND` 为 `TYPE/FUNC/VAR/CONST/MACRO` |
| `GENERATION_ORDER[]` | yes | module name list |
| `CONSISTENCY_RULES[]` | yes | `ID`, `RULE`, `DOC_REF` |
| `PUBLIC_SYMBOLS[]` | optional | `NAME`, `KIND`, optional `ROLE` |
| `FORBIDDEN_SYMBOLS[]` | optional | `NAME`, `REASON`, optional `KIND` |
| `TEST_VECTORS[]` | optional | `NAME`, `INPUT`, `EXPECT`, optional `LEVEL`, `TRACE_REFS` |

Schema evidence: `specs-example/specs_schema/module_spec_schema.json`。

### 3.2 `FILE_SPEC`

| field/category | required? | nested fields |
|---|---|---|
| `KIND` | yes | 固定为 `FILE_SPEC` |
| `FILE` | yes | `TRACE_ID`, `LANG`, `ROLE`, `DOC_REF` |
| `HEADER` | optional | required when present: `PATH`, `DEPENDENCY`, `DATA`, `INTERFACE`; optional `SYSTEM_DEPENDENCY` |
| `HEADER.DATA[]` / `SOURCE.DATA[]` | block-required arrays | `NAME`, `KIND`, `VISIBILITY`, `ROLE`; optional `VALUE`, `TYPE_SPEC` |
| `DATA[].TYPE_SPEC` | optional | required `TYPE_KIND`; optional `FIELDS`, `ENUM_VALUES`, `VARIANTS`, `ALIAS_OF`, `CALLBACK_SIGNATURE` |
| `TYPE_SPEC.FIELDS[]` / `VARIANTS[]` | optional | `NAME`, `TYPE`, `ROLE`; optional `ARRAY_LEN`, nested `TYPE_SPEC` |
| `TYPE_SPEC.ENUM_VALUES[]` | optional | `NAME`, `ROLE`; optional `VALUE` |
| `HEADER.INTERFACE[]` | block-required array | `SIGNATURE`, `NAME`, `KIND`, `FUNCTION_TYPE`, `ROLE`, `VISIBILITY` |
| `SOURCE` | yes | `PATH`, `DEPENDENCY`, `DATA`, `INTERFACE` |
| `SOURCE.INTERFACE[]` | yes as array | `TRACE_ID`, `SIGNATURE`, `NAME`, `KIND`, `ROLE`, `CONTRACT`, `VISIBILITY`; optional `FUNCTION_TYPE` |
| `SOURCE.INTERFACE[].CONTRACT` | required per source interface | `PRECONDITION`, `POSTCONDITION`, `IDEMPOTENT`, `THREAD_SAFETY` |
| `PUBLIC_SYMBOLS[]` | optional | `NAME`, `KIND`; optional `TYPE`, `SIGNATURE`, `ACCESS_PATHS`, `ROLE` |
| `ACCESS_PATHS[]` | optional | `PATH`, `TYPE`; optional `ROLE` |
| `CALL_CONTRACTS[]` | optional | `NAME`, `SIGNATURE`; optional `PARAMS`, `RETURN`, `OWNERSHIP`, `FAILURE` |
| `FORBIDDEN_SYMBOLS[]` | optional | `NAME`, `REASON`, optional `KIND` |
| `TEST_VECTORS[]` | optional | `NAME`, `INPUT`, `EXPECT`, optional `LEVEL`, `TRACE_REFS` |

Schema evidence: `specs-example/specs_schema/file_spec_schema.json`。

### 3.3 `FUNCTION_SPEC`

| field/category | required? | nested fields |
|---|---|---|
| `KIND` | yes | 固定为 `FUNCTION_SPEC` |
| `TRACE_ID` | yes | function trace id；其 parent 必须对应 file trace id |
| `FUNCTION_TYPE` | yes | `ALGORITHM/EVENT/ENTRYPOINT` |
| `ROLE` | yes | function purpose/role |
| `SIGNATURE` | yes | `RAW`, `NAME`, `RETURN`, `PARAMS` |
| `SIGNATURE.PARAMS[]` | yes as array | `TYPE`, `NAME`, `NULLABLE`, `OWNERSHIP` |
| `RELY` | yes | arrays `STRUCT`, `FUNC`, `VAR`；元素包含 `NAME`、`ROLE`，`FUNC` 另含 `KIND` |
| `LOGIC` | conditional | `ALGORITHM` required；`INPUT`, `ACTION`, `OUTPUT`, `INVARIANTS_USED` |
| `EVENT` | conditional | `EVENT` required；`TRIGGER`, `PRECONDITION`, `INPUT`, `ACTION`, `STATE_CHANGE`, `RESPONSE`, `EVENT_TYPE` |
| `PUBLIC_SYMBOLS[]` | optional | `NAME`, `KIND`; optional `TYPE`, `SIGNATURE`, `ROLE` |
| `ACCESS_PATHS[]` | optional | `PATH`, `TYPE`; optional `ROLE` |
| `WIRE_MAPPING[]` | optional | `PACKET`, `WIRE_FIELD`, `STRATEGY`; optional `TARGET`, `SOURCE`, `RULE` |
| `CALL_CONTRACTS[]` | optional | `NAME`, `SIGNATURE`; optional `PARAMS`, `RETURN`, `OWNERSHIP`, `FAILURE` |
| `FORBIDDEN_SYMBOLS[]` | optional | `NAME`, `REASON`, optional `KIND` |
| `TEST_VECTORS[]` | optional | `NAME`, `INPUT`, `EXPECT`, optional `LEVEL`, `TRACE_REFS` |

Schema evidence: `specs-example/specs_schema/function_spec_schema.json`。

## 4. 字段级 Producer / Consumer 矩阵

说明：

- `coder required?` 中的 `loader` 指 loader 建模、关联或 validation；`header` 指 deterministic header renderer；`source` / `repair` 指 prompt。
- `validator required?` 区分 schema、coder loader/semantic、planning readiness。
- `can move to sidecar?` 是静态审计候选，不代表可以直接删除；标记“需实验”表示 Step 2/3 必须验证。

| field/category | current producer | current consumer | coder required? | validator required? | can move to sidecar? | risk if removed | evidence path |
|---|---|---|---|---|---|---|---|
| `*.KIND` | compiler 固定写入；example specs | spec discovery、schema validator | loader: yes | schema: yes | no | 无法发现/加载 spec | `specs_compiler.py:303-310,656-674,719-735`; `coder/specs.py:473-532` |
| `PROTOCOL.NAME` | `lower_protocol_meta_for_coder` | bundle slug、binary name、verifier protocol selection | loader/generation/verifier: yes | schema: yes | no | 输出路径、binary、behavior verifier 选择错误 | `coder_spec_lowering.py:334-405`; `coder/generation.py:65-73`; `coder/verifier.py:24-31,203-216` |
| `PROTOCOL.SPEC_VERSION` | protocol facts/metadata lowering | loader model only | loader: parsed; generation: no | schema: yes | 需实验；更适合 manifest/traceability，但可能影响 protocol scope | 当前 coder 不受影响，研究 traceability 受损 | `coder/specs.py:143-150`; module schema |
| `PROTOCOL.ROLES` | target/profile lowering | binary suffix selection | generation: yes | schema: yes | no | server/broker/app binary naming与运行入口语义错误 | `coder/generation.py:69-73`; `coder/verifier.py:28-31` |
| `PROTOCOL.DEFAULT_PORT` | metadata/facts lowering | deterministic `main.c` renderer | generation: yes | schema: shape | no | 默认端口退回硬编码 `1884`，跨协议错误 | `coder/generation.py:195-223` |
| `PROTOCOL.SCOPE` | metadata/facts lowering、example specs | 当前 loader 未建模、prompt 未消费 | no | schema only | yes | 当前 coder 无直接风险；scope 约束可能丢失，宜留 sidecar/manifest | `coder_spec_lowering.py:390-405`; `coder/specs.py:143-165` |
| `MODULES[].NAME` | implementation plan modules | module lookup、generation order、source prompt | loader/source: yes | schema/coder semantic: yes | no | 无法映射文件到 module | `coder/specs.py:152-165`; `coder/generation.py:525-529`; `coder/prompts.py:123-126` |
| `MODULES[].ROLE` | planning module role | source/repair prompt | source/repair: yes | schema | no/需实验 | 丢失模块责任边界，增加职责越界风险 | `coder/prompts.py:123-126,188-190` |
| `MODULES[].DEPENDENCIES` | module dependencies + derived module graph | loader order validation、source prompt、topological generation | loader/source: yes | coder loader semantic: yes | no | generation order错误、依赖 header 不完整的上游信号丢失 | `specs_compiler.py:693-717`; `coder/specs.py:277-291`; `coder/prompts.py:123-126` |
| `MODULES[].ARTIFACTS` | planned artifacts + public header projection | coder semantic validation | generation: no | coder semantic: yes | validator-sidecar 候选，需 compatibility 实验 | 移除会降低 public API completeness gate | `specs_compiler.py:699-715`; `coder_semantics.py:90-163` |
| `MODULES[].FILES` | file layout lowering | header/source generation、Makefile、structure verifier、module-file mapping | generation/verifier: yes | loader/coder semantic: yes | no | 文件不生成、Makefile 缺 source、module 无法映射 | `coder/generation.py:252-269,547-570`; `coder/verifier.py:168-175` |
| `MODULES[].DOC_REF` | `lower_doc_ref` | loader model 保存但无 downstream consumer | loader: parsed only | schema only | yes | 当前 coder 无直接风险；traceability 应由 sidecar 保留 | `coder/specs.py:152-161`; `coder_spec_lowering.py:531-538` |
| `GENERATION_ORDER` | compiler topological order | loader module ordering、generation iteration | loader/generation: yes | schema/loader: yes | no | dependency 之前生成 consumer，或 fallback 到不可靠 module order | `specs_compiler.py:599-600,723-725`; `coder/specs.py:168-193` |
| `CONSISTENCY_RULES[].RULE` | compiler defaults + implementation plan | source/repair prompt，以整个 object 传入 | source/repair: yes | schema | no/需实验 | 跨文件约束丢失 | `specs_compiler.py:726-734`; `coder/prompts.py:146-147,200-201` |
| `CONSISTENCY_RULES[].ID/DOC_REF` | compiler/traceability lowering | 随整个 consistency object 进入 prompt；无专门逻辑 | source/repair: incidental | schema | `DOC_REF` 可移；`ID` 需实验 | 移除 `DOC_REF` 对 coder 风险低；移除 ID 降低可诊断性 | module schema; `coder/prompts.py:146-147` |
| module `PUBLIC_SYMBOLS` | compiler 聚合 public artifacts | coder semantic validator | generation: no | coder semantic: yes | validator-sidecar 候选，需实验 | public API coverage gate 降级 | `specs_compiler.py:752-759`; `coder_semantics.py:228-249` |
| module `FORBIDDEN_SYMBOLS` | planning constraints + derived forbidden fields | loader machine constraints、source/repair prompt | source/repair: yes | loader machine validation | no | coder 更易 hallucinate 禁止符号 | `specs_compiler.py:736-738`; `coder/prompts.py:34-40,58-64` |
| module `TEST_VECTORS` | planning `test_plan` lowering | source/repair prompt | source/repair: yes; verifier: no | schema/planning readiness | 需实验，不应因 verifier 未读直接移除 | 行为约束丢失；当前 verifier 不会补偿 | `specs_compiler.py:739-751`; `coder/prompts.py:36,67-72,159`; `coder/verifier.py:203-224` |
| `FILE.TRACE_ID` | compiler `_file_trace_id` | file index、function parent linkage、prompt | loader/source/repair: yes | schema/loader: yes | no | function specs orphan、文件关联失败 | `coder/specs.py:91-121,360-364`; `coder/prompts.py:128-131` |
| `FILE.LANG` | compiler 固定 `C` | loader model only | parsed, no active generation branch | schema: yes | 需实验；更适合 manifest if coder 永远固定 C | 多语言扩展/兼容信息丢失 | `specs_compiler.py:658-663`; `coder/specs.py:106-121` |
| `FILE.ROLE` | file responsibility lowering | source/repair prompt | source/repair: yes | schema | no/需实验 | 文件责任边界丢失 | `coder/prompts.py:128-132,192-195` |
| `FILE.DOC_REF` | `lower_doc_ref` | 当前 loader 不单独解析；仅保留在 file `raw`，无 prompt consumer | no | schema only | yes | 当前 coder 无直接风险；traceability 应由 sidecar 保留 | `specs_compiler.py:658-663`; `coder/specs.py:91-121`; `coder/prompts.py:31-55` |
| `HEADER.PATH` | file layout lowering | header output、primary include、indexes、repair | loader/header/source/repair: yes | schema/loader | no | header 无法生成或 source 无 primary include | `coder/specs.py:91-121`; `coder/generation.py:122-163,552-580`; `coder/prompts.py:152-155` |
| `HEADER.DEPENDENCY` | 当前由 `imports_allowed` lowering | rendered quoted includes；repair dependency fallback | header/repair: yes | schema/rendered-header compile when enabled | no | public types/signatures 无 provider declaration；错误 include cycle | `specs_compiler.py:612-615,676-680`; `coder/generation.py:127-140,727` |
| `HEADER.SYSTEM_DEPENDENCY` | schema/example specs；当前 compiler 不生成 | rendered system includes、loader 特定 system type check | header: yes | loader/rendered-header compile | no | `ssize_t`、socket types 等导致 header compile failure | `coder/specs.py:102-115,321-329`; `coder/generation.py:132-140`; existing compile log |
| `HEADER.DATA[].NAME/KIND/VISIBILITY/TYPE_SPEC` | canonical/type inventory lowering | deterministic header declarations、public type validation、source prompt via canonical header | header/source: yes | loader/coder semantic: yes | no | 公共 struct/enum/callback/alias 缺失或不一致 | `header_recipes.py:50-145`; `coder_semantics.py:150-163,191-226` |
| `HEADER.DATA[].ROLE` | planning type purpose | opaque fallback heuristic；artifact role lowering | header fallback/validator: partial | schema/coder semantic indirect | 需实验；结构化 `TYPE_SPEC` 完整时可考虑 sidecar | opaque 判定或 API 语义提示丢失 | `header_recipes.py:121-129`; `coder/specs.py:334-339` |
| `TYPE_SPEC.FIELDS/VARIANTS[].NAME/TYPE/ARRAY_LEN/TYPE_SPEC` | canonical type lowering/example specs | deterministic member rendering、public path/type checks | header: yes | loader/coder semantic: yes | no | invalid/incomplete public ABI、header dependency漏检 | `header_recipes.py:17-47,81-99`; `coder/specs.py:200-266,321-329` |
| nested `ROLE` in fields/enum values | canonical validation notes/example specs | renderer 不消费；schema only | no | schema only | yes/需实验 | 当前 coder 直接风险低，可能丢失语义说明 | file schema; `header_recipes.py:17-99` |
| `HEADER.INTERFACE.SIGNATURE/NAME` | public function lowering | canonical header signature fallback、rendering、validation | header/source/verifier: yes | loader/coder semantic: yes | no | public API 缺失/漂移 | `coder/specs.py:61-73,350-357,572-579`; `coder/generation.py:122-153`; `coder/verifier.py:177-187` |
| `HEADER.INTERFACE.FUNCTION_TYPE/ROLE` | function lowering | loader 保存；当前 renderer 不消费 | loader only | schema | 需实验；`ROLE` 可考虑 sidecar | 当前 header 无直接风险；失去 API purpose 信息 | `coder/specs.py:61-73`; `header_recipes.py` |
| `HEADER.INTERFACE.VISIBILITY` | function lowering | coder semantic public API checks；renderer 当前不按 visibility 过滤 | generation: indirect | coder semantic: yes | no | public/private API gate 失效 | `coder_semantics.py:107-189`; `coder/generation.py:124-153` |
| `SOURCE.PATH` | file layout lowering | source output/index、prompt、repair | loader/source/repair: yes | schema/loader | no | source 不生成或无法 repair | `coder/specs.py:91-121`; `coder/generation.py:562-610`; `coder/prompts.py:121-132` |
| `SOURCE.DEPENDENCY` | self header + 同一 `imports_allowed` lowering | dependency headers supplied to source/repair prompt | source/repair: yes | schema | no | source prompt看不到可调用 API；缺失 include/call declaration | `specs_compiler.py:664-670`; `coder/generation.py:582-594,727`; `coder/prompts.py:149-160` |
| `SOURCE.DATA[].NAME` | type inventory/lowering | source prompt 仅列 private data item names | source: partial | schema | no；应强化 consumer | 私有类型/数据未定义，已有 `mqtt_network_encode_buffer_t` failure | `coder/prompts.py:132`; planning network spec；existing compile log |
| `SOURCE.DATA[].TYPE_SPEC/ROLE/VISIBILITY` | type inventory/lowering | loader 保存为 dict，但 prompt 不传细节，renderer 不处理 | currently under-consumed | schema only | no；更可能应进入 source prompt | private declaration shape/ordering 丢失，source compile failure | `coder/specs.py:115-121`; `coder/prompts.py:132`; existing compile log |
| `SOURCE.INTERFACE.TRACE_ID` | function/file lowering | function spec linkage、canonical signature lookup、prompt function selection | loader/source/repair: yes | loader: yes | no | function spec 不进入 prompt或变 orphan | `coder/specs.py:76-88,340-348,582-588`; `coder/prompts.py:181-185` |
| `SOURCE.INTERFACE.SIGNATURE/NAME/ROLE` | function lowering | canonical fallback、private interface summary、validation | source/repair: yes | loader/coder semantic | no | helper/public function遗漏或签名漂移 | `coder/prompts.py:75-110`; `coder/specs.py:340-357` |
| `SOURCE.INTERFACE.CONTRACT` | behavior contract lowering | 当前 coder loader/model/prompt 不消费 | no | schema only | 需实验；更可能应强化 consumer | pre/postcondition、ownership/thread-safety 信息当前已被浪费 | `specs_compiler.py:70-81`; `coder/specs.py:76-88`; `coder/prompts.py` |
| `SOURCE.INTERFACE.VISIBILITY` | function lowering | coder semantic public/private API checks | generation: indirect | coder semantic: yes | no | private/public 暴露错误无法拦截 | `coder_semantics.py:127-149` |
| file `PUBLIC_SYMBOLS` | compiler 从 public header 投影 | loader public symbol/path set、source prompt、coder semantic | source: yes | loader/coder semantic: yes | no/需实验 | allowed public API 边界与 semantic gate 降级 | `coder/specs.py:239-266`; `coder/prompts.py:37`; `coder_semantics.py:165-189` |
| file `ACCESS_PATHS` | planning access path table lowering | loader wire target validation、source/repair prompt | source/repair: yes | loader/planning readiness | no | coder 发明字段或 wire target 无法验证 | `coder/specs.py:239-266,395-405`; `coder/prompts.py:38,48,157` |
| file `CALL_CONTRACTS` | example specs；当前 compiler 主要写 function-level contracts | loader signature consistency、source/repair prompt | source/repair: yes | loader | 需实验 | 跨函数/跨文件调用约束与 signature check 丢失 | `coder/specs.py:375-393`; `coder/prompts.py:40` |
| file `FORBIDDEN_SYMBOLS` | planning/example specs | loader conflict check、source/repair prompt | source/repair: yes | loader | no | hallucinated/冲突 public symbol 风险增加 | `coder/specs.py:367-386`; `coder/prompts.py:39` |
| file `TEST_VECTORS` | example specs | source/repair prompt、loader codec coverage warning | source/repair: yes; verifier: no | loader warning | 需实验 | 文件级行为要求丢失 | `coder/prompts.py:41`; `coder/specs.py:406-413` |
| `FUNCTION_SPEC.TRACE_ID` | compiler function trace lowering | file linkage、function selection、machine constraint identity | loader/source/repair: yes | schema/loader | no | function orphan或不进入 prompt | `coder/specs.py:124-140,360-364`; `coder/prompts.py:44-53` |
| `FUNCTION_TYPE/ROLE` | function inventory/behavior lowering | function summary选择 EVENT/LOGIC notes、prompt | source: yes | schema | no/需实验 | 行为类别和 purpose 丢失 | `coder/prompts.py:75-100` |
| `SIGNATURE.RAW/NAME` | function signature lowering | canonical source/header signature、prompt、link validation | header/source/repair: yes | loader/coder semantic | no | ABI 漂移、错误函数名 | `coder/specs.py:124-140,565-579`; `coder/prompts.py:75-100` |
| `SIGNATURE.RETURN/PARAMS.*` | signature lowering | loader model；当前 prompt主要使用 `RAW`，planning validator使用 structured signature before lowering | current coder: partial | schema/planning readiness | 需实验；不可直接移除 | structured ownership/nullability/type refs 可能用于未来 deterministic checks；与 RAW 漂移风险 | `coder/specs.py:124-140`; `coder_spec_lowering.py:435-477`; function schema |
| `RELY.FUNC` | `call_contracts` lowering | function summary中的 `relies on` | source: yes | schema | no/需实验 | coder 缺少调用提示；与 `CALL_CONTRACTS` 漂移风险 | `coder_spec_lowering.py:595-614`; `coder/prompts.py:81-97` |
| `RELY.STRUCT/VAR` | signature/internal refs lowering | loader 保存；当前 source prompt不显示 | currently under-consumed | schema | 需实验；更可能强化 consumer | 类型/变量依赖信息浪费，可能造成 missing type/symbol | `coder_spec_lowering.py:595-614`; `coder/prompts.py:75-100` |
| `LOGIC.ACTION/INVARIANTS_USED` | behavior contract lowering | function summary | source: yes | schema | no | 核心行为与 invariant 丢失 | `coder_spec_lowering.py:501-528`; `coder/prompts.py:82-97` |
| `LOGIC.INPUT/OUTPUT` | behavior contract lowering | loader body 保存，但 function summary不显示 | currently under-consumed | schema | 需实验；更可能强化 consumer | 输入/输出行为信息浪费 | `coder/specs.py:124-140`; `coder/prompts.py:82-97` |
| `EVENT.ACTION/STATE_CHANGE` | event contract lowering | function summary | source: yes | schema | no | event behavior/state transition 丢失 | `coder/prompts.py:82-97` |
| other `EVENT.*` | event contract lowering | loader body 保存，但 summary不显示 | currently under-consumed | schema | 需实验；更可能强化 consumer | trigger/precondition/response 信息浪费 | `coder_spec_lowering.py:501-517`; `coder/prompts.py:82-97` |
| function `PUBLIC_SYMBOLS` | schema/example optional；compiler不生成 | 当前 loader/prompt/semantic validator不消费 | no | schema only | yes/omit candidate | 当前 coder 无直接风险；可能与 file/module public symbols重复 | function schema; `coder/prompts.py:44-53`; `coder/specs.py:124-140` |
| function `ACCESS_PATHS` | access path table lowering | source/repair prompt；wire target validation间接依赖 file/public paths | source/repair: yes | planning readiness/loader | no | function级字段访问边界丢失 | `specs_compiler.py:271-321`; `coder/prompts.py:48` |
| function `WIRE_MAPPING` | wire/access binding lowering | source/repair prompt、loader target validation | source/repair: yes | loader/planning readiness | no | wire field storage/skip/reject策略丢失 | `specs_compiler.py:276-321`; `coder/specs.py:395-405`; `coder/prompts.py:49` |
| function `CALL_CONTRACTS` | `call_contracts` lowering | source/repair prompt | source/repair: yes | planning readiness before lowering | no | callee signature/binding/failure行为丢失 | `coder_spec_lowering.py:581-592`; `coder/prompts.py:50` |
| function `FORBIDDEN_SYMBOLS` | planning function constraints | source/repair prompt | source/repair: yes | schema | no | function局部 hallucination guard 丢失 | `coder/prompts.py:51` |
| function `TEST_VECTORS` | compiler 从 wire mappings生成或 example specs | source/repair prompt、loader codec coverage warning | source/repair: yes; verifier: no | loader/planning readiness | 需实验 | wire-facing behavior要求丢失 | `specs_compiler.py:322-339`; `coder/prompts.py:52`; `coder/specs.py:406-413` |
| sidecar traceability fields | `sidecar_payload` | 当前无 core consumer | no | no | already sidecar | 不应移入 strict specs；删除会损失研究 traceability | `coder_spec_lowering.py:677-709` |
| sidecar decisions/refs + `unresolved_lowering` | `sidecar_payload` | coder semantic validator | generation: no | coder semantic: yes | already validator-sidecar | 移除会漏掉 planned public API/unresolved lowering | `coder_semantics.py:41-88,247-277` |

## 5. 谁消费了哪些字段

### 5.1 Coder loader 显式建模的字段

`agent/coder/models.py` 与 `agent/coder/specs.py` 显式建模：

- protocol：`NAME`, `SPEC_VERSION`, `ROLES`, `DEFAULT_PORT`。
- module：`NAME`, `ROLE`, `DEPENDENCIES`, `FILES`, `ARTIFACTS`, `DOC_REF`。
- file：`FILE.TRACE_ID/ROLE/LANG`、header/source paths、header/source dependencies、header/system dependencies、header/source data、header/source interfaces。
- function：`TRACE_ID`, `FUNCTION_TYPE`, `ROLE`, structured signature、`RELY`、`LOGIC/EVENT` body。

同时，所有 file/function 原始对象保存在 `raw`，因此“未显式建模”不等于“不消费”。

Evidence: `agent/coder/models.py:8-105`、`agent/coder/specs.py:61-165`。

### 5.2 Header renderer 消费的字段

直接消费：

- `HEADER.PATH`
- `HEADER.DEPENDENCY`
- `HEADER.SYSTEM_DEPENDENCY`
- `HEADER.DATA` 中 public `TYPE/MACRO/CONST` 与 `TYPE_SPEC`
- `HEADER.INTERFACE`，其最终 signature 优先取关联 `FUNCTION_SPEC.SIGNATURE.RAW`

间接消费：

- public type/member文本决定自动补充 `stdbool.h`、`stddef.h`、`stdint.h`
- `SOURCE.INTERFACE.TRACE_ID` 用于定位 canonical function spec signature

不消费：

- `SOURCE.DATA`
- `SOURCE.INTERFACE.CONTRACT`
- `DOC_REF`
- nested field/enum `ROLE`

Evidence: `agent/coder/generation.py:97-163`、`agent/coder/header_recipes.py:17-145`、`agent/coder/specs.py:572-579`。

### 5.3 Source generation prompt 消费的字段

直接进入 source prompt：

- module `NAME/ROLE/DEPENDENCIES`
- file `TRACE_ID/ROLE/SOURCE.PATH/SOURCE.DEPENDENCY`
- `SOURCE.DATA[].NAME`，但不含其 `TYPE_SPEC`
- generated canonical header
- function canonical signature、`ROLE`、`FUNCTION_TYPE`、`RELY.FUNC`、`LOGIC.ACTION/INVARIANTS_USED` 或 `EVENT.ACTION/STATE_CHANGE`
- private source interfaces without function specs
- module/file/function `FORBIDDEN_SYMBOLS`、file/function `PUBLIC_SYMBOLS/ACCESS_PATHS/CALL_CONTRACTS/TEST_VECTORS`、function `WIRE_MAPPING`
- `CONSISTENCY_RULES`
- rendered dependency headers

repair prompt 消费同类 machine constraints、consistency rules、source dependencies 和 dependency headers，但不重新展示 function behavior summary。

Evidence: `agent/coder/prompts.py:31-220`。

### 5.4 当前只适合作为 sidecar 或 validator-only 的字段

静态证据较强的 sidecar 候选：

- `PROTOCOL.SCOPE`：当前 coder loader/prompt 不消费。
- module/file `DOC_REF`：module `DOC_REF` 仅被 loader 保存；file `DOC_REF` 仅留在 raw；traceability 已有独立 sidecar。
- nested field/enum `ROLE`：当前 renderer 不消费，schema 之外无直接 consumer。
- function `PUBLIC_SYMBOLS`：schema 允许，但 compiler 不生成，当前 coder 不消费。
- planning source fact ids、evidence refs、target directive ids、decision rationale：已经进入 `planning_traceability.json`、`planning_decisions.json`、`planning_ir_refs.json`，不应复制进 strict specs。
- dependency validation reports、repair patches/statistics、candidate validation reports：validator-only。

不能仅因当前 consumer 不足就移入 sidecar的字段：

- `SOURCE.DATA.TYPE_SPEC/ROLE/VISIBILITY`
- `SOURCE.INTERFACE.CONTRACT`
- `SIGNATURE.RETURN/PARAMS.NULLABLE/PARAMS.OWNERSHIP`
- `RELY.STRUCT/VAR`
- `LOGIC.INPUT/OUTPUT` 和完整 `EVENT`

这些字段包含 implementation contract，当前更像是“consumer 未充分使用”，不是“对 coder 无价值”。Step 2/3 应分别验证“移除”与“强化 prompt/validator 消费”的效果。

## 6. Dependency / Header 失败的直接关联

### 6.1 `imports_allowed` 被错误投影到两种 dependency

compiler 从同一 `file_item.imports_allowed` 生成 `imported_headers`，随后：

- 写入 `SOURCE.DEPENDENCY`；
- 同时写入 `HEADER.DEPENDENCY`。

Evidence: `agent/planning/stages/specs_compiler.py:612-615,664-680`。

这混合了至少两类不同需求：

- source-level dependency：实现中调用其他模块 public function，需要在 `.c` 可见；
- header-level dependency：本 header 的 public declarations 按值或通过 typedef/callback/field 依赖其他 header。

风险是 over-include、header cycle，以及 source 需要的 dependency 被误认为 public header dependency。反过来，仅由 public type closure 派生出的 header dependency 也可能不存在于 `imports_allowed`。

### 6.2 dependency fallback 清空真实边，造成假通过

`apply_deterministic_dependency_fallback` 对所有 functions 设置 `calls_allowed=[]`，并对所有 files 设置 `imports_allowed=[]`，然后 `finalize_dependency_graph` 从清空后的输入重新派生 graph。

Evidence:

- `agent/planning/stages/implementation_plan_merger.py:2987-3009`
- `agent/planning/stages/dependencies.py:8-80`

已有样例 `20260609_214554_036173_t/_step_logs/007_implementation_plan.json` 显示：

- 64 个 function 仍有非空 `call_contracts`；
- 0 个 function 有非空 `calls_allowed`；
- 0 个 file 有非空 `imports_allowed`；
- `dependency_graph.function_edges` 为空。

同一运行的 `_validation_reports/008_dependency_validation_report.json` 状态为 `passed`。这不是依赖问题已解决，而是 dependency graph 的输入边被删除。禁止把清空 `calls_allowed/imports_allowed` 当成修复策略；应 fail-closed 并保留 blocking diagnostics。

### 6.3 public type/header closure 不完整

header renderer 要求 `HEADER.DEPENDENCY` 和 `HEADER.SYSTEM_DEPENDENCY` 提供 public declaration 所需 includes。compiler 当前：

- 根据 public signatures 将部分 canonical types 放入当前 `HEADER.DATA`；
- 没有从 public struct field、callback、typedef、function pointer、跨模块 type owner 完整派生 header includes；
- 没有写 `HEADER.SYSTEM_DEPENDENCY`。

Evidence:

- public type lowering：`agent/planning/stages/specs_compiler.py:461-553`
- header include rendering：`agent/coder/generation.py:122-140`
- system dependency schema：`specs-example/specs_schema/file_spec_schema.json:472-510`

已有 planning spec `.../spec_bundle/src/network/network/network_spec.json` 的 public signatures 使用 `ssize_t`，但 `HEADER.SYSTEM_DEPENDENCY` 缺失。已有 compile 日志：

```text
./src/network/network.h:22:1: error: unknown type name 'ssize_t'
./src/network/network.h:23:1: error: unknown type name 'ssize_t'
```

Evidence: `agent/out/mqtt_broker_20260610_102238/_agent_logs/010_compile_stderr_0.txt`。

### 6.4 final compatibility validation 跳过 rendered header compile

正常 coder loader 默认 `validate_rendered_headers=True`，会生成并编译每个 header：

- `agent/coder/specs.py:416-457,491-555`

但 planning compatibility validator 显式调用：

```python
load_spec_bundle_from_root(spec_root, validate_rendered_headers=False)
```

Evidence: `agent/planning/validators/coder_compat.py:10-32`。

之后 `coder_semantics` 只检查 rendered header 是否包含 public symbol 名称，不执行 header compile：

- `agent/planning/validators/coder_semantics.py:279-300`

因此 `specs_compile` 可以报告 coder compatibility passed，但 coder 阶段因 deterministic header compile error 直接阻塞 repair。

### 6.5 重复 dependency/call 表达发生漂移

当前 dependency/call 信息分散在：

- planning internal：`calls_allowed`、`call_contracts`、`imports_allowed`、`signature_dependencies`、`dependency_graph`
- strict module：`MODULES[].DEPENDENCIES`
- strict file：`HEADER.DEPENDENCY`、`SOURCE.DEPENDENCY`、file `CALL_CONTRACTS`
- strict function：`RELY.FUNC`、function `CALL_CONTRACTS`

这些字段有不同 consumer，却没有 final cross-layer closure validation。例如已有样例在 `call_contracts` 非空时，`calls_allowed`、function dependency edges 和 imports 已为空。Step 2 工具必须能检查这些表达之间的映射，而不能只统计某一字段是否存在。

## 7. Artifact Boundary 风险

### 风险 A：dependency 层级混合与边丢失

- 同一 `imports_allowed` 同时生成 source/header dependency。
- dependency fallback 清空 `calls_allowed/imports_allowed`。
- function `CALL_CONTRACTS`、module dependencies、file dependencies 和 dependency graph 可互相不一致。

影响：dependency graph 假通过、header cycle、missing declaration、coder prompt 看不到真实 dependency headers。

### 风险 B：public header/type closure 缺口

- `HEADER.SYSTEM_DEPENDENCY` schema 已存在，但 compiler 不生成。
- public signature、callback、struct field、typedef/alias 的 type owner 与 include 未完整闭合。
- final planning compatibility validation 跳过 rendered header compile。

影响：planning 报告成功，但 deterministic header 在 coder 阶段失败；header failure 不可由 source repair 修复。

### 风险 C：重复 canonical 字段漂移

同一函数 signature 同时存在于：

- `HEADER.INTERFACE.SIGNATURE`
- `SOURCE.INTERFACE.SIGNATURE`
- `FUNCTION_SPEC.SIGNATURE.RAW`
- `CALL_CONTRACTS.SIGNATURE`

同一 call/dependency 同时存在于 `calls_allowed`、`call_contracts`、`RELY.FUNC` 和 file/module dependencies。loader 只对部分 signature mismatch 发 warning，不能保证所有重复表示一致。

影响：renderer、prompt、validator 可能各自看到不同 canonical truth。

### 风险 D：`raw` 隐式消费导致错误 sidecar 判断

多个字段没有 dataclass 成员，但通过 `file_spec.raw` / `function_spec.raw` 进入 prompt 或 validator，例如 `PUBLIC_SYMBOLS`、`ACCESS_PATHS`、`CALL_CONTRACTS`、`FORBIDDEN_SYMBOLS`、`WIRE_MAPPING`、`TEST_VECTORS`。

影响：若只看 `models.py` 或 loader parser，会错误判断这些字段未被 coder 消费并移入 sidecar，实际会削弱 generation 与 validation。

### 风险 E：schema-required 与 coder-required 不一致

例如：

- `SOURCE.INTERFACE.CONTRACT` schema required，但 coder prompt不消费；
- `SOURCE.DATA.TYPE_SPEC` 被 loader保留，但 prompt只列 item names；
- `PROTOCOL.SCOPE`、file/module `DOC_REF` 当前无 generation consumer；
- function `PUBLIC_SYMBOLS` schema允许，但 compiler和coder均未使用。

影响：strict specs 既可能携带无 generation 价值的 traceability 字段，也可能携带重要工程契约但没有有效 consumer。优化不能简单删除字段；必须区分“应移 sidecar”和“应强化消费”。

### 风险 F：validator-sidecar 与 pure sidecar 未明确区分

`planning_decisions.json`、`planning_ir_refs.json` 被 coder semantic validator 消费，而 `planning_traceability.json` 当前不被核心逻辑消费。

影响：若把三个 sidecars 一概当作可选 traceability，可能删除 unresolved lowering 与 planned public API 的 final gate。

## 8. 建议的 Artifact Boundary

### 8.1 应保留在 coder-facing strict specs

静态证据表明下列类别直接影响 generation、header 或 coder compatibility：

- identity/routing：`KIND`、trace ids、module/file names、paths、`MODULES[].FILES`、`GENERATION_ORDER`
- public ABI/header：`HEADER.DEPENDENCY`、`HEADER.SYSTEM_DEPENDENCY`、public `HEADER.DATA.TYPE_SPEC`、`HEADER.INTERFACE`
- source implementation：`SOURCE.PATH`、`SOURCE.DEPENDENCY`、`SOURCE.DATA`、`SOURCE.INTERFACE`
- function contract：canonical signature、behavior、call/access/wire/forbidden/test constraints
- compatibility gates：module/file public artifact/symbol projections，至少在当前 validator contract 下必须保留

### 8.2 应保留在 planning internal IR

- protocol facts、target directives、evidence normalization
- canonical types、type inventory、function contracts、file layout
- `calls_allowed`、`imports_allowed`、`signature_dependencies`、完整 `dependency_graph`
- unresolved questions、repair candidates、stage-level assumptions

这些内容应通过 deterministic compiler 投影到 strict specs，而不是直接交给 coder。

### 8.3 应保留在 sidecar

- evidence/traceability：source fact ids、evidence refs、target directive ids
- planning decision rationale、capability ids、resource/state access 的完整 planning 表达
- strict specs 到 planning IR 的 refs
- unresolved lowering details

其中 decisions/refs sidecars 当前属于 validator-sidecar，不能随意删除。

### 8.4 优先 sidecar/omit 候选

需由 Step 2/3 工具验证：

- strict specs 中的 module/file `DOC_REF`
- `PROTOCOL.SCOPE`
- nested data member/enum `ROLE`
- function `PUBLIC_SYMBOLS`
- 与 sidecar 重复、且无 generation/compatibility consumer 的 traceability detail

### 8.5 优先强化 consumer、而不是移出 strict specs

- `SOURCE.DATA.TYPE_SPEC/ROLE/VISIBILITY`
- `SOURCE.INTERFACE.CONTRACT`
- structured signature ownership/nullability/type refs
- `RELY.STRUCT/VAR`
- 完整 `LOGIC` / `EVENT` contract

这些字段与 downstream code correctness 直接相关，但当前 prompt/validator 消费不足。

## 9. Step 2 工具建设优先支持的字段类别

### P0：Dependency / Header / Type Closure

diff 与 oracle substitution 必须优先支持：

- `MODULES[].DEPENDENCIES`
- `HEADER.DEPENDENCY`
- `HEADER.SYSTEM_DEPENDENCY`
- `SOURCE.DEPENDENCY`
- public `HEADER.DATA.TYPE_SPEC`
- `HEADER.INTERFACE.SIGNATURE`
- public signature/type refs

工具应输出：

- source dependency 与 header dependency 的分离比较；
- public type owner/header closure；
- system type 到 system header coverage；
- rendered header compile status；
- dependency 输入被清空时的 fail-closed diagnostic。

### P1：Call / Dependency Cross-Layer Consistency

优先比较和替换：

- planning `calls_allowed`
- planning `call_contracts`
- planning `imports_allowed`
- planning `signature_dependencies`
- strict function `CALL_CONTRACTS`
- strict `RELY.FUNC`
- final `dependency_graph`

工具必须识别“`CALL_CONTRACTS` 非空但 dependency edges/imports 为空”，不能把空图视为成功。

### P2：Public Symbol / Access / Wire Constraints

支持：

- module/file `PUBLIC_SYMBOLS`
- file/function `ACCESS_PATHS`
- function `WIRE_MAPPING`
- module/file/function `FORBIDDEN_SYMBOLS`

重点比较 semantic coverage 和 target/path closure，不按字段数量评分。

### P3：Behavior、Test Vectors 与 Sidecar

支持：

- `LOGIC` / `EVENT`
- `TEST_VECTORS`
- `SOURCE.INTERFACE.CONTRACT`
- `planning_traceability.json`
- `planning_decisions.json`
- `planning_ir_refs.json`

需要分别测量：

- 移除 behavior detail 对 source generation 的影响；
- 移除 test vectors 对 prompt/coder behavior 的影响；
- sidecar-only traceability 是否保持 coder compatibility；
- validator-sidecar 缺失是否造成 semantic gate 降级。

## 10. 本步骤未执行事项

- 未执行 Step 2。
- 未创建 degradation、diff、oracle substitution 工具。
- 未运行 LLM、coder generation、大规模实验、compile 或 smoke test。
- 未修改 planning/coder core pipeline、schema、renderer 或 validator。
- 未降低 coder compatibility validation 强度。
- 未将 example specs 的 code-derived implementation details 当作 protocol facts。

## 11. Step 1 完成判断

本次静态审计已：

- 列出三类 strict specs 的字段与嵌套字段类别；
- 映射字段 producer、loader/header/prompt/validator consumer；
- 区分 strict specs、planning internal IR、pure sidecar、validator-sidecar、validator-only artifacts；
- 识别 dependency/header 直接风险与现有失败证据；
- 给出至少六类 artifact boundary 风险；
- 明确 Step 2 应优先支持的字段类别。

Step 1 可标记为 `Completed`。后续 Step 2 应构建诊断工具，但不应以清空 dependency 字段、删除 dependency edges 或关闭 compatibility validation 作为任何 degradation/repair profile。
