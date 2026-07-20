# Facts Agent

The facts agent is the first stage of SpecForge. It reads one or more `.txt` technical documents for a protocol and extracts structured protocol facts within the role and feature boundaries defined by a target profile.

The pipeline performs document chunking, surface discovery, candidate retrieval, reranking, category extraction, cross-category reconciliation, and sufficiency validation. Each fact uses `evidence_refs` to link back to `evidence_index` entries from the source documents.

## Usage

Run commands from the repository root. Repeat `--doc` to provide multiple documents.

```bash
python3 -m agent facts validate \
  --protocol-name mqtt \
  --doc document/MQTT_3.1.1.txt \
  --target-profile agent/facts/target_profiles/mqtt_min.json \
  --skip-llm-check

export ALI_API=<your-api-key>
python3 -m agent facts extract \
  --protocol-name mqtt \
  --doc document/MQTT_3.1.1.txt \
  --target-profile agent/facts/target_profiles/mqtt_min.json \
  --output-dir agent/facts/out/mqtt_min

python3 -m agent facts verify --output-dir agent/facts/out/mqtt_min
```

Compare a candidate against the frozen reference facts offline:

```bash
python3 -m agent facts compare \
  --candidate agent/facts/out/mqtt_min/protocol_facts.json \
  --gold agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --contract agent/facts/contracts/mqtt_min_gold_contract.json \
  --rubric agent/facts/contracts/mqtt_min_semantic_rubric.json \
  --out agent/facts/out/mqtt_min/comparison
```

## Outputs

- `protocol_facts.json`: transport, interaction, message, state, routing, resource, error/limit, and minimum-scope facts.
- `run_manifest.json`: inputs, model configuration, token usage, and run status.
- `_agent_logs/`: chunks, prompts, responses, and intermediate selection results.

The facts agent should report only evidence-supported facts. Implementation choices and unresolved information remain open questions for the planning agent. Input is currently limited to `.txt`; `extract` requires a model API, and the verifier does not prove complete semantic correctness.

```bash
python3 -m unittest discover -s agent/facts/tests -v
```
