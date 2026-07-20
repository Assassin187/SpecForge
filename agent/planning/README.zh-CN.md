# Planning Agent

planning agent 是 SpecForge 的核心组件。它把 `protocol_facts.json` 转化为 coder 可直接消费的 protocol specs，规划模块和文件边界、public artifacts、C interfaces、函数行为、call contracts、依赖、wire mappings 和 test vectors，而不是只生成协议摘要。

输出符合 `specs-example/specs_schema/`，包括 `<protocol>_module_spec.json`、模块级 `FILE_SPEC`、每函数 `FUNCTION_SPEC`、`SUMMARY.md`，以及保存 decisions、assumptions、diagnostics 和 metrics 的 `_planning/` 目录。

## 使用

```bash
export ALI_API=<your-api-key>
python3 -m agent planning plan \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --out agent/planning/out/mqtt_min

python3 -m agent planning stages

python3 -m agent planning validate \
  --run-dir agent/planning/out/mqtt_min
```

可从指定 stage 恢复中断的 run：

```bash
python3 -m agent planning plan \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --out agent/planning/out/mqtt_min \
  --resume-from function_behavior_design
```

默认 validation 包括 planning checks、coder spec loader 和 rendered-header compile check。

## 运行结果

每个可恢复 run 都会在 `<out>/_planning/` 保存 manifest 和中间 artifacts。qualified specs 会发布到 `<out>/<protocol>_specs/`；未通过 qualification 时，仍可能保留 candidate specs 供分析。

调用 coder 前请检查：

```text
<out>/_planning/run_manifest.json
<out>/_planning/diagnostics.json
```

以 `run_status`、`qualification_passed` 和 `specs_root` 为准，不能将 candidate-only output 视为 qualified specs。

```bash
python3 -m unittest discover -s agent/planning/tests -v
```
