# MQTT 生成过程对照报告

报告日期：2026-10-12。以下仅比较 MQTT；paper 为三次运行的算术均值，Luna 为两次进入编码并完成生成的历史运行的算术均值。所有时间戳展示为 UTC+8。读取现有日志，不调用模型、不重建、不重新验收。

本次 NoSpec 生成及两种独立验收均通过；paper 三次均通过；Luna 两次均完成生成，其中一次普通验收 15/16，另一次普通验收 16/16，两次 Sanitizer 均为 16/16。总成本下降主要对应移除了 Facts、Design、Specs 和相关审查。只看 Coder，同模型 Luna 历史的平均用量低于本次 NoSpec。

逐响应明细：[完整 CSV（2,095 条完成响应 + 1 条 API 错误）](/home/ljf/SpecForge/expriments/RQ2/analysis/results/mqtt_generation_response_records_20261012.csv)。其中正式比较的六次运行包含 2,035 条完成响应，另有前置失败尝试的 60 条响应。每条明细保存输入/输出/缓存/推理 Token、模型调用耗时、工具调用、工具错误、命令及返回、原始 request/response/tools 路径。

## 1. 样本、条件与统计口径

| 标签 | 记录目录 | 模型 / 推理 | fresh / resumed / manual_edits |
| --- | --- | --- | --- |
| NoSpec 新实验 | [expriments/RQ2/runs/mqtt_gpt6_luna_max_20261012074057Z/mqtt](/home/ljf/SpecForge/expriments/RQ2/runs/mqtt_gpt6_luna_max_20261012074057Z/mqtt/run.json) | gpt-6-luna / max | 是 / 否 / 否 |
| paper round_01 | [runs/paper/round_01/mqtt_01](/home/ljf/SpecForge/runs/paper/round_01/mqtt_01/run.json) | deepseek-flash / high | 是 / 否 / 否 |
| paper round_02 | [runs/paper/round_02/mqtt_01](/home/ljf/SpecForge/runs/paper/round_02/mqtt_01/run.json) | deepseek-flash / high | 是 / 否 / 否 |
| paper round_03 | [runs/paper/round_03/mqtt_01](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/run.json) | deepseek-flash / high | 是 / 否 / 否 |
| Luna 历史① | [runs/gpt6_luna_max_mqtt/20261011T160342Z](/home/ljf/SpecForge/runs/gpt6_luna_max_mqtt/20261011T160342Z/run.json) | gpt-6-luna / max | 否 / 是 / 否 |
| Luna 历史② | [runs/gpt6_luna_max_rounds/20261012T032913Z/mqtt](/home/ljf/SpecForge/runs/gpt6_luna_max_rounds/20261012T032913Z/mqtt/run.json) | gpt-6-luna / max | 否 / 是 / 否 |

两次 Luna 主样本是 `20261011T160342Z` 和 `20261012T032913Z/mqtt`。`20261011T151634Z` 在规格阶段因内部字段错误停止，没有进入编码，单列于附录，不混入这两次均值。

总 Token = input + output；reasoning 是 output 子集，cache_hit 是 input 子集，均不再相加。调用数为完成并保存的模型响应数；工具调用数独立统计。模型调用耗时为各完成响应的 `usage.elapsed_seconds` 之和，包含该请求的服务等待及客户端开销，不是纯推理时间，也不等于控制器墙钟时间。无完成响应的 API 错误没有可核实的 Token/耗时，留空。SDK 内部重试未独立计数。

完整运行和失败的历史 job 均纳入累计；同一 job 续跑后的累计值只计一次，续跑前快照仅用于说明变化。paper 的实现阶段含 Coder 触发的规格回修及重审；本报告另外拆开纯 Coder 用量，避免混淆。所有均值先用原始值计算，再四舍五入；表中小数调用数表示均值。

三份 MQTT 输入在六次运行中完全相同；独立验收器哈希也完全相同：

| 工件 | SHA-256 |
| --- | --- |
| REQUIREMENTS.md | e326b9e32ffed8c6a8eaf447866a5cb66d2e4ed1d1c6fa788b2d384d082f9f2b |
| TASK.md | 508a2cc700406f3f6b6dc44cc00e9b92f81c07b6e92ae0c9d64e8225196faa06 |
| protocol.pdf | c45a1f4f3d0d9a66b3f890dce49a1e2ddeae69cfd67f907a5b6e97a6d64a0b54 |
| mqtt_check.py | e9b2d6d24b3b9944fa5c34679d0fd68272cae1394f3f9357c2c1118d104a975e |

预算由冻结的 `request_001.json` 中 `Bounded job budget` 提取，而非套用当前源码。

| 预算 | RQ2 NoSpec（n=1） | paper MQTT 均值（n=3） | Luna MQTT 均值（n=2） |
| --- | --- | --- | --- |
| 事实 / 设计 / 规格作者 | 移除 | 40 / 100 / 180 | 40 / 100 / 180 |
| 初次语义审查 | 移除 | 60 | 历史① 60→120；历史② 150 |
| 首次 Coder | 300 | 180 | 180 |
| 实现续修 | 40/轮，最多3轮（run.json） | 记录中的续修 job 为40/轮 | 实际未触发 |
| 实际规格回修 / 实现续修次数均值 | 0 / 0 | 0.33 / 0.67 | 0 / 0 |

框架版本不完全相同；RQ2 与 Luna 历史②的主框架哈希相同，RQ2 另有自身 adapter/coder 提示词和流程。版本及哈希详情保存在原始 run.json。paper 的底座及推理配置不同，两个 Luna 样本又有不同的审查预算和恢复过程；当前均值是描述性对照，不能单独形成消融因果结论。

## 2. MQTT 总体均值

| 指标 | RQ2 NoSpec（n=1） | paper MQTT 均值（n=3） | Luna MQTT 均值（n=2） |
| --- | --- | --- | --- |
| 输入 Token | 1,235,219 | 14,034,381 | 16,700,794.50 |
| 输出 Token | 46,703 | 653,181.33 | 440,244 |
| 总 Token | 1,281,922 | 14,687,562.33 | 17,141,038.50 |
| 推理 Token（已含于输出） | 28,429 | 288,269.67 | 260,205 |
| 缓存命中 Token（已含于输入） | 1,112,064 | 11,975,125.33 | 13,870,080 |
| 缓存未命中 Token | 123,155 | 2,059,255.67 | 2,830,714.50 |
| 完成模型响应 | 43 | 376.33 | 431.50 |
| 上下文重建 | 1 | 19.33 | 19.50 |
| 模型调用累计分钟 | 20.652 | 45.859 | 185.576 |
| 开发检查报告总数均值 | 2 | 6.67 | 1.50 |
| 开发检查未通过报告数均值 | 1 | 5.67 | 0.50 |
| 显式排队错误记录数均值 | 0 | 0 | 0 |
| 终止 API 错误记录数均值 | 0 | 0 | 0.50 |
| 生成通过 | 1/1 | 3/3 | 2/2 |
| 完整独立验收通过 | 1/1 | 3/3 | 1/2 |
| 普通验收场景通过均值 | 16/16 | 16/16 | 15.5/16 |
| Sanitizer 验收场景通过均值 | 16/16 | 16/16 | 16/16 |

相对 paper 均值，总 Token 减少 91.27%；相对 Luna 均值减少 92.52%。这些比例包含被移除的整条规格流程，以及 Luna 历史中已发生的重做成本。

## 3. 每个模型任务类别的完整用量

### RQ2 NoSpec（n=1）

| 任务类别 | 输入 | 输出 | 总 Token | 响应数 | 模型秒 | 重建上下文 |
| --- | --- | --- | --- | --- | --- | --- |
| 事实提取 | 0 | 0 | 0 | 0 | 0 | 0 |
| 结构设计 | 0 | 0 | 0 | 0 | 0 | 0 |
| 行为规格作者（含重做） | 0 | 0 | 0 | 0 | 0 | 0 |
| 初次语义审查（含失败与重做） | 0 | 0 | 0 | 0 | 0 | 0 |
| 首次 Coder | 1,235,219 | 46,703 | 1,281,922 | 43 | 1,239.106 | 1 |
| Coder 触发的规格回修 | 0 | 0 | 0 | 0 | 0 | 0 |
| 规格回修后重审 | 0 | 0 | 0 | 0 | 0 | 0 |
| Coder 实现续修 | 0 | 0 | 0 | 0 | 0 | 0 |

NoSpec 对被移除阶段以0显示预算/用量；其阶段状态为 skipped，不表示这些阶段已执行并通过。


### paper MQTT 均值（n=3）

| 任务类别 | 输入 | 输出 | 总 Token | 响应数 | 模型秒 | 重建上下文 |
| --- | --- | --- | --- | --- | --- | --- |
| 事实提取 | 799,896.333 | 29,252 | 829,148.333 | 23 | 122.031 | 1 |
| 结构设计 | 1,789,721 | 146,001.333 | 1,935,722.333 | 51.333 | 594.854 | 2.667 |
| 行为规格作者（含重做） | 4,358,660.667 | 277,668 | 4,636,328.667 | 109.667 | 1,126.462 | 6.667 |
| 初次语义审查（含失败与重做） | 1,952,252.667 | 55,654.667 | 2,007,907.333 | 49 | 261.968 | 2.667 |
| 首次 Coder | 3,563,101.333 | 93,396 | 3,656,497.333 | 100.333 | 406.270 | 4.333 |
| Coder 触发的规格回修 | 11,156 | 719.667 | 11,875.667 | 0.667 | 3.281 | 0 |
| 规格回修后重审 | 737,327 | 19,063.333 | 756,390.333 | 17.667 | 99.376 | 1 |
| Coder 实现续修 | 822,266 | 31,426.333 | 853,692.333 | 24.667 | 137.312 | 1 |



