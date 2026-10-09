# SpecForge 与 MetaGPT 三轮四协议对比：论文图表原始数据

取数日期：2026-10-09（UTC+8）。两套系统各包含三轮 MQTT、CoAP、HTTP/1.1、SMTP，共 12 次实验；每协议每系统 n=3。以下均为留存记录的描述性统计，包含失败和未进入后续阶段的尝试。SpecForge 取完整生成流程；MetaGPT 取原始生成加全部原生反馈续跑后的最终交付。

## 1. 来源与统计口径

| 项目 | 来源或定义 |
| --- | --- |
| SpecForge 索引 | [summary.json](/home/ljf/SpecForge/runs/paper/summary.json)、[runs.csv](/home/ljf/SpecForge/runs/paper/runs.csv) |
| MetaGPT 索引 | [complete_experiments.json](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/complete_experiments.json)、[complete_experiments.csv](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/complete_experiments.csv) |
| 实验范围 | [SpecForge 数据说明](/home/ljf/SpecForge/runs/paper/README.md)、[MetaGPT 数据说明](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/README.md) |
| 模型 | deepseek-flash；thinking enabled；reasoning_effort=high；非流式；单响应输出上限 65536 Token |
| 输入及验收器核对 | 12 组配对的 TASK、REQUIREMENTS、协议原文逐文件 SHA-256 与模型配置一致；11 个已执行的 SpecForge 验收器哈希与对应 MetaGPT 记录一致；第二轮 SpecForge CoAP 未执行独立验收 |
| MetaGPT 版本与路径 | v0.8.2 上游；原始软件公司 SOP 生成，再执行留存的 C99 原生 QA/修复适配。前两轮反馈后补，第三轮生成后自动进入反馈；反馈累计值包含留存的适配/路由修正续跑 |
| 完整普通独立通过 | 该项目普通模式所有必需场景通过；不以编译成功或自测成功代替 |
| 严格双模式通过 | 共同交付约定下普通与原生 sanitize 均完整通过，并满足 ASan/UBSan 插桩检查 |
| ASan/UBSan 参考通过 | SpecForge 使用原生 sanitize；MetaGPT 使用外部追加编译/链接参数的原源码副本。该指标作为插桩参考单列，不能代替 MetaGPT 的严格交付结果 |
| 计划场景数 | 每轮每模式 MQTT=16、CoAP=10、HTTP/1.1=13、SMTP=13；三轮每模式共156项 |
| 主要场景通过率 | 已通过的场景数 / 全部计划场景数；未执行保留在计划分母。另报有结果场景的通过率，防止只统计可运行项目 |
| NA | 未执行、不适用或无记录，不代表零项通过。逐次表保留 NA；总体计划分母仍包含这些尝试 |
| Token 与响应 | 输入+输出；缓存命中包含于输入，推理包含于输出，均不重复累加。响应计已返回用量的模型响应，不等于工具调用数或所有网络尝试 |
| 活动时间 | SpecForge=generation_wall_seconds（完整流程，含内部修复）；MetaGPT=generation_active_seconds+feedback_active_seconds。均不含独立验收；每项目时间之和不是并行墙钟 |
| 成本 | 统一按留存峰时单价估算已知响应；不是历史账单。MetaGPT 两次历史中断请求用量未知，因此其用量和成本并非完整实耗 |

MetaGPT 中断前缀和反馈 before_continuation_* 已包含在最终 run.json 累计值，本表不重复累加；前两轮适配调试/路由续跑开销保留。excluded_runs 不纳入正式 12 次实验。轮次编号表示重复实验，不表示两系统同时启动或随机配对；下文标准差使用样本标准差（ddof=1）。

## 2. 总体重要指标

