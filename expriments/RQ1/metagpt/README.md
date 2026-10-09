# RQ1 MetaGPT 使用环境

目录：`/home/ljf/SpecForge/expriments/RQ1/metagpt`。

这里配置的是官方 MetaGPT 软件公司 SOP：ProductManager → Architect →
ProjectManager → Engineer，可选 QaEngineer。为论文对比固定使用官方标签
[`v0.8.2`](https://github.com/FoundationAgents/MetaGPT/tree/v0.8.2)，提交
`df9bc1858f7d396a7eef5d9718cab7587b63fd62`；不使用 main 分支中变更后的工作流。
注意这个标签的 `setup.py` 仍声明版本 `0.8.1`，所以 `pip show metagpt` 显示
0.8.1；复现时以标签和提交哈希为准。

## 模型与入口

请使用本目录的 `run.py`。它读取现有 `specforge.llm.ModelConfig`，直接复用
SpecForge 的模型、服务地址、密钥环境变量、推理强度及正式输出上限：

| 配置 | 正式运行 |
| --- | --- |
| 模型 | `deepseek-flash` |
| 服务地址 | `https://api.deepseek.com` |
| 密钥 | 环境变量 `DS_API`，不写入文件 |
| 推理 | thinking enabled，`reasoning_effort=high` |
| 流式 | 关闭 |
| 单次输出上限 | 65,536 Token |
| Python | 本地独立 `.venv`，3.11.16 |

MetaGPT 0.8.2 的 LLMConfig 没有 DeepSeek thinking/effort 字段。
`run.py` 通过官方 provider 注册机制扩展原 OpenAILLM，将这两个参数放入
SDK 的 `extra_body`，并保证非流式调用。原始生成轮次使用官方角色、动作、提示词和软件公司编排；
本次新增的 C99 原生反馈适配见本文末尾。
它也使内部的 `Config.default()` 读取同一配置。不会读取或覆盖
`~/.metagpt/config2.yaml`，也不改动 SpecForge 的模型调用代码或已有 Conda 环境。
直接调用 `.venv/bin/metagpt` 会绕过这些配置，请使用下面的入口。

```bash
cd /home/ljf/SpecForge/expriments/RQ1/metagpt

# 如当前 shell 尚未设置密钥，使用与 SpecForge 相同的 DS_API。
# 可交互输入，避免把密钥放入 shell 历史：
read -rsp 'DS_API: ' DS_API
export DS_API
printf '\n'

# 帮助和检查不调用模型；检查无需密钥。
.venv/bin/python run.py --help
.venv/bin/python run.py --check

# 正式运行入口；将需求字符串替换为实验输入。
.venv/bin/python run.py '你的项目需求' \
  --project-name rq1_trial --n-round 5 --investment 0.10
```

生成文件在本目录的 `workspace/`，日志在 `logs/`。请为每次实验使用新的
project-name，并另行保存正式实验的输入、生成结果和独立验收记录。

## 安装兼容性与复现

独立环境安装了官方基础依赖，不安装可选 RAG、Android、搜索及测试扩展。
当前 Node、npm 和 pnpm 已可用。Mermaid 使用官方 `none` 引擎：保留 Mermaid
文本，不额外安装浏览器来渲染 PNG/SVG/PDF。这只影响图的导出。

PyPI 当前已不提供上游固定的 `lancedb==0.4.0`。因此只将源码
`requirements.txt` 的这一行改为目前仍可获取的最早版本 `lancedb==0.14.0`；
差异保存在 `upstream-requirements.patch`。基础软件公司流程没有调用 LanceStore。
LanceDB/RAG 路径未验证，不能将这份环境的验证结果用于宣称这些可选能力通过。
原始生成轮次没有修改上游 Python 源码；C99 反馈阶段的必要适配另有补丁留存。
使用 `constraints.txt` 固定 Pydantic 2.9.2 和
Click 8.1.7，完整安装版本保存在 `requirements.lock`。

需要重新创建时，在本目录运行以下命令；已有源码目录时跳过 clone，已有 patch
时不要重复 apply：

```bash
git clone --depth 1 --branch v0.8.2 \
  https://github.com/FoundationAgents/MetaGPT.git MetaGPT
git -C MetaGPT rev-parse HEAD
git -C MetaGPT apply ../upstream-requirements.patch
uv venv --seed --python /home/ljf/miniconda3/envs/metagpt/bin/python .venv
uv pip install --python .venv/bin/python -r requirements.lock
.venv/bin/python -m pip check
.venv/bin/python run.py --check
```

## 低成本真实检查

```bash
.venv/bin/python run.py --smoke
```

这条命令只执行一次原生 `WriteCode` 动作，生成 `int add(int a, int b)`。
仍使用同一模型、thinking 和 high effort，只把这次检查的输出上限降到 1,024
Token。SDK、LLM 层和 WriteCode 动作层的检查重试均关闭，不启动完整团队生成。
每次调用会收费；配置完成后无需反复执行。

原始响应、C 代码及实际 Token 用量保存在 `smoke/<UTC时间>/`。推理 Token
已经包含在输出 Token，缓存命中 Token 已经包含在输入 Token，不能重复相加。
`validation.json` 保存不调用模型的角色及配置检查。

MetaGPT 的原成本表不包含 deepseek-flash。启动脚本补充了
[2026-10-08 官方价格](https://api-docs.deepseek.com/quick_start/pricing/)
中的峰时缓存未命中价格：输入 $0.30/百万 Token、输出 $1.20/百万 Token。
原生 `--investment` 据此使用保守估算，实际缓存或非峰时费用可能更低。
它在轮次开始时检查预算，单轮可包含并发及多次调用，因此不是账单硬上限。
价格发生变动后应更新启动脚本中的价格记录。

## MQTT 单次实验及运行记录

MQTT 实验入口为 `mqtt_experiment.py`，读取 `cases/mqtt_min` 的原始 TASK.md、
REQUIREMENTS.md 和 MQTT 3.1.1 PDF。使用与 SpecForge 相同的确定性 PDF 提取，
将全部 81 页文本与两个原始需求文件加上文件边界拼成 MetaGPT 的 idea。
运行前逐字节核对三轮 SpecForge MQTT 留存输入、每页文本及模型配置。
MetaGPT 只接收这些原始材料，随后执行官方 `generate_repo`。

```bash
cd /home/ljf/SpecForge/expriments/RQ1/metagpt

# 零调用输入检查，输出目录必须尚不存在。
.venv/bin/python mqtt_experiment.py --prepare-only --out /tmp/mqtt_input_check

# 一次正式生成；每次使用新的输出目录。
.venv/bin/python mqtt_experiment.py --out runs/mqtt_<UTC时间>
```

保持官方默认参数：5 轮、code_review=True、run_tests=False、implement=True、
max_auto_summarize_code=0、investment=3.0；项目名设为 mqtt_broker。
原生 QaEngineer 为可选角色，默认不开启；已完成的原始生成实验保留这个默认值。
本次已有项目的反馈阶段单独启用 QaEngineer，见本文末尾。
原始架构提示词中的 Python 约定也保留，没有针对 MQTT 或 C99 调整角色提示词。

记录器在返回原动作结果和异常的同时保存调用及文件操作证据，不修改调度、
提示词或生成内容。相比 SpecForge 的运行记录，对应保存如下信息：

| 内容 | 实验目录中的位置 |
| --- | --- |
| 原始输入、完整 idea、PDF 页文本和导航、输入等价性 | inputs/、documents/、run.json |
| 模型参数、依赖/工具版本、上游提交和补丁、框架及入口 SHA-256 | run.json |
| 每次真实 HTTP 请求体、原始响应，含 reasoning_content、usage 和结束原因 | logs/llm/request_*.json、response_*.json |
| HTTP 状态、SDK 重试标记、请求所属角色/动作、耗时及缓存/推理 Token | logs/llm/metadata_*.json、run.json |
| 原生动作结果、阶段状态、文件哈希、角色交接快照 | logs/<动作>/、snapshots/、run.json |
| 原生文件读写及完整框架日志 | logs/file_operations.jsonl、logs/framework.log |
| 未修改的最终源码交付 | project/，原生完整仓库在 workspace/ |
| 独立构建/开发测试命令、返回码、耗时和完整 stdout/stderr | evaluation/commands/、evaluation/development/ |
| 同一 MQTT 验收器的普通和 ASan/UBSan 结果、报文及 broker 日志 | evaluation/normal/、evaluation/sanitize/；构建失败时注明未执行 |
| 交付检查、二进制哈希、记录完整性检查及评测脚本 | reports/、run.json |

SpecForge 专有的 Facts/Specs、规格修订/修复、实现修复计数和 delivery.json
一致性检查标为“不适用”。MetaGPT 自己的代码审查由实际动作和调用序列记录，
不转换成 SpecForge 修复次数。

独立评测在原生生成全部结束后执行，直接复用 `specforge.evaluation.evaluate_project`
与 `evaluation/mqtt_check.py` 的 16 个场景，在项目副本上构建和测试。
评测脚本留存在本次实验的 `reports/evaluate_native_output.py`，不调用模型，也不把
验收信息反馈给 MetaGPT。原生交付的可执行文件与外部测试时编译出的文件分开记录。
缺少 `sanitize` 目标的原始失败单独保留。共同输入 R12 未规定这个目标名，
本次在另一份源码副本上通过原生 CFLAGS 参数完成外部 ASan/UBSan 插桩及相同验收，
补充结果保存在 evaluation/instrumented/，不覆盖原报告；脚本在
reports/measure_instrumented_output.py。
`native_pipeline_completed`、TASK 交付检查 `generation_passed` 和独立验收
`evaluation.passed` 的含义分别写入 run.json，不能混用。

本次单次实验结果及成本见 `runs/mqtt_20261008T075400Z/RESULTS.md`。


## CoAP 单次实验

同一记录入口现在支持 `--case coap`；默认仍为 MQTT。CoAP 使用
`cases/coap_min/TASK.md`、`REQUIREMENTS.md` 和完整原始 `spec/rfc7252.txt`，
不从 SpecForge 的 Facts/Specs 或代码提取额外提示。32 个导航分块只用于
输入等价性核对；传给 MetaGPT 的是原始 RFC 全文，保留换行与分页字符。

```bash
cd /home/ljf/SpecForge/expriments/RQ1/metagpt
.venv/bin/python mqtt_experiment.py --case coap --prepare-only --out /tmp/coap_input_check
.venv/bin/python mqtt_experiment.py --case coap --out runs/coap_<UTC时间>
```

模型和原生流程设置与 MQTT 一致，项目名为 `coap_server`。正式运行保持
`run_tests=False`、`max_auto_summarize_code=0`；原生 C99 代码审查记录按实际
动作保存，独立的构建/测试反馈不送回生成团队。每轮入口源文件冻结在
`reports/generation_entrypoint.py`，其哈希在 run.json。

独立验收使用同一 `evaluation/coap_check.py` 的 10 个场景，包括 libcoap
客户端互通；项目副本的开发检查复用 `specforge.pipeline.verify_project`，
按共同任务的交付文件检查传入 runtime contract，不要求 SpecForge 专有的
delivery.json 或 Spec bundle。命令、结果、完整日志与原生交付分开保存。

本次实验目录为 `runs/coap_20261008T093000Z`；结果见其中的 RESULTS.md，
完整调用和阶段记录在 run.json、logs/、snapshots/ 与 evaluation/。

CoAP 本次原生默认构建因 `coap_server.h` 的块注释中含 `src/*.c` 而被
`-Werror` 阻断；原生 Makefile 同样缺少 sanitize 目标。原始失败记录保留。
在独立副本上仅用 `CFLAGS=-Wno-error=comment` 降级该注释告警后，普通
和 ASan/UBSan 插桩的独立验收都为 6/10，开发自测均通过 278 项检查。
生成代码把标准 PUT 方法码错误定义为 2（RFC 7252 要求 3），自测向量
也使用了错误的 0x02。补充测量在 evaluation/compiler_adjusted/，详细
诊断在 reports/failure_diagnosis.json；未修改原生生成源码或将结果回馈生成团队。


## HTTP/1.1 与 SMTP 单次实验

同一入口支持 `--case http11` 和 `--case smtp`。分别使用
`cases/http11_min` 和 `cases/smtp_min` 的原始 TASK.md、REQUIREMENTS.md，
以及完整原始 `rfc9110-rfc9112.txt` 或 `rfc5321.txt`。HTTP 的 67 个、SMTP
的 28 个导航分块，以及输入、模型哈希均与三轮 SpecForge 留存输入一致；
传入 MetaGPT 的仍是完整原始文本，不使用 Facts/Specs 或现有实现。

```bash
cd /home/ljf/SpecForge/expriments/RQ1/metagpt

# 输入核对不调用模型；输出目录必须尚不存在。
.venv/bin/python mqtt_experiment.py --case http11 --prepare-only --out /tmp/http11_input_check
.venv/bin/python mqtt_experiment.py --case smtp --prepare-only --out /tmp/smtp_input_check

# 每条正式生成命令均会调用模型、产生费用，使用新的输出目录。
.venv/bin/python mqtt_experiment.py --case http11 --out runs/http11_<UTC时间>
.venv/bin/python mqtt_experiment.py --case smtp --out runs/smtp_<UTC时间>
```

模型与 MQTT/CoAP 相同，分别以 `http_server` 和 `smtp_server` 为项目名，
保留 5 轮、investment=3.0、code_review=True、run_tests=False 等官方默认
设置。上游 Python 源码、角色提示词和调度没有修改。

本次初始生成进程被会话中断，保留原始目录
`runs/http11_20261008T103809Z` 和 `runs/smtp_20261008T103809Z`。
当时没有可供官方 recover_path 使用的团队检查点。后续执行同一原生流程，
只有完整请求体逐项相同才返回已保存的响应，且在第一条未完成请求前核对
请求体及已有源码字节相同；HTTP 的 53 个和 SMTP 的 48 个已有响应均仅
本地重放，没有重复网络调用。该一次性恢复脚本冻结在每轮 reports/，
不是 MetaGPT 的原生 recover_path。恢复方法与零调用核对在 recovery.json、
reconstruction_boundary.json 和 reconstruction_metric_origins.json 中记录。

最终实验目录及报告：

| 协议 | 文件数 | 已知完整调用 | 普通协议验收 | 外部 ASan/UBSan | 报告 |
| --- | ---: | ---: | --- | --- | --- |
| HTTP/1.1 | 40 | 106 | 未执行：构建失败 | 未执行：构建失败 | [RESULTS.md](runs/http11_20261008T103809Z_continued/RESULTS.md) |
| SMTP | 24 | 65 | 9/13 | 9/13 | [RESULTS.md](runs/smtp_20261008T103809Z_continued/RESULTS.md) |

HTTP 的 `src/hs_parse.c:1` 被原生代码审查写入 `## src/hs_parse.c`，GCC
因此报告语法错误。全部 40 个文件完成写入和审查后，原生 Team.run 还因
保守费用 $3.914760600 超过 investment=$3.0 而触发 NoMoneyException。
官方装饰器捕获异常并序列化团队后返回，所以 native_pipeline_completed=True
仅表示 generate_repo 函数返回，不能据此声称 SOP 无异常完成。
`native_sop_finished_without_framework_error=False`、停止原因及原生检查点
均已保存；没有提高预算、修补源码或追加生成调用。

SMTP 可构建运行，但裸 EHLO 返回 250 而非 501，且无域名 Postmaster
被错误拒绝。四个失败场景在普通及外部插桩测量中一致；VRFY 场景发送的
未加引号邮箱与 RFC String 语法存在验收疑点，报告注明这一限制并保留原始
9/13 分数，未改变验收器或重算。普通验收及插桩测量复用同一 smtp_check.py。
原生 Makefile 缺少 sanitize/test 目标，严格入口失败记录另行保留；共同
需求没有规定这两个目标名。补充插桩在独立副本上保留原生全部编译、链接
参数并追加 ASan/UBSan，未修改源码和 Makefile；场景日志未见检测诊断。
原生自测入口 make check 也已在两种副本上执行，因测试文件的注释及
snprintf 截断告警在 -Werror 下编译失败，自测程序未运行。

两轮独立评测均在生成结束后执行，不把验收失败反馈给 MetaGPT，不调用
模型修复。输入、每次请求/原始响应、推理内容、Token、费用估算、原生动作、
角色和阶段耗时、文件操作、交接快照、构建命令、协议报文及失败诊断均保留。
中断前完成动作使用原始真实耗时，本地重放耗时单独记录；包含中断等待的
墙钟不当作连续生成耗时。依据本次非峰时价格与缓存计数，已知用量估算
HTTP $0.938136396、SMTP $0.517241136，合计 $1.455377532，非账户账单。
另外每轮各有一条中断时未返回的真实请求，其 Token 和费用未知；它们保留
在初始目录并计入实际网络尝试数，不按零费用处理，也不含在已知用量估算中。


## 第二轮四协议实验

四个协议均从空目录独立生成，没有恢复或重放上一轮响应。原始输入及文档
与三轮 SpecForge 留存记录一致，实际请求仍为 deepseek-flash、thinking enabled、
reasoning_effort=high、max_tokens=65536、非流式。上游提示词、动作、调度
与原生默认设置保持一致，生成源码未修改，独立验收不向团队反馈。

本轮统一批次为 `runs/round_02_20261008T145033Z`，完整统计、费用与各协议
报告见 [批次 RESULTS.md](runs/round_02_20261008T145033Z/RESULTS.md)。

| 协议 | 文件数 | 普通验收 | 补充 ASan/UBSan | 已核实的主要问题 |
| --- | ---: | --- | --- | --- |
| MQTT | 29 | 未执行：构建失败 | 未执行 | CodeReview 修订把 Markdown 标题写入 packet.c、server.c |
| CoAP | 27 | 未执行：构建失败 | 未执行 | NetOpen 的 socket 参数遮蔽系统 socket 函数 |
| HTTP/1.1 | 22 | 未执行：构建失败 | 未执行 | main.c 含 Markdown 标题；clean 也因 rm -f build 失败 |
| SMTP | 20 | 5/13 | 5/13 | 邮件地址 JSON 格式、事务状态、VRFY 参数、回复码和长度限制 |

构建失败的协议行为未测，不记为 0/16、0/10 或 0/13。原生 sanitize
入口的失败另行保留；MQTT 虽有 test-asan/SANITIZE=1，语法错误仍阻断
测量。SMTP 补充插桩使用未修改的源码副本、保留原生参数，场景日志
未见 ASan/UBSan/泄漏诊断。四个原生 SOP 均无被捕获框架错误，默认 QA
关闭，仅原生代码审查参与生成，未追加模型修复调用。

有效实验共 281 次真实调用、15,401,961 Token，按缓存计数和非峰时价格
估算 $2.006808759。启动包装器最初过早导入角色，导致旧模型别名及
4,096 输出上限；该批次已中止、标记无效并排除论文对比，保留完整证据。
修正后在每次请求发送前校验共享参数，并隔离各协议原生失败检查点目录。
无效启动的已知费用估算为 $0.070719135，包含它的全部已知费用估算为
$2.077527894；另有 3 次中止请求未返回用量，费用未知。以上不是账户账单，
价格来源、计量细项和无效批次路径均在批次报告中。

完整请求/响应/推理、Token/缓存/费用/耗时、动作与阶段、交接快照、文件
操作、源码、开发测试与协议报文均按前述路径留存。最终只读核对结果
在批次 `reports/final_audit.json`，核对脚本及生成/评测入口已冻结。


## C99 原生反馈修复（已有两轮项目）

`feedback_experiment.py` 在最近两轮有效的八个已生成项目上恢复官方 QA 消息链。
旧的 `mqtt_experiment.py` 与各轮冻结入口继续保留原始生成设置；不会通过它们
再次生成项目。无效的第二轮启动不进入反馈实验。

```bash
cd /home/ljf/SpecForge/expriments/RQ1/metagpt
.venv/bin/python feedback_experiment.py --out runs/feedback_<new_id> --jobs 4
```

反馈运行仍固定使用同一 DeepSeek 地址、模型、thinking、高推理强度、65,536
输出上限和非流式设置。当前用户已明确授权向现有 `https://api.deepseek.com`
发送这八个项目的原始需求、MetaGPT 生成源码、自测及执行反馈，产生追加模型费用。

官方 `QaEngineer` 根据原始 requirement.txt 和生成的 C99 项目运行 `WriteTest`，
生成 Python 标准库的项目级协议测试脚本。官方 `RunCode` 先执行原生 Makefile
的 `make clean && make`，然后执行原有 `test`（仅没有 test 的第一轮 SMTP
使用 check），最后运行 QA 的测试脚本。所有测试都来自 MetaGPT；QA 脚本是
C99 项目的执行测试入口，不是重新生成的实现。

继续使用官方 `RunCode` 的错误分类、一个文件的修复建议和角色路由。这个
固定版本的 Engineer 没有监听 RunCode，适配将它接回已有 `WriteCode`、
`WriteCodeReview` 和文件依赖上下文；测试缺陷通过原生 `DebugError` 修复。
其他必要修改是 C99 的退出码、超时、测试位置及代码块格式。Python 路径保留。
初始源码补丁在每次批次的 reports/c99-adaptation.patch；正式批次最终补丁为
reports/c99-final.patch，入口快照和适配前文件另行保存。

参数取自当前 SpecForge：最多 3 次反馈修复，每次最多 40 个模型响应；初始
QA 的模型响应上限沿用 code 的 180。SDK 的网络重试上限为 2，去掉叠加的
框架连接重试。构建超时 120 秒、自测超时 180 秒。原生代码审查保持开启，
不触发新的规划、设计、任务拆分或规格修订。原生每条执行反馈只选择一个
文件修复；参数的数值上限相同，两个系统的动作粒度仍各自保持原生行为。
SpecForge 没有美元硬上限，因此反馈阶段按模型响应和修复次数限额，不沿用
此前生成阶段的 investment=3.0 软费用限制。

修复工作区只复制原始 project、原生设计/任务文档、依赖关系和原始输入。
模型进程用 bwrap 的明确挂载列表隔离，旧 evaluation、独立验收和其他
项目均不可见。每次本地构建/测试使用独立网络命名空间，以免固定端口冲突。
旧项目与 before_project 的文件哈希在结束时再次核验。

每个 run.json 记录原始生成用量、追加模型请求/响应与 Token、修复次数、
动作耗时、构建/原生自测/QA 自测结果、停止原因和修复后源码哈希；stdout、
stderr、退出码、超时及命令完整留存。before/after 指同一自测链在初次执行
和最后一次执行的结果；早先阶段失败时后续测试明确记为未执行。推理 Token
是输出 Token 的子集，缓存 Token 是输入 Token 的子集，不重复累加。

自测结果不能作为独立协议验收通过率；本次反馈阶段不执行第三方验收。
修复轮数达到上限仍失败的项目按实际结果保留，不追加人工修改或额外轮次。

正式批次中发现了原生 Send To 标题省略冒号和 QA 文件路径前缀的解析问题。
修正后使用 `--resume` 续跑原工作区与同一 QA 测试；第一遍的请求、用量、
耗时、停止原因和源码副本保留，并在最终总量中累加。continuations 记录
曾分发但未执行修复动作的错误路由；修复次数以实际 WriteCode/DebugError
动作计数，没有因为适配修正增加三轮上限。源码版本差异和前后汇总均冻结
在 reports/。首次请求断言失败的零调用启动单独标记无效并保留证据。

结果批次：[RESULTS.md](runs/feedback_20261009T002030Z/RESULTS.md)。


## 第三轮独立生成与完整反馈流程

第三轮入口为 `fresh_round_experiment.py`。四协议各从新的空目录执行原生
ProductManager、Architect、ProjectManager、Engineer 与代码审查，不重放
前两轮响应，也不使用已有实现。生成阶段的官方参数与前两轮一致：5 轮、
investment=3.0、code_review=True。这里的 run_tests=False 仅用于先冻结
未经反馈修复的生成结果；整个第三轮流程随后自动启用原生 QA 和反馈修复。

```bash
cd /home/ljf/SpecForge/expriments/RQ1/metagpt
# 新输出目录先做零调用预检；同一 prepared 目录可以启动正式执行。
.venv/bin/python fresh_round_experiment.py --out runs/round_03_<new_id> --prepare-only
.venv/bin/python fresh_round_experiment.py --out runs/round_03_<new_id>
```

每个协议的原始输入、生成源码与日志保存在独立的 runs/<case>_<id> 中。
随后自动调用已有 `feedback_experiment.py --sources <four_runs> --round 3`，
将本轮原始产物冻结到 feedback/<generation_name>/before_project，再使用同一个 C99 原生
反馈路径。数值限制继续为三次实际修复、每轮四十个响应、初始 QA 一百八十个
响应，构建 120 秒和自测 180 秒。反馈阶段不重新生成项目。

原生模块导入时产生的工具 schemas、日志和默认工作目录被隔离到批次的
runtime/<case>，避免在正式生成入口检查空输出目录之前提前创建该目录。
各协议的原生错误检查点也独立保存；被 Team 捕获的异常单独记录，不能将
生成函数返回等同于原生 SOP 无异常完成。

第三轮原生反馈结束后，统一在冻结副本上评测三轮十二个最终产物。
验收与外部 ASan/UBSan 测量由批次 reports/post_feedback_assessment.py 执行，
不调用模型、不改交付源码、不将验收反馈给 MetaGPT。原有 sanitize 目标结果
与外部追加编译参数的测量分开记录；构建阻断的测试记为未执行。
reports/complete_metagpt_three_rounds.py 核对完整请求、真实 usage、输入、模型、
源码哈希、QA 生成次数和实际修复次数，输出三轮 CSV、JSON 与 RESULTS.md。

第三轮已完成，正式批次为 runs/round_03_20261008T171853Z。四个项目从空目录独立生成，
再进入同一个原生 QA 反馈路径；各原始交付已冻结并核对哈希。第三轮构建通过
2/4 → 4/4，原有自测通过 4/4，全部自测通过 1/4；反馈追加 34 次响应、
4,076,363 Token，反馈并行墙钟 699.072 秒。独立普通验收为 MQTT 0/16、
CoAP 10/10、HTTP 0/13、SMTP 12/13；MQTT/HTTP 的交付运行文件不符合共同 TASK，
未改名或移动文件来改变验收结果，外部插桩验收对这两项记为未执行。

三轮共十二项目，构建通过 5/12 → 11/12，原有自测通过 10/12，全部自测
通过 4/12；全部原始生成与反馈的已知 Token 合计 59,969,944。早先两条
中断生成请求的用量仍未知，未按零成本处理。独立验收只在反馈结束后的
冻结副本上执行，未追加模型调用，交付和原始源码均未改。

完整三轮汇总：[RESULTS.md](runs/round_03_20261008T171853Z/RESULTS.md)、
[CSV](runs/round_03_20261008T171853Z/three_rounds.csv)、
[JSON](runs/round_03_20261008T171853Z/three_rounds_summary.json)、
[最终核对](runs/round_03_20261008T171853Z/reports/three_rounds_final_audit.json)。
原始需求、各次原始模型请求/响应、执行 stdout/stderr、返回码、超时、
修复快照、额外耗时与 Token 统计全部留存；自测与独立验收分别统计。