### Luna MQTT 均值（n=2）

| 任务类别 | 输入 | 输出 | 总 Token | 响应数 | 模型秒 | 重建上下文 |
| --- | --- | --- | --- | --- | --- | --- |
| 事实提取 | 783,026 | 15,624 | 798,650 | 25.500 | 432.515 | 1 |
| 结构设计 | 458,524 | 16,331.500 | 474,855.500 | 23 | 440.796 | 0.500 |
| 行为规格作者（含重做） | 6,552,819.500 | 178,950 | 6,731,769.500 | 171 | 4,523.597 | 8 |
| 初次语义审查（含失败与重做） | 7,798,369.500 | 192,166 | 7,990,535.500 | 180.500 | 4,839.593 | 9 |
| 首次 Coder | 1,108,055.500 | 37,172.500 | 1,145,228 | 31.500 | 898.066 | 1 |
| Coder 触发的规格回修 | 0 | 0 | 0 | 0 | 0 | 0 |
| 规格回修后重审 | 0 | 0 | 0 | 0 | 0 | 0 |
| Coder 实现续修 | 0 | 0 | 0 | 0 | 0 | 0 |



### 纯 Coder 对照

| 指标 | RQ2 NoSpec（n=1） | paper MQTT 均值（n=3） | Luna MQTT 均值（n=2） |
| --- | --- | --- | --- |
| Coder input+output | 1,281,922 | 4,510,189.667 | 1,145,228 |
| Coder 完成响应 | 43 | 125 | 31.500 |
| Coder 模型调用秒 | 1,239.106 | 543.582 | 898.066 |

NoSpec 纯 Coder 总 Token 比 Luna 历史均值多 11.94%，Coder 响应多 36.51%。整体便宜不能解释为编码本身更省。

## 4. 墙钟时间、恢复及不能合并的记录

| 阶段墙钟秒（run.json当前保存值） | NoSpec | paper均值 | Luna① | Luna② |
| --- | --- | --- | --- | --- |
| prepare | 1.400 | 1.496 | 1.413 | 1.464 |
| facts | 移除（skipped） | 122.312 | 506.725 | 358.934 |
| design | 移除（skipped） | 596.746 | 284.191 | 598.463 |
| specs | 移除（skipped） | 1,405.475 | 5,953.006 | 8,085.982 |
| code | 1,249.002 | 710.781 | 889.271 | 917.021 |
| verify | 0.006 | 0.012 | 0.006 | 0.006 |

NoSpec 各生成阶段计时合计 1,250.408 秒（20.840 分钟）；批次 `elapsed_seconds=1,256.618` 秒累计 CLI 活跃执行，包含独立验收及记录。paper 原始编排的生成墙钟均值 2,836.822 秒（47.280 分钟），协议总墙钟均值 2,845.406 秒。计时边界有上述小差异，不把模型计时冒充生成墙钟。

Luna① specs保存的5,953.006秒累加了初次执行及续跑的活跃阶段时间，排除了中间等待。Luna②恢复时覆盖了specs的起止时间及阶段墙钟，当前8,085.982秒仅对应恢复后的第二次规格作者+审查；首次作者+失败审查仍在jobs及日志中，模型计时4,713.897秒。Luna②全部规格任务模型计时为12,783.557秒。现有记录不足以恢复第一次规格阶段的精确控制器墙钟；不使用修改时间推算或填补。

下面的时间区间保留等待间隔，可用于观察运行过程，但不能比较活跃生成速度。

| 运行 | created_at（UTC+8） | verify.finished_at（UTC+8） | 跨度分钟 |
| --- | --- | --- | --- |
| NoSpec 新实验 | 2026-10-12 15:40:58 | 2026-10-12 16:01:48 | 20.840 |
| paper round_01 | 2026-10-03 00:03:51 | 2026-10-03 00:46:21 | 42.500 |
| paper round_02 | 2026-10-03 01:02:22 | 2026-10-03 01:48:02 | 45.671 |
| paper round_03 | 2026-10-07 13:47:02 | 2026-10-07 14:40:43 | 53.672 |
| Luna 历史① | 2026-10-12 00:03:42 | 2026-10-12 11:03:53 | 660.179 |
| Luna 历史② | 2026-10-12 11:29:13 | 2026-10-12 17:09:47 | 340.565 |

Luna①原始审查在60次上限失败，快照总响应186次；续跑同一个审查job，60→120，最终该job累计120次响应，随后Coder27次。续跑前后的审查用量必须相减得到增量，不能再把快照60次加到最终120次。

| 审查记录 | 输入 | 输出 | 总 Token | 响应 | 模型秒 | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| 上限60时 | 2,561,638 | 75,619 | 2,637,257 | 60 | 1,773.225 | response_limit |
| 续跑增量 | 2,709,651 | 80,096 | 2,789,747 | 60 | 1,817.979 | 新增响应 |
| 最终累计 | 5,271,289 | 155,715 | 5,427,004 | 120 | 3,591.204 | gate_passed |

续跑证据：[record.json](/home/ljf/SpecForge/runs/gpt6_luna_max_mqtt/20261011T160342Z/continuations/review_120_001/record.json)；原API记录校验为未修改。

Luna②首次审查完成91次响应后，第92次请求遇到502上游连接错误。恢复后新建05_specs（161次）及06_spec_review（150次）重新执行；先前03_specs（102次）与04_spec_review（91次）均计入总用量。因此规格作者总263次、审查总241次、规格流程总504次，全运行590次。证据：[api_error_092.json](/home/ljf/SpecForge/runs/gpt6_luna_max_rounds/20261012T032913Z/mqtt/logs/04_spec_review/api/api_error_092.json)、[mqtt.log](/home/ljf/SpecForge/runs/gpt6_luna_max_rounds/20261012T032913Z/mqtt.log)。

## 5. 各次运行的全部job、缓存及工具记录

### NoSpec 新实验

| job | 实际/预算响应 | 结果 | 输入 | 输出 | 总 Token | 模型秒 | 上下文重建 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 01_code | 43/300 | gate_passed | 1,235,219 | 46,703 | 1,281,922 | 1,239.106 | 1 |


| job | 缓存命中 | 缓存未命中 | 推理（输出子集） | 工具请求 | 工具error返回 | run_command非零/超时 |
| --- | --- | --- | --- | --- | --- | --- |
| 01_code | 1,112,064 | 123,155 | 28,429 | 93 | 1 | 3 |

工具名称及调用次数（完成响应提出的请求，包括被拒绝的请求）：

| job | 调用分布 |
| --- | --- |
| 01_code | check=1, edit_file=8, list_files=3, read_file=47, run_command=6, search=16, write_file=12 |

原始日志：[logs目录](/home/ljf/SpecForge/expriments/RQ2/runs/mqtt_gpt6_luna_max_20261012074057Z/mqtt/logs)。工具error返回、命令非零及开发检查未通过是不同统计，不等同于实现不一致事件，也不能相加作为修复次数。

### paper round_01

| job | 实际/预算响应 | 结果 | 输入 | 输出 | 总 Token | 模型秒 | 上下文重建 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 01_facts | 18/40 | gate_passed | 641,748 | 34,784 | 676,532 | 135.408 | 1 |
| 02_design | 47/100 | gate_passed | 1,529,870 | 143,325 | 1,673,195 | 581.171 | 3 |
| 03_specs | 105/180 | gate_passed | 4,096,461 | 233,703 | 4,330,164 | 962.201 | 6 |
| 04_spec_review | 60/60 | gate_passed | 2,615,711 | 85,887 | 2,701,598 | 374.846 | 3 |
| 05_code | 129/180 | gate_passed | 4,406,767 | 98,198 | 4,504,965 | 438.338 | 5 |


| job | 缓存命中 | 缓存未命中 | 推理（输出子集） | 工具请求 | 工具error返回 | run_command非零/超时 |
| --- | --- | --- | --- | --- | --- | --- |
| 01_facts | 488,448 | 153,300 | 17,921 | 51 | 0 | 0 |
| 02_design | 1,238,016 | 291,854 | 75,935 | 73 | 1 | 3 |
| 03_specs | 3,527,424 | 569,037 | 78,747 | 149 | 1 | 5 |
| 04_spec_review | 2,292,224 | 323,487 | 53,519 | 151 | 11 | 2 |
| 05_code | 3,859,840 | 546,927 | 44,956 | 184 | 8 | 0 |

工具名称及调用次数（完成响应提出的请求，包括被拒绝的请求）：

| job | 调用分布 |
| --- | --- |
| 01_facts | check=1, list_files=1, read_file=46, write_file=3 |
| 02_design | check=1, edit_file=7, list_files=2, read_file=33, run_command=10, search=1, write_file=19 |
| 03_specs | check=3, edit_file=4, list_files=1, read_file=30, run_command=92, write_file=19 |
| 04_spec_review | check=1, edit_file=33, list_files=1, read_file=89, run_command=24, write_file=3 |
| 05_code | bash=1, check=1, edit_file=11, list_files=3, read_file=116, run_command=29, search=6, write_file=17 |

原始日志：[logs目录](/home/ljf/SpecForge/runs/paper/round_01/mqtt_01/logs)。工具error返回、命令非零及开发检查未通过是不同统计，不等同于实现不一致事件，也不能相加作为修复次数。

### paper round_02

| job | 实际/预算响应 | 结果 | 输入 | 输出 | 总 Token | 模型秒 | 上下文重建 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 01_facts | 18/40 | gate_passed | 706,731 | 24,064 | 730,795 | 98.651 | 1 |
| 02_design | 59/100 | gate_passed | 2,015,152 | 166,480 | 2,181,632 | 676.965 | 3 |
| 03_specs | 112/180 | gate_passed | 4,513,225 | 290,616 | 4,803,841 | 1,145.631 | 7 |
| 04_spec_review | 36/60 | gate_passed | 1,254,550 | 34,455 | 1,289,005 | 165.245 | 2 |
| 05_code | 130/180 | gate_passed | 4,813,074 | 142,249 | 4,955,323 | 589.353 | 6 |


