# NoSpec-SpecForge

从与原始 SpecForge 相同的 TASK、REQUIREMENTS 和协议原文直接生成 Linux C99
项目。所有适配位于 RQ2，主系统、案例和独立验收器不改动。

```text
冻结输入、提取协议文本及索引
→ Coder 直接理解、设计并实现项目和开发测试
→ 普通构建／自测、ASan+UBSan 构建／自测及反馈修复
→ 恢复普通构建、冻结交付
→ 交付副本中的普通和 Sanitizer 独立协议验收
```

Facts、Design、Specs、规格语义审查和规格回修全部移除。没有替代事实库、设计
规格、函数行为文件或规格包。公共头文件由 Coder 编写并可修改，WORKLOG 仍用于
原有检查点恢复。消融对应完整规格构建、审查和约束传递流程的贡献。

首次生成最多 **300 次模型响应**；最多三轮实现修复，每轮最多 **40 次**，合计
上限 420 次。通过开发关卡即停止，不要求用满预算。API 中断恢复沿用原 job 的
响应计数和用量，不能获得额外预算。模型、工具调用、执行沙箱和上下文重建直接
复用主系统；预算差异明确保存于运行记录。

## 运行

环境和密钥要求与主系统相同，默认模型为 `deepseek-flash`。`--model` 支持主系统
`MODEL_CONFIGS` 中的全部模型。执行完整 MQTT、CoAP 独立验收分别需要
`mosquitto_pub`／`mosquitto_sub`、`coap-client-notls`。

从仓库根目录执行四协议各一次，默认顺序为 MQTT、CoAP、HTTP/1.1、SMTP：

```sh
python expriments/RQ2/run.py run --out expriments/RQ2/runs/nospec_01
```

每次实验使用新目录，重复实验手动更换 `--out`。单协议及模型选择：

```sh
python expriments/RQ2/run.py run --out expriments/RQ2/runs/coap_qwen_01 \
  --cases coap --model qwen3.8-flash
```

`--cases` 接受 `mqtt coap http11 smtp` 中的一个或多个协议。输入均来自主系统
对应的 `cases/*_min`，二进制路径和启动参数直接采用其 TASK 中的明确约定。
这些启动元数据不包含协议行为或实现设计。

只准备输入，不调用模型、不启动独立验收：

```sh
python expriments/RQ2/run.py run --out expriments/RQ2/runs/prepare_01 --until prepare
```

`--until code` 停在实现及开发检查通过后；默认 `--until verify` 完成生成，并在
成功时自动独立验收。生成失败时验收为未执行，仍继续批次中的其他协议。

恢复尚未进入独立验收的单个协议：

```sh
python expriments/RQ2/run.py resume --run expriments/RQ2/runs/prepare_01/coap
```

框架、RQ2 适配代码／提示词及已完成阶段产物必须保持原样。失败 job 的手工修改
按主系统行为记录为 `manual_edits`；恢复运行记为 `resumed`，不再标为 fresh。
独立验收开始后禁止恢复生成。对冻结交付单独执行或重新执行验收：

```sh
python expriments/RQ2/run.py evaluate --run expriments/RQ2/runs/nospec_01/coap
```

## 记录与判定

每个协议子目录保存 `inputs`、`documents`、`project`、`reports`、`logs`、`run.json`
以及验收开始后生成的 `evaluation`。NoSpec 运行状态使用独立的版本与流程标识，
不能使用主系统 CLI 恢复。

`delivery.json` 沿用原文件／测试清单结构，仅去掉 `spec_test_refs`：

```json
{
  "schema_version": 1,
  "files": ["server.c", "server.h", "Makefile", "README.md", "dev.py"],
  "tests": [{"id": "interaction", "path": "dev.py", "requirement_ids": ["R01"]}]
}
```

实际清单必须覆盖该任务的全部需求编号。文件存在性、清单完整性、重复测试 ID、
未知需求和需求覆盖仍受检查。编号覆盖与原系统一样属于结构检查，不能单独证明
测试断言覆盖了需求语义。

批次 `summary.json` 在运行、恢复及重新验收后更新，包含逐协议阶段状态、失败原因、
修复次数、模型用量、活跃执行耗时、构建结果及两种独立验收的场景结果。
`elapsed_seconds` 累计 CLI 执行时间，不包含中断后等待恢复的时间。恢复会更新对应
运行行，未开始或未执行的结果不会计为通过。

生成通过与独立验收通过分别记录。独立验收使用原验收脚本，仅在交付副本中运行，
不反馈给 Coder；缺失、跳过或失败场景均不能形成完整通过。
重新验收开始时重置当前验收结果，旧报告保留；新验收异常不能沿用旧的通过状态。
当前默认每协议一次；质量、成功率和稳定性的正式对照需要后续重复运行与对照数据。

## 离线验证

```sh
python -m unittest discover -s expriments/RQ2/tests -v
python -m unittest discover -s tests -v
```

RQ2 测试使用模拟模型和临时 C 项目／验收脚本，不调用真实模型 API，也不产生
协议生成实验结果。SpecForge 对照数据的来源和正式实验运行不属于本次实现。
