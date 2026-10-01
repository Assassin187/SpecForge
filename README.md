# SpecForge

面向应用层网络协议的 Linux C99 项目生成框架：

任务、需求、规范 → 带证据的事实 → 三层 Spec 与实际公共头文件 →
Agent 编写项目、Makefile、README 和开发测试 → 开发验证 → 独立评测。

框架提供网络开发领域先验、工具、阶段调度、Spec 处理、一致性检查及记录。
具体协议身份、功能、行为、模块、目录、函数、类型和接口来自输入与 Agent 设计。
核心生成器不按协议选择提示词，不包含具体协议报文或预制项目模板。

## 环境

Python 3.11+；依赖固定为 openai 1.97.0、jsonschema 4.24.0。
需要 GCC、Make、bubblewrap；PDF 输入额外需要 pdftotext。
设置 DS_API 后运行；密钥不会写入运行记录或挂载进 Agent 命令视图。

模型为 DeepSeek deepseek-flash，高思考、非流式、max_tokens=65536。
缓存命中、未命中、输出和推理子项分别记录，不重复累计推理 tokens。

## 运行

从三个基础输入启动 MQTT 生成：

```sh
python -m specforge run \
  --task cases/mqtt_min/TASK.md \
  --requirements cases/mqtt_min/REQUIREMENTS.md \
  --protocol cases/mqtt_min/spec/mqtt-v3.1.1-os.pdf \
  --out runs/my_mqtt
```

生成结束后单独运行独立评测：

```sh
python -m specforge evaluate --run runs/my_mqtt --evaluator evaluation/mqtt_check.py
```

CoAP 使用完全相同的核心框架：

```sh
python -m specforge run \
  --task cases/coap_min/TASK.md \
  --requirements cases/coap_min/REQUIREMENTS.md \
  --protocol cases/coap_min/spec/rfc7252.txt \
  --out runs/my_coap
python -m specforge evaluate --run runs/my_coap --evaluator evaluation/coap_check.py
```