| job | 缓存命中 | 缓存未命中 | 推理（输出子集） | 工具请求 | 工具error返回 | run_command非零/超时 |
| --- | --- | --- | --- | --- | --- | --- |
| 01_facts | 565,888 | 140,843 | 9,020 | 55 | 0 | 0 |
| 02_design | 1,731,072 | 284,080 | 85,248 | 75 | 3 | 0 |
| 03_specs | 3,874,304 | 638,921 | 100,062 | 184 | 11 | 2 |
| 04_spec_review | 1,011,968 | 242,582 | 19,642 | 73 | 4 | 0 |
| 05_code | 4,146,432 | 666,642 | 67,117 | 239 | 5 | 1 |

工具名称及调用次数（完成响应提出的请求，包括被拒绝的请求）：

| job | 调用分布 |
| --- | --- |
| 01_facts | check=1, list_files=1, read_file=42, search=8, write_file=3 |
| 02_design | check=3, edit_file=1, list_files=1, read_file=34, run_command=16, write_file=20 |
| 03_specs | check=3, edit_file=7, list_files=3, read_file=63, run_command=38, write_file=70 |
| 04_spec_review | check=1, edit_file=4, list_files=2, read_file=55, run_command=9, write_file=2 |
| 05_code | check=1, edit_file=17, list_files=8, read_file=170, run_command=21, search=3, write_file=19 |

原始日志：[logs目录](/home/ljf/SpecForge/runs/paper/round_02/mqtt_01/logs)。工具error返回、命令非零及开发检查未通过是不同统计，不等同于实现不一致事件，也不能相加作为修复次数。

### paper round_03

| job | 实际/预算响应 | 结果 | 输入 | 输出 | 总 Token | 模型秒 | 上下文重建 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 01_facts | 33/40 | gate_passed | 1,051,210 | 28,908 | 1,080,118 | 132.034 | 1 |
| 02_design | 48/100 | gate_passed | 1,824,141 | 128,199 | 1,952,340 | 526.425 | 2 |
| 03_specs | 112/180 | gate_passed | 4,466,296 | 308,685 | 4,774,981 | 1,271.555 | 7 |
| 04_spec_review | 51/60 | gate_passed | 1,986,497 | 46,622 | 2,033,119 | 245.812 | 3 |
| 05_code | 42/180 | spec_gap | 1,469,463 | 39,741 | 1,509,204 | 191.119 | 2 |
| 06_spec_repair | 2/180 | gate_passed | 33,468 | 2,159 | 35,627 | 9.842 | 0 |
| 07_spec_review | 53/60 | gate_passed | 2,211,981 | 57,190 | 2,269,171 | 298.127 | 3 |
| 08_repair | 40/40 | response_limit | 1,560,904 | 71,977 | 1,632,881 | 294.478 | 2 |
| 09_repair | 34/40 | gate_passed | 905,894 | 22,302 | 928,196 | 117.459 | 1 |


| job | 缓存命中 | 缓存未命中 | 推理（输出子集） | 工具请求 | 工具error返回 | run_command非零/超时 |
| --- | --- | --- | --- | --- | --- | --- |
| 01_facts | 919,936 | 131,274 | 10,910 | 78 | 0 | 1 |
| 02_design | 1,646,848 | 177,293 | 59,956 | 70 | 2 | 2 |
| 03_specs | 3,758,336 | 707,960 | 111,402 | 235 | 4 | 5 |
| 04_spec_review | 1,652,480 | 334,017 | 26,719 | 102 | 4 | 0 |
| 05_code | 1,200,640 | 268,823 | 23,154 | 90 | 7 | 0 |
| 06_spec_repair | 22,400 | 11,068 | 1,870 | 5 | 0 | 0 |
| 07_spec_review | 1,886,464 | 325,517 | 32,867 | 96 | 1 | 1 |
| 08_repair | 1,279,232 | 281,672 | 34,026 | 152 | 9 | 0 |
| 09_repair | 823,424 | 82,470 | 11,738 | 43 | 1 | 1 |

工具名称及调用次数（完成响应提出的请求，包括被拒绝的请求）：

| job | 调用分布 |
| --- | --- |
| 01_facts | check=1, edit_file=6, read_file=59, run_command=4, search=5, write_file=3 |
| 02_design | check=1, edit_file=13, list_files=4, read_file=19, run_command=13, write_file=20 |
| 03_specs | check=3, edit_file=2, list_files=2, read_file=79, run_command=60, write_file=89 |
| 04_spec_review | check=1, edit_file=8, list_files=2, read_file=72, run_command=15, search=1, write_file=3 |
| 05_code | edit_file=1, list_files=5, read_file=68, report_spec_gap=2, run_command=10, write_file=4 |
| 06_spec_repair | check=1, read_file=4 |
| 07_spec_review | check=1, list_files=2, read_file=57, run_command=31, write_file=5 |
| 08_repair | edit_file=4, list_files=2, read_file=125, run_command=11, write_file=10 |
| 09_repair | check=1, edit_file=4, list_files=2, read_file=9, run_command=21, write_file=6 |

原始日志：[logs目录](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/logs)。工具error返回、命令非零及开发检查未通过是不同统计，不等同于实现不一致事件，也不能相加作为修复次数。

### Luna 历史①

| job | 实际/预算响应 | 结果 | 输入 | 输出 | 总 Token | 模型秒 | 上下文重建 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 01_facts | 30/40 | gate_passed | 800,242 | 17,120 | 817,362 | 506.323 | 1 |
| 02_design | 17/100 | gate_passed | 283,871 | 10,734 | 294,605 | 283.727 | 0 |
| 03_specs | 79/180 | gate_passed | 2,753,285 | 98,948 | 2,852,233 | 2,351.619 | 3 |
| 04_spec_review | 120/120 | gate_passed | 5,271,289 | 155,715 | 5,427,004 | 3,591.204 | 7 |
| 05_code | 27/180 | gate_passed | 1,018,592 | 37,171 | 1,055,763 | 883.133 | 1 |


| job | 缓存命中 | 缓存未命中 | 推理（输出子集） | 工具请求 | 工具error返回 | run_command非零/超时 |
| --- | --- | --- | --- | --- | --- | --- |
| 01_facts | 620,032 | 180,210 | 6,213 | 104 | 1 | 1 |
| 02_design | 256,512 | 27,359 | 5,784 | 19 | 0 | 0 |
| 03_specs | 2,257,920 | 495,365 | 54,200 | 171 | 13 | 0 |
| 04_spec_review | 4,172,288 | 1,099,001 | 117,521 | 373 | 18 | 7 |
| 05_code | 886,784 | 131,808 | 18,538 | 59 | 8 | 2 |

工具名称及调用次数（完成响应提出的请求，包括被拒绝的请求）：

| job | 调用分布 |
| --- | --- |
| 01_facts | check=1, edit_file=2, read_file=78, run_command=2, search=18, write_file=3 |
| 02_design | check=1, read_file=6, run_command=4, write_file=8 |
| 03_specs | check=4, edit_file=22, list_files=3, read_file=105, run_command=16, write_file=21 |
| 04_spec_review | edit_file=2, list_files=12, read_file=303, run_command=47, write_file=9 |
| 05_code | check=1, edit_file=5, list_files=2, read_file=33, run_command=6, write_file=12 |

原始日志：[logs目录](/home/ljf/SpecForge/runs/gpt6_luna_max_mqtt/20261011T160342Z/logs)。工具error返回、命令非零及开发检查未通过是不同统计，不等同于实现不一致事件，也不能相加作为修复次数。

### Luna 历史②

| job | 实际/预算响应 | 结果 | 输入 | 输出 | 总 Token | 模型秒 | 上下文重建 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 01_facts | 21/40 | gate_passed | 765,810 | 14,128 | 779,938 | 358.707 | 1 |
| 02_design | 29/100 | gate_passed | 633,177 | 21,929 | 655,106 | 597.866 | 1 |
| 03_specs | 102/180 | gate_passed | 3,684,972 | 97,349 | 3,782,321 | 2,465.458 | 4 |
| 04_spec_review | 91/150 | api_error | 3,696,863 | 92,913 | 3,789,776 | 2,248.439 | 5 |
| 05_specs | 161/180 | gate_passed | 6,667,382 | 161,603 | 6,828,985 | 4,230.116 | 9 |
| 06_spec_review | 150/150 | gate_passed | 6,628,587 | 135,704 | 6,764,291 | 3,839.544 | 6 |
| 07_code | 36/180 | gate_passed | 1,197,519 | 37,174 | 1,234,693 | 912.998 | 1 |


| job | 缓存命中 | 缓存未命中 | 推理（输出子集） | 工具请求 | 工具error返回 | run_command非零/超时 |
| --- | --- | --- | --- | --- | --- | --- |
| 01_facts | 623,616 | 142,194 | 6,152 | 70 | 0 | 0 |
| 02_design | 533,504 | 99,673 | 12,929 | 37 | 1 | 0 |
| 03_specs | 3,208,704 | 476,268 | 46,061 | 248 | 18 | 0 |
| 04_spec_review | 3,049,472 | 647,391 | 65,100 | 237 | 11 | 6 |
| 05_specs | 5,382,144 | 1,285,238 | 107,844 | 392 | 13 | 9 |
| 06_spec_review | 5,722,112 | 906,475 | 66,989 | 329 | 10 | 16 |
| 07_code | 1,027,072 | 170,447 | 13,079 | 88 | 4 | 2 |

工具名称及调用次数（完成响应提出的请求，包括被拒绝的请求）：