| 指标 | SpecForge | MetaGPT（生成 + 原生反馈） |
| --- | --- | --- |
| 实验次数 | 12 | 12 |
| 最终普通构建通过 | 11/12 (91.67%) | 11/12 (91.67%) |
| 完整普通独立通过 | 7/12 (58.33%) | 3/12 (25.00%) |
| 严格双模式完整通过 | 7/12 (58.33%) | 0/12 (0.00%) |
| 普通 + ASan/UBSan 参考完整通过 | 7/12 (58.33%) | 3/12 (25.00%) |
| 普通：通过 / 计划场景 | 138/156 (88.46%) | 83/156 (53.21%) |
| 普通：有结果的场景数 | 146 | 143 |
| 普通：通过 / 有结果场景 | 138/146 (94.52%) | 83/143 (58.04%) |
| 普通：未有结果的计划场景 | 10 | 13 |
| ASan/UBSan 参考：通过 / 计划场景 | 138/156 (88.46%) | 83/156 (53.21%) |
| ASan/UBSan 参考：有结果的场景数 | 146 | 101 |
| ASan/UBSan 参考：通过 / 有结果场景 | 138/146 (94.52%) | 83/101 (82.18%) |
| ASan/UBSan 参考：未有结果的计划场景 | 10 | 55 |
| 输入 Token | 190425354 | 49619686 |
| 输出 Token | 7884861 | 10350258 |
| 已知总 Token | 198310215 | 59969944 |
| 输入缓存命中 Token | 165502848 | 36845174 |
| 输入缓存未命中 Token | 24922506 | 12774512 |
| 已返回用量的模型响应数 | 5039 | 911 |
| 用量未知的已记录请求数 | 0 | 2 |
| 输入 Token 缓存命中率 | 86.91% | 74.26% |
| 每项目已知总 Token 均值 | 16525851.25 | 4997495.33 |
| 项目活动时间合计（秒） | 35036.868 | 39708.864 |
| 每项目活动时间均值（分钟） | 48.662 | 55.151 |
| 统一峰时缓存计价估算（USD，已知响应） | 17.931602 | 16.473734 |

严格双模式完整通过为 SpecForge 7/12、MetaGPT 0/12。MetaGPT 唯一产生原生 sanitize 场景结果的第一轮 HTTP/1.1 为0/13，同时报告缺少有效 ASan/UBSan 插桩；其余11条无原生 sanitize 场景结果。因此不可将外部插桩的3/12改记为严格交付通过。普通模式的“有结果”包含验收器实际记录的失败项；缺少规定运行文件导致的0/16或0/13也保留为实际失败记录，不表示服务器成功运行。

## 3. 按协议汇总：正确性

| 协议 | 系统 | n | 最终普通构建 | 完整普通通过 | 严格双模式通过 | 普通 + 插桩参考通过 | 普通：通过/计划 | 普通：有结果项数 | 插桩参考：通过/计划 | 插桩参考：有结果项数 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MQTT | SpecForge | 3 | 3/3 (100.00%) | 3/3 (100.00%) | 3/3 (100.00%) | 3/3 (100.00%) | 48/48 (100.00%) | 48 | 48/48 (100.00%) | 48 |
| MQTT | MetaGPT | 3 | 3/3 (100.00%) | 2/3 (66.67%) | 0/3 (0.00%) | 2/3 (66.67%) | 32/48 (66.67%) | 48 | 32/48 (66.67%) | 32 |
| CoAP | SpecForge | 3 | 2/3 (66.67%) | 2/3 (66.67%) | 2/3 (66.67%) | 2/3 (66.67%) | 20/30 (66.67%) | 20 | 20/30 (66.67%) | 20 |
| CoAP | MetaGPT | 3 | 3/3 (100.00%) | 1/3 (33.33%) | 0/3 (0.00%) | 1/3 (33.33%) | 25/30 (83.33%) | 30 | 25/30 (83.33%) | 30 |
| HTTP/1.1 | SpecForge | 3 | 3/3 (100.00%) | 2/3 (66.67%) | 2/3 (66.67%) | 2/3 (66.67%) | 35/39 (89.74%) | 39 | 35/39 (89.74%) | 39 |
| HTTP/1.1 | MetaGPT | 3 | 2/3 (66.67%) | 0/3 (0.00%) | 0/3 (0.00%) | 0/3 (0.00%) | 0/39 (0.00%) | 26 | 0/39 (0.00%) | 0 |
| SMTP | SpecForge | 3 | 3/3 (100.00%) | 0/3 (0.00%) | 0/3 (0.00%) | 0/3 (0.00%) | 35/39 (89.74%) | 39 | 35/39 (89.74%) | 39 |
| SMTP | MetaGPT | 3 | 3/3 (100.00%) | 0/3 (0.00%) | 0/3 (0.00%) | 0/3 (0.00%) | 26/39 (66.67%) | 39 | 26/39 (66.67%) | 39 |

## 4. 按协议汇总：资源与耗时

