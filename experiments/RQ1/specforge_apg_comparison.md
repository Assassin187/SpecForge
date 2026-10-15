# RQ1：SpecForge 与 APG 的 ICMP 五轮实验原始数据

## 1. 生成与共同协议场景验收

| 方法 | 轮次 | generation_success (1/0) | normal_pass / 14 | sanitize_pass / 14 | normal_wire_pass / 27 | sanitize_wire_pass / 27 | normal_wire_executed / 27 | sanitize_wire_executed / 27 | end_to_end_success（生成且两阶段14场景全通过，1/0） |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| APG | 1 | 1 | 5 | 5 | 13 | 13 | 27 | 27 | 0 |
| APG | 2 | 1 | 5 | 5 | 13 | 13 | 27 | 27 | 0 |
| APG | 3 | 1 | 5 | 5 | 13 | 13 | 27 | 27 | 0 |
| APG | 4 | 1 | 0 | 0 | 0 | 0 | 27 | 27 | 0 |
| APG | 5 | 1 | 0 | 0 | 0 | 0 | 27 | 27 | 0 |
| SpecForge | 1 | 1 | 14 | 14 | 27 | 27 | 27 | 27 | 1 |
| SpecForge | 2 | 1 | 14 | 14 | 27 | 27 | 27 | 27 | 1 |
| SpecForge | 3 | 0 | NA | NA | NA | NA | NA | NA | 0 |
| SpecForge | 4 | 1 | 14 | 14 | 27 | 27 | 27 | 27 | 1 |
| SpecForge | 5 | 1 | 13 | 13 | 26 | 26 | 26 | 26 | 0 |

## 2. 生成时间与用量

| 方法 | 轮次 | generation_seconds | model_seconds | response_count | input_tokens | output_tokens | total_tokens |
| --- | --- | --- | --- | --- | --- | --- | --- |
| APG | 1 | 120.934 | 120.837 | 3 | 12485 | 35025 | 47510 |
| APG | 2 | 71.969 | 71.884 | 2 | 11980 | 23504 | 35484 |
| APG | 3 | 79.959 | 79.906 | 2 | 12139 | 26941 | 39080 |
| APG | 4 | 458.096 | 458.049 | 2 | 13054 | 15772 | 28826 |
| APG | 5 | 499.599 | 499.546 | 2 | 12736 | 20364 | 33100 |
| SpecForge | 1 | 1919.152 | 1879.307 | 299 | 10971543 | 469845 | 11441388 |
| SpecForge | 2 | 2113.483 | 2071.882 | 400 | 14746762 | 515085 | 15261847 |
| SpecForge | 3 | 2297.897 | 2271.608 | 377 | 14367568 | 585680 | 14953248 |
| SpecForge | 4 | 5427.984 | 5417.197 | 197 | 7466963 | 221321 | 7688284 |
| SpecForge | 5 | 5234.855 | 5226.075 | 181 | 6119507 | 211011 | 6330518 |

| 方法 | 轮次 | cache_hit_tokens | cache_miss_tokens | reasoning_tokens |
| --- | --- | --- | --- | --- |
| APG | 1 | 0 | 12485 | 26645 |
| APG | 2 | 6656 | 5324 | 15871 |
| APG | 3 | 6656 | 5483 | 19372 |
| APG | 4 | 11776 | 1278 | 7577 |
| APG | 5 | 10752 | 1984 | 11563 |
| SpecForge | 1 | 9649408 | 1322135 | 184116 |
| SpecForge | 2 | 13023616 | 1723146 | 197292 |
| SpecForge | 3 | 12668544 | 1699024 | 213976 |
| SpecForge | 4 | 6385152 | 1081811 | 111704 |
| SpecForge | 5 | 5270528 | 848979 | 111239 |

## 3. 共同协议场景矩阵（normal / sanitize；1=通过，0=失败，NA=未执行）

| scenario_id | APG_01 | APG_02 | APG_03 | APG_04 | APG_05 | SpecForge_01 | SpecForge_02 | SpecForge_03 | SpecForge_04 | SpecForge_05 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| icmp_echo_request_odd_data | 0 | 0 | 0 | 0 | 0 | 1 | 1 | NA | 1 | 1 |
| icmp_echo_request_even_data | 0 | 0 | 0 | 0 | 0 | 1 | 1 | NA | 1 | 1 |
| icmp_echo_request_empty_data | 0 | 0 | 0 | 0 | 0 | 1 | 1 | NA | 1 | 1 |
| icmp_echo_reply_preservation | 0 | 0 | 0 | 0 | 0 | 1 | 1 | NA | 1 | 1 |
| icmp_destination_unreachable_codes | 1 | 1 | 1 | 0 | 0 | 1 | 1 | NA | 1 | 1 |
| icmp_error_quotation_with_options | 1 | 1 | 1 | 0 | 0 | 1 | 1 | NA | 1 | 1 |
| icmp_time_exceeded_codes | 1 | 1 | 1 | 0 | 0 | 1 | 1 | NA | 1 | 1 |
| icmp_parameter_problem_pointer | 1 | 1 | 1 | 0 | 0 | 1 | 1 | NA | 1 | 1 |
| icmp_source_quench | 1 | 1 | 1 | 0 | 0 | 1 | 1 | NA | 1 | 0 |
| icmp_redirect_gateway | 0 | 0 | 0 | 0 | 0 | 1 | 1 | NA | 1 | 1 |
| icmp_timestamp_request | 0 | 0 | 0 | 0 | 0 | 1 | 1 | NA | 1 | 1 |
| icmp_timestamp_reply_preservation | 0 | 0 | 0 | 0 | 0 | 1 | 1 | NA | 1 | 1 |
| icmp_information_request_reply | 0 | 0 | 0 | 0 | 0 | 1 | 1 | NA | 1 | 1 |
| icmp_checksum_recomputation | 0 | 0 | 0 | 0 | 0 | 1 | 1 | NA | 1 | 1 |

