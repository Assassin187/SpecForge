# RQ2：编码前跨视图契约检查机制消融

实现范围为一个 MQTT 配对块：新生成一次共同事实、设计、行为规格及新会话语义审查，通过共同关卡并冻结后，从同一未发布草稿并行运行 V0/V1/V2。三个条件各自使用独立子进程；三个生成过程均结束后，统一执行独立验收。种子 `20261011` 保存的 **V0 → V2 → V1** 仅为提交及报表顺序，不表示执行或完成顺序。历史规格、参考实现及独立验收结果不进入生成输入。

所有新增实现均在本目录。`checks.py` 实现共同基本检查、纯头文件编译及包完整性；`adapter.py` 通过 `RQ2Pipeline` 子类和作用域内函数包装复用原生控制器；`run.py` 负责冻结、隔离进程与生成完成后的独立验收；`report.py` 只读取记录并导出结果。包装在退出作用域时恢复，不修改原生代码或提示词文件。

## 执行

在仓库根目录安装原生 `pyproject.toml` 所列依赖，并提供 `gcc`、`make`、`nm`、`pdftotext`、`bwrap`。基模型为 `gpt-6-luna`，从主系统的原生 GPT 配置派生，复用其接口、密钥环境变量和 Chat Completions 客户端；模型注册仅在实验运行创建时生效，退出作用域后恢复。所有模型任务使用 `reasoning_effort=max`、`max_tokens=65536`、`stream=false`、`extra_body={}`。原生记录中的 `thinking=enabled` 为统一记录字段，GPT 请求通过 `reasoning_effort` 控制推理。运行真实生成前设置 `DES_CODEX_API`；记录只保存环境变量名称及是否存在。保持本目录 `config.json` 的已确认配置。原生 API 重试设置保留；不重跑失败或中断的共同阶段、分支及配对块。

```bash
python3 -m unittest discover -s /home/ljf/SpecForge/expriments/RQ2/tests -v
python3 -m unittest discover -s /home/ljf/SpecForge/tests -v

python3 /home/ljf/SpecForge/expriments/RQ2/run.py batch \
  --config /home/ljf/SpecForge/expriments/RQ2/config.json \
  --out /home/ljf/SpecForge/expriments/RQ2/runs/mqtt_gpt_6_luna_max_parallel_001

python3 /home/ljf/SpecForge/expriments/RQ2/report.py \
  --experiment /home/ljf/SpecForge/expriments/RQ2/runs/mqtt_gpt_6_luna_max_parallel_001
```

`batch` 在同一控制进程中依次调用共同 `draft` 与并行 `variants`，自动接续，无需外部等待脚本。`draft --config ... --out ...` 与 `variants --experiment ...` 仍可分别执行。`draft` 拒绝已有目录；预先登记共同尝试、三个分支、并行方式及提交顺序。共同失败也输出三个失败记录和汇总，`variants` 会为它们补齐“未执行”验收记录。`variants` 拒绝第二次启动，包括中断或失败后重试；单个分支失败不会中止其他分支。共享 `experiment.json` 只由控制进程更新；它保存 `controller_pid`、各尝试终态和 `evaluation_finished`。子进程输出保存在 `common/process.log` 和 `variants/<variant>/process.log`。

后台启动使用 `nohup` 或 `subprocess.Popen(start_new_session=True)`，将批次标准输出保存到 `runs/<批次名>.batch.log`。启动确认后无需持续监控；批次完成后自动生成 `summary.json/csv/md`。

## 检查边界与控制变量

| 阶段 | 强制检查 |
|---|---|
| 共同设计 | 基本 Schema、路径、FileSpec 与公共头文件存在；尚不要求行为规格和 traceability |
| 共同行为及新会话语义审查、V0 | 共同基本约束 |
| V1 | V0 + 每个公共头文件单独编译、全部头文件共同编译 |
| V2 | V1 + 原生完整 `validate()` |
| 编码后一次规格缺口修订及新会话重审 | 三组均完整检查 |

基本约束保留范围和运行契约、入口、声明所需函数规格、非空行为、公共 TYPE_SPEC、已有 CALL_CONTRACTS 的完整字段、WIRE_MAPPING、测试向量、需求覆盖、事实引用、JSON Pointer 及向量引用。它检查模块声明的文件是否存在，但不检查重复归属、未分配文件、符号归属、函数依赖和跨规格声明一致性。V1 探针只含 `#include`，参数及包含目录规则复用原生 `compile_probe()`。未先运行完整检查再过滤。

原生 `do_specs()` 的导航构造直接读取每个 SOURCE.INTERFACE 的 `FUNCTION_TYPE`，虽然原生 Schema 将该字段设为可选。共同基本关卡要求 Planner 显式补齐此字段，以满足原生调用的输入前提；不推测函数类型，也不与其他视图的类型进行一致性比较。

