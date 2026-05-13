from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from typing import Any

from ..common.llm_client import FixedQwenClient, LLMRequest, LLMResponse, LLMUsage
from .models import PlanningIR
from .prompts import build_file_layout_prompt


FILE_LAYOUT_SCHEMA = "file_layout/v1alpha1"
FILE_LAYOUT_CALL_TOKEN_BUDGET = 30_000
FILE_LAYOUT_COMPLETION_BUDGET = 8_000
FILE_LAYOUT_MIN_COMPLETION_BUDGET = 1_000
FILE_LAYOUT_TOKEN_SAFETY_MARGIN = 512
FILE_LAYOUT_MAX_ATTEMPTS = 3


class FileLayoutGenerationError(RuntimeError):
    def __init__(self, code: str, message: str, attempts: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.attempts = attempts or []


def _safe_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "x"


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _short(text: Any, limit: int = 180) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def _estimate_message_tokens(messages: list[dict[str, str]]) -> int:
    # Conservative enough for budget gating without coupling to a provider tokenizer.
    chars = sum(len(str(message.get("role", ""))) + len(str(message.get("content", ""))) for message in messages)
    return max(1, (chars + 2) // 3)


def _completion_budget_for_prompt(prompt_estimate: int) -> int:
    available = FILE_LAYOUT_CALL_TOKEN_BUDGET - prompt_estimate - FILE_LAYOUT_TOKEN_SAFETY_MARGIN
    return max(0, min(FILE_LAYOUT_COMPLETION_BUDGET, available))


def _summarize_validation_errors(errors: list[str], *, sample_limit: int = 8) -> list[str]:
    summarized: list[str] = []
    for error in errors:
        text = str(error)
        if text.startswith("file_edges do not project dependency graph edges:"):
            raw_ids = [item.strip() for item in text.split(":", 1)[1].split(",") if item.strip()]
            by_kind: dict[str, int] = {}
            for edge_id in raw_ids:
                kind = edge_id.split(":", 1)[0] or "edge"
                by_kind[kind] = by_kind.get(kind, 0) + 1
            summarized.append(
                "file_edges missing dependency graph projection for "
                f"{len(raw_ids)} edge ids; counts_by_kind={by_kind}; "
                "every dependency_graph edge_id present in layout_context.dependency_graph must appear in one or more "
                "file_edges.source_graph_edges; sample_missing="
                f"{raw_ids[:sample_limit]}"
            )
        elif len(text) > 700:
            summarized.append(_short(text, 700))
        else:
            summarized.append(text)
    return summarized[:10]


def _edge_ids(dependency_graph: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for section in ("module_edges", "function_edges", "data_edges"):
        for idx, edge in enumerate(dependency_graph.get(section, [])):
            if isinstance(edge, dict):
                ids.add(_graph_edge_id(edge, section[:-1] if section.endswith("s") else section, idx))
    return ids


def _generate_with_usage(llm_client: FixedQwenClient | None, request: LLMRequest) -> LLMResponse | None:
    if llm_client is None:
        return None
    generate_with_usage = getattr(llm_client, "generate_with_usage", None)
    if callable(generate_with_usage):
        response = generate_with_usage(request)
        if not isinstance(response, LLMResponse):
            raise RuntimeError(f"Expected LLMResponse from generate_with_usage(), got {type(response)!r}")
        return response
    content = llm_client.generate(request)
    return LLMResponse(content=content, usage=LLMUsage(0, 0, 0))


def _load_json_payload_with_error(content: str) -> tuple[dict[str, Any] | None, str]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        payload = json.loads(cleaned)
    except Exception as exc:  # noqa: BLE001
        return None, f"file_layout response is not valid JSON, likely truncated or malformed: {type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return None, "file_layout response root must be a JSON object"
    return payload, ""


def _extract_layout_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("file_layout"), dict):
        return dict(payload["file_layout"])
    plan = payload.get("implementation_plan")
    if isinstance(plan, dict) and isinstance(plan.get("file_layout"), dict):
        return dict(plan["file_layout"])
    return {}


def _module_base(protocol_slug: str, module: dict[str, Any]) -> str:
    name = str(module.get("name", "module"))
    return str(module.get("path") or f"{protocol_slug}/{name}/{name}").rstrip("/")


def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else "."


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _public_symbol(protocol_slug: str, module_name: str, suffix: str) -> str:
    return f"{protocol_slug}_{module_name}_{suffix}"


def _canonical_type_by_module(canonical_types: list[dict[str, Any]], protocol_slug: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in canonical_types:
        if not isinstance(item, dict):
            continue
        owner = str(item.get("owner_module", "")).strip()
        type_name = str(item.get("type_name", "")).strip()
        if owner and type_name:
            result[owner] = type_name
    return result


def _canonical_owner_file_by_module(canonical_types: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in canonical_types:
        if not isinstance(item, dict):
            continue
        owner = str(item.get("owner_module", "")).strip()
        owner_file = str(item.get("owner_file", "")).strip()
        if owner and owner_file:
            result[owner] = owner_file
    return result


def _known_functions_by_module(
    module_graph: list[dict[str, Any]],
    handler_matrix: list[dict[str, Any]],
    protocol_slug: str,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def add(module_name: str, suffix: str, *, kind: str, unit: str, visibility: str = "public") -> None:
        function_name = _public_symbol(protocol_slug, module_name, suffix)
        if any(item.get("name") == function_name for item in result[module_name]):
            return
        result[module_name].append({"name": function_name, "visibility": visibility, "kind": kind, "unit": unit})

    for module in module_graph:
        if not isinstance(module, dict):
            continue
        name = str(module.get("name", ""))
        if not name:
            continue
        result[name].append({"name": _public_symbol(protocol_slug, name, "create"), "visibility": "public", "kind": "lifecycle", "unit": "api"})
        result[name].append({"name": _public_symbol(protocol_slug, name, "destroy"), "visibility": "public", "kind": "lifecycle", "unit": "api"})
        owned = {str(item) for item in module.get("owned_capabilities", []) if str(item).strip()}
        if "role_composition" in owned:
            result[name].append({"name": "main", "visibility": "private", "kind": "entrypoint", "unit": "main"})
        if owned.intersection({"transport_io", "connection_lifecycle"}):
            add(name, "start", kind="runtime", unit="runtime")
            add(name, "run", kind="runtime", unit="runtime")
            add(name, "stop", kind="runtime", unit="runtime")
            add(name, "accept_connection", kind="runtime", unit="runtime")
            add(name, "close_connection", kind="runtime", unit="runtime")
        if owned.intersection({"connection_buffering", "incremental_message_framing", "datagram_message_framing"}):
            add(name, "feed", kind="codec", unit="decode")
        if owned.intersection({"message_decode", "incremental_message_framing", "datagram_message_framing"}):
            add(name, "decode_packet", kind="codec", unit="decode")
            add(name, "decode_next", kind="codec", unit="decode")
        if owned.intersection({"message_encode", "error_response_encoding"}):
            add(name, "encode_packet", kind="codec", unit="encode")
            add(name, "encode_response", kind="codec", unit="encode")
        if "semantic_dispatch" in owned:
            add(name, "dispatch_packet", kind="semantic", unit="handlers")
        if owned.intersection({"state_machine", "state_transition_validation"}):
            add(name, "validate_transition", kind="state", unit="state")
            add(name, "apply_transition", kind="state", unit="state")
        if "protocol_error_policy" in owned:
            add(name, "handle_error", kind="errors", unit="errors")
        if "session_state_ownership" in owned:
            add(name, "add_session", kind="session", unit="state")
            add(name, "get_session", kind="session", unit="state")
            add(name, "remove_session", kind="session", unit="state")
            add(name, "mark_session_connected", kind="session", unit="state")
        if "resource_ownership" in owned:
            add(name, "store_resource", kind="resource", unit="store")
            add(name, "lookup_resource", kind="resource", unit="store")
            add(name, "delete_resource", kind="resource", unit="store")
        if "routing_dispatch" in owned:
            add(name, "subscribe", kind="routing", unit="routing")
            add(name, "unsubscribe", kind="routing", unit="routing")
            add(name, "publish", kind="routing", unit="routing")
            add(name, "remove_session_routes", kind="routing", unit="routing")
    for row in handler_matrix:
        if not isinstance(row, dict):
            continue
        module = str(row.get("handler_module", "")).strip()
        function = str(row.get("handler_function", "")).strip()
        if module and function:
            if not any(item.get("name") == function for item in result[module]):
                result[module].append({"name": function, "visibility": "public", "kind": "handler", "unit": "handlers"})
    return {module: items for module, items in result.items()}


def _new_file(
    *,
    file_id: str,
    module: str,
    source_path: str,
    role: str,
    file_kind: str,
    visibility: str,
    owns_header: str = "",
    defines_functions: list[str] | None = None,
    declares_symbols: list[str] | None = None,
    uses_types: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    decision_refs: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "file_id": file_id,
        "module": module,
        "path": source_path,
        "source_path": source_path,
        "file_kind": file_kind,
        "visibility": visibility,
        "role": role,
        "owns_header": owns_header,
        "header_path": owns_header,
        "defines_functions": sorted({item for item in defines_functions or [] if item}),
        "declares_symbols": sorted({item for item in declares_symbols or [] if item}),
        "uses_types": sorted({item for item in uses_types or [] if item}),
        "evidence_refs": sorted({item for item in evidence_refs or [] if item}),
        "decision_refs": sorted({item for item in decision_refs or [] if item}),
    }


def _split_units_for_module(module: dict[str, Any], handler_functions: list[str]) -> list[str]:
    owned = {str(item) for item in module.get("owned_capabilities", []) if str(item).strip()}
    role = str(module.get("role", "")).lower()
    units: list[str] = []
    if handler_functions and (len(handler_functions) > 1 or owned.intersection({"semantic_dispatch", "role_composition", "protocol_error_policy"})):
        units.append("handlers")
    if owned.intersection({"message_decode", "incremental_message_framing", "datagram_message_framing"}):
        units.append("decode")
    if "message_encode" in owned:
        units.append("encode")
    if "routing_dispatch" in owned or "router" in role:
        units.append("routing")
    if owned.intersection({"state_machine", "state_transition_validation", "session_state_ownership", "recovery_cleanup_policy"}):
        units.append("state")
    if "resource_ownership" in owned or "store" in role:
        units.append("store")
    if owned.intersection({"transport_io", "connection_lifecycle", "connection_buffering", "timer_source", "timeout_handling"}):
        units.append("runtime")
    if owned.intersection({"protocol_error_policy", "connection_termination", "error_response_encoding"}):
        units.append("errors")
    return _dedupe(units)[:4]


def _deterministic_layout(
    module_graph: list[dict[str, Any]],
    canonical_types: list[dict[str, Any]],
    handler_matrix: list[dict[str, Any]],
    dependency_graph: dict[str, Any],
    protocol_slug: str,
    decision_refs: list[str],
) -> dict[str, Any]:
    del dependency_graph
    type_by_module = _canonical_type_by_module(canonical_types, protocol_slug)
    functions_by_module = _known_functions_by_module(module_graph, handler_matrix, protocol_slug)
    files: list[dict[str, Any]] = []
    placement: list[dict[str, Any]] = []
    for module in module_graph:
        if not isinstance(module, dict):
            continue
        name = str(module.get("name", "")).strip()
        if not name:
            continue
        base = _module_base(protocol_slug, module)
        module_dir = _dirname(base)
        source_path = f"{base}.c"
        header_path = f"{base}.h"
        module_functions = functions_by_module.get(name, [])
        lifecycle = [item["name"] for item in module_functions if item.get("kind") == "lifecycle"]
        handlers = [item["name"] for item in module_functions if item.get("kind") == "handler"]
        public_symbols = [
            type_by_module.get(name, f"{protocol_slug}_{name}_t"),
            *[item["name"] for item in module_functions if item.get("visibility") == "public"],
        ]
        api_id = f"{name}:api"
        files.append(
            _new_file(
                file_id=api_id,
                module=name,
                source_path=source_path,
                role=f"Public API and lifecycle entrypoints for {name}",
                file_kind="api_source",
                visibility="public",
                owns_header=header_path,
                defines_functions=lifecycle,
                declares_symbols=public_symbols,
                evidence_refs=_strings(module.get("evidence_refs")),
                decision_refs=decision_refs,
            )
        )
        handler_file_id = api_id
        function_units = [str(item.get("unit", "")) for item in module_functions if item.get("unit") and item.get("unit") != "api"]
        units = _dedupe([*_split_units_for_module(module, handlers), *function_units])
        for unit in units:
            unit_id = f"{name}:{unit}"
            unit_path = f"{module_dir}/main.c" if unit == "main" else f"{module_dir}/{name}_{unit}.c"
            defines = [item["name"] for item in module_functions if item.get("unit") == unit]
            files.append(
                _new_file(
                    file_id=unit_id,
                    module=name,
                    source_path=unit_path,
                    role=f"{unit.replace('_', ' ')} implementation responsibilities for {name}",
                    file_kind=f"{unit}_source",
                    visibility="private",
                    owns_header="" if unit == "main" else _default_header_for_source(unit_path),
                    defines_functions=defines,
                    declares_symbols=[],
                    evidence_refs=_strings(module.get("evidence_refs")),
                    decision_refs=decision_refs,
                )
            )
            if unit == "handlers":
                handler_file_id = unit_id
        for function in lifecycle:
            placement.append(
                {
                    "function_name": function,
                    "module": name,
                    "file_id": api_id,
                    "visibility": "public",
                    "placement_reason": "Lifecycle functions stay with the module public API source.",
                }
            )
        for function in handlers:
            placement.append(
                {
                    "function_name": function,
                    "module": name,
                    "file_id": handler_file_id,
                    "visibility": "public",
                    "placement_reason": "Protocol handlers are placed in the module handler source when one exists.",
                }
            )
        for function in [item for item in module_functions if item.get("kind") not in {"lifecycle", "handler"}]:
            unit = str(function.get("unit") or "api")
            placement.append(
                {
                    "function_name": function["name"],
                    "module": name,
                    "file_id": f"{name}:{unit}" if unit != "api" else api_id,
                    "visibility": str(function.get("visibility", "public")),
                    "placement_reason": "Capability functions are placed with the source unit owning their implementation concern.",
                }
            )
    return {
        "schema_version": FILE_LAYOUT_SCHEMA,
        "origin": "deterministic",
        "files": files,
        "function_placement": placement,
        "file_edges": [],
        "unresolved_layout_questions": [],
    }


def _normalize_source_path(raw_path: Any, module: dict[str, Any], protocol_slug: str) -> str:
    path = str(raw_path or "").strip()
    if not path:
        return f"{_module_base(protocol_slug, module)}.c"
    if path.endswith(".h"):
        return f"{path[:-2]}.c"
    if not path.endswith(".c"):
        return f"{path}.c"
    return path


def _is_main_source(source_path: str) -> bool:
    return source_path == "main.c" or source_path.endswith("/main.c")


def _default_header_for_source(source_path: str) -> str:
    return f"{source_path[:-2]}.h" if source_path.endswith(".c") else ""


def _normalize_header_path(raw_header: Any, source_path: str) -> str:
    if isinstance(raw_header, bool):
        return _default_header_for_source(source_path) if raw_header else ""
    text = str(raw_header or "").strip()
    if not text:
        return ""
    if text.endswith(".c"):
        return f"{text[:-2]}.h"
    if not text.endswith(".h"):
        return f"{text}.h"
    return text


def _normalize_owned_header(raw_file: dict[str, Any], source_path: str) -> tuple[str, str]:
    owns_header = raw_file.get("owns_header")
    header_path = raw_file.get("header_path")
    header_text = str(header_path or "").strip()
    if isinstance(owns_header, bool):
        if not owns_header:
            if header_text:
                return "", "owns_header=false conflicts with non-empty header_path"
            return "", ""
        if header_text:
            return _normalize_header_path(header_text, source_path), ""
        return _normalize_header_path(True, source_path), ""
    raw_header = owns_header if owns_header is not None else header_path
    if not raw_header:
        return "", ""
    raw_header_text = str(raw_header).strip()
    if not raw_header_text.endswith(".h"):
        return "", f"owns_header/header_path must be an actual .h path, not '{raw_header_text}'"
    return _normalize_header_path(raw_header_text, source_path), ""


def _raw_function_placements(raw_layout: dict[str, Any]) -> list[dict[str, Any]] | None:
    raw = raw_layout.get("function_placement", [])
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        placements: list[dict[str, Any]] = []
        for function_name, value in raw.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("function_name", function_name)
                placements.append(item)
            else:
                placements.append({"function_name": str(function_name), "file_id": str(value)})
        return placements
    return None


def _normalize_raw_layout(
    raw_layout: dict[str, Any],
    module_graph: list[dict[str, Any]],
    canonical_types: list[dict[str, Any]],
    handler_matrix: list[dict[str, Any]],
    protocol_slug: str,
    decision_refs: list[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(raw_layout.get("files"), list):
        return None, ["file_layout.files must be an array of source .c file entries"]
    modules = {str(item.get("name")): item for item in module_graph if isinstance(item, dict) and item.get("name")}
    if not modules:
        return None, ["implementation plan has no modules available for file_layout validation"]
    canonical_owner_file_by_module = _canonical_owner_file_by_module(canonical_types)
    functions_by_module = _known_functions_by_module(module_graph, handler_matrix, protocol_slug)
    known_functions = {item["name"] for items in functions_by_module.values() for item in items}
    files: list[dict[str, Any]] = []
    file_ids: set[str] = set()
    owned_headers_by_module: dict[str, list[str]] = defaultdict(list)
    source_paths: set[str] = set()
    header_paths: set[str] = set()
    errors: list[str] = []
    for idx, raw_file in enumerate(raw_layout.get("files", [])):
        if not isinstance(raw_file, dict):
            errors.append(f"files[{idx}] must be an object")
            continue
        module_name = str(raw_file.get("module", "")).strip()
        if module_name not in modules:
            errors.append(f"files[{idx}] references unknown module '{module_name}'")
            continue
        file_id = str(raw_file.get("file_id", "")).strip()
        if not file_id or file_id in file_ids:
            errors.append(f"files[{idx}] has missing or duplicate file_id '{file_id}'")
            continue
        file_ids.add(file_id)
        raw_source_path = raw_file.get("path") or raw_file.get("source_path")
        if not raw_source_path:
            errors.append(f"file '{file_id}' must provide path/source_path ending in .c")
            continue
        raw_source_text = str(raw_source_path).strip()
        raw_kind = str(raw_file.get("file_kind", "")).strip().lower()
        if "header" in raw_kind or raw_source_text.endswith(".h"):
            errors.append(f"file '{file_id}' is a header-only entry; file_layout.files must contain source .c files only")
            continue
        if not raw_source_text.endswith(".c"):
            errors.append(f"file '{file_id}' path/source_path must end with .c, got '{raw_source_text}'")
            continue
        source_path = _normalize_source_path(raw_source_text, modules[module_name], protocol_slug)
        header_path, header_error = _normalize_owned_header(raw_file, source_path)
        if header_error:
            errors.append(f"file '{file_id}' {header_error}")
            continue
        is_main = _is_main_source(source_path)
        if is_main and header_path:
            errors.append(f"file '{file_id}' main.c must be source-only and must not own a header")
            continue
        if not header_path and not is_main:
            header_path = _default_header_for_source(source_path)
        if source_path in source_paths:
            errors.append(f"duplicate source path '{source_path}'")
            continue
        if header_path and header_path in header_paths:
            errors.append(f"duplicate owned header path '{header_path}'")
            continue
        source_paths.add(source_path)
        if header_path:
            header_paths.add(header_path)
            owned_headers_by_module[module_name].append(header_path)
        files.append(
            _new_file(
                file_id=file_id,
                module=module_name,
                source_path=source_path,
                role=str(raw_file.get("role", f"{module_name} source unit")),
                file_kind=str(raw_file.get("file_kind", "source")),
                visibility=str(raw_file.get("visibility", "private")),
                owns_header=header_path,
                defines_functions=_strings(raw_file.get("defines_functions")),
                declares_symbols=_strings(raw_file.get("declares_symbols")),
                uses_types=_strings(raw_file.get("uses_types")),
                evidence_refs=_strings(raw_file.get("evidence_refs")) or _strings(modules[module_name].get("evidence_refs")),
                decision_refs=_strings(raw_file.get("decision_refs")) or decision_refs,
            )
        )
    if errors:
        return None, errors[:20]
    missing_header_modules = [name for name in modules if not owned_headers_by_module.get(name)]
    if missing_header_modules:
        return None, [f"modules missing header owner source file: {', '.join(missing_header_modules)}"]
    canonical_errors = []
    for name in sorted(canonical_owner_file_by_module):
        headers = owned_headers_by_module.get(name, [])
        expected = canonical_owner_file_by_module[name]
        if headers.count(expected) != 1:
            canonical_errors.append(f"{name}: expected exactly one canonical header {expected}, got {headers}")
    if canonical_errors:
        return None, [f"module header ownership must include canonical type owner_file; {item}" for item in canonical_errors]
    placement: list[dict[str, Any]] = []
    placed: set[str] = set()
    raw_placements = _raw_function_placements(raw_layout)
    if raw_placements is None:
        return None, ["file_layout.function_placement must be an array or mapping"]
    for idx, raw_place in enumerate(raw_placements):
        if not isinstance(raw_place, dict):
            errors.append(f"function_placement[{idx}] must be an object")
            continue
        function = str(raw_place.get("function_name", "")).strip()
        file_id = str(raw_place.get("file_id", "")).strip()
        module = str(raw_place.get("module", "")).strip()
        if not module:
            module = next((mod for mod, items in functions_by_module.items() if any(item["name"] == function for item in items)), "")
        if function not in known_functions or file_id not in file_ids or module not in modules or function in placed:
            errors.append(f"invalid function placement function='{function}' module='{module}' file_id='{file_id}'")
            continue
        placed.add(function)
        placement.append(
            {
                "function_name": function,
                "module": module,
                "file_id": file_id,
                "visibility": str(raw_place.get("visibility", "public")),
                "placement_reason": str(raw_place.get("placement_reason", "LLM file layout placement")),
            }
        )
    if errors:
        return None, errors[:20]
    if placed != known_functions:
        missing = sorted(known_functions - placed)
        unknown = sorted(placed - known_functions)
        messages = []
        if missing:
            messages.append(f"function_placement missing {len(missing)} functions; sample={missing[:10]}")
        if unknown:
            messages.append(f"function_placement has unknown functions: {unknown[:10]}")
        return None, messages
    return {
        "schema_version": FILE_LAYOUT_SCHEMA,
        "origin": "llm",
        "files": files,
        "function_placement": placement,
        "file_edges": [],
        "unresolved_layout_questions": _strings(raw_layout.get("unresolved_layout_questions")),
    }, []


def _graph_edge_id(edge: dict[str, Any], fallback_prefix: str, idx: int) -> str:
    return str(edge.get("edge_id") or edge.get("id") or f"{fallback_prefix}:{idx}")


def _add_file_edge(
    edges: list[dict[str, Any]],
    *,
    consumer_file: str,
    provider_file: str,
    dependency_kind: str,
    include_scope: str,
    required_symbols: list[str],
    source_graph_edges: list[str],
    reason: str,
) -> None:
    if not consumer_file or not provider_file or consumer_file == provider_file:
        return
    key = (consumer_file, provider_file, dependency_kind, include_scope)
    for edge in edges:
        if (
            edge.get("consumer_file"),
            edge.get("provider_file"),
            edge.get("dependency_kind"),
            edge.get("include_scope"),
        ) == key:
            edge["required_symbols"] = sorted({*edge.get("required_symbols", []), *required_symbols})
            edge["source_graph_edges"] = sorted({*edge.get("source_graph_edges", []), *source_graph_edges})
            reasons = [str(edge.get("reason", "")).strip(), reason.strip()]
            edge["reason"] = "; ".join(dict.fromkeys(item for item in reasons if item))
            return
    edges.append(
        {
            "edge_id": f"file_edge:{_safe_slug(consumer_file)}:{_safe_slug(provider_file)}:{_safe_slug(dependency_kind)}:{_safe_slug(include_scope)}",
            "consumer_file": consumer_file,
            "provider_file": provider_file,
            "dependency_kind": dependency_kind,
            "include_scope": include_scope,
            "required_symbols": sorted({item for item in required_symbols if item}),
            "source_graph_edges": sorted({item for item in source_graph_edges if item}),
            "reason": reason,
        }
    )


def _canonical_file_by_module(files: list[dict[str, Any]], canonical_types: list[dict[str, Any]]) -> dict[str, str]:
    canonical_header_by_module = _canonical_owner_file_by_module(canonical_types)
    result: dict[str, str] = {}
    fallback: dict[str, str] = {}
    for item in files:
        module = str(item.get("module", ""))
        file_id = str(item.get("file_id", ""))
        header = str(item.get("owns_header") or item.get("header_path") or "").strip()
        if not module or not file_id or not header:
            continue
        fallback.setdefault(module, file_id)
        if canonical_header_by_module.get(module) == header:
            result[module] = file_id
    for module, file_id in fallback.items():
        result.setdefault(module, file_id)
    return result


def _derive_file_edges(
    layout: dict[str, Any],
    dependency_graph: dict[str, Any],
    canonical_types: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    files = [item for item in layout.get("files", []) if isinstance(item, dict)]
    public_file_by_module = _canonical_file_by_module(files, canonical_types or [])
    source_file_by_function = {
        str(item.get("function_name")): str(item.get("file_id"))
        for item in layout.get("function_placement", [])
        if isinstance(item, dict) and item.get("function_name") and item.get("file_id")
    }
    files_by_id = {str(item.get("file_id")): item for item in files if item.get("file_id")}
    edges: list[dict[str, Any]] = []
    for idx, edge in enumerate(dependency_graph.get("module_edges", [])):
        if not isinstance(edge, dict):
            continue
        consumer = str(edge.get("consumer_module", ""))
        provider = str(edge.get("provider_module", ""))
        include_scope = "header" if "compile_time" in str(edge.get("dependency_kind", "")).lower() else "source"
        _add_file_edge(
            edges,
            consumer_file=public_file_by_module.get(consumer, ""),
            provider_file=public_file_by_module.get(provider, ""),
            dependency_kind=f"module_{edge.get('dependency_kind', 'dependency')}",
            include_scope=include_scope,
            required_symbols=[],
            source_graph_edges=[_graph_edge_id(edge, "module_edge", idx)],
            reason=str(edge.get("reason", "")),
        )
    for idx, edge in enumerate(dependency_graph.get("function_edges", [])):
        if not isinstance(edge, dict):
            continue
        callee_module = str(edge.get("callee_module", ""))
        _add_file_edge(
            edges,
            consumer_file=source_file_by_function.get(str(edge.get("caller", "")), ""),
            provider_file=source_file_by_function.get(str(edge.get("callee", ""))) or public_file_by_module.get(callee_module, ""),
            dependency_kind=f"function_{edge.get('dependency_kind', 'call')}",
            include_scope="source",
            required_symbols=[str(edge.get("callee", ""))],
            source_graph_edges=[_graph_edge_id(edge, "function_edge", idx)],
            reason=str(edge.get("reason", "")),
        )
    for idx, edge in enumerate(dependency_graph.get("data_edges", [])):
        if not isinstance(edge, dict):
            continue
        provider_module = str(edge.get("provider_module", ""))
        _add_file_edge(
            edges,
            consumer_file=source_file_by_function.get(str(edge.get("function", "")), ""),
            provider_file=public_file_by_module.get(provider_module, ""),
            dependency_kind=f"data_{edge.get('data_kind', 'access')}",
            include_scope="source",
            required_symbols=[str(edge.get("struct", ""))],
            source_graph_edges=[_graph_edge_id(edge, "data_edge", idx)],
            reason=str(edge.get("reason", "")),
        )
    for edge in edges:
        consumer = files_by_id.get(str(edge.get("consumer_file", "")))
        provider = files_by_id.get(str(edge.get("provider_file", "")))
        if consumer is not None:
            consumer["uses_types"] = sorted({*consumer.get("uses_types", []), *edge.get("required_symbols", [])})
        if provider is not None:
            provider["declares_symbols"] = sorted({*provider.get("declares_symbols", []), *edge.get("required_symbols", [])})
    return sorted(edges, key=lambda item: (str(item.get("consumer_file", "")), str(item.get("provider_file", "")), str(item.get("dependency_kind", ""))))


def _validate_layout(
    layout: dict[str, Any],
    module_graph: list[dict[str, Any]],
    canonical_types: list[dict[str, Any]] | None = None,
    handler_matrix: list[dict[str, Any]] | None = None,
    dependency_graph: dict[str, Any] | None = None,
    protocol_slug: str = "protocol",
) -> list[str]:
    errors: list[str] = []
    canonical_types = canonical_types or []
    handler_matrix = handler_matrix or []
    dependency_graph = dependency_graph or {}
    module_names = {str(item.get("name")) for item in module_graph if isinstance(item, dict) and item.get("name")}
    known_functions = {
        item["name"]
        for items in _known_functions_by_module(module_graph, handler_matrix, protocol_slug).values()
        for item in items
    }
    known_graph_ids = _edge_ids(dependency_graph)
    files = [item for item in layout.get("files", []) if isinstance(item, dict)]
    file_ids = {str(item.get("file_id")) for item in files if item.get("file_id")}
    source_paths = [str(item.get("source_path") or item.get("path")) for item in files if item.get("source_path") or item.get("path")]
    header_paths = [str(item.get("owns_header") or item.get("header_path")) for item in files if item.get("owns_header") or item.get("header_path")]
    headers_by_module: dict[str, list[str]] = defaultdict(list)
    for item in files:
        module = str(item.get("module", ""))
        header = str(item.get("owns_header") or item.get("header_path") or "").strip()
        if module and header:
            headers_by_module[module].append(header)
    canonical_owner_file_by_module = _canonical_owner_file_by_module(canonical_types)
    if len(file_ids) != len([item for item in files if item.get("file_id")]):
        errors.append("files contain duplicate file_id values")
    if len(source_paths) != len(set(source_paths)):
        errors.append("files contain duplicate source paths")
    if len(header_paths) != len(set(header_paths)):
        errors.append("files contain duplicate owned headers")
    for module_name in module_names:
        module_files = [item for item in files if str(item.get("module")) == module_name]
        if not module_files:
            errors.append(f"module '{module_name}' has no file_layout.files entry")
        if not headers_by_module.get(module_name):
            errors.append(f"module '{module_name}' has no header owner")
        expected_header = canonical_owner_file_by_module.get(module_name)
        if expected_header and headers_by_module.get(module_name, []).count(expected_header) != 1:
            errors.append(f"module '{module_name}' must own exactly one canonical header '{expected_header}'")
    for item in files:
        module_name = str(item.get("module", ""))
        source_path = str(item.get("source_path") or item.get("path") or "")
        header_path = str(item.get("owns_header") or item.get("header_path") or "").strip()
        if module_name not in module_names:
            errors.append(f"file '{item.get('file_id')}' references unknown module '{module_name}'")
        if _is_main_source(source_path):
            if header_path:
                errors.append(f"main source file '{item.get('file_id')}' must not own a header")
        elif not header_path:
            errors.append(f"non-main source file '{item.get('file_id')}' must own a header")
        defines = {str(name) for name in item.get("defines_functions", []) if str(name).strip()}
        unknown_defines = sorted(defines - known_functions)
        if unknown_defines:
            errors.append(f"file '{item.get('file_id')}' defines unknown functions: {', '.join(unknown_defines)}")
        if not header_path and not defines:
            errors.append(f"source-only file '{item.get('file_id')}' does not define any functions")
    for edge in layout.get("file_edges", []):
        if not isinstance(edge, dict):
            errors.append("file_edges contains a non-object item")
            continue
        for key in ("consumer_file", "provider_file"):
            if str(edge.get(key, "")) not in file_ids:
                errors.append(f"file edge references unknown {key} '{edge.get(key)}'")
        include_scope = str(edge.get("include_scope", ""))
        if include_scope not in {"header", "source"}:
            errors.append(f"file edge '{edge.get('edge_id')}' has invalid include_scope '{include_scope}'")
        for ref in edge.get("source_graph_edges", []):
            if str(ref) not in known_graph_ids:
                errors.append(f"file edge '{edge.get('edge_id')}' references unknown source graph edge '{ref}'")
    placements = [item for item in layout.get("function_placement", []) if isinstance(item, dict)]
    placed = [str(item.get("function_name", "")) for item in placements if item.get("function_name")]
    if len(placed) != len(set(placed)):
        errors.append("function_placement contains duplicate functions")
    missing = sorted(known_functions - set(placed))
    if missing:
        errors.append(f"function_placement missing functions: {', '.join(missing)}")
    unknown = sorted(set(placed) - known_functions)
    if unknown:
        errors.append(f"function_placement references unknown functions: {', '.join(unknown)}")
    for placement in placements:
        file_id = str(placement.get("file_id", ""))
        function_name = str(placement.get("function_name", ""))
        if file_id not in file_ids:
            errors.append(f"function placement for '{function_name}' references unknown file_id '{file_id}'")
            continue
        file_item = next((item for item in files if str(item.get("file_id")) == file_id), {})
        if function_name not in {str(name) for name in file_item.get("defines_functions", [])}:
            errors.append(f"function placement for '{function_name}' points to file '{file_id}' but that file does not define it")
    projected_graph_ids = {
        str(ref)
        for edge in layout.get("file_edges", [])
        if isinstance(edge, dict)
        for ref in edge.get("source_graph_edges", [])
        if str(ref).strip()
    }
    missing_graph_ids = sorted(known_graph_ids - projected_graph_ids)
    if missing_graph_ids:
        errors.append(f"file_edges do not project dependency graph edges: {', '.join(missing_graph_ids)}")
    return errors


def _compact_layout_context(
    planning_ir: PlanningIR,
    profile: dict[str, Any],
    chosen_architecture: dict[str, Any],
    module_graph: list[dict[str, Any]],
    canonical_types: list[dict[str, Any]],
    handler_matrix: list[dict[str, Any]],
    dependency_graph: dict[str, Any],
    protocol_slug: str,
) -> dict[str, Any]:
    functions_by_module = _known_functions_by_module(module_graph, handler_matrix, protocol_slug)
    compact_edges: dict[str, list[dict[str, Any]]] = {}
    for section in ("module_edges", "function_edges", "data_edges"):
        compact_items: list[dict[str, Any]] = []
        for idx, edge in enumerate(dependency_graph.get(section, [])):
            if not isinstance(edge, dict):
                continue
            item: dict[str, Any] = {
                "edge_id": _graph_edge_id(edge, section[:-1] if section.endswith("s") else section, idx),
            }
            for key in (
                "consumer_module",
                "provider_module",
                "dependency_kind",
                "caller",
                "callee",
                "caller_module",
                "callee_module",
                "function",
                "module",
                "data_kind",
                "struct",
            ):
                if edge.get(key):
                    item[key] = edge[key]
            capabilities = edge.get("required_capabilities", [])
            if isinstance(capabilities, list) and capabilities:
                item["required_capabilities"] = [str(cap) for cap in capabilities]
            compact_items.append(item)
        compact_edges[section] = compact_items
    return {
        "protocol_name": planning_ir.protocol_name,
        "target_role": profile.get("target_role"),
        "minimum_v1_surface": [
            item.get("name")
            for item in planning_ir.minimum_v1.get("must_support_surface", [])
            if isinstance(item, dict) and item.get("name")
        ],
        "modules": [
            {
                "name": str(item.get("name", "")),
                "role": _short(item.get("role", "")),
                "path": str(item.get("path", "")),
                "dependencies": [str(dep) for dep in item.get("dependencies", [])],
                "owned_capabilities": [str(cap) for cap in item.get("owned_capabilities", [])],
                "evidence_refs": [str(ref) for ref in item.get("evidence_refs", [])],
            }
            for item in module_graph
            if isinstance(item, dict)
        ],
        "canonical_types": [
            {
                "type_name": str(item.get("type_name", "")),
                "owner_module": str(item.get("owner_module", "")),
                "owner_file": str(item.get("owner_file", "")),
            }
            for item in canonical_types
            if isinstance(item, dict)
        ],
        "functions_by_module": functions_by_module,
        "handler_matrix": [
            {
                "surface_unit": str(item.get("surface_unit", "")),
                "handler_module": str(item.get("handler_module", "")),
                "handler_function": str(item.get("handler_function", "")),
            }
            for item in handler_matrix
            if isinstance(item, dict)
        ],
        "dependency_graph": compact_edges,
        "architecture_relationship_hints": [
            _short(item, 140)
            for item in chosen_architecture.get("component_relationships", [])
            if str(item).strip()
        ],
        "output_contract": {
            "origin": "llm",
            "schema_version": FILE_LAYOUT_SCHEMA,
            "function_placement_shape": "array of objects with function_name, module, file_id, visibility, placement_reason",
        },
    }


def build_file_layout(
    planning_ir: PlanningIR,
    profile: dict[str, Any],
    chosen_architecture: dict[str, Any],
    implementation_plan: dict[str, Any],
    llm_client: FixedQwenClient | None = None,
    log: Any = None,
    log_artifact: Any = None,
    register_usage: Any = None,
) -> dict[str, Any]:
    protocol_slug = _safe_slug(planning_ir.protocol_name)
    module_graph = [item for item in implementation_plan.get("module_graph", []) if isinstance(item, dict)]
    handler_matrix = [item for item in implementation_plan.get("handler_matrix", []) if isinstance(item, dict)]
    canonical_types = [item for item in implementation_plan.get("canonical_types", []) if isinstance(item, dict)]
    dependency_graph = implementation_plan.get("dependency_graph", {})
    dependency_graph = dependency_graph if isinstance(dependency_graph, dict) else {}
    traceability = implementation_plan.get("traceability", {})
    decision_refs = _strings(traceability.get("decision_ids", [])) if isinstance(traceability, dict) else []
    if llm_client is None:
        layout = _deterministic_layout(module_graph, canonical_types, handler_matrix, dependency_graph, protocol_slug, decision_refs)
        # Unit-test helper path only. Real PlanningAgent validation requires an LLM.
        layout["file_edges"] = _derive_file_edges(layout, dependency_graph, canonical_types)
        validation_errors = _validate_layout(layout, module_graph, canonical_types, handler_matrix, dependency_graph, protocol_slug)
        layout["schema_version"] = FILE_LAYOUT_SCHEMA
        layout["validation"] = {
            "file_count": len([item for item in layout.get("files", []) if isinstance(item, dict)]),
            "file_edge_count": len([item for item in layout.get("file_edges", []) if isinstance(item, dict)]),
            "origin": "deterministic_test_helper",
            "accepted": not validation_errors,
            "attempt_count": 0,
            "token_budget": FILE_LAYOUT_CALL_TOKEN_BUDGET,
            "per_call_token_budget": FILE_LAYOUT_CALL_TOKEN_BUDGET,
            "tokens_used": 0,
            "errors": validation_errors,
        }
        return layout

    layout_context = _compact_layout_context(
        planning_ir,
        profile,
        chosen_architecture,
        module_graph,
        canonical_types,
        handler_matrix,
        dependency_graph,
        protocol_slug,
    )
    attempts: list[dict[str, Any]] = []
    tokens_used = 0
    validation_errors: list[str] = []
    for attempt_idx in range(1, FILE_LAYOUT_MAX_ATTEMPTS + 1):
        messages = build_file_layout_prompt(layout_context, _summarize_validation_errors(validation_errors) if attempt_idx > 1 else None)
        prompt_estimate = _estimate_message_tokens(messages)
        completion_budget = _completion_budget_for_prompt(prompt_estimate)
        estimated_call_tokens = prompt_estimate + completion_budget
        if completion_budget < FILE_LAYOUT_MIN_COMPLETION_BUDGET or estimated_call_tokens > FILE_LAYOUT_CALL_TOKEN_BUDGET:
            attempts.append(
                {
                    "attempt": attempt_idx,
                    "accepted": False,
                    "error": "call_token_budget_exceeded_before_request",
                    "prompt_token_estimate": prompt_estimate,
                    "completion_budget": completion_budget,
                    "estimated_call_tokens": estimated_call_tokens,
                }
            )
            raise FileLayoutGenerationError(
                "file_layout_token_budget_exceeded",
                "file_layout LLM call would exceed the 30000 token budget",
                attempts,
            )
        if log_artifact is not None:
            log_artifact(f"file_layout_prompt_attempt_{attempt_idx}", json.dumps(messages, ensure_ascii=False, indent=2), ".json")
        if log is not None:
            log(f"file_layout llm_request_start attempt={attempt_idx} prompt_estimate={prompt_estimate} call_budget={FILE_LAYOUT_CALL_TOKEN_BUDGET}")
        started_at = time.perf_counter()
        try:
            response = _generate_with_usage(
                llm_client,
                LLMRequest(
                    messages=messages,
                    top_p=0.3,
                    temperature=0.1,
                    is_stream=True,
                    max_completion_tokens=completion_budget,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            attempts.append(
                {
                    "attempt": attempt_idx,
                    "accepted": False,
                    "error": f"llm_request_failed:{type(exc).__name__}",
                    "prompt_token_estimate": prompt_estimate,
                    "completion_budget": completion_budget,
                    "duration_ms": duration_ms,
                }
            )
            validation_errors = [f"LLM request failed: {type(exc).__name__}: {exc}"]
            continue
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        if response is None:
            attempts.append(
                {
                    "attempt": attempt_idx,
                    "accepted": False,
                    "error": "empty_llm_response",
                    "prompt_token_estimate": prompt_estimate,
                    "completion_budget": completion_budget,
                    "duration_ms": duration_ms,
                }
            )
            validation_errors = ["LLM response was empty"]
            continue
        if register_usage is not None:
            register_usage("file_layout", f"generate_attempt_{attempt_idx}", response.usage)
        call_tokens = response.usage.total_tokens or (prompt_estimate + max(1, len(response.content) // 3))
        tokens_used += call_tokens
        if log_artifact is not None:
            log_artifact(f"file_layout_response_attempt_{attempt_idx}", response.content, ".txt")
        payload, payload_error = _load_json_payload_with_error(response.content)
        if log_artifact is not None and isinstance(payload, dict):
            log_artifact(f"file_layout_response_normalized_attempt_{attempt_idx}", json.dumps(payload, ensure_ascii=False, indent=2), ".json")
        layout: dict[str, Any] | None = None
        if payload_error:
            validation_errors = [payload_error]
        else:
            raw_layout = _extract_layout_payload(payload)
            layout, normalization_errors = _normalize_raw_layout(
                raw_layout,
                module_graph,
                canonical_types,
                handler_matrix,
                protocol_slug,
                decision_refs,
            )
            if layout is None:
                validation_errors = normalization_errors
            else:
                layout["file_edges"] = _derive_file_edges(layout, dependency_graph, canonical_types)
                validation_errors = _validate_layout(layout, module_graph, canonical_types, handler_matrix, dependency_graph, protocol_slug)
        attempts.append(
            {
                "attempt": attempt_idx,
                "accepted": not validation_errors,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "prompt_token_estimate": prompt_estimate,
                "completion_budget": completion_budget,
                "estimated_call_tokens": estimated_call_tokens,
                "duration_ms": duration_ms,
                "errors": _summarize_validation_errors(validation_errors),
            }
        )
        if log is not None:
            log(
                f"file_layout llm_response_done attempt={attempt_idx} "
                f"accepted={'yes' if not validation_errors else 'no'} errors={len(validation_errors)} "
                f"duration_ms={duration_ms}"
            )
        if call_tokens > FILE_LAYOUT_CALL_TOKEN_BUDGET:
            raise FileLayoutGenerationError(
                "file_layout_token_budget_exceeded",
                "file_layout LLM call exceeded the 30000 token budget",
                attempts,
            )
        if not validation_errors and layout is not None:
            layout["schema_version"] = FILE_LAYOUT_SCHEMA
            layout["origin"] = "llm"
            layout["validation"] = {
                "file_count": len([item for item in layout.get("files", []) if isinstance(item, dict)]),
                "file_edge_count": len([item for item in layout.get("file_edges", []) if isinstance(item, dict)]),
                "origin": "llm",
                "accepted": True,
                "attempt_count": attempt_idx,
                "token_budget": FILE_LAYOUT_CALL_TOKEN_BUDGET,
                "per_call_token_budget": FILE_LAYOUT_CALL_TOKEN_BUDGET,
                "tokens_used": tokens_used,
                "attempts": attempts,
                "errors": [],
            }
            return layout
    raise FileLayoutGenerationError(
        "file_layout_invalid_llm_output",
        "LLM did not produce a valid file_layout within the retry budget",
        attempts,
    )


def apply_file_layout_to_modules(
    module_graph: list[dict[str, Any]],
    file_layout: dict[str, Any],
    canonical_types: list[dict[str, Any]],
    handler_matrix: list[dict[str, Any]],
    protocol_name: str,
) -> list[dict[str, Any]]:
    protocol_slug = _safe_slug(protocol_name)
    type_by_module = _canonical_type_by_module(canonical_types, protocol_slug)
    functions_by_module = _known_functions_by_module(module_graph, handler_matrix, protocol_slug)
    files_by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in file_layout.get("files", []):
        if isinstance(item, dict) and item.get("module"):
            files_by_module[str(item["module"])].append(item)
    updated: list[dict[str, Any]] = []
    for module in module_graph:
        name = str(module.get("name", ""))
        item = dict(module)
        module_files = files_by_module.get(name, [])
        paths = []
        artifacts = []
        for file_item in module_files:
            source_path = str(file_item.get("source_path") or file_item.get("path") or "").strip()
            header_path = str(file_item.get("owns_header") or file_item.get("header_path") or "").strip()
            if header_path:
                paths.append(header_path)
                artifacts.append({"KIND": "PUBLIC_HEADER" if str(file_item.get("visibility")) == "public" else "INTERNAL_HEADER", "PATH": header_path})
            if source_path:
                paths.append(source_path)
                artifacts.append({"KIND": "SOURCE", "PATH": source_path, "FILE_ID": str(file_item.get("file_id", ""))})
        artifacts.append({"KIND": "PUBLIC_TYPE", "NAME": type_by_module.get(name, f"{protocol_slug}_{name}_t")})
        artifacts.extend({"KIND": "PUBLIC_FUNCTION", "NAME": function["name"]} for function in functions_by_module.get(name, []) if function.get("visibility") == "public")
        item["files"] = _dedupe(paths)
        item["artifacts"] = artifacts
        updated.append(item)
    return updated
