from __future__ import annotations

import json
from typing import Any

from .models import Chunk, NormalizedDoc, RerankCandidate


SYSTEM_PROMPT = """You extract protocol semantics from technical documents into strict JSON.

Rules:
- Output JSON only, with no Markdown fences and no commentary.
- Use only facts supported by the provided document chunks.
- Keep claims implementation-oriented and protocol-semantic, not tutorial-style.
- Prefer protocol-generic vocabulary that works across message-oriented, command-oriented, and resource-oriented application protocols.
- If a fact is unclear, represent the uncertainty explicitly in open_questions instead of inventing.
- Every concrete fact item must carry evidence_refs that point to evidence entries returned in the same JSON object.
"""

RERANK_SYSTEM_PROMPT = """You rerank chunk candidates for a protocol-semantic extraction task.

Rules:
- Output JSON only, with no Markdown fences and no commentary.
- Rank chunk IDs by how useful they are for the target task.
- Prefer chunks that define protocol surface units, fields, state, routing, resources, limits, or error behavior.
- Penalize references, acknowledgements, examples, and weakly related narrative text.
- Do not extract protocol facts yet; only rank the candidates.
"""


SURFACE_DISCOVERY_SCHEMA = """
{
  "surface_units": [
    {
      "name": "...",
      "kind": "message|command|method|response|option|field_family|code_space",
      "direction": "client_to_server|server_to_client|bidirectional|mixed|n/a",
      "summary": "...",
      "evidence_refs": ["disc_e1"]
    }
  ],
  "stateful_objects": [
    {
      "name": "...",
      "scope": "connection|session|transaction|exchange|resource|implementation_defined",
      "summary": "...",
      "evidence_refs": ["disc_e2"]
    }
  ],
  "resource_objects": [
    {
      "name": "...",
      "summary": "...",
      "evidence_refs": ["disc_e3"]
    }
  ],
  "parameter_candidates": [
    {
      "name": "...",
      "kind": "timer|length|count|default|recommended|limit|constant",
      "value_or_rule": "...",
      "evidence_refs": ["disc_e4"]
    }
  ],
  "evidence": [
    {
      "evidence_id": "disc_e1",
      "doc_path": "...",
      "section_hint": "...",
      "chunk_id": "...",
      "excerpt": "..."
    }
  ],
  "open_questions": [
    {
      "category": "surface_discovery",
      "question_type": "document_not_extracted|external_dependency|spec_undefined|implementation_policy_needed",
      "question": "...",
      "blocking_impact": "low|medium|high",
      "suggested_followup": "..."
    }
  ]
}
"""


