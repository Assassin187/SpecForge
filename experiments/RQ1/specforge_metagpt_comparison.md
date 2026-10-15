# SpecForge 与 MetaGPT 五轮四协议对比：论文图表原始数据

取数日期：2026-10-13（UTC+8）。两套系统各包含五轮 MQTT、CoAP、HTTP/1.1、SMTP，共20次实验；每协议每系统 n=5。正确性、用量及修复统计包含失败和未进入后续阶段的尝试；代码行数均值只以已产生最终C实现的尝试为分母，已有源码但构建或验收失败的项目仍纳入。SpecForge 取完整生成流程；MetaGPT 取原始生成加全部原生反馈后的最终交付。

本次以 MetaGPT 五轮合并索引更新全表，原三轮归档及12条历史指标保留。新增8个项目初始生成均结束；MG-R04-HTTP/1.1反馈异常且未执行独立验收，合并状态为merged_with_incomplete_records，共19条完整链和1条不完整链，该次仍纳入20次尝试及计划场景分母。SpecForge 第1–3轮为 deepseek-flash/high，第4–5轮为 gpt-6-luna/max；MetaGPT 五轮均为 deepseek-flash/high。五轮汇总是混合模型配置下的描述性数据，不能解释为控制相同基础模型的框架效果；相同模型的历史观察见第1–3轮逐次记录。

## 1. 来源与统计口径

| 项目 | 来源或定义 |
| --- | --- |
| SpecForge 索引 | [summary.json](/home/ljf/SpecForge/runs/paper/summary.json)、[runs.csv](/home/ljf/SpecForge/runs/paper/runs.csv) |
| MetaGPT 索引 | [complete_experiments.json](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/complete_experiments.json)、[complete_experiments.csv](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/complete_experiments.csv) |
| 实验范围 | [SpecForge 数据说明](/home/ljf/SpecForge/runs/paper/README.md)、[MetaGPT 五轮数据说明](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/README.md) |
| 保留的 MetaGPT 三轮历史 | [原归档](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/README.md)、[五轮目录中的独立历史副本](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/README.md)；原12条指标未改写 |
| 行为范围与场景分类 | [四协议行为范围及完整场景清单](/home/ljf/SpecForge/expriments/RQ1/protocol_behavior_scenarios.md)；固定场景ID及分类沿用原表，清单中的三轮累计数为历史口径 |
| 模型 | SpecForge 第1–3轮 DeepSeek/high、第4–5轮 GPT-6-luna/max；MetaGPT 第1–5轮 DeepSeek/high。配置均为非流式、单响应上限65536 Token；具体模型配置见原始记录 |
| 输入及验收器核对 | 20组同轮同协议的 TASK、REQUIREMENTS、协议原文实际字节SHA-256一致；双方各19份已执行独立验收报告使用相同协议验收器；SF-R02-CoAP及MG-R04-HTTP/1.1未执行验收。模型配置仅前12组相同 |
| MetaGPT 版本与路径 | v0.8.2上游软件公司SOP生成，随后执行留存的C99原生QA/修复适配。前两轮反馈后补，第3–5轮自动进入反馈；第4–5轮两轮并行、轮内四协议并行 |
| 修复与响应上限 | 实现修复最多3轮；MetaGPT每修复轮40响应、初始QA 180响应。SpecForge review通常为60，第四轮MQTT续接扩至120，第五轮续接扩至150；具体预算与续接历史保留，不能宣称五轮配置完全相同 |
| 完整普通独立通过 | 普通模式所有必需场景均执行且通过；不以编译成功或自测成功代替 |
| 严格双模式通过 | 共同交付约定下普通与原生sanitize均完整通过，并满足ASan/UBSan插桩检查 |
| ASan/UBSan 参考通过 | SpecForge使用原生sanitize；MetaGPT使用外部追加编译/链接参数的原源码副本，不能替代严格交付结果 |
| 计划场景数 | 每轮每模式 MQTT=16、CoAP=10、HTTP/1.1=13、SMTP=13；五轮每系统每模式共260项 |
| 主要场景通过率 | 通过项数 / 全部计划项数，未执行保留在计划分母；另报通过 / 有结果项数 |
| 异常与边界场景通过率 | 普通模式固定A／B／A+B子集，通过项数 / 全部计划子集项数；每轮MQTT=11、CoAP=7、HTTP/1.1=11、SMTP=11，五轮共200项。每场景只计一次，未执行仍在分母 |
| NA | 未执行、不适用或无记录，不代表零项通过；逐次分数保留NA，计划场景仍在总体分母 |
| Token 与响应 | 输入+输出；缓存命中已包含于输入，推理已包含于输出，均不重复累加。响应计已返回用量的响应，不等于全部网络尝试或工具调用 |
| 生成活动时间 | SpecForge=generation_wall_seconds（完整流程及内部修复）；MetaGPT=generation_active_seconds+feedback_active_seconds。均不含独立验收、人工等待，不等于并行墙钟 |
| 耗时缺失与分母 | SpecForge第5轮4条generation_wall_seconds在索引中为null，保持NA，不能用API耗时、连续跨度或0替代。已知均值按n_time计：SpecForge=16、MetaGPT=20；SpecForge全部20次的完整生成均值为NA |
| C代码行数 | 最终服务实现.c与.h的cloc 2.08 code（SLOC）之和，排除空行、注释、tests/及构建/历史文件；逐文件计数，不去重同内容文件。SpecForge取project，MetaGPT取反馈后的project |
| 平均代码行数分母 | 已产生最终.c实现的尝试，包含有源码的失败项目。SpecForge n_code=19、MetaGPT n_code=20；SF-R02-CoAP未编码，记NA |
| 统一成本折算 | 沿用留存DeepSeek峰时单价，对已知用量作统一单价折算；GPT记录同样只是归一化值，不是GPT报价或历史账单。未知请求费用不补零 |

MetaGPT中断前缀和反馈before_continuation_*已包含在最终累计值，不重复累加；历史适配调试和路由续跑开销保留。excluded_runs及另外的GPT→DeepSeek混合续接批次不纳入这20条记录。轮次是重复实验编号，不表示两系统同时启动或随机配对；样本标准差采用ddof=1。

## 2. 总体重要指标

