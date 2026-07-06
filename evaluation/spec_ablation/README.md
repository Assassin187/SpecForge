# RQ2: Spec Form Transfer Ablation

## 1. 实验目标

本实验用于回答 RQ2：

```text
RQ2: How do different coder-facing specification forms affect the quality of generated protocol implementations?
```

该实验只研究 coder-facing specification form 对协议实现生成质量的影响，不评价 facts agent 的事实抽取能力，也不评价 planning agent 从 protocol facts 生成 protocol specs 的能力。实验中的所有输入均来自同一套完整的 reference specs，即 `specs-example/<protocol>_specs`。S1 和 S2 由该完整 specs 确定性转换得到，S3 直接使用该完整 specs。这样可以避免不同 LLM 规划结果带来的内容差异，使实验变量尽可能集中在 specification form 和 coder-visible information 上。

本实验比较三种规格形式：

```text
S1 SpecFS-Local
    + global module/file/function project graph
    = S2 SpecFS-ProjectGraph
    + SpecForge engineering semantics and protocol-specific constraints
    = S3 Full-SpecForge
```

三组的递进关系如下：

1. S1 只提供当前 translation unit 的局部 SpecFS view，包括 function-local specification blocks 和 raw header declarations。
2. S2 在 S1 的基础上额外暴露全局 module/file/function project graph，包括 module/file membership、dependency edges 和 generation order。
3. S3 在 S2 的基础上进一步提供完整的 SpecForge engineering semantics、protocol-specific grounding 和 machine-readable constraints。

因此，S1 vs. S2 用于检验 global project structure visibility 的独立作用；S2 vs. S3 用于检验完整工程语义和协议约束的额外作用；S1 vs. S3 用于衡量完整 SpecForge specification form 相对局部 SpecFS specification view 的总体收益。

## 2. 实验范围

本实验关注的是 specification form 对 coder agent 的影响，而不是协议事实抽取或规划质量。因此，实验不从 technical documents 或 protocol facts 开始，也不调用 planning agent 生成新的 specs。

实验输入统一为：

```text
specs-example/<protocol>_specs
```

其中完整 specs 被视为 source oracle。S1 和 S2 是对该 oracle 的确定性投影，S3 是未经降级的原始 Full-SpecForge 输入。

实验对象包括：

```text
MQTT
HTTP/1.1
CoAP
SMTP
```

这些协议用于覆盖不同的协议实现特征，包括二进制消息解析、文本请求响应、会话状态维护、命令序列处理和服务端运行时行为。

## 3. 总体控制原则

本实验的主要控制原则是：三组尽可能共享相同的代码生成任务、项目结构、公共 ABI、编译流程、修复流程和行为验证流程；唯一系统性变化应来自 coder 可见的 specification form。

具体控制内容如下：

```text
same source oracle
same protocol set
same generated project layout
same public function signatures
same public type declarations
same header/source paths
same binary name
same argv contract
same deterministic headers
same deterministic main.c
same deterministic Makefile
same compile flags
same compiler diagnostics extraction
same maximum repair rounds
same repairable source-file selection policy
same behavior verifier
same runtime contract
same model configuration
```

三组不要求 prompt 文本逐字一致，因为 S1、S2 和 S3 的 specification form 本身不同。但三组应保持相同的代码生成任务语义，即都要求 LLM 为当前 translation unit 生成 C source file，并遵守相同的输出约束、编译约束和文件修改边界。

尤其需要注意的是，S3 使用现有 SpecForge coder，其 prompt 形式和具体内容不做修改。S1 和 S2 的代码生成器不是重新设计一个新的 prompt 工程系统，而是基于现有 SpecForge coder 的设计迁移而来。S1/S2 只改变 specification 信息的获取形式和可见 payload，不改变代码生成任务本身。

## 4. 与现有 coder 的适配边界

现有 SpecForge coder 的核心流程是：

```text
load spec_bundle
-> validate specs
-> deterministic header generation
-> LLM source generation
-> deterministic main.c generation
-> deterministic Makefile generation
-> compile
-> source-level repair
-> behavior validation
```

其中，`.h`、`main.c` 和 `Makefile` 均由 deterministic renderer 或 deterministic template 生成；LLM 只参与普通 `.c` source generation 和 repair `.c`。该边界在三组实验中保持一致。

因此，本实验中的三组都遵守以下规则：

