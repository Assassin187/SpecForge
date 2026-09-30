# SpecForge

本实现按顺序完成：任务、需求和协议 PDF → 带原文位置的事实 → 三层实现
Spec 和公共头文件 → 多文件 C 项目 → 独立构建与 MQTT 行为验收。
自动事实归并为最多 32 条；最小案例设计限制为 3–4 个源文件、最多 3 个公共
头文件和 24 个规划函数，将主要预算留给协议行为和实际编码。
Facts、Planner、Coder 共用一个原生工具调用循环。Planner 分别执行结构设计
和行为 Spec 两个任务；每个任务使用新的模型上下文，通过磁盘产物交接。

第一版固定为 Linux C99 的 MQTT 3.1.1 最小 broker：匿名 Clean Session=1、
无 Will、QoS 0、精确及 `+`/`#` 主题匹配、SUBSCRIBE/SUBACK、PING 和清理。
完整范围见 `cases/mqtt_min/REQUIREMENTS.md`。不包含 QoS 1/2、retained、
持久会话、UNSUBSCRIBE、认证、TLS 或保活超时调度。

## 运行

在本仓库目录使用 Python 3.11+；本轮验证环境为 Python 3.12.9。
Python 依赖固定为 `openai==1.97.0`、`jsonschema==4.24.0`。
系统需要 GCC、make、pdftotext、bubblewrap 和 Mosquitto pub/sub 工具。
本机 bubblewrap 必须能建立 user、PID、network namespaces，并支持本地回环。
密钥仅从环境变量 `DS_API` 读取，不保存到运行目录或命令环境。

```bash
python -m pip install -e .
export DS_API='<your-key>'
python -m specforge run \
  --task cases/mqtt_min/TASK.md \
  --requirements cases/mqtt_min/REQUIREMENTS.md \
  --protocol cases/mqtt_min/spec/mqtt-v3.1.1-os.pdf \
  --out runs/my-mqtt
```

输出目录必须不存在。`--until prepare|facts|design|specs|verify` 可以停在某个
阶段；默认执行到验收。任何必需检查失败返回非零退出码。

```bash
python -m specforge resume --run runs/my-mqtt
python -m specforge validate --specs runs/my-mqtt/specs/r001
python -m specforge code --specs assets/mqtt_reference/bundle --out runs/my-manual
python -m specforge verify --project runs/my-mqtt/project
python -m unittest discover -s tests -v
```

`code` 是人工或已有 Spec 的下游验证，标记 `spec_only`，使用六个历史场景及
Sanitizer 检查。完整自动链路及 `verify` 使用 16 个场景。恢复保留已通过且
哈希未变的阶段，沿用原有响应和修复预算，标记 `resumed=true`、`fresh=false`。
实现、提示词、Schema 或测试发生变化时，恢复拒绝混合版本，需新建运行。
失败阶段的外部文件编辑被记录为 `manual_edits`；不能计入 fresh 结果。

生成项目的构建和启动：

```bash
cd runs/my-mqtt/project
make clean
make
./mqtt_broker 1883
```

## 文件与责任

| 文件 | 责任 |
|---|---|
| specforge/llm.py | DeepSeek Chat Completions，原生 tool_calls、reasoning_content 和用量 |
| specforge/agent.py | 共享循环、响应上限、磁盘检查点、新上下文 |
| specforge/tools.py | 按阶段限定的文件工具、bubblewrap 命令、超时和日志 |
| specforge/documents.py | 冻结三个输入、PDF 物理页/行索引、证据位置检查 |
| specforge/specs.py | 原 Schema、引用和归属、GCC ABI 探针、发布 bundle、构建骨架 |
| specforge/pipeline.py | 固定阶段、run.json、恢复、一次 Spec 回修和三次实现修复 |
| specforge/mqtt_check.py | 独立 MQTT 报文测试、进程清理和 Sanitizer 诊断 |
| prompts/ | 三个角色的任务前缀 |
| schemas/ | 未修改的三个旧 Spec Schema 和配套产物 Schema |
| assets/ | 完整原人工 Spec、规范化人工 bundle、公共头文件、离线事实与迁移账本 |

公共头文件由 Planner 写出，分别和共同包含时经过 GCC 检查。额外探针检查
函数声明、参数类型、结构成员、回调和枚举值。通过发布的 Spec bundle 以
哈希冻结。Coder 获得整个项目的编码能力，但不能改公共接口或 Makefile；
它的视图不包含原始 PDF、抽取协议文本、旧代码、人工参考答案或 gold facts。
运行说明由控制器按固定最小范围和实际源码清单生成并冻结；首个自动试运行
虽然通过了行为测试，模型 README 仍错误声称支持 UNSUBSCRIBE。因此 README
与头文件、Makefile 一起只读提供，验收比较其实际内容，避免未实现功能的声明。
Schema、位置和接口检查确认结构一致性；协议行为正确性由实际运行验证。