| job | 调用分布 |
| --- | --- |
| 01_facts | check=1, edit_file=2, list_files=1, read_file=54, search=9, write_file=3 |
| 02_design | check=2, edit_file=1, list_files=4, read_file=14, run_command=5, write_file=11 |
| 03_specs | check=2, edit_file=29, list_files=5, read_file=162, run_command=23, search=2, write_file=25 |
| 04_spec_review | edit_file=9, list_files=6, read_file=176, run_command=37, search=3, write_file=6 |
| 05_specs | check=1, edit_file=8, list_files=11, read_file=264, run_command=96, search=3, write_file=9 |
| 06_spec_review | edit_file=19, list_files=8, read_file=237, run_command=55, search=3, write_file=7 |
| 07_code | check=1, edit_file=8, list_files=3, read_file=60, run_command=6, write_file=10 |

原始日志：[logs目录](/home/ljf/SpecForge/runs/gpt6_luna_max_rounds/20261012T032913Z/mqtt/logs)。工具error返回、命令非零及开发检查未通过是不同统计，不等同于实现不一致事件，也不能相加作为修复次数。

## 6. 开发验证、具体失败与修复

NoSpec 首次Coder内部完成了构建及测试修正，外层 implementation_repairs=0。记录：response16未使用参数在-Werror下使构建失败；response17调整，response19构建通过；response24发现bad_flags测试断言错误，response27改0x82为0x80；response28再发现单层+与topic /的测试预期错误，response31修正；response32协议单测通过；response37完整开发测试通过，response43完成关卡。另有CONNECT字段偏移及匹配算法编辑，完整编辑证据在CSV对应的原始响应路径。

Luna① Coder的具体记录：response6的五次JSON字段选择失败；response11临时查看脚本KeyError；response15两处&&/||括号警告导致构建失败，response16修正、17构建通过；response18三次公共只读头文件写入被拒绝；response21开发测试通过，response26完整构建/自测通过，27关卡通过。独立普通验收随后出现退出处理失败，未反馈给Coder。

Luna② Coder的具体记录：response4尝试覆盖公共只读头文件被拒绝；response7编辑工具参数错误；response12发现CONNECT flags掩码恒真，response13批量替换因old出现两次被拒绝，response14分开修正、16编译通过；response17 JSON字段选择失败，response28读取行数超限；response34完整构建/自测通过，response36关卡通过。最终两种独立验收均通过。

paper第三次：05_code因两项规格内部冲突报告spec_gap，06_spec_repair及07_spec_review修订并重审；08_repair达到40次响应上限；09_repair继续34次后通过。这是一次规格回修、两次实现续修，不能把其内部每次失败重复计为一轮修复。

### NoSpec 新实验 开发检查全记录

| 检查 | 结果 | 普通构建 | Sanitizer构建 | 恢复交付构建 | 普通开发测试 | Sanitizer开发测试 | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [verify_001](/home/ljf/SpecForge/expriments/RQ2/runs/mqtt_gpt6_luna_max_20261012074057Z/mqtt/reports/verify_001/report.json) | 未通过 | exit=0; 0.415s | exit=0; 0.716s | exit=0; 0.415s | failed; tests=0; 0.315s | failed; tests=0; 0.315s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/SpecForge/expriments/RQ2/runs/mqtt_gpt6_luna_max_20261012074057Z/mqtt/project/delivery.json'; normal development tests failed; sanitize development tests failed |
| [verify_002](/home/ljf/SpecForge/expriments/RQ2/runs/mqtt_gpt6_luna_max_20261012074057Z/mqtt/reports/verify_002/report.json) | 通过 | exit=0; 0.415s | exit=0; 0.766s | exit=0; 0.416s | passed; tests=2; 1.117s | passed; tests=2; 1.167s | 无 |


### paper round_01 开发检查全记录

| 检查 | 结果 | 普通构建 | Sanitizer构建 | 恢复交付构建 | 普通开发测试 | Sanitizer开发测试 | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [verify_001](/home/ljf/SpecForge/runs/paper/round_01/mqtt_01/reports/verify_001/report.json) | 未通过 | exit=2; 0.033s | exit=2; 0.033s | exit=2; 0.033s | not_executed; tests=未记录; 未记录s | not_executed; tests=未记录; 未记录s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/SpecForge/runs/stability/20261002T160137Z_two_rounds/round_01/mqtt_01/project/delivery.json'; normal clean build failed: /logs/command_0001.log; sanitize clean build failed: /logs/command_0002.log; Final delivery build failed |
| [verify_002](/home/ljf/SpecForge/runs/paper/round_01/mqtt_01/reports/verify_002/report.json) | 未通过 | exit=2; 0.033s | exit=2; 0.033s | exit=2; 0.033s | not_executed; tests=未记录; 未记录s | not_executed; tests=未记录; 未记录s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/SpecForge/runs/stability/20261002T160137Z_two_rounds/round_01/mqtt_01/project/delivery.json'; normal clean build failed: /logs/command_0001.log; sanitize clean build failed: /logs/command_0002.log; Final delivery build failed |
| [verify_003](/home/ljf/SpecForge/runs/paper/round_01/mqtt_01/reports/verify_003/report.json) | 未通过 | exit=2; 0.033s | exit=2; 0.033s | exit=2; 0.033s | not_executed; tests=未记录; 未记录s | not_executed; tests=未记录; 未记录s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/SpecForge/runs/stability/20261002T160137Z_two_rounds/round_01/mqtt_01/project/delivery.json'; normal clean build failed: /logs/command_0001.log; sanitize clean build failed: /logs/command_0002.log; Final delivery build failed |
| [verify_004](/home/ljf/SpecForge/runs/paper/round_01/mqtt_01/reports/verify_004/report.json) | 未通过 | exit=0; 0.365s | exit=0; 0.666s | exit=0; 0.365s | failed; tests=0; 0.366s | failed; tests=0; 0.616s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/SpecForge/runs/stability/20261002T160137Z_two_rounds/round_01/mqtt_01/project/delivery.json'; normal development tests failed; sanitize development tests failed |
| [verify_005](/home/ljf/SpecForge/runs/paper/round_01/mqtt_01/reports/verify_005/report.json) | 通过 | exit=0; 0.365s | exit=0; 0.667s | exit=0; 0.365s | passed; tests=2; 2.569s | passed; tests=2; 3.120s | 无 |


### paper round_02 开发检查全记录

| 检查 | 结果 | 普通构建 | Sanitizer构建 | 恢复交付构建 | 普通开发测试 | Sanitizer开发测试 | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [verify_001](/home/ljf/SpecForge/runs/paper/round_02/mqtt_01/reports/verify_001/report.json) | 未通过 | exit=2; 0.033s | exit=2; 0.034s | exit=2; 0.034s | not_executed; tests=未记录; 未记录s | not_executed; tests=未记录; 未记录s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/SpecForge/runs/stability/20261002T160137Z_two_rounds/round_02/mqtt_01/project/delivery.json'; normal clean build failed: /logs/command_0001.log; sanitize clean build failed: /logs/command_0002.log; Final delivery build failed |
| [verify_002](/home/ljf/SpecForge/runs/paper/round_02/mqtt_01/reports/verify_002/report.json) | 未通过 | exit=2; 0.033s | exit=2; 0.033s | exit=2; 0.033s | not_executed; tests=未记录; 未记录s | not_executed; tests=未记录; 未记录s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/SpecForge/runs/stability/20261002T160137Z_two_rounds/round_02/mqtt_01/project/delivery.json'; normal clean build failed: /logs/command_0001.log; sanitize clean build failed: /logs/command_0002.log; Final delivery build failed |
| [verify_003](/home/ljf/SpecForge/runs/paper/round_02/mqtt_01/reports/verify_003/report.json) | 未通过 | exit=0; 0.415s | exit=0; 0.716s | exit=0; 0.416s | failed; tests=0; 0.033s | failed; tests=0; 0.033s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/SpecForge/runs/stability/20261002T160137Z_two_rounds/round_02/mqtt_01/project/delivery.json'; normal development tests failed; sanitize development tests failed |
| [verify_004](/home/ljf/SpecForge/runs/paper/round_02/mqtt_01/reports/verify_004/report.json) | 未通过 | exit=0; 0.415s | exit=0; 0.716s | exit=0; 0.416s | failed; tests=0; 0.166s | failed; tests=0; 0.216s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/SpecForge/runs/stability/20261002T160137Z_two_rounds/round_02/mqtt_01/project/delivery.json'; normal development tests failed; sanitize development tests failed |
| [verify_005](/home/ljf/SpecForge/runs/paper/round_02/mqtt_01/reports/verify_005/report.json) | 未通过 | exit=0; 0.416s | exit=0; 0.716s | exit=0; 0.416s | failed; tests=0; 3.721s | failed; tests=0; 4.171s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/SpecForge/runs/stability/20261002T160137Z_two_rounds/round_02/mqtt_01/project/delivery.json'; normal development tests failed; sanitize development tests failed |
| [verify_006](/home/ljf/SpecForge/runs/paper/round_02/mqtt_01/reports/verify_006/report.json) | 通过 | exit=0; 0.416s | exit=0; 0.717s | exit=0; 0.416s | passed; tests=16; 3.821s | passed; tests=16; 4.371s | 无 |


### paper round_03 开发检查全记录

