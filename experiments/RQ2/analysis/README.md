# RQ2 一致性指标与现有数据分析

分析日期：2026-10-12。读取 `runs/paper` 的12次历史运行，以及
`expriments/RQ2/runs/mqtt_gpt6_luna_max_20261012074057Z/mqtt`。
本分析不调用模型、不重新构建或验收、不修改历史记录和交付项目。

现有记录能够支持量化，但目前不能据此证明“面向实现的中间规格减少实现不一致”。
三次历史MQTT的共同类别确认项数为2、6、2，新NoSpec为2；四次运行的最终独立验收均通过。
这不是同底座、同实验条件的重复对照。

## 1. 统计单位与证据规则

采用已确认的范围：仅已证实的工件/组件间矛盾。

每项必须给出两侧不相容的契约、声明或预期及原始证据。组件包括函数、类型、
构建/启动入口以及测试helper。仅发现一条测试失败或编译错误不足以计数。
测试调用违反已有接口/状态前置条件，或者测试oracle与正确实现不相容，可以计数；
测试预期正确、实现单独有算法/协议错误，则不属于本次狭义统计。

按独立的矛盾义务计数。同一接口/语义约束在多个报文、调用点、普通/Sanitizer构建、
重复检查中出现，合并为一项；涉及不同契约的矛盾分别计数。
例如两种报文都被错误认为“body缺失仍为COMPLETE”合并；类型提取未屏蔽Version位与
把Message ID当作Token是两条独立布局约束。同一个修正响应可以处理多项。

分类如下：

| 字段 | 含义 | 两组共同主指标 |
|---|---|---|
| `interface_call` | 接口声明/使用、返回类型、调用参数、前置状态、所有权或启动约定不相容 | 是 |
| `implementation_test` | 实现与测试oracle、fixture或测试状态假设不相容 | 是 |
| `inherited_spec` | Coder阶段暴露的规格内部矛盾，例如LOGIC与向量、调用者与被调用者规格冲突 | 单独报告 |

代码单独违反协议原文、一般算法错误、缺失尚未生成的文件、无用变量/参数、编译选项、
运行环境以及临时调试脚本错误均不计。新NoSpec中的CONNECT偏移、通配符算法修正和
unused-parameter修正因此未计入。

这里的数值是**证据标注v1得到的已确认观察下界**，没有穷尽全部历史快照或证明不存在
其他矛盾。标注由本次分析生成，尚未由独立标注者复核。零表示本次没有确认该类别事件，
不能解释为不存在该类别问题。

## 2. 可从两组记录提取的量化指标

| 指标 | 定义 | 数据来源与边界 |
|---|---|---|
| 实现阶段矛盾数 `C` | 每次运行的 `interface_call + implementation_test` | 源码/接口、响应中的改前/改后工件、命令和开发验证；需要语义复核 |
| 矛盾发生运行比例 `IR` | `C>0`的运行数 / 已复核且进入Coder的运行数 | 未进入代码的运行记N/A；未标注的新运行记unknown |
| 修正响应占比 `CR` | 至少含一项共同类别修正的Coder响应数 / code与repair响应总数 | 按响应去重；不把外层repair次数当作事件数 |
| 修正响应Token占比 `CT` | 上述响应的input+output Tokens / 全部Coder input+output Tokens | reasoning已属于output，不再相加；上下文和同响应其他活动也计入 |
| 规格内部冲突 `SC` | `inherited_spec`项数 | 单列，不并入共同主指标及其修正响应用量 |

`CR/CT`描述**包含修正操作的响应用量**，不是完整修复成本或因果增量成本。
定位、读文件、复测等开销可能发生在其他响应；一个标注响应也可能含无关工作。
Planner规格回修不在Coder用量分母内，不能把整个规格/实现repair job的Token数解释为纯不一致修复成本。

同时保留生成关卡通过、独立验收通过和每种模式的场景通过数，作为质量结果。
不以最终通过、外层修复轮次或任意失败命令次数替代上述一致性指标。
不把一次普通与一次Sanitizer执行当成两次独立生成样本。

## 3. MQTT 同协议结果

以下计数为已确认观察下界。`SC`不计入`C`。

| 运行 | 底座 | `C` | `SC` | Coder响应 | 修正响应 | `CR` | 修正响应Tokens | `CT` | 普通/Sanitizer验收 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| SpecForge round_01 | DeepSeek/high | 2 | 0 | 129 | 3 | 2.33% | 93,709 | 2.08% | 各16/16 |
| SpecForge round_02 | DeepSeek/high | 6 | 0 | 130 | 5 | 3.85% | 163,640 | 3.30% | 各16/16 |
| SpecForge round_03 | DeepSeek/high | 2 | 2 | 116 | 2 | 1.72% | 119,706 | 2.94% | 各16/16 |
| NoSpec 本次 | gpt-6-luna/max | 2 | 0 | 43 | 2 | 4.65% | 14,221 | 1.11% | 各16/16 |

历史MQTT的`C`均值为3.33，范围2–6；新NoSpec为2。历史`IR`为3/3，新NoSpec为1/1。
这些数值未显示规格组在本指标上的优势；响应占比与Token占比也不能在不同底座之间
直接解释为规格作用。NoSpec只有一次运行，无法估计其运行间稳定性。

已核对四次运行的TASK、REQUIREMENTS、protocol.pdf哈希分别完全一致，且MQTT独立验收器哈希相同。
历史模型记录为`deepseek-flash/high`，新模型为`gpt-6-luna/max`；框架哈希亦不完全一致。
当前原系统首次Coder预算为180，NoSpec已确认预算为300；历史记录未单独保存预算字段，
不能把当前源码数值当作每个历史版本预算已经核实的证明。
正式比较需要按协议、底座和已记录实验条件分层解释，并补齐重复对照。
本次保留用户确认的300次NoSpec预算，不调整生成设置。

