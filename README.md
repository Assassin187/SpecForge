# SpecForge

SpecForge is an agent pipeline for network protocol implementation:

```text
technical documents -> facts agent -> protocol facts
-> planning agent -> protocol specs -> coder agent -> protocol implementation
```

The planning agent is the central research artifact. Existing work has explored extracting protocol facts from technical documents and generating code from engineering specifications. SpecForge addresses the missing middle layer by turning factual protocol knowledge into actionable engineering structure.

The planning agent analyzes message formats, state machines, transport requirements, error handling, module boundaries, APIs, and dependencies. It produces schema-constrained `PROTOCOL_MODULE_SPEC`, `FILE_SPEC`, and `FUNCTION_SPEC` artifacts rather than a natural-language summary. `TRACE_ID`, `DOC_REF`, and `TRACE_REFS` preserve the provenance of engineering decisions.

## Repository Structure

- `agent/facts/`: extracts `protocol_facts.json` from `.txt` technical documents.
- `agent/planning/`: converts protocol facts into implementation-oriented protocol specs.
- `agent/coder/`: generates a C project from protocol specs, then compiles, repairs, and behavior-checks it.
- `specs-example/`: reference specs and their JSON Schemas.
- `protocol-example/`: independently buildable reference protocol implementations.
- `evaluation/`: planning utility and spec form ablation experiments.
- `tools/specs_optimization/`: protocol specs diagnostic tools.

## Requirements

The main pipeline requires Linux, Python 3, `gcc`, and `make`. Model calls use an OpenAI-compatible API:

```bash
python3 -m pip install openai transformers
export ALI_API=<your-api-key>
cd /path/to/SpecForge
```

The default model and endpoint are defined in `agent/common/llm_client.py`.

## Quick Start

The following commands run the MQTT minimum profile:

```bash
python3 -m agent facts extract \
  --protocol-name mqtt \
  --doc document/MQTT_3.1.1.txt \
  --target-profile agent/facts/target_profiles/mqtt_min.json \
  --output-dir agent/facts/out/mqtt_min

python3 -m agent planning plan \
  --facts agent/facts/out/mqtt_min/protocol_facts.json \
  --out agent/planning/out/mqtt_min

python3 -m agent coder \
  --spec-root agent/planning/out/mqtt_min/mqtt_specs \
  --output-dir agent/out/mqtt_min \
  generate
```

You can also generate code directly from the reference specs:

```bash
python3 -m agent coder \
  --spec-root specs-example/mqtt_specs \
  --output-dir agent/out/mqtt_reference \
  generate
```

Some planning runs produce candidate specs only. Before invoking the coder, check `specs_root` and the qualification fields in `<out>/_planning/run_manifest.json`.

## Tests

```bash
python3 -m unittest discover -s agent/facts/tests -v
python3 -m unittest discover -s agent/planning/tests -v
python3 -m unittest discover -s agent/coder/tests -v
```

The included implementations and behavior tests target minimum profiles and do not demonstrate full RFC conformance.