| 检查 | 结果 | 普通构建 | Sanitizer构建 | 恢复交付构建 | 普通开发测试 | Sanitizer开发测试 | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [verify_001](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/reports/verify_001/report.json) | 未通过 | exit=2; 0.033s | exit=2; 0.033s | exit=2; 0.034s | not_executed; tests=未记录; 未记录s | not_executed; tests=未记录; 未记录s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/.codex/worktrees/2851/SpecForge-Re/runs/validation/20261007T053821Z_cleanup_one_round/round_01/mqtt_01/project/delivery.json'; normal clean build failed: /logs/command_0001.log; sanitize clean build failed: /logs/command_0002.log; Final delivery build failed |
| [verify_002](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/reports/verify_002/report.json) | 未通过 | exit=2; 0.033s | exit=2; 0.033s | exit=2; 0.034s | not_executed; tests=未记录; 未记录s | not_executed; tests=未记录; 未记录s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/.codex/worktrees/2851/SpecForge-Re/runs/validation/20261007T053821Z_cleanup_one_round/round_01/mqtt_01/project/delivery.json'; normal clean build failed: /logs/command_0001.log; sanitize clean build failed: /logs/command_0002.log; Final delivery build failed |
| [verify_003](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/reports/verify_003/report.json) | 未通过 | exit=2; 0.033s | exit=2; 0.033s | exit=2; 0.034s | not_executed; tests=未记录; 未记录s | not_executed; tests=未记录; 未记录s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/.codex/worktrees/2851/SpecForge-Re/runs/validation/20261007T053821Z_cleanup_one_round/round_01/mqtt_01/project/delivery.json'; normal clean build failed: /logs/command_0001.log; sanitize clean build failed: /logs/command_0002.log; Final delivery build failed |
| [verify_004](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/reports/verify_004/report.json) | 未通过 | exit=2; 0.033s | exit=2; 0.034s | exit=2; 0.034s | not_executed; tests=未记录; 未记录s | not_executed; tests=未记录; 未记录s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/.codex/worktrees/2851/SpecForge-Re/runs/validation/20261007T053821Z_cleanup_one_round/round_01/mqtt_01/project/delivery.json'; normal clean build failed: /logs/command_0001.log; sanitize clean build failed: /logs/command_0002.log; Final delivery build failed |
| [verify_005](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/reports/verify_005/report.json) | 未通过 | exit=2; 0.033s | exit=2; 0.034s | exit=2; 0.034s | not_executed; tests=未记录; 未记录s | not_executed; tests=未记录; 未记录s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/.codex/worktrees/2851/SpecForge-Re/runs/validation/20261007T053821Z_cleanup_one_round/round_01/mqtt_01/project/delivery.json'; normal clean build failed: /logs/command_0001.log; sanitize clean build failed: /logs/command_0002.log; Final delivery build failed |
| [verify_006](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/reports/verify_006/report.json) | 未通过 | exit=0; 0.966s | exit=0; 1.367s | exit=0; 0.916s | passed; tests=0; 4.322s | failed; tests=0; 0.316s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/.codex/worktrees/2851/SpecForge-Re/runs/validation/20261007T053821Z_cleanup_one_round/round_01/mqtt_01/project/delivery.json'; sanitize development tests failed |
| [verify_007](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/reports/verify_007/report.json) | 未通过 | exit=0; 0.916s | exit=0; 1.318s | exit=0; 0.916s | passed; tests=0; 4.471s | passed; tests=0; 4.722s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/.codex/worktrees/2851/SpecForge-Re/runs/validation/20261007T053821Z_cleanup_one_round/round_01/mqtt_01/project/delivery.json' |
| [verify_008](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/reports/verify_008/report.json) | 未通过 | exit=0; 0.916s | exit=0; 1.368s | exit=0; 0.917s | passed; tests=0; 4.472s | passed; tests=0; 4.721s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/.codex/worktrees/2851/SpecForge-Re/runs/validation/20261007T053821Z_cleanup_one_round/round_01/mqtt_01/project/delivery.json' |
| [verify_009](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/reports/verify_009/report.json) | 通过 | exit=0; 0.966s | exit=0; 1.218s | exit=0; 0.967s | passed; tests=12; 4.472s | passed; tests=12; 4.721s | 无 |


### Luna 历史① 开发检查全记录

| 检查 | 结果 | 普通构建 | Sanitizer构建 | 恢复交付构建 | 普通开发测试 | Sanitizer开发测试 | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [verify_001](/home/ljf/SpecForge/runs/gpt6_luna_max_mqtt/20261011T160342Z/reports/verify_001/report.json) | 通过 | exit=0; 0.215s | exit=0; 0.816s | exit=0; 0.215s | passed; tests=1; 0.916s | passed; tests=1; 0.966s | 无 |


### Luna 历史② 开发检查全记录

| 检查 | 结果 | 普通构建 | Sanitizer构建 | 恢复交付构建 | 普通开发测试 | Sanitizer开发测试 | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [verify_001](/home/ljf/SpecForge/runs/gpt6_luna_max_rounds/20261012T032913Z/mqtt/reports/verify_001/report.json) | 未通过 | exit=2; 0.032s | exit=2; 0.033s | exit=2; 0.033s | not_executed; tests=未记录; 未记录s | not_executed; tests=未记录; 未记录s | Missing or invalid delivery.json: [Errno 2] No such file or directory: '/home/ljf/SpecForge/runs/gpt6_luna_max_rounds/20261012T032913Z/mqtt/project/delivery.json'; normal clean build failed: /logs/command_0001.log; sanitize clean build failed: /logs/command_0002.log; Final delivery build failed |
| [verify_002](/home/ljf/SpecForge/runs/gpt6_luna_max_rounds/20261012T032913Z/mqtt/reports/verify_002/report.json) | 通过 | exit=0; 0.265s | exit=0; 0.916s | exit=0; 0.265s | passed; tests=12; 0.416s | passed; tests=12; 0.516s | 无 |


开发tests数是交付清单测试条目数，不是断言数或独立场景数；清单尚未写入时可能为0或未记录。开发检查中的缺文件/缺Makefile、被拒绝的工具操作及普通编译警告均不自动计为组件矛盾。最终verify阶段复用同一项目哈希对应的开发报告，因此约0.006秒并不表示只在0.006秒内完成全部构建测试。

## 7. 独立验收的全部具体数据

| 运行 | 普通构建秒 / exit | Sanitizer构建秒 / exit | 普通场景秒 / 通过 | Sanitizer场景秒 / 通过 | 完整通过 |
| --- | --- | --- | --- | --- | --- |
| NoSpec 新实验 | 0.415 / 0 | 0.715 / 0 | 2.324 / 16/16 | 2.497 / 16/16 | 是 |
| paper round_01 | 0.365 / 0 | 0.665 / 0 | 2.020 / 16/16 | 2.230 / 16/16 | 是 |
| paper round_02 | 0.415 / 0 | 0.715 / 0 | 2.324 / 16/16 | 2.507 / 16/16 | 是 |
| paper round_03 | 0.916 / 0 | 1.316 / 0 | 2.320 / 16/16 | 2.539 / 16/16 | 是 |
| Luna 历史① | 0.215 / 0 | 0.866 / 0 | 6.326 / 15/16 | 2.472 / 16/16 | 否 |
| Luna 历史② | 0.265 / 0 | 0.915 / 0 | 2.316 / 16/16 | 2.479 / 16/16 | 是 |

场景单元格为“通过比例；平均场景秒”。paper及Luna均值先按运行计算；普通与Sanitizer分别列出，不把两种执行当作两次独立生成。

### 普通

| 场景 | RQ2 NoSpec（n=1） | paper MQTT 均值（n=3） | Luna MQTT 均值（n=2） |
| --- | --- | --- | --- |
| mqtt_cross_client_pubsub | 1/1; 0.045 | 3/3; 0.032 | 2/2; 0.045 |
| mqtt_buffer_before_eof | 1/1; 0.064 | 3/3; 0.050 | 1/2; 2.063 |
| mqtt_malformed_survival | 1/1; 0.064 | 3/3; 0.051 | 2/2; 0.065 |
| mqtt_disconnect_handler | 1/1; 0.064 | 3/3; 0.051 | 2/2; 0.064 |
| mqtt_smoke_test | 1/1; 0.187 | 3/3; 0.187 | 2/2; 0.187 |
| mqtt_mosquitto_interop | 1/1; 0.095 | 3/3; 0.073 | 2/2; 0.096 |
| mqtt_connect_semantics | 1/1; 0.024 | 3/3; 0.024 | 2/2; 0.024 |
| mqtt_suback_contract | 1/1; 0.065 | 3/3; 0.064 | 2/2; 0.064 |
| mqtt_exact_binary_payload | 1/1; 0.144 | 3/3; 0.144 | 2/2; 0.144 |
| mqtt_plus_filter_boundaries | 1/1; 0.305 | 3/3; 0.305 | 2/2; 0.305 |
| mqtt_hash_filter_boundaries | 1/1; 0.185 | 3/3; 0.185 | 2/2; 0.184 |
| mqtt_fragmented_input | 1/1; 0.745 | 3/3; 0.745 | 2/2; 0.744 |
| mqtt_coalesced_input | 1/1; 0.064 | 3/3; 0.051 | 2/2; 0.064 |
| mqtt_ping_exchange | 1/1; 0.064 | 3/3; 0.051 | 2/2; 0.064 |
| mqtt_disconnect_subscription_cleanup | 1/1; 0.144 | 3/3; 0.144 | 2/2; 0.144 |
| mqtt_invalid_packet_connection_isolation | 1/1; 0.065 | 3/3; 0.065 | 2/2; 0.065 |


### ASan+UBSan