| 指标 | SpecForge | MetaGPT（生成 + 原生反馈） |
| --- | --- | --- |
| 实验次数 | 20 | 20 |
| 最终普通构建通过 | 19/20 (95.00%) | 18/20 (90.00%) |
| 完整普通独立通过 | 12/20 (60.00%) | 6/20 (30.00%) |
| 严格双模式完整通过 | 12/20 (60.00%) | 0/20 (0.00%) |
| 普通 + ASan/UBSan 参考完整通过 | 12/20 (60.00%) | 6/20 (30.00%) |
| 普通：通过 / 计划场景 | 238/260 (91.54%) | 151/260 (58.08%) |
| 异常与边界：通过 / 计划场景 | 181/200 (90.50%) | 112/200 (56.00%) |
| 普通：有结果的场景数 | 250 | 234 |
| 普通：通过 / 有结果场景 | 238/250 (95.20%) | 151/234 (64.53%) |
| 普通：未有结果的计划场景 | 10 | 26 |
| ASan/UBSan 参考：通过 / 计划场景 | 239/260 (91.92%) | 151/260 (58.08%) |
| ASan/UBSan 参考：有结果的场景数 | 250 | 176 |
| ASan/UBSan 参考：通过 / 有结果场景 | 239/250 (95.60%) | 151/176 (85.80%) |
| ASan/UBSan 参考：未有结果的计划场景 | 10 | 84 |
| 输入 Token | 289735785 | 79853491 |
| 输出 Token | 10745630 | 16740997 |
| 已知总 Token | 300481415 | 96594488 |
| 输入缓存命中 Token | 249973632 | 60437995 |
| 输入缓存未命中 Token | 39762153 | 19415496 |
| 已返回用量的模型响应数 | 7735 | 1517 |
| 用量未知的已记录请求数 | 4 | 2 |
| 输入 Token 缓存命中率 | 86.28% | 75.69% |
| 每项目已知总 Token 均值 | 15024070.75 | 4829724.40 |
| 活动时间有记录的项目数 n_time | 16 | 20 |
| 已知项目活动时间合计（秒） | 58960.361 | 68978.644 |
| 全部尝试的完整生成均值（分钟） | NA | 57.482 |
| 已知生成活动时间均值（分钟） | 61.417 | 57.482 |
| 有C实现的项目数 n_code | 19 | 20 |
| C代码行数合计（C/H，SLOC） | 31747 | 42659 |
| 平均C代码行数（C/H，SLOC） | 1670.89 | 2132.95 |
| 统一DeepSeek峰时单价折算（USD，已知响应） | 26.323244 | 26.276473 |

严格双模式以原生sanitize交付及插桩检查判定；外部插桩的成功不改记为严格通过。普通模式“有结果”包含验收器实际记录的失败项；缺少规定运行文件所导致的0/16或0/13是实际失败记录。异常／边界指标使用同一普通验收结果的固定子集；构建或启动失败也会影响该值，不能单独归因于某个生成模块。

## 3. 按协议汇总：正确性

完整任务通过率 = 普通独立验收完整通过全部预设场景的尝试数 / 该协议全部5次尝试；包含框架内部修复及MetaGPT原生反馈后的最终交付。任一场景失败或未执行均不计为完整通过。

| 协议 | 系统 | n | 最终普通构建 | 完整任务通过次数 | 完整任务通过率 | 严格双模式通过 | 普通 + 插桩参考通过 | 普通：通过/计划 | 异常与边界：通过/计划 | 普通：有结果项数 | 插桩参考：通过/计划 | 插桩参考：有结果项数 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MQTT | SpecForge | 5 | 5/5 (100.00%) | 4/5 | 80.00% | 4/5 (80.00%) | 4/5 (80.00%) | 79/80 (98.75%) | 54/55 (98.18%) | 80 | 80/80 (100.00%) | 80 |
| MQTT | MetaGPT | 5 | 5/5 (100.00%) | 3/5 | 60.00% | 0/5 (0.00%) | 3/5 (60.00%) | 48/80 (60.00%) | 33/55 (60.00%) | 80 | 48/80 (60.00%) | 48 |
| CoAP | SpecForge | 5 | 4/5 (80.00%) | 4/5 | 80.00% | 4/5 (80.00%) | 4/5 (80.00%) | 40/50 (80.00%) | 28/35 (80.00%) | 40 | 40/50 (80.00%) | 40 |
| CoAP | MetaGPT | 5 | 5/5 (100.00%) | 3/5 | 60.00% | 0/5 (0.00%) | 3/5 (60.00%) | 45/50 (90.00%) | 31/35 (88.57%) | 50 | 45/50 (90.00%) | 50 |
| HTTP/1.1 | SpecForge | 5 | 5/5 (100.00%) | 3/5 | 60.00% | 3/5 (60.00%) | 3/5 (60.00%) | 60/65 (92.31%) | 50/55 (90.91%) | 65 | 60/65 (92.31%) | 65 |
| HTTP/1.1 | MetaGPT | 5 | 3/5 (60.00%) | 0/5 | 0.00% | 0/5 (0.00%) | 0/5 (0.00%) | 12/65 (18.46%) | 10/55 (18.18%) | 39 | 12/65 (18.46%) | 13 |
| SMTP | SpecForge | 5 | 5/5 (100.00%) | 1/5 | 20.00% | 1/5 (20.00%) | 1/5 (20.00%) | 59/65 (90.77%) | 49/55 (89.09%) | 65 | 59/65 (90.77%) | 65 |
| SMTP | MetaGPT | 5 | 5/5 (100.00%) | 0/5 | 0.00% | 0/5 (0.00%) | 0/5 (0.00%) | 46/65 (70.77%) | 38/55 (69.09%) | 65 | 46/65 (70.77%) | 65 |

## 4. 按协议汇总：资源、生成耗时与代码规模

Token均值以每协议全部5次尝试为分母；耗时均值和样本标准差仅按已有活动时间的n_time计。SpecForge各协议n_time=4（第1–4轮），MetaGPT为5，不能将该列称为双方五轮完整耗时均值。代码均值和样本标准差按有最终C实现的n_code计，包含有源码的失败项目；代码量不代表实现质量。

