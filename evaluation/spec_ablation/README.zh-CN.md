# Spec Form Ablation

本目录研究 coder-facing specification forms 对生成质量的影响。四组处理都来自同一套 `specs-example/<protocol>_specs`：

- S1：local function specs 和 raw headers。
- S2：S1 加 global project graph。
- S3：S2 加 structured type/interface grounding。
- S4：完整的 SpecForge protocol specs。

`transformer.py` 会确定性投影 S1-S3，再由 `view_generator.py` 消费；S4 直接使用 coder。本实验不评价 facts extraction 或 planning quality。

## 使用

```bash
python3 -m evaluation.spec_ablation.transformer \
  --protocol all \
  --view all \
  --output evaluation/spec_ablation/specs \
  --overwrite

python3 -m evaluation.spec_ablation.view_generator \
  --view s2 \
  --view-root evaluation/spec_ablation/specs/mqtt/s2 \
  validate
```

运行 generation experiment：

```bash
export ALI_API=<your-api-key>
evaluation/spec_ablation/run_baseline_generation.sh \
  --protocol mqtt \
  --view all \
  --start-round 1 \
  --rounds 3
```

生成单个 S1-S3 run：

```bash
python3 -m evaluation.spec_ablation.view_generator \
  --view s3 \
  --view-root evaluation/spec_ablation/specs/mqtt/s3 \
  --output-dir evaluation/spec_ablation/out/mqtt_s3 \
  generate
```

结果写入 `evaluation/spec_ablation/out/`。主要报告 end-to-end success、behavior pass rate、compile/repair status、failure taxonomy 和 token usage。

```bash
python3 -m unittest discover -s evaluation/spec_ablation/tests -v
```