| 协议 | 系统 | 总 Token（已知） | 总 Token 均值 | 总 Token 样本标准差 | 响应数合计 | 活动时间均值（秒） | 活动时间样本标准差（秒） | 估算USD合计 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MQTT | SpecForge | 44062687 | 14687562.33 | 1323870.61 | 1129 | 2836.822 | 345.445 | 4.420335 |
| MQTT | MetaGPT | 13738667 | 4579555.67 | 2920861.76 | 205 | 2823.709 | 976.160 | 3.693140 |
| CoAP | SpecForge | 47325348 | 15775116.00 | 4095570.82 | 1185 | 2657.762 | 268.615 | 4.171909 |
| CoAP | MetaGPT | 10280728 | 3426909.33 | 961105.81 | 199 | 2422.865 | 518.053 | 3.019243 |
| HTTP/1.1 | SpecForge | 55430275 | 18476758.33 | 1699110.70 | 1396 | 3080.111 | 138.282 | 4.776113 |
| HTTP/1.1 | MetaGPT | 22189814 | 7396604.67 | 3259701.32 | 285 | 4374.794 | 1618.372 | 5.483805 |
| SMTP | SpecForge | 51491905 | 17163968.33 | 3824108.03 | 1329 | 3104.260 | 452.208 | 4.563245 |
| SMTP | MetaGPT | 13760735 | 4586911.67 | 738450.79 | 222 | 3614.920 | 139.099 | 4.277547 |

## 5. 按轮次汇总

| 轮次 | 系统 | n | 完整普通通过 | 严格双模式通过 | 普通 + 插桩参考通过 | 普通：通过/计划 | 总 Token（已知） | 响应数 | 项目活动时间合计（秒） | 估算USD |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | SpecForge | 4 | 2/4 (50.00%) | 2/4 (50.00%) | 2/4 (50.00%) | 47/52 (90.38%) | 73357728 | 1862 | 11774.858 | 6.201814 |
| 1 | MetaGPT | 4 | 1/4 (25.00%) | 0/4 (0.00%) | 1/4 (25.00%) | 31/52 (59.62%) | 23459463 | 315 | 14903.615 | 6.185991 |
| 2 | SpecForge | 4 | 2/4 (50.00%) | 2/4 (50.00%) | 2/4 (50.00%) | 40/52 (76.92%) | 58461794 | 1499 | 10940.420 | 5.717199 |
| 2 | MetaGPT | 4 | 1/4 (25.00%) | 0/4 (0.00%) | 1/4 (25.00%) | 30/52 (57.69%) | 20700241 | 324 | 12916.972 | 5.518179 |
| 3 | SpecForge | 4 | 3/4 (75.00%) | 3/4 (75.00%) | 3/4 (75.00%) | 51/52 (98.08%) | 66490693 | 1678 | 12321.590 | 6.012589 |
| 3 | MetaGPT | 4 | 1/4 (25.00%) | 0/4 (0.00%) | 1/4 (25.00%) | 22/52 (42.31%) | 15810240 | 272 | 11888.277 | 4.769564 |

## 6. 逐次原始结果（可用于作图）

每行链接到该次 run.json 或统一 experiment.json。1=通过，0=未达到完整通过；场景分数为通过/有结果项数。NA 项的计划数量仍按对应协议计入总体分母。MetaGPT 第一轮 HTTP 原生 sanitize 的 `0/13*` 带插桩无效标记。SpecForge 的外部插桩列为不适用。