| 协议 | 系统 | 总 Token（已知） | 总 Token 均值 | 总 Token 样本标准差 | 响应数合计 | n_time | 已知活动时间均值（秒） | 已知活动时间样本标准差（秒） | 已知生成活动时间均值（分钟） | n_code | 平均C代码行数（C/H，SLOC） | 代码行数样本标准差 | 统一单价折算USD合计 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MQTT | SpecForge | 78344764 | 15668952.80 | 5008740.05 | 1992 | 4 | 4036.270 | 2415.419 | 67.271 | 5 | 1710.60 | 507.73 | 7.341790 |
| MQTT | MetaGPT | 22030658 | 4406131.60 | 2079268.80 | 350 | 5 | 3096.759 | 807.563 | 51.613 | 5 | 1875.40 | 233.19 | 5.993025 |
| CoAP | SpecForge | 69661934 | 13932386.80 | 5180188.02 | 1776 | 4 | 3293.658 | 1290.564 | 54.894 | 4 | 1261.50 | 248.20 | 6.068757 |
| CoAP | MetaGPT | 18272089 | 3654417.80 | 799250.04 | 336 | 5 | 2608.623 | 456.687 | 43.477 | 5 | 1647.00 | 244.41 | 5.064480 |
| HTTP/1.1 | SpecForge | 73041041 | 14608208.20 | 5603255.67 | 1885 | 4 | 3417.528 | 684.215 | 56.959 | 5 | 1933.00 | 629.36 | 6.075713 |
| HTTP/1.1 | MetaGPT | 34168033 | 6833606.60 | 2473619.48 | 454 | 5 | 4544.417 | 1362.060 | 75.740 | 5 | 2694.80 | 384.67 | 8.649592 |
| SMTP | SpecForge | 79433676 | 15886735.20 | 4668922.07 | 2082 | 4 | 3992.634 | 1814.707 | 66.544 | 5 | 1696.60 | 220.93 | 6.836983 |
| SMTP | MetaGPT | 22123708 | 4424741.60 | 1233740.73 | 377 | 5 | 3545.929 | 396.629 | 59.099 | 5 | 2314.60 | 331.29 | 6.569376 |

## 5. 按轮次汇总

| 轮次 | 系统 | 模型 | n | 完整普通通过 | 严格双模式通过 | 普通 + 插桩参考通过 | 普通：通过/计划 | 异常与边界：通过/计划 | 总 Token（已知） | 响应数 | n_time | 已知项目活动时间合计（秒） | 统一单价折算USD |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | SpecForge | DeepSeek/high | 4 | 2/4 (50.00%) | 2/4 (50.00%) | 2/4 (50.00%) | 47/52 (90.38%) | 35/40 (87.50%) | 73357728 | 1862 | 4 | 11774.858 | 6.201814 |
| 1 | MetaGPT | DeepSeek/high | 4 | 1/4 (25.00%) | 0/4 (0.00%) | 1/4 (25.00%) | 31/52 (59.62%) | 22/40 (55.00%) | 23459463 | 315 | 4 | 14903.615 | 6.185991 |
| 2 | SpecForge | DeepSeek/high | 4 | 2/4 (50.00%) | 2/4 (50.00%) | 2/4 (50.00%) | 40/52 (76.92%) | 31/40 (77.50%) | 58461794 | 1499 | 4 | 10940.420 | 5.717199 |
| 2 | MetaGPT | DeepSeek/high | 4 | 1/4 (25.00%) | 0/4 (0.00%) | 1/4 (25.00%) | 30/52 (57.69%) | 22/40 (55.00%) | 20700241 | 324 | 4 | 12916.972 | 5.518179 |
| 3 | SpecForge | DeepSeek/high | 4 | 3/4 (75.00%) | 3/4 (75.00%) | 3/4 (75.00%) | 51/52 (98.08%) | 39/40 (97.50%) | 66490693 | 1678 | 4 | 12321.590 | 6.012589 |
| 3 | MetaGPT | DeepSeek/high | 4 | 1/4 (25.00%) | 0/4 (0.00%) | 1/4 (25.00%) | 22/52 (42.31%) | 17/40 (42.50%) | 15810240 | 272 | 4 | 11888.277 | 4.769564 |
| 4 | SpecForge | GPT-6-luna/max | 4 | 2/4 (50.00%) | 2/4 (50.00%) | 2/4 (50.00%) | 50/52 (96.15%) | 38/40 (95.00%) | 32749854 | 888 | 4 | 23923.493 | 2.920854 |
| 4 | MetaGPT | DeepSeek/high | 4 | 2/4 (50.00%) | 0/4 (0.00%) | 2/4 (50.00%) | 35/52 (67.31%) | 25/40 (62.50%) | 17763329 | 296 | 4 | 15506.966 | 5.111213 |
| 5 | SpecForge | GPT-6-luna/max | 4 | 3/4 (75.00%) | 3/4 (75.00%) | 3/4 (75.00%) | 50/52 (96.15%) | 38/40 (95.00%) | 69421346 | 1808 | 0 | NA | 5.470788 |
| 5 | MetaGPT | DeepSeek/high | 4 | 1/4 (25.00%) | 0/4 (0.00%) | 1/4 (25.00%) | 33/52 (63.46%) | 26/40 (65.00%) | 18861215 | 310 | 4 | 13762.814 | 4.691526 |

## 6. 逐次原始结果（可用于作图）

每行链接到该次run.json或统一experiment.json。1=达到通过标准，0=未达到；场景列为通过/有结果项数。NA的计划项数仍纳入总体分母。MG-R01-HTTP/1.1的原生sanitize 0/13*带插桩无效标记；SpecForge外部插桩列不适用。

