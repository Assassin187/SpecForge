# Specs Optimization Tools

This directory contains offline tools for analyzing the value of information in protocol specs. The tools do not modify the planning agent or coder.

## Spec Degradation

```bash
python3 tools/specs_optimization/degrade_specs.py \
  --input specs-example/mqtt_specs \
  --output /tmp/specforge_degraded \
  --profiles full,no_calls,no_test_vectors \
  --validate
```

Each profile produces `specs/`, `manifest.json`, and `summary.md`.

## Spec Comparison

```bash
python3 tools/specs_optimization/compare_specs.py \
  --gold specs-example/mqtt_specs \
  --planning /path/to/planning/specs \
  --output /tmp/specforge_comparison \
  --match-mode trace_id
```

The command produces `comparison.json` and `summary.md`, covering module/file/function structure, types, signatures, behavior, wire/access information, dependencies, and test vectors.

## Oracle substitution

```bash
python3 tools/specs_optimization/oracle_substitute.py \
  --planning /path/to/planning/specs \
  --gold specs-example/mqtt_specs \
  --output /tmp/specforge_oracle \
  --strategy P+GoldDependency \
  --validate
```

The command produces substituted `specs/`, `substitution_manifest.json`, and `summary.md`. Reference specs are a code-derived oracle and must not be presented as protocol facts extracted from technical documents.

The tools fail closed on missing or ambiguous inputs, invalid JSON, a non-unique module spec, or an existing output directory. Use `--overwrite` explicitly to replace output.
