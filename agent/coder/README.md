# Coder Agent

The coder agent is the final stage of SpecForge. It loads `PROTOCOL_MODULE_SPEC`, `FILE_SPEC`, and `FUNCTION_SPEC` artifacts, generates a C project, and can run compilation, bounded source repair, and minimum-profile behavior checks.

Public headers and the Makefile are rendered deterministically from structured specs, while source files are model-generated. Repair is limited to source files mapped from compiler diagnostics and must not invent public APIs or protocol behavior missing from the specs.

## Usage

Global options must appear before the subcommand.

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

Generate source files without compiling, repairing, or running behavior checks:

```bash
python3 -m agent coder \
  --spec-root specs-example/mqtt_specs \
  --output-dir agent/out/mqtt_reference \
  --skip-repair \
  generate
```

Copy and repair an existing project:

```bash
python3 -m agent coder \
  --spec-root specs-example/mqtt_specs \
  repair \
  --project-dir agent/out/mqtt_reference/mqtt
```

Verify an existing run, or test only an already compiled binary:

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

`test` currently supports `mqtt`, `coap`, `http`, and `smtp`.

## Outputs

The output directory contains the protocol project and `_agent_logs/`. `run_manifest.json` records inputs, model calls, token usage, compile/repair state, and behavior-check results. Standalone `repair` does not modify the original project.

Behavior validation is a minimum-profile smoke test, not full RFC conformance. See `protocol_behavior_val/README.md` for details.

```bash
python3 -m unittest discover -s agent/coder/tests -v
```
