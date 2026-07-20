# Specs Optimization Tools

本目录包含用于分析 protocol specs 信息价值的离线工具，不会修改 planning agent 或 coder。

## Specs 退化

```bash
python3 tools/specs_optimization/degrade_specs.py \
  --input specs-example/mqtt_specs \
  --output /tmp/specforge_degraded \
  --profiles full,no_calls,no_test_vectors \
  --validate
```

每个 profile 会输出 `specs/`、`manifest.json` 和 `summary.md`。

## Specs 比较

```bash
python3 tools/specs_optimization/compare_specs.py \
  --gold specs-example/mqtt_specs \
  --planning /path/to/planning/specs \
  --output /tmp/specforge_comparison \
  --match-mode trace_id
```

输出 `comparison.json` 和 `summary.md`，覆盖 module/file/function 结构、types、signatures、behavior、wire/access 信息、dependencies 和 test vectors。

## Oracle substitution

```bash
python3 tools/specs_optimization/oracle_substitute.py \
  --planning /path/to/planning/specs \
  --gold specs-example/mqtt_specs \
  --output /tmp/specforge_oracle \
  --strategy P+GoldDependency \
  --validate
```

输出替换后的 `specs/`、`substitution_manifest.json` 和 `summary.md`。reference specs 是 code-derived oracle，不能表述为从 technical documents 抽取出的 protocol facts。

工具会 fail closed：缺失或含糊的输入、无效 JSON、非唯一 module spec 和已存在的输出目录都会失败；覆盖输出时需显式传入 `--overwrite`。