| 记录 | 最终普通构建(1/0) | 框架自身关卡(1/0) | 普通场景 | 原生 sanitize 场景 | 外部 ASan/UBSan 场景 | 完整普通(1/0) | 严格双模式(1/0) | 普通 + 插桩参考(1/0) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [SF-R01-MQTT](/home/ljf/SpecForge/runs/paper/round_01/mqtt_01/run.json) | 1 | 1 | 16/16 | 16/16 | NA | 1 | 1 | 1 |
| [MG-R01-MQTT](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/round_01/mqtt/experiment.json) | 1 | 0 | 16/16 | NA | 16/16 | 1 | 0 | 1 |
| [SF-R01-CoAP](/home/ljf/SpecForge/runs/paper/round_01/coap_01/run.json) | 1 | 1 | 10/10 | 10/10 | NA | 1 | 1 | 1 |
| [MG-R01-CoAP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/round_01/coap/experiment.json) | 1 | 0 | 6/10 | NA | 6/10 | 0 | 0 | 0 |
| [SF-R01-HTTP/1.1](/home/ljf/SpecForge/runs/paper/round_01/http11_01/run.json) | 1 | 1 | 9/13 | 9/13 | NA | 0 | 0 | 0 |
| [MG-R01-HTTP/1.1](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/round_01/http11/experiment.json) | 1 | 1 | 0/13 | 0/13* | NA | 0 | 0 | 0 |
| [SF-R01-SMTP](/home/ljf/SpecForge/runs/paper/round_01/smtp_01/run.json) | 1 | 1 | 12/13 | 12/13 | NA | 0 | 0 | 0 |
| [MG-R01-SMTP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/round_01/smtp/experiment.json) | 1 | 1 | 9/13 | NA | 9/13 | 0 | 0 | 0 |
| [SF-R02-MQTT](/home/ljf/SpecForge/runs/paper/round_02/mqtt_01/run.json) | 1 | 1 | 16/16 | 16/16 | NA | 1 | 1 | 1 |
| [MG-R02-MQTT](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/round_02/mqtt/experiment.json) | 1 | 0 | 16/16 | NA | 16/16 | 1 | 0 | 1 |
| [SF-R02-CoAP](/home/ljf/SpecForge/runs/paper/round_02/coap_01/run.json) | 0 | 0 | NA | NA | NA | 0 | 0 | 0 |
| [MG-R02-CoAP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/round_02/coap/experiment.json) | 1 | 1 | 9/10 | NA | 9/10 | 0 | 0 | 0 |
| [SF-R02-HTTP/1.1](/home/ljf/SpecForge/runs/paper/round_02/http11_01/run.json) | 1 | 1 | 13/13 | 13/13 | NA | 1 | 1 | 1 |
| [MG-R02-HTTP/1.1](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/round_02/http11/experiment.json) | 0 | 0 | NA | NA | NA | 0 | 0 | 0 |
| [SF-R02-SMTP](/home/ljf/SpecForge/runs/paper/round_02/smtp_01/run.json) | 1 | 1 | 11/13 | 11/13 | NA | 0 | 0 | 0 |
| [MG-R02-SMTP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/round_02/smtp/experiment.json) | 1 | 0 | 5/13 | NA | 5/13 | 0 | 0 | 0 |
| [SF-R03-MQTT](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/run.json) | 1 | 1 | 16/16 | 16/16 | NA | 1 | 1 | 1 |
| [MG-R03-MQTT](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/round_03/mqtt/experiment.json) | 1 | 1 | 0/16 | NA | NA | 0 | 0 | 0 |
| [SF-R03-CoAP](/home/ljf/SpecForge/runs/paper/round_03/coap_01/run.json) | 1 | 1 | 10/10 | 10/10 | NA | 1 | 1 | 1 |
| [MG-R03-CoAP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/round_03/coap/experiment.json) | 1 | 0 | 10/10 | NA | 10/10 | 1 | 0 | 1 |
| [SF-R03-HTTP/1.1](/home/ljf/SpecForge/runs/paper/round_03/http11_01/run.json) | 1 | 1 | 13/13 | 13/13 | NA | 1 | 1 | 1 |
| [MG-R03-HTTP/1.1](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/round_03/http11/experiment.json) | 1 | 0 | 0/13 | NA | NA | 0 | 0 | 0 |
| [SF-R03-SMTP](/home/ljf/SpecForge/runs/paper/round_03/smtp_01/run.json) | 1 | 1 | 12/13 | 12/13 | NA | 0 | 0 | 0 |
| [MG-R03-SMTP](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/round_03/smtp/experiment.json) | 1 | 0 | 12/13 | NA | 12/13 | 0 | 0 | 0 |

“框架自身关卡”在 SpecForge 中取 generation_passed（生成、交付一致性及开发关卡），在 MetaGPT 中取 all_selftests_passed（构建、原有自测与生成QA均通过）；两者测试内容不同，不能把这列解释为相同测试套件的通过率。相应计数分别为11/12与4/12。

## 7. 逐次 Token 与统一成本（可用于作图）

整数不使用千位分隔符。`known_total_tokens=input_tokens+output_tokens`；`cache_hit_tokens+cache_miss_tokens=input_tokens`。所有统计包含失败尝试。

