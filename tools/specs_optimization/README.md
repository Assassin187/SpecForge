# Specs Optimization Diagnostic Toolkit

This directory contains the minimal Step 2 diagnostic tools for SpecForge specs optimization. The tools are standalone and do not modify `agent/planning` or `agent/coder`.

## Tools

### `degrade_specs.py`

Generate degraded copies of a gold/example specs bundle.

```bash
python tools/specs_optimization/degrade_specs.py \
  --input specs-example/mqtt_specs \
  --output /tmp/specforge_diag_mqtt \
  --profiles full,no_calls \
  --validate
```

Outputs:

- `<output>/<profile>/specs/`
- `<output>/<profile>/manifest.json`
- `<output>/<profile>/summary.md`
- `<output>/sidecar_only_traceability/traceability_sidecar.json` when that profile is used

Supported profiles:

- `full`
- `no_behavior_detail`
- `no_wire_binding`
- `no_calls`
- `min_interface`
- `no_test_vectors`
- `sidecar_only_traceability`

### `compare_specs.py`

Compare gold/example specs with planning-generated specs.

```bash
python tools/specs_optimization/compare_specs.py \
  --gold specs-example/mqtt_specs \
  --planning agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/spec_bundle \
  --output /tmp/specforge_diag_compare
```

Outputs:

- `<output>/comparison.json`
- `<output>/summary.md`

The JSON report includes module coverage, file coverage, function count, function family coverage, public type coverage, signature coverage, behavior field presence, wire/access field presence, calls/dependency presence, test vector presence, and validation diagnostics.

### `oracle_substitute.py`

Create an oracle-substituted planning bundle. Step 2 implements `P+GoldDependency`; `P+GoldType` and `P+GoldSignature` are skeleton strategies with explicit skipped reasons.

```bash
python tools/specs_optimization/oracle_substitute.py \
  --planning agent/planning/out/mqtt/broker__c__linux_epoll__minimum_v1/20260609_214554_036173_t/spec_bundle \
  --gold specs-example/mqtt_specs \
  --output /tmp/specforge_diag_oracle \
  --strategy P+GoldDependency \
  --validate
```

Outputs:

- `<output>/<strategy>/specs/`
- `<output>/<strategy>/substitution_manifest.json`
- `<output>/<strategy>/summary.md`

## Fail-Closed Rules

The tools fail closed for:

- missing input roots;
- unreadable or non-object JSON specs;
- unknown or missing `KIND`;
- zero or multiple `PROTOCOL_MODULE_SPEC` files;
- existing output directories unless `--overwrite` is supplied;
- non-schema `TRACEABILITY` fields in `sidecar_only_traceability`.

Validation diagnostics are written into manifests/reports. `oracle_substitute.py --validate` returns non-zero if the generated bundle fails validation, so downstream experiments do not silently accept an unsafe oracle bundle.

## Boundary Notes

The oracle source is always recorded as `gold_spec_code_derived_oracle`, not as a protocol fact. These tools must not be used to claim that code-derived details came from technical documents.

`P+GoldDependency` replaces only matched module/file dependency fields. It does not clear unmatched dependencies, and it does not synthesize a successful dependency graph.
