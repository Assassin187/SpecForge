# Coder Agent

coder agent 是 SpecForge 的最后阶段。它加载 `PROTOCOL_MODULE_SPEC`、`FILE_SPEC` 和 `FUNCTION_SPEC`，生成 C 项目，并可执行编译、有限轮次的 source repair 和 minimum-profile 行为检查。

public headers 和 Makefile 由结构化 specs 确定性生成，source files 由模型生成。repair 只处理能映射到 compiler diagnostics 的 source files，不应补造 specs 中缺失的 public API 或协议行为。

## 使用

全局选项必须写在子命令之前：

```bash
export ALI_API=<your-api-key>

python3 -m agent coder \
  --spec-root specs-example/mqtt_specs \
  validate

python3 -m agent coder \
  --spec-root specs-example/mqtt_specs \
  --output-dir agent/out/mqtt_reference \
  --max-repair-rounds 3 \
  generate
```

只生成 source files：

```bash
python3 -m agent coder \
  --spec-root specs-example/mqtt_specs \
  --output-dir agent/out/mqtt_reference \
  --skip-repair \
  generate
```

复制并 repair 已有项目：

```bash
python3 -m agent coder \
  --spec-root specs-example/mqtt_specs \
  repair \
  --project-dir agent/out/mqtt_reference/mqtt
```

验证已有 run，或只测试已编译 binary：

```bash
python3 -m agent coder \
  --spec-root specs-example/mqtt_specs \
  --output-dir agent/out/mqtt_reference \
  verify

python3 -m agent coder test \
  --protocol mqtt \
  --project-dir agent/out/mqtt_reference/mqtt \
  --binary mqtt_broker
```

`test` 当前支持 `mqtt`、`coap`、`http` 和 `smtp`。

## 输出

输出目录包含协议项目和 `_agent_logs/`。`run_manifest.json` 记录输入、模型调用、token 用量、compile/repair 状态和行为检查结果。独立 `repair` 不会修改原始项目。

行为验证是 minimum-profile smoke test，不代表完整 RFC conformance。详见 `protocol_behavior_val/README.zh-CN.md`。

```bash
python3 -m unittest discover -s agent/coder/tests -v
```