| 记录ID | input_tokens | output_tokens | known_total_tokens | cache_hit_tokens | cache_miss_tokens | model_responses | unknown_usage_requests | estimated_peak_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SF-R01-MQTT | 13290557 | 595897 | 13886454 | 11405952 | 1884605 | 359 | 0 | 1.348894 |
| MG-R01-MQTT | 3101053 | 715650 | 3816703 | 2167040 | 934013 | 63 | 0 | 1.151986 |
| SF-R01-CoAP | 18879240 | 621503 | 19500743 | 16641920 | 2237320 | 484 | 0 | 1.516851 |
| MG-R01-CoAP | 2674184 | 723195 | 3397379 | 1771648 | 902536 | 60 | 0 | 1.149225 |
| SF-R01-HTTP/1.1 | 18353102 | 684535 | 19037637 | 16106752 | 2246350 | 472 | 0 | 1.591988 |
| MG-R01-HTTP/1.1 | 9270237 | 1535858 | 10806095 | 7415808 | 1854429 | 116 | 1 | 2.443853 |
| SF-R01-SMTP | 20178039 | 754855 | 20932894 | 17738624 | 2439415 | 547 | 0 | 1.744082 |
| MG-R01-SMTP | 4548608 | 890678 | 5439286 | 3375744 | 1172864 | 76 | 1 | 1.440927 |
| SF-R02-MQTT | 13302732 | 657864 | 13960596 | 11329664 | 1973068 | 355 | 0 | 1.449335 |
| MG-R02-MQTT | 6731590 | 1074559 | 7806149 | 5050619 | 1680971 | 96 | 0 | 1.824066 |
| SF-R02-CoAP | 10801013 | 588696 | 11389709 | 9308416 | 1492597 | 283 | 0 | 1.210065 |
| MG-R02-CoAP | 3680340 | 722100 | 4402440 | 2876160 | 804180 | 82 | 0 | 1.125031 |
| SF-R02-HTTP/1.1 | 19099209 | 725311 | 19824520 | 16712960 | 2386249 | 511 | 0 | 1.686526 |
| MG-R02-HTTP/1.1 | 3582076 | 728775 | 4310851 | 2511488 | 1070588 | 75 | 0 | 1.210775 |
| SF-R02-SMTP | 12671012 | 615957 | 13286969 | 10779520 | 1891492 | 350 | 0 | 1.371273 |
| MG-R02-SMTP | 3245254 | 935547 | 4180801 | 2509952 | 735302 | 71 | 0 | 1.358307 |
| SF-R03-MQTT | 15509854 | 705783 | 16215637 | 13189760 | 2320094 | 415 | 0 | 1.622106 |
| MG-R03-MQTT | 1616435 | 499380 | 2115815 | 1248635 | 367800 | 46 | 0 | 0.717088 |
| SF-R03-CoAP | 15816351 | 618545 | 16434896 | 13748864 | 2067487 | 418 | 0 | 1.444993 |
| MG-R03-CoAP | 2016071 | 464838 | 2480909 | 1420544 | 595527 | 57 | 0 | 0.744987 |
| SF-R03-HTTP/1.1 | 15903291 | 664827 | 16568118 | 13847552 | 2055739 | 413 | 0 | 1.497599 |
| MG-R03-HTTP/1.1 | 5973492 | 1099376 | 7072868 | 4360960 | 1612532 | 94 | 0 | 1.829177 |
| SF-R03-SMTP | 16620954 | 651088 | 17272042 | 14692864 | 1928090 | 432 | 0 | 1.447890 |
| MG-R03-SMTP | 3180346 | 960302 | 4140648 | 2136576 | 1043770 | 75 | 0 | 1.478313 |

统一费率来自留存的 [pricing_verified.json](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/batch_records/generation_round_02/reports/pricing_verified.json)，不查询或采用当前报价。每百万Token：缓存输入0.006 USD、未缓存输入0.30 USD、输出1.20 USD。计算式：`estimated_peak_usd=(cache_hit_tokens*0.006+cache_miss_tokens*0.30+output_tokens*1.20)/1000000`。估算使用未舍入值聚合，表中USD保留6位小数；合计不要求等于逐行已舍入数值之和。不同日期、峰谷时段、未知请求与账单差异不据此推断。

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

