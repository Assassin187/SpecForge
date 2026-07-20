# Facts Agent

facts agent 是 SpecForge 的第一阶段。它读取同一协议的一份或多份 `.txt` technical documents，并在 target profile 限定的角色与功能边界内抽取结构化 protocol facts。

流程包括文档切分、surface discovery、候选召回、rerank、分类抽取、跨类别 reconciliation 和充分性校验。每个事实通过 `evidence_refs` 关联 `evidence_index`，以追踪其文档依据。

## 使用

在仓库根目录运行；可重复传入 `--doc`：

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

离线比较 candidate 与冻结的 reference facts：

```bash
python3 -m agent facts compare \
  --candidate agent/facts/out/mqtt_min/protocol_facts.json \
  --gold agent/facts/gold_facts/mqtt_min/protocol_facts.json \
  --contract agent/facts/contracts/mqtt_min_gold_contract.json \
  --rubric agent/facts/contracts/mqtt_min_semantic_rubric.json \
  --out agent/facts/out/mqtt_min/comparison
```

## 输出

- `protocol_facts.json`：transport、interaction、message、state、routing、resource、error/limit 与 minimum-scope facts。
- `run_manifest.json`：输入、模型配置、token 用量和运行状态。
- `_agent_logs/`：chunks、prompts、responses 和中间选择结果。

facts agent 应只报告有 evidence 支持的事实；实现策略和未解决信息保留为 open questions，交由 planning agent 处理。当前只支持 `.txt` 输入，`extract` 需要模型 API，verifier 也不能证明完整的语义正确性。

```bash
python3 -m unittest discover -s agent/facts/tests -v
```