## 4. 新 NoSpec 的两项具体事件及成本

| ID | 相互矛盾的假设 | 检出 → 修正 → 复测 |
|---|---|---|
| N01 | `bad_flags`用SUBSCRIBE正确flags的0x82，却要求仅检查frame/flags的decoder返回INVALID；改为错误flags的0x80 | response24 → 27 → 28 |
| N02 | 测试期望单层`+`匹配topic `/`的两个空层级；实现和协议原文要求只匹配一层 | response28 → 31 → 32 |

N01不意味着`82 00`是合法完整SUBSCRIBE。frame decoder检查帧/固定flags，broker另外校验
SUBSCRIBE body；原负例混淆了这两层职责。原文证据在 `documents/page_040.txt`。
N02原文证据在 `documents/page_057.txt` 与 `page_058.txt`。
原生失败日志分别为 `logs/01_code/commands/command_0003.log` 与 `command_0004.log`。
两项最终均由测试修正解决。

response27和31共输入13,840、输出381，即14,221 Tokens；模型计时16.316秒。
占43次响应的4.65%，占1,281,922个Coder Tokens的1.11%。

从首次失败后的response25到两项单测通过的response32，闭区间为8次响应、98,211 Tokens、
模型计时90.215秒，占Coder Tokens的7.66%。这是包含checkpoint、读取、修正和复测的
**观察到的反馈窗口**，不是纯修复成本，也不是控制器总墙钟耗时。

最终生成关卡通过，外层 `implementation_repairs=0`。这与存在两项已修正矛盾并不冲突，
因为它们都在首次Coder job内完成。`verify_001`中的重复失败和两种模式执行没有增加事件数。
最终独立验收为普通16/16、Sanitizer16/16。

## 5. 全部历史记录的描述性汇总

| 历史运行 | `C` | `SC` | Coder响应 | 普通验收 | Sanitizer验收 |
|---|---:|---:|---:|---|---|
| round_01 CoAP | 6 | 2 | 153 | 10/10 | 10/10 |
| round_01 HTTP/1.1 | 9 | 0 | 179 | 9/13 | 9/13 |
| round_01 MQTT | 2 | 0 | 129 | 16/16 | 16/16 |
| round_01 SMTP | 5 | 2 | 171 | 12/13 | 12/13 |
| round_02 CoAP | N/A | N/A | 0 | 未执行 | 未执行 |
| round_02 HTTP/1.1 | 3 | 0 | 154 | 13/13 | 13/13 |
| round_02 MQTT | 6 | 0 | 130 | 16/16 | 16/16 |
| round_02 SMTP | 1 | 0 | 113 | 11/13 | 11/13 |
| round_03 CoAP | 3 | 0 | 126 | 10/10 | 10/10 |
| round_03 HTTP/1.1 | 4 | 0 | 138 | 13/13 | 13/13 |
| round_03 MQTT | 2 | 2 | 116 | 16/16 | 16/16 |
| round_03 SMTP | 7 | 0 | 176 | 12/13 | 12/13 |

12次历史运行中11次进入Coder。确认共同类别48项（接口/调用17项，实现/测试31项），
另有规格内部冲突6项，共54项；`IR=11/11`。未进入Coder的CoAP不是“零矛盾成功样本”。
生成关卡11/12通过；端到端独立验收7/12通过，已执行验收中的通过比例为7/11。
这些混合协议数字是历史数据描述，不能与单次NoSpec MQTT直接作为同分布对照。

规格冲突来自三个Coder报告：round_01 CoAP、round_01 SMTP、round_03 MQTT，各两项。
其原始证据均为对应运行的 `reports/05_code_spec_gap_evidence.log`。
其他典型事件包括CoAP运输返回值与编码返回值类型混用、HTTP访问不透明conn_t字段、
测试发送二进制数据却使用strlen、测试复用旧mqtt_frame，以及测试误解缓冲区所有权。

部分命令通过 `tail` 或其他包装返回exit_code=0但内部已有失败输出。
本次复核同时读取开发验证、原生命令完整日志以及修正工件，而非仅筛选非零退出码。
历史 `source_records` 的旧路径/轮次标签未作为主键，主键使用当前实际运行目录和原始run.json。

## 6. 复现与后续数据接入

```bash
python -B expriments/RQ2/analysis/analyze.py --out expriments/RQ2/analysis/results
```

产物：

- `results/metrics.csv`：逐运行指标，可导入统计工具。
- `results/metrics.json`：分类计数、原始用量、输入/框架哈希、验收场景计数和反馈窗口。
- `results/incidents.csv`：逐事件冲突描述、原始证据路径与Coder修正响应。
- `annotations.json`：本次证据标注，可逐项复核、修改后重算。

追加已完成的新运行：

```bash
python -B expriments/RQ2/analysis/analyze.py --run /home/ljf/SpecForge/expriments/RQ2/runs/NEW_BATCH/mqtt --out /tmp/rq2_metrics
```

脚本自动提取该运行的生成/验收结果和用量；未经事件复核的一致性字段为空且
`audit_status=unreviewed`。需要以相同规则将事件与证据加入annotations.json后才能得到一致性计数，
不能用正则或修复轮次自动代替语义判断。

本次消融删除Facts、Design、Specs及相关审查/规格回修整条机制，论文归因范围应与此相符；
它不能单独隔离某个规格字段的作用。当前结果验证了这些指标可以从两组既有记录提取，
尚不能作为“显式规格降低不一致”的实证结论。