1. LLM 不生成头文件。
2. LLM 不修改头文件。
3. LLM 不生成或修改 `main.c`。
4. LLM 不生成或修改 `Makefile`。
5. LLM 只负责生成普通 `.c` source file。
6. compile failure 后的 repair 也只允许修改 `.c` source file。
7. 如果错误来自 deterministic header，则记录为 header/spec lowering failure，而不是交给 LLM 修复。

S1 和 S2 需要适配现有 coder，是因为它们不直接使用 Full-SpecForge 的完整 `PROTOCOL_MODULE_SPEC`、`FILE_SPEC` 和 `FUNCTION_SPEC` 作为 coder-visible input。适配目标不是改变 coder 的任务，而是改变 coder 看到的 specification view。

可以将三组的 coder-facing view 概括为：

```text
S1:
  source generation task + deterministic headers + local SpecFS function blocks for the current translation unit

S2:
  source generation task + deterministic headers + same local SpecFS function blocks + global project graph

S3:
  existing SpecForge coder prompt + full SpecForge specs
```

其中 S3 是完整系统设置，不修改现有 coder prompt。S1/S2 是为了消融实验而构造的兼容性输入视图，尽量复用现有 coder 的 source generation、compile、repair 和 behavior validation pipeline。

## 5. S1: SpecFS-Local

## 5.1 设计目标

S1 用于模拟一种局部 SpecFS-style specification view。它只向 coder 暴露当前 translation unit 的局部生成上下文，包括当前 `.c` source file 需要实现的 function-local contracts 和 raw header declarations。S1 不暴露全局 module/file/function project graph，也不暴露 SpecForge 的工程语义和协议约束。

S1 回答的问题是：

```text
仅依赖当前 translation unit 的 function-local contracts 和 raw C declarations，是否足以驱动可编译、可运行的多文件协议实现？
```

这里的 S1 不是“完全没有文件信息”的单函数生成 baseline。为了在多文件 C 项目设置下保持三组可比，S1 仍然需要知道当前正在生成哪个 translation unit，以及该 translation unit 中需要实现哪些 function specs。但这种信息仅用于定义当前 source generation unit，不构成全局文件拓扑、模块边界或依赖图。

## 5.2 Coder-visible specification form

S1 中每个 function 被转换为一个 SpecFS-style `.spec` artifact。每个 `.spec` 只包含以下四类信息：

```text
[PROMPT]
[RELY]
[GUARANTEE]
[SPECIFICATION]
```

其中：

```text
[PROMPT]
描述该 function 的局部实现任务。

[RELY]
列出该 function 可以依赖的 C declarations，包括相关 struct、function、variable 以及必要的自然语言说明。

[GUARANTEE]
给出该 function 必须实现的 raw C signature。

[SPECIFICATION]
描述 function-local behavior contract，包括：
- Pre-Condition
- Post-Condition
- Invariant
- System Algorithm
```

S1 中的 header 以 SpecFS-style `.header` 形式暴露给 coder。该 `.header` 只包含 header dependencies 和 canonical C declarations。它是 prompt-visible artifact，用于告诉 LLM 当前可用的公共声明；它不是由 LLM 生成的实际 `.h` 文件。

实际参与编译的 `.h` 文件仍由 deterministic header renderer 生成。这样可以保持 S1 与 S2/S3 在 public ABI 上可比，也避免把头文件生成能力混入实验变量。

S1 prompt 中允许出现当前 translation unit 的最小局部上下文：

```text
current target source file
canonical public headers
function-local SpecFS blocks assigned to this source file
```

S1 prompt 中不应出现全局项目结构：

```text
full source file list
full function-to-file map
module list
module membership
module dependency graph
file dependency graph
generation order
current file position in the global project graph
```

因此，S1 更准确地表示为 local SpecFS view，而不是 absolutely topology-free SpecFS view。

## 5.3 从 Full-SpecForge 到 S1 的确定性投影

S1 从完整 SpecForge specs 确定性投影得到。主要投影关系为：

```text
function role / local task information
  -> [PROMPT]

function signature
  -> [GUARANTEE]

RELY.STRUCT / RELY.FUNC / RELY.VAR
  -> [RELY]

RELY item role or explanation
  -> [RELY] 中对应 declaration 的自然语言说明

LOGIC.INPUT or EVENT precondition/input
  -> Pre-Condition

LOGIC.OUTPUT or EVENT response/state change
  -> Post-Condition

LOGIC.INVARIANTS_USED
  -> Invariant

LOGIC.ACTION or EVENT action
  -> System Algorithm
```