共同 `do_specs()` 保留原生清除作者审查声明及新会话审查，仅抑制发布。Planner 提示词只在调用时替换强制提前编译和 ABI 修复指令，保留协议规划、语义审查及正常自主工具使用；三组获得相同提示词和修订任务，诊断内容不同。每组固定两个独立修订任务，各最多 40 次响应；第一轮通过或预算耗尽仍执行第二轮，原生客户端 API 重试之外没有额外重试。最终关卡通过后原生发布，包加载只检查结构、索引、路径、身份和哈希。

下游复用原生 `do_code()`，`spec_only=False`，初始 180 次响应、最多 3 次各 40 响应代码修复、最多 1 次 180 响应行为修订和 120 响应独立重审。Coder 仅挂载发布规格及本分支开发报告；公共头文件只读。第一次 Coder 返回或抛出异常时，立即保存 `snapshots/initial`，早于后续规格和代码修复。`snapshots/final` 始终关联最近实际发布的规格；未发布的新修订不覆盖该关联。

2026-10-12 用户授权将 `gpt-6-luna/max` 的语义审查累计响应上限从 60 改为 120，并续接主实验 MQTT 第二次及 RQ2 MQTT 第二批次的预算耗尽任务。`config.json` 的共同审查及编码后重审现均为 120。`resume.py` 在作用域内复用原生恢复和审查任务，跳过已通过的行为生成，保留独立审查的记录与 WORKLOG，并从原 `progress.json` 的第 61 次响应继续，因此最多追加 60 次。共享目录中的原生 `LIMITS["review"]` 同时已有 60→120 的修改；续跑入口以还原该数字后的 SHA-256 验证它是唯一允许的原生变更，归档原哈希并登记新哈希。其他原生文件变更会阻止续跑。

续跑入口分别为 `python3 /home/ljf/SpecForge/expriments/RQ2/resume.py main --run /home/ljf/SpecForge/runs/gpt6_luna_max_mqtt/20261011T160342Z` 和 `python3 /home/ljf/SpecForge/expriments/RQ2/resume.py rq2 --experiment /home/ljf/SpecForge/expriments/RQ2/runs/mqtt_gpt_6_luna_max_parallel_002`。只有指定模型的 60 次独立审查预算耗尽任务能恢复；该入口不覆盖其他失败或已启动的 RQ2 分支。原失败状态、规格、审查进度以及 RQ2 原配置、冻结清单、汇总和未执行占位结果保存在 `continuations/review_120_001`。变更授权、预算、PID、原始 API 哈希和结果写入其中的 `record.json`，原前 60 次 API 请求/响应保持原文件。RQ2 冻结清单记录此次预算和实验适配代码变更，共同生成耗时与 Token 保留续跑前消耗；审查通过后仍自动并行执行 V0/V1/V2，再完成独立验收与汇总。主实验生成通过后自动执行 MQTT 独立验收。

## 记录与判定

- `experiment.json`：三个登记尝试、顺序与终态；`freeze.json`：输入、工具版本及二进制、模型配置、原生文件和实验代码 SHA-256。
- `frozen/`：配置、任务、需求、MQTT 3.1.1 PDF、验收器、实际适配提示词、经原生关卡批准的事实与范围、完整正式草稿。消费前校验哈希，发生变化即拒绝使用。
- `common/run` 和 `variants/<variant>/run`：原生状态、API 请求/原始响应、完整命令输出和退出码；`reports/rq2_gates` 保存每次检查的策略、阶段、内容哈希、完整诊断/探针和耗时。
- `snapshots/precode`：两轮前后规格与诊断；`snapshots/initial`、`snapshots/final`：代码、实际发布包、任务结果及哈希；未启动 Coder 时保存明确缺失原因。
- `evaluation/<variant>/`：生成全结束后才运行的验收，位于所有 Agent 挂载之外；`build0` 是初始快照副本的普通 clean build，`initial` 和 `final` 各使用底层 `evaluate_project()` 执行普通及 ASan/UBSan 的全部 16 场景，没有 legacy 参数。
- `summary.json/csv/md`：全部尝试进入分母、配对差值、成本、耗时、修复及修改文件统计；`audit/<variant>`：初始代码/规格、构建诊断、根因缺陷人工审计表及自主命令审计索引。人工表不会被再次导出覆盖。

Code 判断初始项目所需源文件、头文件、Makefile、README、开发测试和交付清单完整，不以开发关卡通过代替。Build₀ 要求副本的普通构建成功并生成约定二进制。Accept 要求终态两个模式的全部 16 场景通过，缺失或未执行均失败。完整初始代码的 Defects 为 `pending`，缺失为 `NA`，不自动以检查器诊断定义缺陷真值。自主命令的等价检查须人工审计，不能从命令关键字推断。

Tokens 从原始 API 响应用量汇总，并逐任务与原生统计交叉核对；输入+输出只加一次，推理是输出的子集，缓存单列。缺失用量/API 错误标记未知并保留已知完成的部分小计。每组成本含完整共同成本，实际批次成本则仅计共同一次。报告不触发生成、修复、编译或检查反馈。

一个配对块验证实现和观察初步差异，不作统计有效性结论。受控错误和 `assets/mqtt_reference` 仅用于离线测试。