| 场景 | RQ2 NoSpec（n=1） | paper MQTT 均值（n=3） | Luna MQTT 均值（n=2） |
| --- | --- | --- | --- |
| mqtt_cross_client_pubsub | 1/1; 0.072 | 3/3; 0.068 | 2/2; 0.076 |
| mqtt_buffer_before_eof | 1/1; 0.078 | 3/3; 0.058 | 2/2; 0.070 |
| mqtt_malformed_survival | 1/1; 0.079 | 3/3; 0.063 | 2/2; 0.074 |
| mqtt_disconnect_handler | 1/1; 0.070 | 3/3; 0.060 | 2/2; 0.074 |
| mqtt_smoke_test | 1/1; 0.193 | 3/3; 0.200 | 2/2; 0.198 |
| mqtt_mosquitto_interop | 1/1; 0.101 | 3/3; 0.082 | 2/2; 0.103 |
| mqtt_connect_semantics | 1/1; 0.038 | 3/3; 0.030 | 2/2; 0.030 |
| mqtt_suback_contract | 1/1; 0.070 | 3/3; 0.077 | 2/2; 0.070 |
| mqtt_exact_binary_payload | 1/1; 0.150 | 3/3; 0.159 | 2/2; 0.154 |
| mqtt_plus_filter_boundaries | 1/1; 0.319 | 3/3; 0.319 | 2/2; 0.319 |
| mqtt_hash_filter_boundaries | 1/1; 0.199 | 3/3; 0.197 | 2/2; 0.191 |
| mqtt_fragmented_input | 1/1; 0.751 | 3/3; 0.756 | 2/2; 0.751 |
| mqtt_coalesced_input | 1/1; 0.070 | 3/3; 0.060 | 2/2; 0.071 |
| mqtt_ping_exchange | 1/1; 0.078 | 3/3; 0.063 | 2/2; 0.070 |
| mqtt_disconnect_subscription_cleanup | 1/1; 0.158 | 3/3; 0.157 | 2/2; 0.154 |
| mqtt_invalid_packet_connection_isolation | 1/1; 0.071 | 3/3; 0.075 | 2/2; 0.071 |


Luna①唯一失败：普通模式mqtt_buffer_before_eof，耗时4.063秒，原始错误为`AssertionError: Broker failed to terminate gracefully`；Sanitizer对应场景通过。这是该冻结交付的一次验收观察，不能仅凭此确认失败的根因或稳定复现率。证据：[report.json](/home/ljf/SpecForge/runs/gpt6_luna_max_mqtt/20261011T160342Z/evaluation/evaluation_001/report.json)。

## 8. 生成工件规模与已有一致性标注

| 工件规模（最终工件，含空行/注释） | RQ2 NoSpec（n=1） | paper MQTT 均值（n=3） | Luna MQTT 均值（n=2） |
| --- | --- | --- | --- |
| 协议事实条数 | 0 | 54 | 20.50 |
| 模块数 | 0 | 7.33 | 4 |
| 文件规格数 | 0 | 7.33 | 4 |
| 函数规格数 | 0 | 64.33 | 16 |
| 规格内公共头文件数 | 0 | 6.67 | 3 |
| 实现.c文件数（不含tests） | 3 | 7.33 | 4 |
| 实现.c行数 | 910 | 2,251.33 | 1,210.50 |
| .h文件数 | 2 | 6.67 | 3 |
| .h行数 | 41 | 731 | 62 |
| 交付清单文件数 | 9 | 18.67 | 10.50 |
| 交付清单测试条目数 | 2 | 10 | 6.50 |
| 清单需求ID数 | 12 | 12 | 12 |

NoSpec中事实/模块/规格计数为0表示这些结构化工件被移除。工件数量和代码行数不是协议正确性或需求语义覆盖的度量。

已有标注只覆盖paper和NoSpec，本次不把日志错误数当作Luna一致性事件。以下沿用已确认观察下界，尚未由独立标注者复核：

| 一致性指标（按运行算术均值） | NoSpec本次 | paper MQTT均值 | Luna历史两次 |
| --- | --- | --- | --- |
| 接口/调用矛盾 | 0 | 0.667 | 未标注 |
| 实现/测试矛盾 | 2 | 2.667 | 未标注 |
| 共同类别C | 2 | 3.333 | 未标注 |
| 规格内部矛盾SC（单列） | 0 | 0.667 | 未标注 |
| 含修正的Coder响应 | 2 | 3.333 | 未标注 |
| CR（%） | 4.651 | 2.632 | 未标注 |
| CT（%） | 1.109 | 2.774 | 未标注 |
| 含修正响应总Token | 14,221 | 125,685 | 未标注 |

NoSpec两项均为开发测试预期修正；修正响应27及31共14,221 Token、16.316模型秒。response25–32反馈窗口合计8响应、98,211 Token、90.215模型秒，其中包含定位、读取、检查点及复测，不能称为纯修复成本。完整标注与原始证据：[README.md](/home/ljf/SpecForge/expriments/RQ2/analysis/README.md)、[incidents.csv](/home/ljf/SpecForge/expriments/RQ2/analysis/results/incidents.csv)。

paper已确认的共同类别C均值3.33，NoSpec为2；不同底座/预算以及单次NoSpec样本不足以证明规格机制减少或增加不一致。Luna未标注表示未知，不能填0。

## 9. 未进入编码的前置失败尝试

记录：[run.json](/home/ljf/SpecForge/runs/gpt6_luna_max_mqtt/20261011T151634Z/run.json)。该尝试为fresh=true、resumed=false、manual_edits=false；事实29次、设计31次，共60次完成响应，1,708,030 Token，模型累计1,588.624秒。规格阶段在0.002秒内因`KeyError: FUNCTION_TYPE`停止，没有规格作者模型响应、Coder或独立验收。其fact/design墙钟为684.741/904.898秒。它是单独的失败尝试，单列披露，不加入本报告选取的两次完整Luna生成均值。

| job | 响应 | 输入 | 输出 | 总 Token | 模型秒 | 缓存命中 | 缓存未命中 | 推理 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 01_facts | 29 | 940,005 | 26,924 | 966,929 | 684.263 | 752,640 | 187,365 | 17,479 |
| 02_design | 31 | 701,148 | 39,953 | 741,101 | 904.361 | 588,800 | 112,348 | 19,049 |


## 10. 校验与证据边界

已逐一核对全部2,095条response记录：每个job的完成响应数与response文件数一致；逐响应输入/输出/缓存/推理/调用耗时累计与job一致；全部job累计与run.json总usage一致；正式六个样本的input_hashes及验收器哈希一致。paper的阶段/总量与数据集索引交叉核对。CSV中的API错误为单独一行，不虚构Token及耗时。

原始运行、交付及日志未改写；只新增本报告与逐响应CSV。报告未执行新的实验。没有完整记录的墙钟、失败请求用量、SDK内部重试、未标注一致性事件、每次API响应精确时间戳、修复因果增量成本以及稳定复现率均不推测。原始请求/模型响应/工具完整返回通过CSV绝对路径追溯。

## 11. 补充：逐阶段生成、修改与独立修复的数据

补充时间：2026-10-12。保留上述MQTT样本与均值。这里区分整个生成/修复任务的用量，和任务内部发生具体文件写入/修改的响应用量。原日志没有逐操作Token或单独的内部修复墙钟，所以不能把同一响应的Token拆给其中某一个操作。

### 11.1 各阶段实际生成和修正的对象

| 阶段 | 生成/审查对象 | 本补充中如何统计内部修改 |
| --- | --- | --- |
| 事实提取 | scope.json、facts.json；此阶段不生成实现C代码 | 事实/范围JSON的成功写入、成功编辑 |
| 结构设计 | module/file Specs、ABI公共.h及部分入口.c | 分别统计结构化规格与C/H文件操作 |
| 行为规格作者 | 函数规格、WIRE_MAPPING、测试向量、traceability、ABI对齐 | 统计规格、ABI的成功写入/编辑；命令生成也留在原始命令记录中 |
| 语义审查 | 现有规格、向量、需求semantic_review | 审查任务全量单列；编辑响应是其中一个可核实子集 |
| 首次Coder | 实现C/H、Makefile、开发测试、README、delivery | 源码/头文件与测试分别统计；不把首次job视为无修正 |
| 规格回修/重审 | 修订规格并重新审查、发布 | 有独立job则报告整个job用量；另核对实际落盘修改的证据 |
| Coder续修 | 已有交付的续写、修正、开发复测 | 报告整个repair job，另外分出其文件操作子集 |
| prepare / verify / 独立验收 | 提取输入、确定性构建/测试及冻结交付副本验收 | 没有模型生成/修复用量；独立验收不反馈给Coder |

以下文件操作统计仅以有实际成功返回的write_file/edit_file为依据，排除WORKLOG检查点、失败/拒绝操作和old==new的无变化编辑。write_file可能是新建也可能是覆盖已有文件；edit_file也可能是补全或调整。因此使用“写入/修改响应”，不将其自动命名为纯首次生成/纯错误修复。

命令也会生成/修改工件；不能用工具名称覆盖全部真实修复。本补充保留每个run_command原文及结果，命令操作包含于下表“其他响应”；另单列人工核实的paper第三次规格与测试修正。未逐条语义复核的命令写入不自动分类。

### 11.2 各阶段内部文件写入/修改用量：不重复计数的完整分区

每格为“总Token / 完成响应数 / 模型秒”。一个响应只能进入下列四栏之一，因此四栏相加等于该阶段全量。模型调用秒包含该响应中的其他工作及服务等待，不能解释为文件修改本身的耗时。

#### RQ2 NoSpec（n=1）

| 阶段 | 含成功写入，无成功编辑 | 含成功编辑，无成功写入 | 同响应含写入与编辑 | 其他响应 |
| --- | --- | --- | --- | --- |
| 事实提取 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 结构设计 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 行为规格作者（含重做） | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 语义审查（含失败与重做） | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 首次 Coder | 382,031 / 10 / 626.046 | 301,454 / 8 / 188.498 | 0 / 0 / 0 | 598,437 / 25 / 424.562 |
| 规格回修 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 规格回修后重审 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Coder 实现续修 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |


#### paper MQTT均值（n=3）