S1 保留 function-local behavior detail。也就是说，S1 不是弱化行为语义的 baseline，而是只移除全局 project graph、跨文件/跨模块工程结构和协议专用约束。

## 5.4 S1 不暴露的信息

S1 不向 coder 暴露以下信息：

```text
protocol metadata
module metadata
module role
module list
module membership
module dependency graph
module artifact inventory
generation order
full source file list
full function-to-file map
file role
file dependency graph
current file position in the global project graph
source data inventory
function type classification
structured event model
public symbol surface
access paths
wire mapping
call contracts
forbidden symbols
consistency rules
test vectors
doc references
trace references
```

S1 也不通过伪造空的 module/file/function specs 接入现有 SpecForge loader。这样做会重新把被消融的 hierarchy 信息暴露给 coder，使 S1 不再是有效的 local SpecFS baseline。

## 5.5 S1 的代码生成方式

S1 虽然没有 coder-visible global project graph，但仍然需要生成完整 C 项目。因此，实验运行器在内部使用 hidden execution manifest 进行调度。该 manifest 只用于 evaluator 控制，不进入 generation prompt 或 repair prompt。

Hidden manifest 可以记录：

```text
target source paths
target header paths
function-to-source grouping
source generation scheduling
Makefile source list
binary name
argv contract
behavior verifier runtime contract
```

Hidden manifest 不包含，也不向 coder 暴露：

```text
module role
file role
module dependency semantics
file dependency semantics
global project graph
behavior guidance
type ownership guidance
wire rules
access paths
test vectors
negative constraints
engineering rationale
```

在 source generation 时，S1 仍采用 file-level generation unit。也就是说，对于同一个 `.c` 文件，runner 会把该文件对应的多个 function `.spec` 拼接到一次 source generation prompt 中。该拼接只是为了生成完整 translation unit，不向模型提供全局项目拓扑、模块语义或文件依赖语义。

S1 的代码生成输入可以抽象为：

```text
current target source file
deterministic canonical headers
SpecFS-style header declarations
function-local SpecFS blocks for functions in this source file
same source generation task semantics as the existing coder
same output constraints as the existing coder
```

LLM 输出完整 `.c` source file。随后，该 source file 与 deterministic headers、deterministic main.c 和 deterministic Makefile 一起进入相同 compile/repair/behavior validation pipeline。

## 6. S2: SpecFS-ProjectGraph

## 6.1 设计目标

S2 用于测试 global project structure visibility 的独立作用。S2 保留与 S1 完全相同的 function-local semantic payload，并额外向 coder 暴露全局 module/file/function project graph。

S2 回答的问题是：

```text
在当前 translation unit 的 function-local specification 内容相同的情况下，显式暴露全局 project-level structural context 是否改善多文件协议实现生成？
```

因此，S2 的关键是只增加 project graph visibility，而不增加完整 SpecForge 的工程语义、协议 grounding 或 machine-readable constraints。

## 6.2 Coder-visible specification form

S2 的 coder-visible input 包含两部分：

```text
1. 与 S1 完全相同的 local SpecFS-style function blocks
2. 额外的 global module/file/function project graph
```

S2 中每个 function 的 `[PROMPT]`、`[RELY]`、`[GUARANTEE]` 和 `[SPECIFICATION]` 与 S1 同源，且应在规范化后保持一致。S2 不修改 function-local behavior contract。

S2 额外暴露的 global project graph 包括：

```text
Module-level:
- global module list
- module name
- module contains file IDs
- module dependency edges

File-level:
- global source/header file list
- file trace ID
- header path
- source path
- file contains function IDs
- file dependency edges
- generation order

Function-level:
- function trace ID
- function membership/linkage information
- current function/file position in the global project graph
```

这些信息使 coder 能够看到当前 translation unit 在整个多文件项目中的位置、当前 file 属于哪个 module、其他 files 包含哪些 functions、source files 之间的依赖关系以及生成顺序。

## 6.3 S2 不暴露的信息

S2 不暴露以下信息：

```text
protocol-specific metadata beyond common runtime input
module role descriptions
file role descriptions
function role descriptions beyond S1 [PROMPT]
artifact inventory
structured header data inventory
structured source data inventory
function type classification
structured event model
parameter nullability
ownership annotations
public symbol surface
access paths
wire mapping
call contracts
forbidden symbols
test vectors
consistency rules
doc references
trace references
```

S2 的边界比一般的 “hierarchical engineering core” 更严格。它只测试 global project graph visibility 本身，不把 architecture description、role explanation、data inventory、ownership、wire/access grounding 或 test oracle 一并加入。

