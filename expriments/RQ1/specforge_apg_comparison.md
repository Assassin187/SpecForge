# RQ1：SpecForge 与 APG 的 ICMP 对比实验原始数据

实验日期：2026-10-09（Asia/Shanghai）。本表汇总各方法三次运行，采用最新独立验收记录；SpecForge 第二次采用重新验收后的 `evaluation_003`。数值直接取自运行记录及验收报告，未重新生成协议代码。

协议为 **ICMPv4 / RFC 792（1981 年 9 月）**，覆盖 8 类消息、11 个 Type。共同模型配置：`deepseek-flash`，`base_url=https://api.deepseek.com`，`reasoning_effort=high`，`thinking=enabled`，`max_tokens=65536`，`stream=false`；使用相同模型传输设置。

判分范围为 **14 个共同协议场景、每阶段 27 个固定输入字节探针**。普通构建与 ASan/UBSan 构建分别验收；两阶段属于同一次生成的重复检查，不能当作两次独立生成样本。SpecForge 的自测程序契约与容量不足检查共两个额外场景不进入 APG 对比。

## 1. 每次运行的生成与验收结果

`1` 表示成功，`0` 表示失败，`NA` 表示未执行。场景/探针列均为通过数量，分母在表头中给出。端到端成功定义为生成完成且 14 个共同场景在两个阶段全部通过。

| 方法 | run_id | generation_success | normal_pass / 14 | sanitize_pass / 14 | wire_pass / 27（每阶段） | end_to_end_success |
| --- | --- | --- | --- | --- | --- | --- |
| APG | apg_01 | 1 | 5 | 5 | 13 | 0 |
| APG | apg_02 | 1 | 5 | 5 | 13 | 0 |
| APG | apg_03 | 1 | 5 | 5 | 13 | 0 |
| SpecForge | specforge_01 | 1 | 14 | 14 | 27 | 1 |
| SpecForge | specforge_02 | 1 | 14 | 14 | 27 | 1 |
| SpecForge | specforge_03 | 0 | NA | NA | NA | 0 |

SpecForge `specforge_01`、`specforge_02` 的完整套件均为普通/ASan/UBSan 各 **16/16**，包含上述两个额外场景。APG 的这两项为“不适用”，未计为失败。SpecForge `specforge_03` 生成失败，未执行独立验收，其场景列保留 `NA`；端到端指标因生成未完成记为 `0`。

## 2. 每次运行的时间与模型用量

时间单位为秒；tokens 与响应次数均为整数，不加千位分隔符，便于直接用于绘图。`total_tokens = input_tokens + output_tokens`。`response_count` 取运行记录的 `usage.requests`，表示已记录模型响应数；不代表所有底层 HTTP 请求或 SDK 重试次数。

| run_id | generation_seconds | model_seconds | response_count | input_tokens | output_tokens | total_tokens |
| --- | --- | --- | --- | --- | --- | --- |
| apg_01 | 120.934 | 120.837 | 3 | 12485 | 35025 | 47510 |
| apg_02 | 71.969 | 71.884 | 2 | 11980 | 23504 | 35484 |
| apg_03 | 79.959 | 79.906 | 2 | 12139 | 26941 | 39080 |
| specforge_01 | 1919.152 | 1879.307 | 299 | 10971543 | 469845 | 11441388 |
| specforge_02 | 2113.483 | 2071.882 | 400 | 14746762 | 515085 | 15261847 |
| specforge_03 | 2297.897 | 2271.608 | 377 | 14367568 | 585680 | 14953248 |

`generation_seconds` 仅计实际执行生成流程的时间：APG 取 `total_active_generation_seconds`；SpecForge 取生成 CLI 子进程的起止时间。两个计时入口包含的启动开销略有不同。均不计独立验收、验收器标定、重新验收及人工操作间隔；失败运行的已消耗时间保留。`model_seconds` 为所有已记录模型调用耗时之和。