运行目录包含 `inputs/`、`documents/`、`facts/`、`design_work/`、`specs/r001/`、
`project/`、`snapshots/`、`reports/`、`logs/`、`run.json`。发生一次 Spec 回修时
增加 `specs/r002/`，同时保存原 Spec 和项目快照。完整 API 请求/响应、工具结果
和命令输出保留在日志中；模型完成声明不能改变关卡结果。

同一模型响应里的多个工具调用依次执行。响应被截断时不执行其中的调用。
文件读取默认 160 行，工具返回约 8000 字符，完整命令输出可通过日志再读。
每八次响应或最近输入超过 32000 tokens 时要求更新简短 WORKLOG.md，完成
当前工具交互后重建上下文。Facts/结构/行为/初始代码分别最多 40/40/80/120
次响应；每次实现修复最多 20 次。达到上限或超出修复边界保留失败产物。

## 独立验收

每个场景启动独立 broker 进程，严格比较完整报文、主题、payload、标志和长度，
并保存实际输入、期望、返回和进程日志。正常及 ASan/UBSan 构建分别执行相同
场景，SIGTERM 正常退出时启用 LSan。缺少必要外部工具为 `environment_blocked`，
不会计为通过。最终交付恢复为正常构建的可执行文件。

历史六场景为跨客户端转发、EOF 前完整缓冲处理、错误报文后存活、断开处理、
连续烟雾测试和真实 Mosquitto 客户端互通；新增十场景验证连接顺序、SUBACK
顺序、二进制和空 payload、`+`、`#`、分段、合并、PING、订阅清理和异常隔离。

稳定性实验冻结源代码、提示词、Schema、验收和输入后，进行三个全新运行：
不恢复、不人工修改、不读取金标准或前次项目，使用同一模型设置和执行边界。
失败序列完整保留。三次全部通过才报告本轮最小案例可复现，不声称完整 MQTT
合规或模型输出具有字节级确定性。

## 研究方法与参考资产

保留 SpecForge 的“规范 → 中间 Spec → 项目级协议代码”故事，以及
PROTOCOL_MODULE_SPEC、FILE_SPEC、FUNCTION_SPEC 的原有字段体系。实现改为
Planner 写实际头文件并由编译器核对，Coder 联合实现整项目。没有继承旧项目的
多级注册、规划降级、运行图推导和按函数孤立生成系统。

RepLLM 的阶段职责、文件共享、接口先行和执行反馈用于本链路；没有增加 XML
解析或另一层 SCoT 表示。Kimi 实验说明完整项目编码适合交给成熟编码角色推进；
验收由控制器维护，并独立于 Coder 的自测和完成声明。
行为规划任务直接携带已批准的需求描述；EOF 处理顺序和连接关闭责任必须在
具体函数算法中闭合。Schema 语法以紧凑原文提供，避免每次检查点后重新翻页。

人工参考规范的空 DOC_REF 保持其来源限制；只有从公共头文件能直接确认的
opaque TYPE_SPEC 被补齐。原 `../` 路径规范化为项目相对路径，失效 publish
TRACE_REF 改为已有 handle_packet。原始文件、哈希和迁移记录完整保留。
人工 Spec→代码结果不计入自动事实或自动规划成功率。

用量分别记录输入、缓存命中、未命中、输出和推理 tokens；推理是输出的子项。
供应商缺失字段记为 null，不伪造为零。请求次数以 SDK 请求为单位；SDK 自身
最多两次传输重试，没有额外模型重试器、模型切换或金标准替换。


实际 API 测试发现 16384 的上限可能被 high 模式思考全部消耗而没有工具调用，
因此执行版本将 max_tokens 调整为 32768；模型、思考级别及响应次数和修复边界
保持一致。该参数同时包含思考及最终输出，不按纯代码输出预算理解。

## 已完成的验收结果

冻结版本的三个独立 fresh 运行全部通过。从基础三文件开始，完成事实、三层
Spec、公共接口、多文件 C 项目和独立验收，正常及 Sanitizer 各 16/16 场景，
共 96 次必需场景执行。没有恢复、人工修改或金标准输入；实现修复次数分别
为 0、2、3，Spec 回修均为 0。16 项框架测试通过。

运行耗时约 19.8–24.8 分钟；输入约 389–486 万 tokens，其中缓存未命中约
95–127 万，输出约 27–33 万（已包含推理）。这些是成本基线，尚不能声称
比 Kimi 或旧 SpecForge 更省 tokens。实际 fresh 序列未触发 Spec 回修，
该路径目前由控制器边界测试覆盖。

完整日志、失败原型、逐阶段耗时和用量见
[验收记录](runs/RESULTS.md) 与
[机器可读审计](runs/stability_01/report.json)。
可直接构建和运行的示例位于
[首个生成项目](runs/stability_01/fresh_01/project/README.md)。