SpecForge 的 generation_active_seconds 对应索引 generation_wall_seconds，包含 Facts→Design→Specs→Code→Verify 以及内部规格/实现修复；不是仅首次代码生成。MetaGPT 分列原始生成和累计反馈。SpecForge 的规格修复3次、实现修复6次；MetaGPT 的WriteCode实现修复16次、DebugError测试修复16次，共32次。修复类别和路径不同，不把不存在的类别填0，也不视为等价工作量。

| 系统 / 记录 | 生成阶段Token | 反馈阶段Token | 生成阶段响应 | 反馈阶段响应 | 生成活动秒数 | 反馈活动秒数 | 独立测量累计秒数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SpecForge（完整流程） | 198310215 | NA | 5039 | NA | 35036.868 | NA | NA |
| MetaGPT（全部12条） | 45261468 | 14708476 | 792 | 119 | 33488.088 | 6220.776 | 113.801 |

并行墙钟不与项目活动秒数混合：SpecForge 三轮原记录墙钟之和9990.407秒；MetaGPT 留存的前两轮反馈批次墙钟1420.480秒，第三轮生成+反馈批次墙钟4370.256秒。两组范围不同，本表不据此给出整体墙钟加速比。MetaGPT 独立测量113.801秒包含初次外部测量开销；SpecForge protocol_wall_seconds-generation_wall_seconds 还含外层编排，因此不作为同口径独立验收时长。

## 9. 解释结果时必须保留的限制

- SpecForge 第二轮CoAP在语义审查响应上限处停止，未编码、构建或独立验收；10个计划场景保留在每模式分母。
- MetaGPT 第二轮HTTP最终构建失败，普通和插桩验收未执行；第三轮MQTT/HTTP运行文件不符合共同TASK约定，普通验收分别实际记录0/16、0/13。未移动或改名交付来改变结果。
- MetaGPT 第一轮HTTP原始生成因investment=3.0预算结束，NoMoneyException已被原生包装捕获；generate_repo返回、文件写出和审查完成不代表无异常完成。最终行包含该原始停止及后续反馈。
- MetaGPT 第一轮HTTP和SMTP各有一次历史中断请求用量未知。59,969,944是已知Token合计，不是完整消耗；其成本比较同样仅限已知响应。
- MetaGPT CoAP两次QA初始化失败（0项运行），第三轮HTTP QA未报告数量；原生QA与独立协议验收保持分开。所有已返回用量、适配调试和续跑开销保留。
- 每协议仅3次观察，两系统轮次不同时。均值/标准差用于描述及误差条原始数据，不构成统计显著性或同时环境下的因果结论。

## 10. 核对记录与源索引指纹

本表只读取留存数据，未调用模型、重跑构建/验收或修改交付。核对了两个CSV和JSON的12条对应记录、24条原始用量/响应数及SpecForge全部5039对请求/响应的用量合计、逐次评测分数、MetaGPT生成→反馈→最终评测关联、12组输入实际字节哈希与模型配置、11份已执行SpecForge评测的验收器哈希，以及合计与留存汇总的一致性。MetaGPT完整归档的既有审计见 [ARCHIVE_VALIDATION.json](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/ARCHIVE_VALIDATION.json)；此次未重新执行完整18730文件归档校验。

| 源索引 | SHA-256 |
| --- | --- |
| [runs/paper/summary.json](/home/ljf/SpecForge/runs/paper/summary.json) | 31ba5e7723848fd8aceab97d76661cffcb1cd9b7796d1dd4829c0089d99c129e |
| [runs/paper/runs.csv](/home/ljf/SpecForge/runs/paper/runs.csv) | f84ba79425c5eb7de314a103b85cccad6fec49f9b90e05e204f6581b52d58ac1 |
| [expriments/RQ1/metagpt/paper_data/three_rounds_20261009/complete_experiments.json](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/complete_experiments.json) | e625069a97ec09159f70e9717508a3c4f893d3ae9d352c62520543efa0ddb11c |
| [expriments/RQ1/metagpt/paper_data/three_rounds_20261009/complete_experiments.csv](/home/ljf/SpecForge/expriments/RQ1/metagpt/paper_data/three_rounds_20261009/complete_experiments.csv) | d3c0b1de22f094cbc961c949a0a97e6770ad933ed2a5e6ed11755b838c03c492 |