| run_id | cache_hit_tokens | cache_miss_tokens | reasoning_tokens |
| --- | --- | --- | --- |
| apg_01 | 0 | 12485 | 26645 |
| apg_02 | 6656 | 5324 | 15871 |
| apg_03 | 6656 | 5483 | 19372 |
| specforge_01 | 9649408 | 1322135 | 184116 |
| specforge_02 | 13023616 | 1723146 | 197292 |
| specforge_03 | 12668544 | 1699024 | 213976 |

缓存命中/未命中 tokens 是输入 tokens 的拆分；reasoning tokens 是输出 tokens 的子集，不能再次加到总 tokens 中。本表没有按现行价格换算费用。

## 3. 共同协议场景的逐项原始数据

每列对应一次生成；`1=通过`、`0=失败`、`NA=未执行`。普通与 ASan/UBSan 的逐项结果完全相同，以下矩阵适用于两个阶段。

| scenario_id | apg_01 | apg_02 | apg_03 | specforge_01 | specforge_02 | specforge_03 |
| --- | --- | --- | --- | --- | --- | --- |
| icmp_echo_request_odd_data | 0 | 0 | 0 | 1 | 1 | NA |
| icmp_echo_request_even_data | 0 | 0 | 0 | 1 | 1 | NA |
| icmp_echo_request_empty_data | 0 | 0 | 0 | 1 | 1 | NA |
| icmp_echo_reply_preservation | 0 | 0 | 0 | 1 | 1 | NA |
| icmp_destination_unreachable_codes | 1 | 1 | 1 | 1 | 1 | NA |
| icmp_error_quotation_with_options | 1 | 1 | 1 | 1 | 1 | NA |
| icmp_time_exceeded_codes | 1 | 1 | 1 | 1 | 1 | NA |
| icmp_parameter_problem_pointer | 1 | 1 | 1 | 1 | 1 | NA |
| icmp_source_quench | 1 | 1 | 1 | 1 | 1 | NA |
| icmp_redirect_gateway | 0 | 0 | 0 | 1 | 1 | NA |
| icmp_timestamp_request | 0 | 0 | 0 | 1 | 1 | NA |
| icmp_timestamp_reply_preservation | 0 | 0 | 0 | 1 | 1 | NA |
| icmp_information_request_reply | 0 | 0 | 0 | 1 | 1 | NA |
| icmp_checksum_recomputation | 0 | 0 | 0 | 1 | 1 | NA |

## 4. 绘图用汇总指标

每种方法均以全部三次尝试统计生成成功率、端到端成功率和生成成本。“已执行验收的场景通过率”只对实际完成验收的运行统计，不将 `NA` 当作场景失败或通过。均值及标准差由上面的逐次数据计算，标准差采用样本标准差（`ddof=1`），保留三位小数。

| 方法 | 尝试次数 | 生成成功率 | 端到端成功率 | 已执行验收的场景通过率（每阶段） | 生成时间均值 ± SD (s) | 总 tokens 均值 ± SD |
| --- | --- | --- | --- | --- | --- | --- |
| APG | 3 | 3/3 (100.00%) | 0/3 (0.00%) | 15/42 (35.71%) | 90.954 ± 26.269 | 40691.333 ± 6172.800 |
| SpecForge | 3 | 2/3 (66.67%) | 2/3 (66.67%) | 28/28 (100.00%) | 2110.177 ± 189.394 | 13885494.333 ± 2122274.761 |

该场景汇总中 APG 的分母为 `3 × 14 = 42`，SpecForge 为 `2 × 14 = 28`。SpecForge 的 `100%` 是完成验收运行的条件通过率；其全部尝试的端到端成功率为 `2/3`。

## 5. 记录解释与对比边界