| 记录 | 最终普通构建(1/0) | 框架自身关卡(1/0) | 普通场景 | 原生 sanitize 场景 | 外部 ASan/UBSan 场景 | 完整普通(1/0) | 严格双模式(1/0) | 普通 + 插桩参考(1/0) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [SF-R01-MQTT](/home/ljf/SpecForge/runs/paper/round_01/mqtt_01/run.json) | 1 | 1 | 16/16 | 16/16 | NA | 1 | 1 | 1 |
| [MG-R01-MQTT](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_01/mqtt/experiment.json) | 1 | 0 | 16/16 | NA | 16/16 | 1 | 0 | 1 |
| [SF-R01-CoAP](/home/ljf/SpecForge/runs/paper/round_01/coap_01/run.json) | 1 | 1 | 10/10 | 10/10 | NA | 1 | 1 | 1 |
| [MG-R01-CoAP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_01/coap/experiment.json) | 1 | 0 | 6/10 | NA | 6/10 | 0 | 0 | 0 |
| [SF-R01-HTTP/1.1](/home/ljf/SpecForge/runs/paper/round_01/http11_01/run.json) | 1 | 1 | 9/13 | 9/13 | NA | 0 | 0 | 0 |
| [MG-R01-HTTP/1.1](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_01/http11/experiment.json) | 1 | 1 | 0/13 | 0/13* | NA | 0 | 0 | 0 |
| [SF-R01-SMTP](/home/ljf/SpecForge/runs/paper/round_01/smtp_01/run.json) | 1 | 1 | 12/13 | 12/13 | NA | 0 | 0 | 0 |
| [MG-R01-SMTP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_01/smtp/experiment.json) | 1 | 1 | 9/13 | NA | 9/13 | 0 | 0 | 0 |
| [SF-R02-MQTT](/home/ljf/SpecForge/runs/paper/round_02/mqtt_01/run.json) | 1 | 1 | 16/16 | 16/16 | NA | 1 | 1 | 1 |
| [MG-R02-MQTT](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_02/mqtt/experiment.json) | 1 | 0 | 16/16 | NA | 16/16 | 1 | 0 | 1 |
| [SF-R02-CoAP](/home/ljf/SpecForge/runs/paper/round_02/coap_01/run.json) | 0 | 0 | NA | NA | NA | 0 | 0 | 0 |
| [MG-R02-CoAP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_02/coap/experiment.json) | 1 | 1 | 9/10 | NA | 9/10 | 0 | 0 | 0 |
| [SF-R02-HTTP/1.1](/home/ljf/SpecForge/runs/paper/round_02/http11_01/run.json) | 1 | 1 | 13/13 | 13/13 | NA | 1 | 1 | 1 |
| [MG-R02-HTTP/1.1](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_02/http11/experiment.json) | 0 | 0 | NA | NA | NA | 0 | 0 | 0 |
| [SF-R02-SMTP](/home/ljf/SpecForge/runs/paper/round_02/smtp_01/run.json) | 1 | 1 | 11/13 | 11/13 | NA | 0 | 0 | 0 |
| [MG-R02-SMTP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_02/smtp/experiment.json) | 1 | 0 | 5/13 | NA | 5/13 | 0 | 0 | 0 |
| [SF-R03-MQTT](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/run.json) | 1 | 1 | 16/16 | 16/16 | NA | 1 | 1 | 1 |
| [MG-R03-MQTT](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_03/mqtt/experiment.json) | 1 | 1 | 0/16 | NA | NA | 0 | 0 | 0 |
| [SF-R03-CoAP](/home/ljf/SpecForge/runs/paper/round_03/coap_01/run.json) | 1 | 1 | 10/10 | 10/10 | NA | 1 | 1 | 1 |
| [MG-R03-CoAP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_03/coap/experiment.json) | 1 | 0 | 10/10 | NA | 10/10 | 1 | 0 | 1 |
| [SF-R03-HTTP/1.1](/home/ljf/SpecForge/runs/paper/round_03/http11_01/run.json) | 1 | 1 | 13/13 | 13/13 | NA | 1 | 1 | 1 |
| [MG-R03-HTTP/1.1](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_03/http11/experiment.json) | 1 | 0 | 0/13 | NA | NA | 0 | 0 | 0 |
| [SF-R03-SMTP](/home/ljf/SpecForge/runs/paper/round_03/smtp_01/run.json) | 1 | 1 | 12/13 | 12/13 | NA | 0 | 0 | 0 |
| [MG-R03-SMTP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_03/smtp/experiment.json) | 1 | 0 | 12/13 | NA | 12/13 | 0 | 0 | 0 |
| [SF-R04-MQTT](/home/ljf/SpecForge/runs/paper/round_04/mqtt_01/run.json) | 1 | 1 | 15/16 | 16/16 | NA | 0 | 0 | 0 |
| [MG-R04-MQTT](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_04/experiments/mqtt/experiment.json) | 1 | 1 | 16/16 | NA | 16/16 | 1 | 0 | 1 |
| [SF-R04-CoAP](/home/ljf/SpecForge/runs/paper/round_04/coap_01/run.json) | 1 | 1 | 10/10 | 10/10 | NA | 1 | 1 | 1 |
| [MG-R04-CoAP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_04/experiments/coap/experiment.json) | 1 | 1 | 10/10 | NA | 10/10 | 1 | 0 | 1 |
| [SF-R04-HTTP/1.1](/home/ljf/SpecForge/runs/paper/round_04/http11_01/run.json) | 1 | 1 | 12/13 | 12/13 | NA | 0 | 0 | 0 |
| [MG-R04-HTTP/1.1](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_04/experiments/http11/experiment.json) | 0 | 0 | NA | NA | NA | 0 | 0 | 0 |
| [SF-R04-SMTP](/home/ljf/SpecForge/runs/paper/round_04/smtp_01/run.json) | 1 | 1 | 13/13 | 13/13 | NA | 1 | 1 | 1 |
| [MG-R04-SMTP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_04/experiments/smtp/experiment.json) | 1 | 1 | 9/13 | NA | 9/13 | 0 | 0 | 0 |
| [SF-R05-MQTT](/home/ljf/SpecForge/runs/paper/round_05/mqtt_01/run.json) | 1 | 1 | 16/16 | 16/16 | NA | 1 | 1 | 1 |
| [MG-R05-MQTT](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_05/experiments/mqtt/experiment.json) | 1 | 1 | 0/16 | NA | NA | 0 | 0 | 0 |
| [SF-R05-CoAP](/home/ljf/SpecForge/runs/paper/round_05/coap_01/run.json) | 1 | 1 | 10/10 | 10/10 | NA | 1 | 1 | 1 |
| [MG-R05-CoAP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_05/experiments/coap/experiment.json) | 1 | 0 | 10/10 | NA | 10/10 | 1 | 0 | 1 |
| [SF-R05-HTTP/1.1](/home/ljf/SpecForge/runs/paper/round_05/http11_01/run.json) | 1 | 1 | 13/13 | 13/13 | NA | 1 | 1 | 1 |
| [MG-R05-HTTP/1.1](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_05/experiments/http11/experiment.json) | 1 | 1 | 12/13 | NA | 12/13 | 0 | 0 | 0 |
| [SF-R05-SMTP](/home/ljf/SpecForge/runs/paper/round_05/smtp_01/run.json) | 1 | 1 | 11/13 | 11/13 | NA | 0 | 0 | 0 |
| [MG-R05-SMTP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_05/experiments/smtp/experiment.json) | 1 | 1 | 11/13 | NA | 11/13 | 0 | 0 | 0 |

“框架自身关卡”在SpecForge取generation_passed，在MetaGPT取all_selftests_passed，两者测试内容不同；相应计数为19/20与10/20，不能解释为相同套件的通过率。

## 7. 逐次 Token 与统一成本（可用于作图）

整数不使用千位分隔符。总Token=输入+输出；缓存命中+未命中=输入。所有统计包含失败尝试。