规范输入支持 PDF 和 UTF-8 txt。CoAP 文本来自
[RFC Editor 的 RFC 7252](https://www.rfc-editor.org/rfc/rfc7252.txt)。
具体范围见案例需求，不宣称完整 MQTT 或 CoAP 合规。

另提供两个相近规模的输入样例，每个同样包含 TASK、REQUIREMENTS 和一份规范输入。
范围按核心业务闭环和实现复杂度选择，不要求需求条数相同。SMTP 覆盖基础会话、
多收件人事务、DATA 透明传输、postmaster、本地 JSON 捕获和错误恢复；
HTTP/1.1 覆盖 GET/HEAD/POST/PUT、二进制资源、持久连接和 chunked 请求。
这两份样例尚未执行生成实验，也尚未配备独立评测套件。

```sh
python -m specforge run \
  --task cases/smtp_min/TASK.md \
  --requirements cases/smtp_min/REQUIREMENTS.md \
  --protocol cases/smtp_min/spec/rfc5321.txt \
  --out runs/my_smtp
python -m specforge run \
  --task cases/http11_min/TASK.md \
  --requirements cases/http11_min/REQUIREMENTS.md \
  --protocol cases/http11_min/spec/rfc9110-rfc9112.txt \
  --out runs/my_http11
```

SMTP 规范来自 [RFC 5321](https://www.rfc-editor.org/rfc/rfc5321.txt)；
HTTP 的单份规范输入保留 [RFC 9112](https://www.rfc-editor.org/rfc/rfc9112.txt)
和 [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.txt) 的完整原文与来源边界。

其他入口：

```sh
python -m specforge run ... --until prepare   # facts/design/specs/code/verify 同样支持
python -m specforge resume --run runs/my_mqtt
python -m specforge validate --specs runs/my_mqtt/specs/r001
python -m specforge code --specs assets/mqtt_reference/bundle --out runs/spec_only
python -m specforge verify --project runs/my_mqtt/project
python -m unittest discover -s tests -v
```

run 默认执行到 verify，成功表示生成与开发验证完成，独立评测单独记录。
输出目录必须是新目录。失败退出非零。旧版本运行只读保留，不能跨版本恢复；
评测开始后该项目冻结，resume 不再开启生成或修复任务。
人工 Spec-only 下游实验不计入 fresh 端到端结果。

## Agent 决策与交接

Facts Agent 从任务和需求确定范围，提取有原文位置的协议事实。
Planner 先设计模块、文件、实际公共头文件，再完成函数行为、wire mapping、
状态、调用、资源契约、规划测试向量与溯源映射。
Coder 只读取发布的 Spec，不读取原始规范、人工参考答案或之前的项目。
它选择私有实现，编写源码、Makefile、README、开发测试及 delivery.json。

PROTOCOL_MODULE_SPEC、FILE_SPEC、FUNCTION_SPEC 保留三层形式。
FILE_SPEC SOURCE 增加可选 SYSTEM_DEPENDENCY，与 HEADER 同名字段语义相同；
旧人工 Spec 兼容。源码和头文件目录由 Planner 选择，新 ABI 文件保留完整项目
相对路径。公共类型、声明及组合包含由 GCC 探针检查；系统调用按声明头文件核对。
Schema、引用和接口检查不证明协议语义正确。
TEST_VECTORS.TRACE_REFS 可关联已定义的函数、文件或 scope 中真实需求 ID；
未知引用仍被拒绝。溯源 spec_refs 使用 JSON Spec 路径和 JSON Pointer。

发布 bundle/SUMMARY 从真实 Spec 和 scope 生成，不写入固定协议身份。
公共头文件在编码阶段只读；其他项目交付由 Agent 生成。
delivery.json 列出交付源码、测试和测试数据，并将开发测试关联到需求与 Spec
TEST_VECTORS。编译结果、哈希、日志和报告由控制器记录。

Makefile 的通用执行接口为 all、clean、test、sanitize；内容由 Coder 编写。
开发关卡执行 clean build、自测、ASan/UBSan 自测及正常交付重建，
检查真实 Sanitizer instrumentation，并保存完整输出。test 使用当前构建，
不能将 Sanitizer 二进制悄悄换成普通二进制。README 由 Agent 依据真实实现核对。

## 稳定机制与独立评测

原生工具调用顺序执行；截断回答不执行其中工具。文件读取、工具输出和命令时间
有界。每二十四次响应或输入超过 64000 tokens 保存 WORKLOG 并重建上下文；
最后八次响应保留交付与检查工具，优先完成当前任务。
Facts/Design/Behavior/Code 上限为 40/100/180/180 次，每次实现修复 40 次。
最多三次实现修复、一次 Spec 回修；只有构建、自测、Sanitizer 和一致性反馈可
进入修复。协议特定独立评测不参与这些阶段。

协议独立套件统一放在 evaluation/：mqtt_check.py 保留原来的 16 个场景，
coap_check.py 包含 10 类基础请求响应场景。
两套资产不挂载、不复制进生成项目，不提供场景 ID、预期结果或失败报告给 Agent。
evaluate 使用交付副本，分别重建普通和 Sanitizer 版本，原项目不被改动。
所有必需场景必须实际通过；跳过、未执行和环境阻塞不能计为成功。
MQTT 互通评测需要 mosquitto_pub/sub，CoAP 互通需要 coap-client-notls；
这些工具不是基础生成入口的必要依赖。

运行目录保存 inputs、documents、facts、design_work、specs/r001、project、
snapshots、reports、logs 和 run.json；独立结果保存在隔离的 evaluation 目录。
v2 run.json 分别记录 generation_passed 和 evaluation，明确 fresh、resume、
人工编辑、修复次数、源码/提示词/Schema/输入哈希及成本。

人工资产与历史运行原样保留。历史三次成功结果属于旧的 MQTT 专用版本，
其中开发曾使用独立验收反馈；不能作为当前隔离评测版本的成功证据。
本轮正式冻结序列 sequence_11 已完成三次 MQTT 加一次 CoAP，全部通过：
MQTT 每次 16/16 场景、CoAP 10/10 场景，普通和 Sanitizer 构建均通过。
四次均为 fresh，未 resume、未人工修改生成产物，独立评测未参与开发修复。
30 项框架单元测试通过。该轮验收已按约定结束，未执行 HTTP 或额外协议生成实验。
详细产物链接、哈希核对、用量、耗时及前期失败见
[本轮实现与验收结果](runs/domain_generalization/RESULTS.md)。