## 6.4 S2 与现有 coder 的适配

S2 可以在内部使用 loader-compatible envelope，以便复用现有 coder 的一部分 project assembly、validation、compile、repair 和 behavior testing 机制。但 internal envelope 与 coder-visible prompt view 必须分离。

也就是说：

```text
Internal representation:
  可以保留运行所需的 loader-compatible placeholders。

Coder-visible representation:
  只能包含 S1 的 local SpecFS blocks + allowed global project graph。
```

如果某些字段只是为了满足现有 schema 或 loader 要求而存在，它们不能进入 prompt。特别是 access paths、wire mapping、test vectors、forbidden symbols 和 consistency rules 等字段不能因为 renderer 默认行为而泄漏给 S2。

S2 的 source generation 方式与 S1 相同，仍然以 `.c` file 为 generation unit。不同之处在于，S2 在 function-local blocks 之外，额外提供全局 project graph，使模型能够理解当前 source file 与其他 files/modules 的结构关系。

S2 的代码生成输入可以抽象为：

```text
current target source file
deterministic canonical headers
same local SpecFS-style function blocks as S1
global module/file/function project graph
same source generation task semantics as the existing coder
same output constraints as the existing coder
```

LLM 输出完整 `.c` source file。后续编译、repair 和 behavior validation 与 S1/S3 保持一致。

## 7. S3: Full-SpecForge

## 7.1 设计目标

S3 是完整 SpecForge setting。它直接使用未经降级的 `specs-example/<protocol>_specs` 和现有 SpecForge coder。

S3 回答的问题是：

```text
完整 SpecForge engineering specs 相对 local function-local specs 和 project-graph-only specs 是否提供额外收益？
```

S3 是完整系统的上界对照，不对现有 coder prompt 形式和具体内容做任何修改。

## 7.2 Coder-visible specification form

S3 使用完整的 SpecForge coder-facing specs，主要包括：

```text
PROTOCOL_MODULE_SPEC
FILE_SPEC
FUNCTION_SPEC
```

相较 S2，S3 额外提供：

```text
protocol metadata
module/file/function roles
artifact inventory
structured public/private data
function classification
event model
parameter nullability and ownership
public or allowed symbol surface
canonical access paths
wire field to memory field mappings
cross-function call contracts
forbidden symbols and negative constraints
module/file/function test vectors
cross-module consistency rules
trace references when non-empty
```

这些信息共同构成完整的 implementation-oriented engineering contract。S3 的意义不是只测试 JSON schema，而是测试完整 SpecForge specs 对 LLM source generation 的帮助。

## 7.3 S3 的代码生成方式

S3 直接使用现有 SpecForge coder 的完整流程：

```text
load full spec_bundle
-> validate PROTOCOL_MODULE_SPEC / FILE_SPEC / FUNCTION_SPEC
-> deterministic header generation
-> LLM source generation with existing coder prompt
-> deterministic main.c generation
-> deterministic Makefile generation
-> compile
-> source-level repair with existing coder repair prompt
-> behavior validation
```

S3 不修改现有 coder 的 prompt、renderer、repair prompt 或输入字段。这样可以保证 S3 代表真实 Full-SpecForge setting，而不是一个为了消融实验重新构造的变体。

## 7.4 S3 结果解释边界

S3 的效果只能归因于实际存在并进入 prompt 的字段。如果某些字段在当前 `specs-example` 中为空，例如 `CALL_CONTRACTS` 或 `DOC_REF`，则不能把 S3 的实验收益归因于这些字段。

此外，S2 vs. S3 的差异不应被解释为“纯 JSON 格式”的收益。该比较同时改变了信息量和工程语义，因此更准确的表述是：

```text
完整 SpecForge engineering contract 的组合效应。
```

## 8. 三组代码生成流程对比

三组都生成相同结构的 C 协议项目。项目中包括：

```text
deterministic headers
LLM-generated source files
deterministic main.c
deterministic Makefile
compiled binary
behavior validation logs
```

三组的主要差异在于 LLM 生成 `.c` source file 时看到的 specification payload 不同。

可以概括为：

```text
S1 source generation:
  deterministic headers
  + local SpecFS-style function-local blocks for current translation unit
  -> LLM generates .c source file

S2 source generation:
  deterministic headers
  + same local SpecFS-style function-local blocks
  + global module/file/function project graph
  -> LLM generates .c source file

S3 source generation:
  deterministic headers
  + full SpecForge specs
  + existing SpecForge coder prompt
  -> LLM generates .c source file
```