| 记录ID | 模型 | input_tokens | output_tokens | known_total_tokens | cache_hit_tokens | cache_miss_tokens | response_count | unknown_usage_requests | 统一单价折算USD |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SF-R01-MQTT | DeepSeek/high | 13290557 | 595897 | 13886454 | 11405952 | 1884605 | 359 | 0 | 1.348894 |
| MG-R01-MQTT | DeepSeek/high | 3101053 | 715650 | 3816703 | 2167040 | 934013 | 63 | 0 | 1.151986 |
| SF-R01-CoAP | DeepSeek/high | 18879240 | 621503 | 19500743 | 16641920 | 2237320 | 484 | 0 | 1.516851 |
| MG-R01-CoAP | DeepSeek/high | 2674184 | 723195 | 3397379 | 1771648 | 902536 | 60 | 0 | 1.149225 |
| SF-R01-HTTP/1.1 | DeepSeek/high | 18353102 | 684535 | 19037637 | 16106752 | 2246350 | 472 | 0 | 1.591988 |
| MG-R01-HTTP/1.1 | DeepSeek/high | 9270237 | 1535858 | 10806095 | 7415808 | 1854429 | 116 | 1 | 2.443853 |
| SF-R01-SMTP | DeepSeek/high | 20178039 | 754855 | 20932894 | 17738624 | 2439415 | 547 | 0 | 1.744082 |
| MG-R01-SMTP | DeepSeek/high | 4548608 | 890678 | 5439286 | 3375744 | 1172864 | 76 | 1 | 1.440927 |
| SF-R02-MQTT | DeepSeek/high | 13302732 | 657864 | 13960596 | 11329664 | 1973068 | 355 | 0 | 1.449335 |
| MG-R02-MQTT | DeepSeek/high | 6731590 | 1074559 | 7806149 | 5050619 | 1680971 | 96 | 0 | 1.824066 |
| SF-R02-CoAP | DeepSeek/high | 10801013 | 588696 | 11389709 | 9308416 | 1492597 | 283 | 0 | 1.210065 |
| MG-R02-CoAP | DeepSeek/high | 3680340 | 722100 | 4402440 | 2876160 | 804180 | 82 | 0 | 1.125031 |
| SF-R02-HTTP/1.1 | DeepSeek/high | 19099209 | 725311 | 19824520 | 16712960 | 2386249 | 511 | 0 | 1.686526 |
| MG-R02-HTTP/1.1 | DeepSeek/high | 3582076 | 728775 | 4310851 | 2511488 | 1070588 | 75 | 0 | 1.210775 |
| SF-R02-SMTP | DeepSeek/high | 12671012 | 615957 | 13286969 | 10779520 | 1891492 | 350 | 0 | 1.371273 |
| MG-R02-SMTP | DeepSeek/high | 3245254 | 935547 | 4180801 | 2509952 | 735302 | 71 | 0 | 1.358307 |
| SF-R03-MQTT | DeepSeek/high | 15509854 | 705783 | 16215637 | 13189760 | 2320094 | 415 | 0 | 1.622106 |
| MG-R03-MQTT | DeepSeek/high | 1616435 | 499380 | 2115815 | 1248635 | 367800 | 46 | 0 | 0.717088 |
| SF-R03-CoAP | DeepSeek/high | 15816351 | 618545 | 16434896 | 13748864 | 2067487 | 418 | 0 | 1.444993 |
| MG-R03-CoAP | DeepSeek/high | 2016071 | 464838 | 2480909 | 1420544 | 595527 | 57 | 0 | 0.744987 |
| SF-R03-HTTP/1.1 | DeepSeek/high | 15903291 | 664827 | 16568118 | 13847552 | 2055739 | 413 | 0 | 1.497599 |
| MG-R03-HTTP/1.1 | DeepSeek/high | 5973492 | 1099376 | 7072868 | 4360960 | 1612532 | 94 | 0 | 1.829177 |
| SF-R03-SMTP | DeepSeek/high | 16620954 | 651088 | 17272042 | 14692864 | 1928090 | 432 | 0 | 1.447890 |
| MG-R03-SMTP | DeepSeek/high | 3180346 | 960302 | 4140648 | 2136576 | 1043770 | 75 | 0 | 1.478313 |
| SF-R04-MQTT | GPT-6-luna/max | 10127279 | 319688 | 10446967 | 8193536 | 1933743 | 273 | 0 | 1.012910 |
| MG-R04-MQTT | DeepSeek/high | 3278103 | 817898 | 4096001 | 2523642 | 754461 | 74 | 0 | 1.222958 |
| SF-R04-CoAP | GPT-6-luna/max | 6016412 | 236466 | 6252878 | 5185536 | 830876 | 160 | 0 | 0.564135 |
| MG-R04-CoAP | DeepSeek/high | 3718447 | 676954 | 4395401 | 2970368 | 748079 | 72 | 0 | 1.054591 |
| SF-R04-HTTP/1.1 | GPT-6-luna/max | 6667143 | 192817 | 6859960 | 5715456 | 951687 | 192 | 0 | 0.551179 |
| MG-R04-HTTP/1.1 | DeepSeek/high | 5414351 | 1225380 | 6639731 | 4156672 | 1257679 | 87 | 0 | 1.872700 |
| SF-R04-SMTP | GPT-6-luna/max | 8895008 | 295041 | 9190049 | 7584768 | 1310240 | 263 | 0 | 0.792630 |
| MG-R04-SMTP | DeepSeek/high | 2003276 | 628920 | 2632196 | 1342592 | 660684 | 63 | 0 | 0.960965 |
| SF-R05-MQTT | GPT-6-luna/max | 23274310 | 560800 | 23835110 | 19546624 | 3727686 | 590 | 1 | 1.908546 |
| MG-R05-MQTT | DeepSeek/high | 3471026 | 724964 | 4195990 | 2837883 | 633143 | 71 | 0 | 1.076927 |
| SF-R05-CoAP | GPT-6-luna/max | 15638145 | 445563 | 16083708 | 13242880 | 2395265 | 431 | 1 | 1.332712 |
| MG-R05-CoAP | DeepSeek/high | 2980643 | 615317 | 3595960 | 2183424 | 797219 | 65 | 0 | 0.990647 |
| SF-R05-HTTP/1.1 | GPT-6-luna/max | 10519029 | 231777 | 10750806 | 9134080 | 1384949 | 297 | 1 | 0.748422 |
| MG-R05-HTTP/1.1 | DeepSeek/high | 4501164 | 837324 | 5338488 | 3612416 | 888748 | 82 | 0 | 1.293088 |
| SF-R05-SMTP | GPT-6-luna/max | 18173105 | 578617 | 18751722 | 15867904 | 2305201 | 490 | 1 | 1.481108 |
| MG-R05-SMTP | DeepSeek/high | 4866795 | 863982 | 5730777 | 3965824 | 900971 | 92 | 0 | 1.330865 |