| 阶段 | 含成功写入，无成功编辑 | 含成功编辑，无成功写入 | 同响应含写入与编辑 | 其他响应 |
| --- | --- | --- | --- | --- |
| 事实提取 | 120,001.33 / 1.67 / 62.488 | 83,920.67 / 1.67 / 10.505 | 12,668.33 / 0.33 / 12.103 | 612,558 / 19.33 / 36.936 |
| 结构设计 | 691,678.67 / 13.67 / 382.766 | 120,658 / 2.33 / 30.056 | 23,616.33 / 0.33 / 8.519 | 1,099,769.33 / 35 / 173.513 |
| 行为规格作者（含重做） | 1,275,318 / 22 / 686.069 | 82,191 / 1.67 / 6.577 | 50,465.33 / 1 / 24.719 | 3,228,354.33 / 85 / 409.097 |
| 语义审查（含失败与重做） | 0 / 0 / 0 | 229,390.33 / 6.33 / 32.963 | 0 / 0 / 0 | 1,778,517 / 42.67 / 229.005 |
| 首次 Coder | 405,116.33 / 8.33 / 162.355 | 317,820.67 / 8 / 24.294 | 0 / 0 / 0 | 2,933,560.33 / 84 / 219.620 |
| 规格回修 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 11,875.67 / 0.67 / 3.281 |
| 规格回修后重审 | 19,861.67 / 0.33 / 1.310 | 0 / 0 / 0 | 0 / 0 / 0 | 736,528.67 / 17.33 / 98.066 |
| Coder 实现续修 | 147,294.67 / 4 / 65.428 | 82,412.67 / 2 / 12.695 | 0 / 0 / 0 | 623,985 / 18.67 / 59.189 |


#### Luna MQTT均值（n=2）

| 阶段 | 含成功写入，无成功编辑 | 含成功编辑，无成功写入 | 同响应含写入与编辑 | 其他响应 |
| --- | --- | --- | --- | --- |
| 事实提取 | 64,300.50 / 2 / 92.968 | 93,585 / 1.50 / 70.545 | 0 / 0 / 0 | 640,764.50 / 22 / 269.002 |
| 结构设计 | 205,789 / 9 / 289.921 | 0 / 0 / 0 | 0 / 0 / 0 | 269,066.50 / 14 / 150.875 |
| 行为规格作者（含重做） | 640,123 / 16 / 810.761 | 645,438 / 16 / 411.676 | 0 / 0 / 0 | 5,446,208.50 / 139 / 3,301.160 |
| 语义审查（含失败与重做） | 46,939.50 / 1 / 84.451 | 552,126 / 11.50 / 326.502 | 0 / 0 / 0 | 7,391,470 / 168 / 4,428.639 |
| 首次 Coder | 357,055 / 8 / 530.740 | 195,559.50 / 4.50 / 75.981 | 0 / 0 / 0 | 592,613.50 / 19 / 291.344 |
| 规格回修 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 规格回修后重审 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Coder 实现续修 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |


paper第三次06_spec_repair只进行了读取/check，日志中没有保存修改，结构检查即通过。实际规格修改发生在后续07_spec_review的命令中，例如响应48保存了SUBACK及会话等向量修正；命令末尾断言失败没有回滚此前保存。06_spec_repair的35,627 Token在“其他响应”中，不能把该任务名当成已完成修复动作的证明。

### 11.3 每个阶段中的C/H代码与开发测试生成、修改

每格仍为“总Token / 响应数 / 模型秒”，只选择确实包含相应成功文件操作的响应。tests目录优先归为开发测试，即使文件后缀是.c；其余.c/.h归为实现源码/头文件。设计阶段的代码操作主要是ABI头文件。不同对象视图可能选到同一响应，下面四栏不能作为互斥分区相加；11.2才是完整互斥分区。命令中的写入/编辑不包含于此工具操作视图。

#### RQ2 NoSpec（n=1）

| 阶段 | C/H文件写入响应 | C/H文件修改响应 | 开发测试写入响应 | 开发测试修改响应 |
| --- | --- | --- | --- | --- |
| 事实提取 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 结构设计 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 行为规格作者（含重做） | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 语义审查（含失败与重做） | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 首次 Coder | 196,247 / 5 / 348.965 | 287,233 / 6 / 172.182 | 85,415 / 2 / 179.714 | 14,221 / 2 / 16.316 |
| 规格回修 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 规格回修后重审 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Coder 实现续修 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |


#### paper MQTT均值（n=3）

| 阶段 | C/H文件写入响应 | C/H文件修改响应 | 开发测试写入响应 | 开发测试修改响应 |
| --- | --- | --- | --- | --- |
| 事实提取 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 结构设计 | 329,928.33 / 6.67 / 137.195 | 124,763.67 / 2 / 37.013 | 0 / 0 / 0 | 0 / 0 / 0 |
| 行为规格作者（含重做） | 0 / 0 / 0 | 26,879.67 / 0.33 / 1.931 | 0 / 0 / 0 | 0 / 0 / 0 |
| 语义审查（含失败与重做） | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 首次 Coder | 218,621.67 / 4.67 / 67.749 | 191,311 / 3.67 / 9.162 | 99,074.33 / 1.67 / 64.720 | 86,643.33 / 3.33 / 11.813 |
| 规格回修 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 规格回修后重审 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Coder 实现续修 | 83,732.33 / 2 / 35.881 | 41,798.33 / 1 / 7.539 | 22,828.67 / 0.33 / 16.259 | 40,614.33 / 1 / 5.156 |


#### Luna MQTT均值（n=2）

| 阶段 | C/H文件写入响应 | C/H文件修改响应 | 开发测试写入响应 | 开发测试修改响应 |
| --- | --- | --- | --- | --- |
| 事实提取 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 结构设计 | 90,829.50 / 4 / 58.692 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 行为规格作者（含重做） | 0 / 0 / 0 | 32,913 / 1 / 40.674 | 0 / 0 / 0 | 0 / 0 / 0 |
| 语义审查（含失败与重做） | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 首次 Coder | 187,496.50 / 4.50 / 305.163 | 195,559.50 / 4.50 / 75.981 | 49,433 / 1 / 152.231 | 0 / 0 / 0 |
| 规格回修 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 规格回修后重审 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Coder 实现续修 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |


因此，NoSpec外层repair=0仍包含6个直接C代码修改响应、2个开发测试修改响应。Luna也存在首次Coder内部的源码修改；不能把外层repair=0当成零修正。

### 11.4 独立修复、续跑和重做的完整任务用量

| 运行 | 任务 | 性质 | 输入 | 输出 | 总Token | 响应 | 模型秒 | 结果 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| paper round_03 | 06_spec_repair | 规格回修 | 33,468 | 2,159 | 35,627 | 2 | 9.842 | gate_passed |
| paper round_03 | 07_spec_review | 回修后的语义重审 | 2,211,981 | 57,190 | 2,269,171 | 53 | 298.127 | gate_passed |
| paper round_03 | 08_repair | 第一轮Coder续修 | 1,560,904 | 71,977 | 1,632,881 | 40 | 294.478 | response_limit |
| paper round_03 | 09_repair | 第二轮Coder续修 | 905,894 | 22,302 | 928,196 | 34 | 117.459 | gate_passed |
| Luna 历史② | 05_specs | API错误后规格作者重新执行 | 6,667,382 | 161,603 | 6,828,985 | 161 | 4,230.116 | gate_passed |
| Luna 历史② | 06_spec_review | API错误后语义审查重新执行 | 6,628,587 | 135,704 | 6,764,291 | 150 | 3,839.544 | gate_passed |
| Luna 历史① | 04_spec_review响应61–120 | 60→120上限后的同job续跑增量 | 2,709,651 | 80,096 | 2,789,747 | 60 | 1,817.979 | 最终gate_passed |

paper前两次、NoSpec和两次Luna均没有独立的Coder repair job；其阶段内部修改仍已计入11.2/11.3。上表增量及重做任务已包含在整次运行与阶段总量，不能再额外加一次。

| 独立修复/继续工作的均值 | RQ2 NoSpec（n=1） | paper MQTT均值（n=3） | Luna MQTT均值（n=2） |
| --- | --- | --- | --- |
| 规格回修 | 0 / 0 / 0 | 11,875.67 / 0.67 / 3.281 | 0 / 0 / 0 |
| 规格回修后重审 | 0 / 0 / 0 | 756,390.33 / 17.67 / 99.376 | 0 / 0 / 0 |
| Coder 实现续修 | 0 / 0 / 0 | 853,692.33 / 24.67 / 137.312 | 0 / 0 / 0 |
| 审查上限续跑增量（不是Coder repair） | 0 / 0 / 0 | 0 / 0 / 0 | 1,394,873.50 / 30 / 908.990 |
| API失败后的规格作者+审查重做 | 0 / 0 / 0 | 0 / 0 / 0 | 6,796,638 / 155.50 / 4,034.830 |

重做的“review”属于审查，不等于已证实修复操作；API异常对应恢复成本也不是协议实现缺陷成本。

### 11.5 NoSpec首次Coder内的具体代码生成与修正

| 活动 | 响应编号 | 响应数 | 输入Token | 输出Token | 总Token | 模型秒 |
| --- | --- | --- | --- | --- | --- | --- |
| 源码/头文件写入 | 7, 8, 10, 14, 15 | 5 | 182,213 | 14,034 | 196,247 | 348.965 |
| 源码修改 | 9, 17, 18, 20, 22, 23 | 6 | 280,055 | 7,178 | 287,233 | 172.182 |
| 开发测试写入 | 21, 36 | 2 | 75,978 | 9,437 | 85,415 | 179.714 |
| 开发测试修改 | 27, 31 | 2 | 13,840 | 381 | 14,221 | 16.316 |
| 构建/交付/文档写入 | 11, 39, 42 | 3 | 96,293 | 4,076 | 100,369 | 97.367 |
| 其他响应 | 1, 2, 3, 4, 5, 6, 12, 13, 16, 19, 24, 25, 26, 28, 29, 30, 32, 33, 34, 35, 37, 38, 40, 41, 43 | 25 | 586,840 | 11,597 | 598,437 | 424.562 |