## 4. 完整套件验收（16个场景）

| 方法 | 轮次 | normal_pass | normal_fail | normal_not_applicable | sanitize_pass | sanitize_fail | sanitize_not_applicable |
| --- | --- | --- | --- | --- | --- | --- | --- |
| APG | 1 | 5 | 9 | 2 | 5 | 9 | 2 |
| APG | 2 | 5 | 9 | 2 | 5 | 9 | 2 |
| APG | 3 | 5 | 9 | 2 | 5 | 9 | 2 |
| APG | 4 | 0 | 14 | 2 | 0 | 14 | 2 |
| APG | 5 | 0 | 14 | 2 | 0 | 14 | 2 |
| SpecForge | 1 | 16 | 0 | 0 | 16 | 0 | 0 |
| SpecForge | 2 | 16 | 0 | 0 | 16 | 0 | 0 |
| SpecForge | 3 | NA | NA | NA | NA | NA | NA |
| SpecForge | 4 | 16 | 0 | 0 | 16 | 0 | 0 |
| SpecForge | 5 | 15 | 1 | 0 | 15 | 1 | 0 |

## 5. 五轮汇总

| 方法 | 尝试次数 | 生成成功率 | 端到端成功率 | 已执行验收轮数 | 已执行验收的共同场景通过率（每阶段） | 生成时间均值 ± 样本SD (s, ddof=1) | 总tokens均值 ± 样本SD (ddof=1) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| APG | 5 | 5/5 (100.00%) | 0/5 (0.00%) | 5 | 15/70 (21.43%) | 246.111 ± 213.773 | 36800.000 ± 7051.756 |
| SpecForge | 5 | 4/5 (80.00%) | 3/5 (60.00%) | 4 | 55/56 (98.21%) | 3398.674 ± 1770.739 | 11135057.000 ± 4082482.578 |

## 6. 原始记录索引

| 方法 | 轮次 | 生成与用量记录 | 验收记录 | 生成计时记录 |
| --- | --- | --- | --- | --- |
| APG | 1 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_01/run.json) | [验收记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_01/reports/independent/summary.json) | [计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_01/run.json) |
| APG | 2 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_02/run.json) | [验收记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_02/reports/independent/summary.json) | [计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_02/run.json) |
| APG | 3 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_03/run.json) | [验收记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_03/reports/independent/summary.json) | [计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_03/run.json) |
| APG | 4 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_gpt6luna_max_01/run.json) | [验收记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_gpt6luna_max_01/reports/independent/summary.json) | [计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_gpt6luna_max_01/run.json) |
| APG | 5 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_gpt6luna_max_02/run.json) | [验收记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_gpt6luna_max_02/reports/independent/summary.json) | [计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_gpt6luna_max_02/run.json) |
| SpecForge | 1 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_01/run.json) | [验收记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_01/evaluation/evaluation_002/report.json) | [计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_01/reports/experiment/experiment.json) |
| SpecForge | 2 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_02/run.json) | [验收记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_02/evaluation/evaluation_003/report.json) | [计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_02/reports/background/status.json) |
| SpecForge | 3 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_03/run.json) | NA | [计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_03/reports/background/status.json) |
| SpecForge | 4 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_gpt6luna_max_01/run.json) | [验收记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_gpt6luna_max_01/evaluation/evaluation_002/report.json) | [计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_gpt6luna_max_01/reports/background/status.json) |
| SpecForge | 5 | [run.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_gpt6luna_max_02/run.json) | [验收记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_gpt6luna_max_02/evaluation/evaluation_002/report.json) | [计时记录](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_gpt6luna_max_02/reports/background/status.json) |

## 7. APG 手写校验和函数修正复验（保留网络序返回约定）

| APG轮次 | normal_before / 14 | normal_after / 14 | sanitize_before / 14 | sanitize_after / 14 | wire_before / 27（每阶段） | wire_after / 27（每阶段） | 报文自身校验和正确数before / 27（每阶段） | 报文自身校验和正确数after / 27（每阶段） | 手写函数固定探针after / 3 | ASan/UBSan诊断数after | 复验记录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 5 | 5 | 5 | 5 | 13 | 13 | 24 | 27 | 3 | 0 | [record.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_01/reports/checksum_fixed/record.json) |
| 2 | 5 | 5 | 5 | 5 | 13 | 13 | 24 | 27 | 3 | 0 | [record.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_02/reports/checksum_fixed/record.json) |
| 3 | 5 | 5 | 5 | 5 | 13 | 13 | 24 | 27 | 3 | 0 | [record.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_03/reports/checksum_fixed/record.json) |
| 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | [record.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_gpt6luna_max_01/reports/checksum_fixed/record.json) |
| 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | [record.json](/home/ljf/SpecForge/expriments/RQ1/icmp/runs/apg_gpt6luna_max_02/reports/checksum_fixed/record.json) |
