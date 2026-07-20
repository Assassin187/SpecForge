# Planning Agent

The planning agent is the core component of SpecForge. It converts `protocol_facts.json` into protocol specs that the coder can consume directly. It plans module and file boundaries, public artifacts, C interfaces, function behavior, call contracts, dependencies, wire mappings, and test vectors instead of producing only a protocol summary.

Outputs conform to `specs-example/specs_schema/` and include `<protocol>_module_spec.json`, module-level `FILE_SPEC` files, per-function `FUNCTION_SPEC` files, `SUMMARY.md`, and a `_planning/` directory containing decisions, assumptions, diagnostics, and metrics.

## Usage

```bash
export ALI_API=<your-api-key>
python3 -m agent planning plan \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --out agent/planning/out/mqtt_min

python3 -m agent planning stages

python3 -m agent planning validate \
  --run-dir agent/planning/out/mqtt_min
```

Resume an interrupted run from a selected stage:

```bash
python3 -m agent planning plan \
  --facts agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --out agent/planning/out/mqtt_min \
  --resume-from function_behavior_design
```

By default, validation includes planning checks, the coder spec loader, and a rendered-header compile check.

## Run Results

Every recoverable run stores its manifest and intermediate artifacts under `<out>/_planning/`. Qualified specs are published to `<out>/<protocol>_specs/`; failed qualification may still leave candidate specs for analysis.

Before using the coder, inspect:

```text
<out>/_planning/run_manifest.json
<out>/_planning/diagnostics.json
```

Use `run_status`, `qualification_passed`, and `specs_root` as the authoritative result. Do not treat candidate-only output as qualified specs.

```bash
python3 -m unittest discover -s agent/planning/tests -v
```