- **APG 首轮格式适配。** 原生解析器只接受带 JSON 代码围栏的回复，首次模型回复为有效裸 JSON，导致该次原生尝试生成不完整代码。修复解析器后复用首次分析回复，再调用一次代码生成。主表的首轮成本包括全部三次模型响应与两个实际执行区间：`85.044 + 35.890 = 120.934 s`、`47510 tokens`，未只采用格式适配后的 `35.890 s`。最终验收对象为该首轮适配后的完整生成项目；原生失败记录保留。
- **SpecForge 第二轮重新验收。** 原 `evaluation_002` 的 `13/16` 受头文件解析器未剥离 `extern "C" {` 影响。仅修复验收器接口发现逻辑，保持输入、期望字节和判分规则不变，重新标定后 `evaluation_003` 为两阶段各 `16/16`；生成项目与用量未修改，主表采用最新结果。第一轮验收也使用接口/编译适配副本。
- **SpecForge 第三轮生成失败。** 生成测试规格要求固定 Code 为 0 的接口处理非法 Code，但公开 API 未提供 Code 参数；代码阶段报告规格冲突并触发规格修复，后续规格评审达到 60 次响应上限后停止。运行记录为 `generation_passed=false`、`stop_reason=code_failed`，没有最终交付验收结果。
- **生成任务与辅助代码。** APG 按原生流程生成结构体及填充函数，并使用上游手写 `common.h`；SpecForge 额外生成独立校验和、可执行自测与容量检查。APG 的原始 `common.h` 校验和函数在三个锚定输入中有两个错误，因此 APG 的行为结果包含该上游辅助代码的影响，不能全部归因于模型生成。生成后均未人工修补协议代码。
- **API 判分约定。** 使用原独立验收器的共同输入、期望报文和场景判据，通过适配器调用各自 API。标量使用相同主机序哨兵值，字节数组保持原样。APG 的 `void` 填充函数没有返回报文长度，按测试输入确定的固定报文范围比对字节，不判其长度报告或容量契约；字节探针全部使用有效容量输入。
- **重复运行与计时条件。** 两种方法各有三次运行。第二、三轮的四个生成进程在同一批次并行启动；首轮未使用这一并行批次。缓存命中量也有差异，因此时间/成本表是本次实测记录，不是控制并发与缓存后的性能基准。这些结果是离线消息构造验收，不是 APG 论文的 ping 互通或 CSR 指标。

## 6. 原始记录索引

下列文件为各数据行的来源；旧验收报告保留，汇总仅使用表中指定的最新报告。

| run_id | 模型/用量/生成状态 | 验收来源 | 计时来源 |
| --- | --- | --- | --- |
| apg_01 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_01/run.json) | [最新验收报告](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_01/reports/independent/summary.json) | [生成计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_01/run.json) |
| apg_02 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_02/run.json) | [最新验收报告](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_02/reports/independent/summary.json) | [生成计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_02/run.json) |
| apg_03 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_03/run.json) | [最新验收报告](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_03/reports/independent/summary.json) | [生成计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_03/run.json) |
| specforge_01 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_01/run.json) | [最新验收报告](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_01/evaluation/evaluation_002/report.json) | [生成计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_01/reports/experiment/experiment.json) |
| specforge_02 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_02/run.json) | [最新验收报告](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_02/evaluation/evaluation_003/report.json) | [生成计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/background_20261009_112740/specforge_02.status.json) |
| specforge_03 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_03/run.json) | NA（生成失败，未验收） | [生成计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/background_20261009_112740/specforge_03.status.json) |

第二轮重新验收的详细证据：[reacceptance/record.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_02/reports/reacceptance/record.json)。第三轮失败证据：[代码阶段 result.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_03/logs/05_code/api/result.json)、[规格评审 result.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_03/logs/07_spec_review/api/result.json)。

RFC 792 的 SHA-256：`58714393ded142bacf188d7e8977eef98f4110c4c87ac94595f750df5664c2c6`。三次 SpecForge 的冻结 `framework_hashes` 与 `input_hashes` 分别完全一致；三次 APG 的 `source_hashes` 完全一致，具体逐文件哈希见各自 `run.json`。APG 上游提交为 `6642c58ccd533ce2a1bf4d2dfe33b8dc72016122`，本地分类模型路径为 `/home/ljf/models/all-MiniLM-L6-v2`，本次 ICMP 专用流程未调用分类模型。