TASK_DEFINITIONS: dict[str, dict[str, Any]] = {
    "transport_overview": {
        "category": "transport",
        "instruction": "Extract transport facts that determine the network/runtime implementation shape.",
        "schema": """
{
  "category": "transport",
  "task": "transport_overview",
  "facts": {
    "network_stack": {"value": "tcp|udp|mixed|unknown", "evidence_refs": ["transport_e1"]},
    "connection_model": {"value": "connectionless|connection_oriented|control_plus_data|mixed|unknown", "evidence_refs": ["transport_e2"]},
    "channels": [{"name": "main", "transport": "tcp", "purpose": "...", "evidence_refs": ["transport_e3"]}],
    "runtime_implications": [{"summary": "...", "evidence_refs": ["transport_e4"]}]
  },
  "evidence": [{"evidence_id": "transport_e1", "doc_path": "...", "section_hint": "...", "chunk_id": "...", "excerpt": "..."}],
  "open_questions": [{"category": "transport", "question_type": "document_not_extracted", "question": "...", "blocking_impact": "medium", "suggested_followup": "..."}]
}
""",
    },
    "interaction_overview": {
        "category": "interaction_model",
        "instruction": "Extract the protocol interaction style, actors, flows, and surface-unit correspondences.",
        "schema": """
{
  "category": "interaction_model",
  "task": "interaction_overview",
  "facts": {
    "style": {"value": "request_response|command_response|publish_subscribe|mixed|unknown", "evidence_refs": ["interaction_e1"]},
    "roles": [{"name": "client", "summary": "...", "evidence_refs": ["interaction_e2"]}],
    "interaction_units": [{"name": "request", "summary": "...", "evidence_refs": ["interaction_e3"]}],
    "core_flows": [{"name": "...", "summary": "...", "surface_units": ["..."], "evidence_refs": ["interaction_e4"]}]
  },
  "evidence": [{"evidence_id": "interaction_e1", "doc_path": "...", "section_hint": "...", "chunk_id": "...", "excerpt": "..."}],
  "open_questions": [{"category": "interaction_model", "question_type": "document_not_extracted", "question": "...", "blocking_impact": "medium", "suggested_followup": "..."}]
}
""",
    },
    "message_surface_catalog": {
        "category": "message_model",
        "instruction": "Extract the complete protocol surface catalog and shared framing/field facts. Cover messages, commands, methods, responses, code spaces, options, and field families as applicable.",
        "schema": """
{
  "category": "message_model",
  "task": "message_surface_catalog",
  "facts": {
    "framing": {"value": "binary|line_oriented|mixed|unknown", "evidence_refs": ["message_e1"]},
    "surface_catalog": [{"name": "...", "kind": "message|command|method|response|option|field_family|code_space", "direction": "client_to_server|server_to_client|bidirectional|mixed|n/a", "summary": "...", "evidence_refs": ["message_e2"]}],
    "shared_fields": [{"name": "...", "applies_to": ["..."], "summary": "...", "constraints": ["..."], "evidence_refs": ["message_e3"]}],
    "global_framing_rules": [{"summary": "...", "evidence_refs": ["message_e4"]}],
    "code_spaces": [{"name": "...", "entries": [{"name": "...", "meaning": "...", "evidence_refs": ["message_e5"]}], "evidence_refs": ["message_e6"]}]
  },
  "evidence": [{"evidence_id": "message_e1", "doc_path": "...", "section_hint": "...", "chunk_id": "...", "excerpt": "..."}],
  "open_questions": [{"category": "message_model", "question_type": "document_not_extracted", "question": "...", "blocking_impact": "high", "suggested_followup": "..."}]
}
""",
    },
    "message_entry_details": {
        "category": "message_model",
        "instruction": "Extract per-surface-unit details. Each entry must represent one message, command, method, or response form and include layout/syntax, fields, preconditions, responses, state effects, and error paths.",
        "schema": """
{
  "category": "message_model",
  "task": "message_entry_details",
  "facts": {
    "message_or_command_entries": [
      {
        "name": "...",
        "kind": "message|command|method|response",
        "direction": "client_to_server|server_to_client|bidirectional|mixed|n/a",
        "syntax_or_layout": "...",
        "fields": [{"name": "...", "role": "...", "constraints": ["..."], "evidence_refs": ["message_e7"]}],
        "preconditions": [{"summary": "...", "evidence_refs": ["message_e8"]}],
        "expected_responses": [{"surface_unit": "...", "summary": "...", "evidence_refs": ["message_e9"]}],
        "state_effects": [{"summary": "...", "evidence_refs": ["message_e10"]}],
        "error_paths": [{"condition": "...", "required_action": "...", "evidence_refs": ["message_e11"]}],
        "evidence_refs": ["message_e12"]
      }
    ]
  },
  "evidence": [{"evidence_id": "message_e7", "doc_path": "...", "section_hint": "...", "chunk_id": "...", "excerpt": "..."}],
  "open_questions": [{"category": "message_model", "question_type": "document_not_extracted", "question": "...", "blocking_impact": "high", "suggested_followup": "..."}]
}
""",
    },
    "state_structure": {
        "category": "state_model",
        "instruction": "Extract explicit state scopes, state objects, state nodes, retained data, and cleanup rules. Do not summarize loosely; express implementable state structure.",
        "schema": """
{
  "category": "state_model",
  "task": "state_structure",
  "facts": {
    "state_scopes": [{"name": "connection", "summary": "...", "evidence_refs": ["state_e1"]}],
    "state_objects": [{"name": "...", "scope": "connection|session|transaction|exchange|resource|implementation_defined", "summary": "...", "evidence_refs": ["state_e2"]}],
    "state_nodes": [{"name": "...", "scope": "connection|session|transaction|exchange|resource|implementation_defined", "summary": "...", "retained_data_refs": ["..."], "evidence_refs": ["state_e3"]}],
    "retained_data": [{"name": "...", "scope": "connection|session|transaction|exchange|resource|implementation_defined", "summary": "...", "cleanup_conditions": ["..."], "evidence_refs": ["state_e4"]}],
    "cleanup_rules": [{"name": "...", "summary": "...", "affected_objects": ["..."], "evidence_refs": ["state_e5"]}]
  },
  "evidence": [{"evidence_id": "state_e1", "doc_path": "...", "section_hint": "...", "chunk_id": "...", "excerpt": "..."}],
  "open_questions": [{"category": "state_model", "question_type": "document_not_extracted", "question": "...", "blocking_impact": "high", "suggested_followup": "..."}]
}
""",
    },
    "state_transitions": {
        "category": "state_model",
        "instruction": "Extract explicit state transitions, triggers, actions, retained-data effects, cleanup effects, timers, constants, defaults, and boundary values.",
        "schema": """
{
  "category": "state_model",
  "task": "state_transitions",
  "facts": {
    "transitions": [{"name": "...", "from_state": "...", "trigger": "...", "conditions": ["..."], "actions": ["..."], "to_state": "...", "retained_data_effects": ["..."], "cleanup_effects": ["..."], "evidence_refs": ["state_e6"]}],
    "timers_and_constants": [{"name": "...", "kind": "timer|constant|default|recommended|limit", "value_or_rule": "...", "scope": "...", "evidence_refs": ["state_e7"]}]
  },
  "evidence": [{"evidence_id": "state_e6", "doc_path": "...", "section_hint": "...", "chunk_id": "...", "excerpt": "..."}],
  "open_questions": [{"category": "state_model", "question_type": "document_not_extracted", "question": "...", "blocking_impact": "high", "suggested_followup": "..."}]
}
""",
    },
    "routing_rules": {
        "category": "routing_model",
        "instruction": "Extract dispatch keys, dispatch targets, and explicit matching/routing rules.",
        "schema": """
{
  "category": "routing_model",
  "task": "routing_rules",
  "facts": {
    "dispatch_keys": [{"name": "...", "summary": "...", "evidence_refs": ["routing_e1"]}],
    "dispatch_targets": [{"name": "...", "summary": "...", "evidence_refs": ["routing_e2"]}],
    "matching_rules": [{"summary": "...", "evidence_refs": ["routing_e3"]}]
  },
  "evidence": [{"evidence_id": "routing_e1", "doc_path": "...", "section_hint": "...", "chunk_id": "...", "excerpt": "..."}],
  "open_questions": [{"category": "routing_model", "question_type": "document_not_extracted", "question": "...", "blocking_impact": "medium", "suggested_followup": "..."}]
}
""",
    },
    "resource_objects": {
        "category": "resource_model",
        "instruction": "Extract protocol resource objects, their linked surface units, and main business operations.",
        "schema": """
{
  "category": "resource_model",
  "task": "resource_objects",
  "facts": {
    "resource_objects": [{"name": "...", "kind": "logical_resource|message_object|file_object|transaction_object|implementation_defined", "creation_rules": ["..."], "lifetime_rules": ["..."], "deletion_rules": ["..."], "persistence": "none|connection_scoped|session_scoped|server_persistent|implementation_defined", "ownership": "...", "linked_surface_units": ["..."], "evidence_refs": ["resource_e1"]}],
    "business_operations": [{"name": "...", "summary": "...", "resource_objects": ["..."], "linked_surface_units": ["..."], "evidence_refs": ["resource_e2"]}]
  },
  "evidence": [{"evidence_id": "resource_e1", "doc_path": "...", "section_hint": "...", "chunk_id": "...", "excerpt": "..."}],
  "open_questions": [{"category": "resource_model", "question_type": "document_not_extracted", "question": "...", "blocking_impact": "high", "suggested_followup": "..."}]
}
""",
    },
    "resource_lifecycle": {
        "category": "resource_model",
        "instruction": "Extract lifecycle rules, persistence scope, ownership/authority, and storage boundaries for protocol resource objects.",
        "schema": """
{
  "category": "resource_model",
  "task": "resource_lifecycle",
  "facts": {
    "lifecycle_rules": [{"summary": "...", "resource_objects": ["..."], "evidence_refs": ["resource_e3"]}],
    "persistence_scope": [{"name": "...", "scope": "none|connection_scoped|session_scoped|server_persistent|implementation_defined", "summary": "...", "evidence_refs": ["resource_e4"]}],
    "ownership_and_authority": [{"actor": "...", "capability": "...", "summary": "...", "evidence_refs": ["resource_e5"]}],
    "storage_requirements": [{"summary": "...", "evidence_refs": ["resource_e6"]}]
  },
  "evidence": [{"evidence_id": "resource_e3", "doc_path": "...", "section_hint": "...", "chunk_id": "...", "excerpt": "..."}],
  "open_questions": [{"category": "resource_model", "question_type": "document_not_extracted", "question": "...", "blocking_impact": "high", "suggested_followup": "..."}]
}
""",
    },
    "error_matrix": {
        "category": "error_and_limits",
        "instruction": "Extract a condition-action matrix for protocol errors and malformed inputs. Use explicit condition -> action entries, not summaries.",
        "schema": """
{
  "category": "error_and_limits",
  "task": "error_matrix",
  "facts": {
    "error_matrix": [{"condition": "...", "applicable_surface_units": ["..."], "actor": "...", "required_action": "...", "state_cleanup": "...", "severity": "warning|error|fatal", "normative_strength": "MUST|SHOULD|MAY|UNKNOWN", "evidence_refs": ["error_e1"]}]
  },
  "evidence": [{"evidence_id": "error_e1", "doc_path": "...", "section_hint": "...", "chunk_id": "...", "excerpt": "..."}],
  "open_questions": [{"category": "error_and_limits", "question_type": "document_not_extracted", "question": "...", "blocking_impact": "high", "suggested_followup": "..."}]
}
""",
    },
    "limits_and_security": {
        "category": "error_and_limits",
        "instruction": "Extract parameterized limits and security facts. Separate fixed constants, defaults, recommended values, hard bounds, configurable bounds, and external security dependencies.",
        "schema": """
{
  "category": "error_and_limits",
  "task": "limits_and_security",
  "facts": {
    "limits": {
      "fixed_protocol_constants": [{"name": "...", "value_or_rule": "...", "unit": "...", "scope": "...", "evidence_refs": ["limit_e1"]}],
      "defaults": [{"name": "...", "value_or_rule": "...", "unit": "...", "scope": "...", "evidence_refs": ["limit_e2"]}],
      "recommended_values": [{"name": "...", "value_or_rule": "...", "unit": "...", "scope": "...", "evidence_refs": ["limit_e3"]}],
      "hard_bounds": [{"name": "...", "value_or_rule": "...", "unit": "...", "scope": "...", "evidence_refs": ["limit_e4"]}],
      "configurable_bounds": [{"name": "...", "value_or_rule": "...", "unit": "...", "scope": "...", "evidence_refs": ["limit_e5"]}]
    },
    "security_requirements": {
      "base_protocol_requirements": [{"summary": "...", "evidence_refs": ["security_e1"]}],
      "recommended_practices": [{"summary": "...", "evidence_refs": ["security_e2"]}],
      "external_security_dependencies": [{"name": "...", "summary": "...", "evidence_refs": ["security_e3"]}],
      "explicitly_out_of_scope_security_features": [{"summary": "...", "evidence_refs": ["security_e4"]}]
    },
    "out_of_scope_candidates": [{"summary": "...", "evidence_refs": ["security_e5"]}]
  },
  "evidence": [{"evidence_id": "limit_e1", "doc_path": "...", "section_hint": "...", "chunk_id": "...", "excerpt": "..."}],
  "open_questions": [{"category": "error_and_limits", "question_type": "external_dependency", "question": "...", "blocking_impact": "medium", "suggested_followup": "..."}]
}
""",
    },
    "minimum_boundary": {
        "category": "minimum_v1",
        "instruction": "Extract only the minimum implementation boundary. Do not repeat the full protocol surface here; only include the minimum subsets and obligations required for a viable implementation.",
        "schema": """
{
  "category": "minimum_v1",
  "task": "minimum_boundary",
  "facts": {
    "must_support_surface": [{"name": "...", "reason": "...", "evidence_refs": ["v1_e1"]}],
    "must_support_state_behaviors": [{"summary": "...", "linked_state_objects": ["..."], "evidence_refs": ["v1_e2"]}],
    "must_support_error_paths": [{"condition": "...", "required_action": "...", "evidence_refs": ["v1_e3"]}],
    "must_support_limits": [{"name": "...", "value_or_rule": "...", "evidence_refs": ["v1_e4"]}],
    "may_defer_features": [{"summary": "...", "evidence_refs": ["v1_e5"]}],
    "implementation_assumptions": [{"summary": "...", "evidence_refs": ["v1_e6"]}]
  },
  "evidence": [{"evidence_id": "v1_e1", "doc_path": "...", "section_hint": "...", "chunk_id": "...", "excerpt": "..."}],
  "open_questions": [{"category": "minimum_v1", "question_type": "implementation_policy_needed", "question": "...", "blocking_impact": "medium", "suggested_followup": "..."}]
}
""",
    },
}


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def build_surface_discovery_prompt(
    protocol_name: str,
    docs: list[NormalizedDoc],
    chunks: list[Chunk],
    scope_envelope: dict[str, Any],
) -> list[dict[str, str]]:
    chunk_payload = [
        {
            "chunk_id": chunk.chunk_id,
            "doc_path": chunk.doc_path,
            "section_hint": chunk.section_hint,
            "section_id": chunk.section_id,
            "section_order_in_doc": chunk.section_order_in_doc,
            "chunk_order_in_section": chunk.chunk_order_in_section,
            "keywords": chunk.keywords,
            "text": chunk.text,
        }
        for chunk in chunks
    ]
    doc_meta = [{"path": str(doc.path), "title": doc.title} for doc in docs]
    content = f"""Discover the implementation-relevant protocol surface for `{protocol_name}`.

This task must stay protocol-generic. Do not assume any protocol-specific vocabulary ahead of time.
The target profile is a scope directive, not technical-document evidence. Discover the complete lightweight
surface index, but apply this separate scope envelope to detailed extraction:
{_json(scope_envelope)}

Identify:
- surface units: messages, commands, methods, responses, options, field families, code spaces
- stateful objects
- resource objects
- parameter candidates such as timers, constants, limits, defaults, and recommended values

Document set:
{_json(doc_meta)}

Relevant chunks:
{_json(chunk_payload)}

Return exactly this JSON shape:
{SURFACE_DISCOVERY_SCHEMA}
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def build_category_task_prompt(
    protocol_name: str,
    docs: list[NormalizedDoc],
    category: str,
    task_name: str,
    discovery_facts: dict[str, Any],
    chunks: list[Chunk],
    scope_envelope: dict[str, Any],
) -> list[dict[str, str]]:
    if task_name not in TASK_DEFINITIONS:
        raise KeyError(f"Unknown task_name: {task_name}")
    task_def = TASK_DEFINITIONS[task_name]
    chunk_payload = [
        {
            "chunk_id": chunk.chunk_id,
            "doc_path": chunk.doc_path,
            "section_hint": chunk.section_hint,
            "section_id": chunk.section_id,
            "section_order_in_doc": chunk.section_order_in_doc,
            "chunk_order_in_section": chunk.chunk_order_in_section,
            "keywords": chunk.keywords,
            "text": chunk.text,
        }
        for chunk in chunks
    ]
    doc_meta = [{"path": str(doc.path), "title": doc.title} for doc in docs]
    content = f"""Extract the `{category}` semantics for protocol `{protocol_name}` using the generic task `{task_name}`.