三组的 deterministic 部分保持一致：

```text
headers:
  generated deterministically from canonical public ABI

main.c:
  generated deterministically from runtime contract

Makefile:
  generated deterministically from source list and binary target
```

三组的 LLM 参与范围保持一致：

```text
ordinary .c source generation
source-level repair for .c files only
```

三组的 LLM 不参与：

```text
header generation
main.c generation
Makefile generation
header repair
main.c repair
Makefile repair
```

## 9. Repair 设计

三组都允许最多相同轮数的 source-level repair。repair 的输入必须遵守各组的 specification boundary。

S1 repair 可见信息：

```text
current source file
compiler diagnostics
deterministic canonical headers
corresponding local SpecFS-style function blocks
```

S2 repair 可见信息：

```text
current source file
compiler diagnostics
deterministic canonical headers
same local SpecFS-style function blocks as S1
global module/file/function project graph
```

S3 repair 可见信息：

```text
current source file
compiler diagnostics
deterministic canonical headers
full SpecForge specs
existing SpecForge repair prompt
```

S1/S2 repair 不能重新注入 Full-SpecForge fields。否则 repair 阶段会破坏实验组别边界，使 S1/S2 不再是有效 baseline。

如果 compile error 指向 deterministic header，则该问题不交给 LLM repair，而是记录为 header/spec lowering issue。这样可以避免把 deterministic artifact 的问题错误归因于 source generation 能力。

## 10. 公平性控制

## 10.1 统一输入来源

S1、S2 和 S3 均来自同一套 full specs。S1/S2 不通过 LLM 重写 specs，而是通过 deterministic projection 得到。这样可以避免由于不同 specification 内容质量导致的混淆。

## 10.2 统一 public ABI

三组使用相同的 public function signatures、public type declarations 和 header/source paths。S1/S2 获得 raw header declarations 不构成信息泄漏，因为 SpecFS-style specification 本身允许 `.header` 提供 C declarations。

如果三组 header guard、格式或注释不完全一致，应比较 normalized declaration hash 和 dependency set，而不是要求 header 文件逐字节相同。

## 10.3 统一代码生成单位

三组都以 `.c` file-level generation unit 生成代码。S1 虽然没有 visible global project graph，但仍通过 hidden execution manifest 在 evaluator 内部确定 function-to-source grouping。该 grouping 只用于定义当前 translation unit，不进入 prompt，因此不改变 S1 的 visible specification form。

## 10.4 统一运行环境

三组使用相同的模型配置、编译参数、repair round 上限、运行时参数和 behavior verifier。compile success、repair iterations、behavior pass rate 和 end-to-end success 在相同条件下比较。

## 10.5 Prompt leakage 控制

S1/S2 需要避免被消融字段重新进入 prompt。

S1 禁止出现：

```text
PROTOCOL_MODULE_SPEC
FILE_SPEC
FUNCTION_SPEC
GENERATION_ORDER
module list
module membership
module role
module dependency graph
full source file list
full function-to-file map
file dependency graph
current file position in global project graph
ACCESS_PATHS
WIRE_MAPPING
CALL_CONTRACTS
FORBIDDEN_SYMBOLS
TEST_VECTORS
CONSISTENCY_RULES
DOC_REF
TRACE_REFS
```

S2 禁止出现：

```text
protocol-specific grounding
module/file/function role descriptions beyond S1 blocks
artifact inventory
structured data inventory
function type classification
event model
ownership
nullability
ACCESS_PATHS
WIRE_MAPPING
CALL_CONTRACTS
FORBIDDEN_SYMBOLS
TEST_VECTORS
CONSISTENCY_RULES
DOC_REF
TRACE_REFS
```

S2 允许出现 global project graph information，但不能出现完整 SpecForge engineering semantics。

## 11. 指标

本实验主要关注 generated implementation 的可编译性、可修复性和行为正确性。

Primary metrics:

```text
end-to-end success:
  final compile success and all required behavior scenarios pass

final compile success:
  whether the project compiles after allowed repair rounds

behavior scenario pass rate:
  passed behavior scenarios / total behavior scenarios

held-out behavior or interop pass rate:
  passed held-out or external interaction scenarios / total held-out or interop scenarios
```

Secondary metrics:

```text
initial compile success
repair iterations
repaired file count
failure stage
failure category
missing or undefined symbol count
missing type/field/header count
forbidden or nonexistent symbol hallucination count
generated source LoC
generation token usage
repair token usage
wall-clock time
```

