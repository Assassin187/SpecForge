# Planning Utility Evaluation

本目录实现 planning agent 的 downstream utility evaluation。它从冻结的 MQTT minimum protocol facts 出发，比较：

- `fs-direct-coder`：从 facts-derived context 直接生成代码。
- `nl-plan-code`：先生成 natural-language engineering plan，再生成代码。
- `one-shot-structured-planning`：用一次模型调用完成 structured planning。
- `full-specforge`：使用完整的 staged planning agent。

该实验衡量 implementation-oriented protocol specs 是否改善 downstream compilation 和 protocol behavior，不评价 facts agent 的 extraction accuracy。

## 使用

```bash
export ALI_API=<your-api-key>

python3 -m evaluation.planning_utility.run_matrix \
  --protocol mqtt \
  --output-root evaluation/planning_utility/out

python3 -m evaluation.planning_utility.run_matrix \
  --protocol mqtt \
  --method full-specforge \
  --full-planning-dir mqtt=/path/to/planning/run
```

`--max-repair-rounds` 控制 M2/M3 的 coder repair，`--max-repair-calls` 控制 direct 和 natural-language baselines 的 bounded repair。

## 输出

每个方法保存 allowed inputs 及其 hashes、planning 或 baseline artifacts、generated project、logs 和 `summary.json`。主要指标是 end-to-end success、required behavior scenario pass rate 和 final compile success。

重算保存 run 的 metrics：

```bash
python3 -m evaluation.planning_utility.recalculate_saved_rq1_metrics \
  --output evaluation/planning_utility/out/recalculated_metrics.json
```

独立 repair 一个 M0/M1 项目：

```bash
python3 -m evaluation.planning_utility.repair_cli \
  --project-dir /path/to/coder_out/mqtt \
  --method fs-direct-coder \
  --protocol mqtt \
  --binary-name mqtt_broker \
  --argv-contract './mqtt_broker <port>'
```

```bash
python3 -m unittest discover -s evaluation/planning_utility/tests -v
```