Task intent:
{task_def["instruction"]}

General constraints:
- Stay protocol-generic in reasoning and structure.
- Prefer complete catalogs and explicit implementable constraints over representative examples.
- If the document set does not define something clearly, emit an open_question rather than guessing.
- Evidence refs must point to evidence entries returned in this JSON object.
- Extract only included_surface and allowed_shared_dependencies from the scope envelope.
- Do not expand into excluded_features merely because a selected chunk mentions them.
- Return a newly suspected dependency as an open_question instead of silently adding it.

Scope envelope (directive, not document evidence):
{_json(scope_envelope)}

Document set:
{_json(doc_meta)}

Discovery context:
{_json(discovery_facts)}

Relevant chunks:
{_json(chunk_payload)}

Return exactly this JSON shape:
{task_def["schema"]}
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def build_reconciliation_prompt(
    protocol_name: str,
    discovery_facts: dict[str, Any],
    category_facts: dict[str, dict[str, Any]],
    scope_envelope: dict[str, Any],
) -> list[dict[str, str]]:
    content = f"""Reconcile extracted protocol facts for `{protocol_name}`.

This is a protocol-generic consistency pass. Do not invent missing facts or reintroduce excluded scope.

Scope envelope:
{_json(scope_envelope)}
Check:
- whether interaction flows reference surface units that are present in message_model
- whether minimum_v1 items map back to main facts
- whether state transitions reference known surface units, state objects, or resource objects
- whether error matrix coverage appears incomplete for key surface units

Discovery facts:
{_json(discovery_facts)}

Category facts:
{_json(category_facts)}

Return exactly this JSON shape:
{{
  "consistency_findings": [
    {{
      "severity": "info|warning|error",
      "summary": "...",
      "categories": ["message_model", "interaction_model"]
    }}
  ],
  "coverage_gaps": [
    {{
      "category": "message_model",
      "summary": "..."
    }}
  ],
  "global_open_questions": [
    {{
      "category": "message_model",
      "question_type": "document_not_extracted|external_dependency|spec_undefined|implementation_policy_needed",
      "question": "...",
      "blocking_impact": "low|medium|high",
      "suggested_followup": "..."
    }}
  ],
  "planner_risks": [
    {{
      "summary": "..."
    }}
  ]
}}
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def build_rerank_prompt(protocol_name: str, category: str, candidates: list[RerankCandidate]) -> list[dict[str, str]]:
    content = f"""Rerank chunk candidates for protocol `{protocol_name}` and task `{category}`.

Goal:
- Judge which chunk candidates are most valuable for extracting the target semantics.
- Prefer chunks that define protocol surface units, formats, state rules, routing rules, resource semantics, limits, and error behavior.
- De-prioritize references, acknowledgements, examples, and weakly related narrative text.

Candidate cards:
{_json([candidate.__dict__ for candidate in candidates])}

Return exactly this JSON shape:
{{
  "category": "{category}",
  "ranked_chunk_ids": ["chunk-id-1", "chunk-id-2"],
  "high_priority_chunk_ids": ["chunk-id-1"],
  "notes": ["short rationale"],
  "open_questions": ["..."]
}}
"""
    return [
        {"role": "system", "content": RERANK_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