源码写入为响应7（mqtt.h）、8（protocol.c）、10（broker.h）、14（broker.c）、15（main.c）。代码修改响应9修正固定头flags判断；17–18删除未使用参数并更新调用；20调整通配符末级处理；22–23修正CONNECT最小长度及client-ID字段偏移。测试修改为27与31，分别修正bad_flags负例与单层+匹配/的预期。六类在本次NoSpec中确实互斥，共43响应、1,281,922 Token、1,239.106模型秒。

这些源码修改和较窄的“组件间矛盾”是不同口径。已有一致性标注的C=2只对应测试预期矛盾，不涵盖一般协议算法错误、编译警告或全部源码编辑。本补充不改写已有一致性标注。

### 11.6 命令修正的已核实例子与数据边界

| paper第三次响应 | 已核实保存的文件 | 保存状态 | 输入Token | 输出Token | 总Token | 模型秒 |
| --- | --- | --- | --- | --- | --- | --- |
| 07_spec_review/35 | /work/functions/src_wire.c_wire_decode_empty.json, /work/files/src/wire.c.json, /work/functions/src_wire.c_wire_decode_connect.json, /work/module_spec.json | success | 27,851 | 3,909 | 31,760 | 15.962 |
| 07_spec_review/48 | /work/files/src/broker.c.json, /work/files/src/topic.c.json, /work/files/src/session.c.json | persisted_before_later_assertion_failure | 19,931 | 2,856 | 22,787 | 11.557 |
| 07_spec_review/50 | /work/files/src/topic.c.json | success | 25,098 | 595 | 25,693 | 2.572 |
| 08_repair/40 | /work/tests/dev_test.py | success | 71,669 | 1,449 | 73,118 | 6.149 |

这些是经命令及返回、后续读取或最终工件核实的文件修改响应，用量包含同响应的其他工作。响应48在保存三个JSON后，末尾检查因topic输入/预期数组长度不一致失败；响应50补齐对应预期并复测通过。因此非零命令退出码不能直接解释为“没有保存修复”。

paper第三次08_repair响应40通过run_command修改tests/dev_test.py：将“非法UTF-8却期待CONNACK2”的fixture换成合法非ASCII UTF-8，并补充非法UTF-8应静默断开的检查。该完整响应输入71,669、输出1,449，合计73,118 Token，模型计时6.149秒；包含修正及复测。它在11.2中属于“其他响应”，11.3的edit_file测试视图不会包含它，但整个repair job用量已包含。证据：[response_040.json](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/logs/08_repair/api/response_040.json)、[tools_040.json](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/logs/08_repair/api/tools_040.json)。

同理，规格作者或审查中的命令可能批量生成/改写多个JSON或ABI文件。完整命令和返回均已保存在逐响应CSV与原日志；本次仅将有直接成功返回的文件工具动作作明确分类，未声称已经完成所有命令的语义修复标注。不能把“其他响应”当作零生成或零修复。

CSV新增了artifact_operation_class、成功写入/编辑次数、检查点/无变化编辑次数、逐文件动作与文件类型、代码/测试写入和修改响应标志、已核实命令文件修正字段。API错误行的这些用量字段留空。所有互斥分区的input/output/cache/reasoning/requests/elapsed均已逐阶段加总验证；原始响应和工具返回哈希读取前后相同。

## 12. 按六类Coder活动与修复任务比较：响应数及总Token

本节对应六类活动及独立修复/恢复任务的比较，只列完成响应数与总Token。MQTT样本保持为RQ2一次、paper三次及两次完整Luna历史运行。Coder统计包含首次code与实现repair；Planner规格回修及语义审查另列。

活动按成功保存的文件操作归类；不含检查点、失败/拒绝操作和无变化编辑。已核实的paper第三次08_repair响应40命令测试修改归入测试修改；其余命令操作保留在“其他响应”。修改可能包含补全，不自动等同于错误修复。每个响应完整input+output只计一次。

### 完整Coder：首次生成及实现续修

每格为“响应数 / 总Token”；历史值按运行求算术均值。

| 活动 | RQ2 NoSpec（1次） | paper MQTT（3次均值） | Luna MQTT（2次均值） |
| --- | --- | --- | --- |
| 源码及头文件写入 | 5 / 196,247 | 6.33 / 284,282.67 | 4.50 / 187,496.50 |
| 源码修改 | 6 / 287,233 | 4.67 / 233,109.33 | 4.50 / 195,559.50 |
| 开发测试写入 | 2 / 85,415 | 2 / 121,903 | 1 / 49,433 |
| 开发测试修改 | 2 / 14,221 | 4.67 / 151,630.33 | 0 / 0 |
| 构建、交付与文档写入 | 3 / 100,369 | 2.67 / 96,153.33 | 2.50 / 120,125.50 |
| 读取、检查、复测等其他响应 | 25 / 598,437 | 104.33 / 3,605,039.67 | 19 / 592,613.50 |
| 同响应涉及多类活动 | 0 / 0 | 0.33 / 18,071.33 | 0 / 0 |
| 合计 | 43 / 1,281,922 | 125 / 4,510,189.67 | 31.50 / 1,145,228 |


### 首次Coder任务

每格为“响应数 / 总Token”；历史值按运行求算术均值。

| 活动 | RQ2 NoSpec（1次） | paper MQTT（3次均值） | Luna MQTT（2次均值） |
| --- | --- | --- | --- |
| 源码及头文件写入 | 5 / 196,247 | 4.67 / 218,621.67 | 4.50 / 187,496.50 |
| 源码修改 | 6 / 287,233 | 3.67 / 191,311 | 4.50 / 195,559.50 |
| 开发测试写入 | 2 / 85,415 | 1.67 / 99,074.33 | 1 / 49,433 |
| 开发测试修改 | 2 / 14,221 | 3.33 / 86,643.33 | 0 / 0 |
| 构建、交付与文档写入 | 3 / 100,369 | 2 / 87,420.33 | 2.50 / 120,125.50 |
| 读取、检查、复测等其他响应 | 25 / 598,437 | 85 / 2,973,426.67 | 19 / 592,613.50 |
| 同响应涉及多类活动 | 0 / 0 | 0 / 0 | 0 / 0 |
| 合计 | 43 / 1,281,922 | 100.33 / 3,656,497.33 | 31.50 / 1,145,228 |


### 独立Coder实现续修任务

每格为“响应数 / 总Token”；历史值按运行求算术均值。

| 活动 | RQ2 NoSpec（1次） | paper MQTT（3次均值） | Luna MQTT（2次均值） |
| --- | --- | --- | --- |
| 源码及头文件写入 | 0 / 0 | 1.67 / 65,661 | 0 / 0 |
| 源码修改 | 0 / 0 | 1 / 41,798.33 | 0 / 0 |
| 开发测试写入 | 0 / 0 | 0.33 / 22,828.67 | 0 / 0 |
| 开发测试修改 | 0 / 0 | 1.33 / 64,987 | 0 / 0 |
| 构建、交付与文档写入 | 0 / 0 | 0.67 / 8,733 | 0 / 0 |
| 读取、检查、复测等其他响应 | 0 / 0 | 19.33 / 631,613 | 0 / 0 |
| 同响应涉及多类活动 | 0 / 0 | 0.33 / 18,071.33 | 0 / 0 |
| 合计 | 0 / 0 | 24.67 / 853,692.33 | 0 / 0 |


paper第三次08_repair响应34同时写src/main.c及Makefile，共54,214 Token，均值为0.33响应、18,071.33 Token。无法把同一响应的input+output精确分给两项，故增加“同响应涉及多类活动”一行；未重复加入源码写入或构建写入。七行因此构成互斥完整分区。较早11.3按对象选择响应的视图允许重叠，其对象小计不应与本节互斥分区直接混用。

### 独立修复、续跑与重做任务均值

| 工作 | RQ2 NoSpec（1次） | paper MQTT（3次均值） | Luna MQTT（2次均值） |
| --- | --- | --- | --- |
| 规格回修任务 | 0 / 0 | 0.67 / 11,875.67 | 0 / 0 |
| 规格回修后的重审 | 0 / 0 | 17.67 / 756,390.33 | 0 / 0 |
| Coder实现续修 | 0 / 0 | 24.67 / 853,692.33 | 0 / 0 |
| 审查上限扩展后的续跑增量 | 0 / 0 | 0 / 0 | 30 / 1,394,873.50 |
| API失败后的规格作者及审查重做 | 0 / 0 | 0 / 0 | 155.50 / 6,796,638 |

Coder实现续修已包含于第一张完整Coder表，不再额外相加。规格回修、重审、审查续跑和重做均已包含于整个生成流程的原始总量。两次Luna中的这两项恢复任务分别只在一次运行发生，因此这里除以2表示均值。

paper第三次06_spec_repair任务共35,627 Token，仅做读取/check；实际规格修改发生在后续07_spec_review。任务标签与实际修正动作分开统计。

### 描述性比较

RQ2完整Coder为43响应、1,281,922 Token，比paper完整Coder均值少71.58%，比同模型Luna完整Coder均值多11.94%。这里比较的是Coder用量，与包含Facts/Design/Specs的全流程总Token降幅不同。

RQ2源码修改为6响应、287,233 Token；paper均值为4.67响应、233,109.33 Token；Luna均值为4.5响应、195,559.50 Token。RQ2开发测试修改为2响应、14,221 Token，paper为4.67响应、151,630.33 Token，Luna在本操作口径下未记录测试修改响应。零不证明不存在测试缺陷。

同一响应中的上下文、读取及其他活动一并计入，其Token不能解释为纯修改操作或修复因果成本。底座、预算及恢复条件不同；这些是记录层面的比较。