统一费率来自留存的 [pricing_verified.json](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/batch_records/generation_round_02/reports/pricing_verified.json)，不采用当前报价。每百万Token：缓存输入0.006 USD、未缓存输入0.30 USD、输出1.20 USD。计算式：`normalized_peak_usd=(cache_hit_tokens*0.006+cache_miss_tokens*0.30+output_tokens*1.20)/1000000`。使用未舍入值聚合，USD展示6位小数。SpecForge第4–5轮GPT的数值只表示统一DeepSeek费率归一化，不是GPT费用；不据此推断实际账单或成本优势。

## 8. 逐次活动时间与修复（可用于作图）

| 记录ID | generation_active_seconds | feedback_active_seconds | total_active_seconds | spec_repairs | implementation_repairs | test_repairs |
| --- | --- | --- | --- | --- | --- | --- |
| SF-R01-MQTT | 2549.942 | NA | 2549.942 | 0 | 0 | NA |
| MG-R01-MQTT | 2223.484 | 454.805 | 2678.289 | NA | 1 | 2 |
| SF-R01-CoAP | 2756.887 | NA | 2756.887 | 1 | 1 | NA |
| MG-R01-CoAP | 2021.295 | 686.263 | 2707.558 | NA | 1 | 2 |
| SF-R01-HTTP/1.1 | 2964.447 | NA | 2964.447 | 0 | 0 | NA |
| MG-R01-HTTP/1.1 | 5370.376 | 629.779 | 6000.155 | NA | 1 | 2 |
| SF-R01-SMTP | 3503.582 | NA | 3503.582 | 1 | 3 | NA |
| MG-R01-SMTP | 2929.709 | 587.904 | 3517.613 | NA | 1 | 2 |
| SF-R02-MQTT | 2740.248 | NA | 2740.248 | 0 | 0 | NA |
| MG-R02-MQTT | 3408.727 | 455.695 | 3864.422 | NA | 2 | 1 |
| SF-R02-CoAP | 2353.671 | NA | 2353.671 | 0 | 0 | NA |
| MG-R02-CoAP | 2534.433 | 201.707 | 2736.140 | NA | 1 | 1 |
| SF-R02-HTTP/1.1 | 3233.276 | NA | 3233.276 | 0 | 0 | NA |
| MG-R02-HTTP/1.1 | 2241.807 | 521.696 | 2763.503 | NA | 3 | 0 |
| SF-R02-SMTP | 2613.225 | NA | 2613.225 | 0 | 0 | NA |
| MG-R02-SMTP | 2835.754 | 717.153 | 3552.907 | NA | 0 | 3 |
| SF-R03-MQTT | 3220.277 | NA | 3220.277 | 1 | 2 | NA |
| MG-R03-MQTT | 1773.925 | 154.492 | 1928.417 | NA | 0 | 0 |
| SF-R03-CoAP | 2862.729 | NA | 2862.729 | 0 | 0 | NA |
| MG-R03-CoAP | 1293.183 | 531.714 | 1824.897 | NA | 1 | 2 |
| SF-R03-HTTP/1.1 | 3042.610 | NA | 3042.610 | 0 | 0 | NA |
| MG-R03-HTTP/1.1 | 3666.118 | 694.606 | 4360.724 | NA | 3 | 0 |
| SF-R03-SMTP | 3195.974 | NA | 3195.974 | 0 | 0 | NA |
| MG-R03-SMTP | 3189.277 | 584.962 | 3774.239 | NA | 2 | 1 |
| SF-R04-MQTT | 7634.612 | NA | 7634.612 | 0 | 0 | NA |
| MG-R04-MQTT | 3246.646 | 527.719 | 3774.365 | NA | 1 | 2 |
| SF-R04-CoAP | 5201.345 | NA | 5201.345 | 0 | 0 | NA |
| MG-R04-CoAP | 2627.597 | 398.764 | 3026.361 | NA | 0 | 2 |
| SF-R04-HTTP/1.1 | 4429.780 | NA | 4429.780 | 0 | 0 | NA |
| MG-R04-HTTP/1.1 | 4983.991 | 806.523 | 5790.514 | NA | 2 | 0 |
| SF-R04-SMTP | 6657.756 | NA | 6657.756 | 0 | 0 | NA |
| MG-R04-SMTP | 2283.468 | 632.258 | 2915.726 | NA | 1 | 2 |
| SF-R05-MQTT | NA | NA | NA | 0 | 0 | NA |
| MG-R05-MQTT | 3040.145 | 198.156 | 3238.301 | NA | 0 | 1 |
| SF-R05-CoAP | NA | NA | NA | 0 | 0 | NA |
| MG-R05-CoAP | 1992.119 | 756.042 | 2748.161 | NA | 1 | 2 |
| SF-R05-HTTP/1.1 | NA | NA | NA | 0 | 0 | NA |
| MG-R05-HTTP/1.1 | 3327.710 | 479.481 | 3807.191 | NA | 0 | 2 |
| SF-R05-SMTP | NA | NA | NA | 0 | 0 | NA |
| MG-R05-SMTP | 3355.067 | 614.094 | 3969.161 | NA | 1 | 2 |

SpecForge完整流程包含Facts→Design→Specs→Code→Verify及内部修复，规格修复3次、实现修复6次。MetaGPT分列原始生成与累计反馈，WriteCode实现修复22次、DebugError测试修复29次，共51次。类别和路径不同，不把不存在的类别填0，也不视为等价工作量。

| 系统 / 记录 | 生成阶段Token | 反馈阶段Token | 生成阶段响应 | 反馈阶段响应 | 生成活动秒数（已知） | 反馈活动秒数 | 独立测量累计秒数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SpecForge（20条；时间仅16条已知） | 300481415 | NA | 7735 | NA | 58960.361 | NA | NA |
| MetaGPT（全部20条） | 74260070 | 22334418 | 1334 | 183 | 58344.831 | 10633.813 | 153.010 |