Compile success 不能替代 behavior success。论文中的主要结论应优先基于 end-to-end success 和 behavior pass rate。

如果 S3 specs 中包含 test vectors，则 evaluator 不能只使用与 test vectors 完全重合的场景。结果应区分：

```text
in-spec scenarios:
  检验 coder 是否遵循可见 oracle

held-out scenarios:
  检验实现是否具备超出可见 test vectors 的行为泛化能力

interop scenarios:
  检验生成实现能否与外部 client/server 交互
```

## 12. 结果解释

S1 vs. S2：

```text
在 function-local semantic payload 相同的条件下，global project structure visibility 是否改善多文件协议实现生成？
```

如果 S2 优于 S1，可以说明全局 module/file/function project graph 有助于模型维护跨函数、跨文件或跨模块的一致性。但不能把该收益归因于 protocol grounding、wire mapping、test vectors 或 negative constraints，因为这些信息在 S2 中不可见。

S2 vs. S3：

```text
在已有 global project graph 的基础上，完整 SpecForge engineering semantics 和 protocol-specific constraints 是否继续带来收益？
```

如果 S3 优于 S2，可以说明完整 implementation-oriented specs 的组合信息对代码生成有进一步帮助。但该收益不应被归因于单一字段，除非实验中单独做了字段级消融。

S1 vs. S3：

```text
完整 SpecForge coder-facing specs 相对 local SpecFS-style specs 的总体收益。
```

该比较反映从局部 function-local specification view 到完整 implementation-oriented protocol specs 的整体差异。

## 13. 有效性威胁

第一，`specs-example` 是 reference specs 或 code-derived oracle。本实验只能证明 specification form 对 coder usability 的影响，不能证明 planning agent 能从 technical documents 自动恢复同等质量的 specs。

第二，S1 使用 hidden execution manifest 进行 project assembly 和 source scheduling。如果 manifest 内容泄漏到 prompt，会破坏 S1 的 local SpecFS baseline。因此必须将 manifest 限定为 evaluator-only control information。

第三，S1/S2 的代码生成器基于现有 coder 迁移设计，但它们的 prompt 不可能与 S3 完全一致。实验控制目标是保持任务语义、输出约束、deterministic artifacts、compile/repair pipeline 和 behavior verifier 一致，而不是强制 prompt 文本逐字一致。

第四，S2 vs. S3 同时改变表示结构和信息量，因此该比较不能解释为纯格式差异，而应解释为完整 SpecForge engineering contract 的组合效应。

第五，完整 specs 通常比 S1/S2 更长。Prompt length 和 token usage 是 treatment 的一部分，不应人为补齐或截断 S1/S2，但应在结果中报告各组 token usage。

第六，当前 behavior tests 是 minimum functional conformance 或 behavior smoke tests，不等价于完整 RFC compliance。因此论文中应避免把通过 behavior tests 表述为完整协议一致性。

## 14. 推荐论文表述

可以在论文中这样描述该实验：

```text
To isolate the impact of coder-facing specification form, we derive three specification views from the same full reference specs. S1 exposes a local SpecFS-style view for the current translation unit, including function-local contracts and raw header declarations. S2 preserves the same local function payload but additionally exposes the global module/file/function project graph. S3 uses the original full SpecForge specs and the existing SpecForge coder without modifying its prompt. For S1 and S2, we migrate the existing coder design while changing only the form of specification information visible to the model. Across all groups, headers, main.c, and Makefile are generated deterministically; LLM calls are restricted to source-file generation and source-level repair. This setup keeps the generation task, project structure, compile pipeline, and behavior verifier comparable, while varying the specification information available to the coder.
```

中文表述：

```text
为隔离 coder-facing specification form 的影响，我们从同一套完整 reference specs 中确定性派生三种 specification view。S1 暴露当前 translation unit 的局部 SpecFS-style view，包括 function-local contracts 和 raw header declarations；S2 保留与 S1 相同的局部函数语义内容，但额外暴露全局 module/file/function project graph；S3 使用未经降级的完整 SpecForge specs，并保持现有 SpecForge coder 的 prompt 形式和具体内容不变。对于 S1 和 S2，我们基于现有 coder 迁移代码生成设计，仅改变模型可见的 specification 信息形式。三组均使用 deterministic headers、deterministic main.c 和 deterministic Makefile，LLM 只参与 source-file generation 和 source-level repair。该设置使生成任务、项目结构、编译流程和行为验证器保持可比，同时系统性改变 coder 可见的 specification 信息。
```