并行墙钟与项目活动秒数分别记录，不能相加作为同一种耗时。SpecForge第5轮的活动时间缺失；API累计耗时和包含等待的protocol跨度均不代填。MetaGPT新增第4、5轮生成+反馈批次墙钟分别为 5800.050、4120.239秒；两轮同时运行，二者之和不等于整批墙钟。独立验收时间不计入生成均值，本表不提供混合范围的墙钟加速比。

### 8.1 逐次异常与边界场景及最终代码规模

场景列采用固定计划分母；有结果项数区分实测失败和未执行。SF-R02-CoAP无实现，代码行数为NA；已有实现但验收失败的项目仍按最终源码计数。链接指向实际计数的最终项目目录。

| 记录ID / 最终项目 | 异常与边界：通过/计划 | 有结果项数 | C源文件数 | 头文件数 | .c SLOC | .h SLOC | C/H SLOC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [SF-R01-MQTT](/home/ljf/SpecForge/runs/paper/round_01/mqtt_01/project) | 11/11 | 11 | 7 | 7 | 1763 | 178 | 1941 |
| [MG-R01-MQTT](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_01/mqtt/feedback/project) | 11/11 | 11 | 7 | 7 | 1637 | 215 | 1852 |
| [SF-R01-CoAP](/home/ljf/SpecForge/runs/paper/round_01/coap_01/project) | 7/7 | 7 | 6 | 5 | 1170 | 193 | 1363 |
| [MG-R01-CoAP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_01/coap/feedback/project) | 4/7 | 7 | 7 | 6 | 1313 | 240 | 1553 |
| [SF-R01-HTTP/1.1](/home/ljf/SpecForge/runs/paper/round_01/http11_01/project) | 7/11 | 11 | 8 | 7 | 2120 | 178 | 2298 |
| [MG-R01-HTTP/1.1](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_01/http11/feedback/project) | 0/11 | 11 | 13 | 14 | 1944 | 395 | 2339 |
| [SF-R01-SMTP](/home/ljf/SpecForge/runs/paper/round_01/smtp_01/project) | 10/11 | 11 | 9 | 8 | 1660 | 163 | 1823 |
| [MG-R01-SMTP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_01/smtp/feedback/project) | 7/11 | 11 | 10 | 10 | 2241 | 220 | 2461 |
| [SF-R02-MQTT](/home/ljf/SpecForge/runs/paper/round_02/mqtt_01/project) | 11/11 | 11 | 7 | 6 | 1707 | 196 | 1903 |
| [MG-R02-MQTT](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_02/mqtt/feedback/project) | 11/11 | 11 | 8 | 8 | 1788 | 332 | 2120 |
| [SF-R02-CoAP](/home/ljf/SpecForge/runs/paper/round_02/coap_01/project) | 0/7 | 0 | 0 | 0 | NA | NA | NA |
| [MG-R02-CoAP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_02/coap/feedback/project) | 6/7 | 7 | 9 | 8 | 1557 | 296 | 1853 |
| [SF-R02-HTTP/1.1](/home/ljf/SpecForge/runs/paper/round_02/http11_01/project) | 11/11 | 11 | 10 | 9 | 2437 | 206 | 2643 |
| [MG-R02-HTTP/1.1](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_02/http11/feedback/project) | 0/11 | 0 | 9 | 9 | 1967 | 296 | 2263 |
| [SF-R02-SMTP](/home/ljf/SpecForge/runs/paper/round_02/smtp_01/project) | 9/11 | 11 | 7 | 6 | 1596 | 155 | 1751 |
| [MG-R02-SMTP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_02/smtp/feedback/project) | 5/11 | 11 | 8 | 8 | 2172 | 293 | 2465 |
| [SF-R03-MQTT](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/project) | 11/11 | 11 | 8 | 7 | 2119 | 200 | 2319 |
| [MG-R03-MQTT](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_03/mqtt/feedback/project) | 0/11 | 11 | 7 | 6 | 1343 | 216 | 1559 |
| [SF-R03-CoAP](/home/ljf/SpecForge/runs/paper/round_03/coap_01/project) | 7/7 | 7 | 7 | 6 | 1334 | 218 | 1552 |
| [MG-R03-CoAP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_03/coap/feedback/project) | 7/7 | 7 | 6 | 6 | 1066 | 281 | 1347 |
| [SF-R03-HTTP/1.1](/home/ljf/SpecForge/runs/paper/round_03/http11_01/project) | 11/11 | 11 | 9 | 8 | 1979 | 207 | 2186 |
| [MG-R03-HTTP/1.1](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_03/http11/feedback/project) | 0/11 | 11 | 11 | 11 | 2823 | 310 | 3133 |
| [SF-R03-SMTP](/home/ljf/SpecForge/runs/paper/round_03/smtp_01/project) | 10/11 | 11 | 8 | 8 | 1796 | 170 | 1966 |
| [MG-R03-SMTP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/round_03/smtp/feedback/project) | 10/11 | 11 | 8 | 8 | 1973 | 322 | 2295 |
| [SF-R04-MQTT](/home/ljf/SpecForge/runs/paper/round_04/mqtt_01/project) | 10/11 | 11 | 4 | 3 | 1022 | 33 | 1055 |
| [MG-R04-MQTT](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_04/feedback/mqtt_20261013T052827Z/project) | 11/11 | 11 | 10 | 8 | 1833 | 252 | 2085 |
| [SF-R04-CoAP](/home/ljf/SpecForge/runs/paper/round_04/coap_01/project) | 7/7 | 7 | 3 | 2 | 903 | 81 | 984 |
| [MG-R04-CoAP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_04/feedback/coap_20261013T052827Z/project) | 7/7 | 7 | 7 | 7 | 1580 | 361 | 1941 |
| [SF-R04-HTTP/1.1](/home/ljf/SpecForge/runs/paper/round_04/http11_01/project) | 10/11 | 11 | 4 | 3 | 1179 | 65 | 1244 |
| [MG-R04-HTTP/1.1](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_04/feedback/http11_20261013T052827Z/project) | 0/11 | 0 | 10 | 12 | 2672 | 311 | 2983 |
| [SF-R04-SMTP](/home/ljf/SpecForge/runs/paper/round_04/smtp_01/project) | 11/11 | 11 | 4 | 3 | 1403 | 34 | 1437 |
| [MG-R04-SMTP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_04/feedback/smtp_20261013T052827Z/project) | 7/11 | 11 | 7 | 6 | 1574 | 180 | 1754 |
| [SF-R05-MQTT](/home/ljf/SpecForge/runs/paper/round_05/mqtt_01/project) | 11/11 | 11 | 4 | 3 | 1271 | 64 | 1335 |
| [MG-R05-MQTT](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_05/feedback/mqtt_20261013T052829Z/project) | 0/11 | 11 | 7 | 7 | 1510 | 251 | 1761 |
| [SF-R05-CoAP](/home/ljf/SpecForge/runs/paper/round_05/coap_01/project) | 7/7 | 7 | 3 | 2 | 1058 | 89 | 1147 |
| [MG-R05-CoAP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_05/feedback/coap_20261013T052829Z/project) | 7/7 | 7 | 7 | 7 | 1252 | 289 | 1541 |
| [SF-R05-HTTP/1.1](/home/ljf/SpecForge/runs/paper/round_05/http11_01/project) | 11/11 | 11 | 4 | 3 | 1223 | 71 | 1294 |
| [MG-R05-HTTP/1.1](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_05/feedback/http11_20261013T052829Z/project) | 10/11 | 11 | 12 | 12 | 2412 | 344 | 2756 |
| [SF-R05-SMTP](/home/ljf/SpecForge/runs/paper/round_05/smtp_01/project) | 9/11 | 11 | 4 | 3 | 1475 | 31 | 1506 |
| [MG-R05-SMTP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/round_05/feedback/smtp_20261013T052829Z/project) | 9/11 | 11 | 13 | 14 | 2264 | 334 | 2598 |

代码计数工具为 [cloc v2.08](https://github.com/AlDanial/cloc/tree/v2.08)，工具SHA-256：`0a551a86c7785880bcaa0fdfe6107c064abfbebc7cc26b7eaeac423fe2c0d49e`。按最终项目.c/.h逐文件计数，使用--skip-uniqueness，不编译、运行项目或调用模型。复算方式：

```bash
perl /path/to/cloc-2.08.pl --include-ext=c,h --exclude-dir=tests,build,build-sanitize,snapshots --skip-uniqueness --by-file --json <最终项目目录>
```

## 9. 解释结果时必须保留的限制

- 五轮汇总包含模型与推理预算差异：SpecForge前3轮DeepSeek/high、后2轮GPT-6-luna/max，MetaGPT五轮DeepSeek/high；不能将总体差异单独归因于框架。
- SpecForge第四轮MQTT与第五轮有响应预算扩展和续接；第五轮4条活动时间未完整留存，因此完整五轮平均生成耗时为NA。API耗时与包含等待的跨度不能代替活动时间。
- SpecForge第二轮CoAP在语义审查响应上限处停止，未编码、构建或验收；每模式10项计划场景及该次用量均保留。
- MetaGPT第二轮HTTP最终构建失败；第四轮HTTP最终构建因缺少类型／缓冲相关实现出现未定义符号，随后RunCode选中了不存在的src/types.c，原生反馈以native_framework_error停止。该次反馈状态failed，验收入口要求completed而未生成报告；源码、用量、耗时和失败状态均保留，独立分数为NA。
- MetaGPT第三轮MQTT/HTTP和第五轮MQTT的运行文件位置不符合共同TASK约定，普通验收实际记录失败。未移动或改名交付以改变结果。
- MetaGPT第一轮HTTP原始生成因investment=3.0结束，原生包装捕获NoMoneyException；生成返回、文件写出和审查完成不表示无异常完成。
- SpecForge第五轮有4份未返回用量的网关502错误记录；MetaGPT第一轮HTTP和SMTP各有一次历史中断请求用量未知。已知Token合计为SpecForge 300481415、MetaGPT 96594488；未记录用量不补零，也不据此认定请求产生了费用。成本折算仅覆盖已知响应。
- 原生QA与独立协议验收分别记录；自测通过不能代替共同验收。所有已返回用量、适配调试和续跑开销保留，外部插桩不能代替严格sanitize交付。
- 异常与边界分类为固定套件的事后描述性拆分，各协议五轮分母为55、35、55、55；结果也受构建或启动受阻影响。
- 每协议每系统仅5次观察，轮次不同时；均值和样本标准差是描述性数据，不构成统计显著性或相同环境、模型下的因果结论。

## 10. 核对记录与源索引指纹

本次只读取留存记录和最终源码，未为取数调用模型、重跑构建／验收或修改交付。核对双方各20条索引及CSV/JSON对应关系、SpecForge全部7735条已返回响应的用量合计、MetaGPT阶段累计用量与合并索引、逐次场景分数、20组输入实际字节哈希、双方各19份已执行独立验收报告的验收器指纹，以及最终C/H文件哈希和cloc计数。旧三轮正确性、异常／边界、Token和代码规模统计已复现。MetaGPT合并核对见 [ARCHIVE_VALIDATION.json](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/ARCHIVE_VALIDATION.json)：原18729项SHA-256清单已对原目录及独立历史副本核验，原12条历史指标保持不变；新增8条尝试均保留，其中7条链完整、第四轮HTTP缺失独立验收。归档核对passed表示保存和合并核对通过，不表示全部实验通过或全部链完整。

| 源索引 | SHA-256 |
| --- | --- |
| [runs/paper/summary.json](/home/ljf/SpecForge/runs/paper/summary.json) | 7658a91f57adf890d42b5435ec05bcb2f5ed78c12924f838f4811cfd5adf7159 |
| [runs/paper/runs.csv](/home/ljf/SpecForge/runs/paper/runs.csv) | 56a817485d971950a6858ae7ef236125411db4cb2047a3a9021de1d192cb5894 |
| [expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/complete_experiments.json](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/complete_experiments.json) | 63db24d4800eb4e9798ec2bdefd11a1663a082c4aca574ac5d0d4b02a184fe30 |
| [expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/complete_experiments.csv](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/complete_experiments.csv) | 78ee67595a75c42e7c3dfe60354dd32ef45c88634bb5ab999fab4b89ad2b182c |
| [expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/complete_experiments.json](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/complete_experiments.json) | e625069a97ec09159f70e9717508a3c4f893d3ae9d352c62520543efa0ddb11c |
| [expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/complete_experiments.csv](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/five_rounds_20261013T052357Z/history/three_rounds_20261009/complete_experiments.csv) | d3c0b1de22f094cbc961c949a0a97e6770ad933ed2a5e6ed11755b838c03c492 |
| [expriments/RQ1/protocol_behavior_scenarios.md](/home/ljf/SpecForge/expriments/RQ1/protocol_behavior_scenarios.md) | 67ac4c00584fe0a587755a34c3ed32b96373bfe7fda94c190f10af7ffc38474f |
