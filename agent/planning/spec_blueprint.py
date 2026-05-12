from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import PlanningIR, SpecBlueprint, TargetProfile


BLUEPRINT_SCHEMA = "spec_blueprint/v1alpha1"
GENERIC_C_PROFILE = "generic_c_from_plan"


def _safe_slug(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(text)).strip("_") or "x"


def _decision_ids(implementation_plan: dict[str, Any]) -> list[str]:
    traceability = implementation_plan.get("traceability", {})
    if not isinstance(traceability, dict):
        return []
    raw_refs = traceability.get("decision_ids", [])
    if not isinstance(raw_refs, list):
        return []
    return [str(item).strip() for item in raw_refs if str(item).strip()]


def _profile_ref(profile_id: str, name: str) -> str:
    return f"{profile_id}:{name}"


def _signature(return_type: str, name: str, params: list[dict[str, Any]]) -> str:
    raw_params = ", ".join(f"{item['TYPE']} {item['NAME']}".strip() for item in params) or "void"
    return f"{return_type} {name}({raw_params})"


def _function_interface(function: dict[str, Any]) -> dict[str, Any]:
    signature = dict(function.get("signature", {}))
    return {
        "TRACE_ID": str(function.get("trace_id", "")),
        "SIGNATURE": str(signature.get("RAW", "")),
        "NAME": str(function.get("name", "")),
        "KIND": "FUNC",
        "FUNCTION_TYPE": str(function.get("function_type", "ALGORITHM")),
        "ROLE": str(function.get("role", "")),
        "CONTRACT": _default_contract(),
        "VISIBILITY": str(function.get("visibility", "public")).lower(),
    }


def _header_interface(function: dict[str, Any]) -> dict[str, Any]:
    source = _function_interface(function)
    return {
        "SIGNATURE": source["SIGNATURE"],
        "NAME": source["NAME"],
        "KIND": source["KIND"],
        "FUNCTION_TYPE": str(function.get("function_type", "ALGORITHM")),
        "ROLE": source["ROLE"],
        "VISIBILITY": source["VISIBILITY"],
    }


def _default_contract() -> dict[str, Any]:
    return {
        "PRECONDITION": [{"TEXT": "Caller provides arguments matching the function signature."}],
        "POSTCONDITION": [{"TEXT": "Function preserves module invariants and reports success or failure through its return value or documented side effect."}],
        "IDEMPOTENT": False,
        "THREAD_SAFETY": "SINGLE_THREAD_ONLY",
    }


def _dependency_graph(implementation_plan: dict[str, Any]) -> dict[str, Any]:
    graph = implementation_plan.get("dependency_graph", {})
    return graph if isinstance(graph, dict) else {}


def _file_layout(implementation_plan: dict[str, Any]) -> dict[str, Any]:
    layout = implementation_plan.get("file_layout", {})
    return layout if isinstance(layout, dict) else {}


def _module_edges_for(dependency_graph: dict[str, Any], module_name: str) -> list[dict[str, Any]]:
    return [
        item
        for item in dependency_graph.get("module_edges", [])
        if isinstance(item, dict) and str(item.get("consumer_module", "")) == module_name
    ]


def _function_edges_for(dependency_graph: dict[str, Any], function_name: str) -> list[dict[str, Any]]:
    return [
        item
        for item in dependency_graph.get("function_edges", [])
        if isinstance(item, dict) and str(item.get("caller", "")) == function_name
    ]


def _data_edges_for(dependency_graph: dict[str, Any], function_name: str) -> list[dict[str, Any]]:
    return [
        item
        for item in dependency_graph.get("data_edges", [])
        if isinstance(item, dict) and str(item.get("function", "")) == function_name
    ]


def _edge_ref(edge: dict[str, Any], fallback: str) -> str:
    return str(edge.get("edge_id") or edge.get("contract_id") or edge.get("id") or fallback)


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _source_graph_refs(edge: dict[str, Any]) -> list[str]:
    refs = edge.get("source_graph_edges", [])
    if not isinstance(refs, list):
        return []
    return [str(item) for item in refs if str(item).strip()]


def _layout_files(file_layout: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in file_layout.get("files", []) if isinstance(item, dict)]


def _layout_file_edges(file_layout: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in file_layout.get("file_edges", []) if isinstance(item, dict)]


def _files_by_id(file_layout: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("file_id")): item for item in _layout_files(file_layout) if item.get("file_id")}


def _public_file_by_module(file_layout: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in _layout_files(file_layout):
        module = str(item.get("module", ""))
        if module and item.get("owns_header"):
            result.setdefault(module, item)
    return result


def _function_placement(file_layout: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in file_layout.get("function_placement", []):
        if isinstance(item, dict) and item.get("function_name") and item.get("file_id"):
            result[str(item["function_name"])] = str(item["file_id"])
    return result


def _trace_id_for_file(protocol_slug: str, file_item: dict[str, Any]) -> str:
    module = str(file_item.get("module", "module"))
    file_id = _safe_slug(str(file_item.get("file_id") or file_item.get("path") or "file"))
    return f"{protocol_slug}/{module}/{file_id}"


def _module_dependency_projection(dependency_graph: dict[str, Any], module_name: str) -> list[dict[str, Any]]:
    projection: list[dict[str, Any]] = []
    for idx, edge in enumerate(dependency_graph.get("module_edges", [])):
        if not isinstance(edge, dict):
            continue
        consumer = str(edge.get("consumer_module", ""))
        provider = str(edge.get("provider_module", ""))
        if module_name not in {consumer, provider}:
            continue
        projection.append(
            {
                "GRAPH_EDGE_ID": _edge_ref(edge, f"module_edge:{idx}"),
                "DIRECTION": "outgoing" if consumer == module_name else "incoming",
                "CONSUMER_MODULE": consumer,
                "PROVIDER_MODULE": provider,
                "DEPENDENCY_KIND": str(edge.get("dependency_kind", "")),
                "REQUIRED_CAPABILITIES": list(edge.get("required_capabilities", [])),
                "ROLE": str(edge.get("reason", "")),
            }
        )
    for idx, contract in enumerate(dependency_graph.get("interface_contracts", [])):
        if not isinstance(contract, dict):
            continue
        provider = str(contract.get("provider_module", ""))
        consumers = [str(item) for item in contract.get("consumer_modules", [])] if isinstance(contract.get("consumer_modules"), list) else []
        if module_name != provider and module_name not in consumers:
            continue
        projection.append(
            {
                "GRAPH_EDGE_ID": _edge_ref(contract, f"interface_contract:{idx}"),
                "DIRECTION": "provides" if module_name == provider else "consumes",
                "PROVIDER_MODULE": provider,
                "CONSUMER_MODULES": consumers,
                "DEPENDENCY_KIND": "interface_contract",
                "REQUIRED_CAPABILITIES": list(contract.get("provided_capabilities", [])),
                "ROLE": "Public interface contract derived from dependency_graph",
            }
        )
    return projection


def _file_edges_for(file_layout: dict[str, Any], file_id: str, *, direction: str = "outgoing") -> list[dict[str, Any]]:
    key = "consumer_file" if direction == "outgoing" else "provider_file"
    return [edge for edge in _layout_file_edges(file_layout) if str(edge.get(key, "")) == file_id]


def _header_for_provider_file(file_layout: dict[str, Any], provider_file_id: str) -> str:
    files_by_id = _files_by_id(file_layout)
    public_by_module = _public_file_by_module(file_layout)
    provider = files_by_id.get(provider_file_id)
    if not provider:
        return ""
    direct = str(provider.get("owns_header") or provider.get("header_path") or "").strip()
    if direct:
        return direct
    module_public = public_by_module.get(str(provider.get("module", "")))
    if module_public:
        return str(module_public.get("owns_header") or module_public.get("header_path") or "").strip()
    return ""


def _module_public_header(file_layout: dict[str, Any], module_name: str) -> str:
    file_item = _public_file_by_module(file_layout).get(module_name)
    if not file_item:
        return ""
    return str(file_item.get("owns_header") or file_item.get("header_path") or "").strip()


def _file_dependency_projection(file_layout: dict[str, Any], file_id: str) -> list[dict[str, Any]]:
    projection: list[dict[str, Any]] = []
    for edge in _file_edges_for(file_layout, file_id, direction="outgoing"):
        projection.append(
            {
                "FILE_EDGE_ID": _edge_ref(edge, f"file_edge:{len(projection)}"),
                "CONSUMER_FILE": str(edge.get("consumer_file", "")),
                "PROVIDER_FILE": str(edge.get("provider_file", "")),
                "DEPENDENCY_KIND": str(edge.get("dependency_kind", "")),
                "INCLUDE_SCOPE": str(edge.get("include_scope", "")),
                "REQUIRED_SYMBOLS": list(edge.get("required_symbols", [])),
                "GRAPH_EDGE_IDS": _source_graph_refs(edge),
                "ROLE": str(edge.get("reason", "")),
            }
        )
    return projection


def _owner_file_by_module(implementation_plan: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in implementation_plan.get("canonical_types", []):
        if not isinstance(item, dict):
            continue
        owner = str(item.get("owner_module", "")).strip()
        owner_file = str(item.get("owner_file", "")).strip()
        if owner and owner_file:
            result[owner] = owner_file
    return result


def _rely_for(dependency_graph: dict[str, Any], function_name: str) -> dict[str, list[dict[str, Any]]]:
    structs = []
    vars_ = []
    for edge in _data_edges_for(dependency_graph, function_name):
        struct_name = str(edge.get("struct", "")).strip()
        var_name = str(edge.get("var") or edge.get("variable") or edge.get("symbol") or "").strip()
        data_kind = str(edge.get("data_kind", ""))
        item = {
            "MODULE": str(edge.get("provider_module", "")),
            "ROLE": str(edge.get("reason", "")),
            "DATA_KIND": data_kind,
            "GRAPH_EDGE_ID": _edge_ref(edge, f"data_edge:{function_name}:{len(structs) + len(vars_)}"),
        }
        if struct_name and "var" not in data_kind.lower():
            structs.append({"NAME": struct_name, **item})
        elif var_name:
            vars_.append({"NAME": var_name, **item})
    funcs = []
    for edge in _function_edges_for(dependency_graph, function_name):
        callee = str(edge.get("callee", "")).strip()
        if not callee:
            continue
        funcs.append(
            {
                "NAME": callee,
                "MODULE": str(edge.get("callee_module", "")),
                "ROLE": str(edge.get("reason", "")),
                "DEPENDENCY_KIND": str(edge.get("dependency_kind", "")),
                "GRAPH_EDGE_ID": _edge_ref(edge, f"function_edge:{function_name}:{callee}"),
            }
        )
    return {
        "STRUCT": list({item["NAME"]: item for item in structs}.values()),
        "FUNC": list({item["NAME"]: item for item in funcs}.values()),
        "VAR": list({item["NAME"]: item for item in vars_}.values()),
    }


def _call_contracts_for(dependency_graph: dict[str, Any], function_name: str) -> list[dict[str, Any]]:
    contracts = []
    for edge in _function_edges_for(dependency_graph, function_name):
        contracts.append(
            {
                "NAME": str(edge.get("callee", "")),
                "CALLER": function_name,
                "PROVIDER_MODULE": str(edge.get("callee_module", "")),
                "DEPENDENCY_KIND": str(edge.get("dependency_kind", "")),
                "REQUIRED_CAPABILITIES": list(edge.get("required_capabilities", [])),
                "ROLE": str(edge.get("reason", "")),
                "GRAPH_EDGE_ID": _edge_ref(edge, f"function_edge:{function_name}:{edge.get('callee', '')}"),
                "SOURCE_MODULE_EDGE": str(edge.get("source_module_edge", "")),
            }
        )
    for edge in _data_edges_for(dependency_graph, function_name):
        contracts.append(
            {
                "NAME": str(edge.get("struct", "")),
                "CALLER": function_name,
                "PROVIDER_MODULE": str(edge.get("provider_module", "")),
                "DEPENDENCY_KIND": str(edge.get("data_kind", "")),
                "REQUIRED_CAPABILITIES": list(edge.get("required_capabilities", [])),
                "ROLE": str(edge.get("reason", "")),
                "GRAPH_EDGE_ID": _edge_ref(edge, f"data_edge:{function_name}:{edge.get('struct', '')}"),
                "SOURCE_MODULE_EDGE": str(edge.get("source_module_edge", "")),
            }
        )
    return [item for item in contracts if item.get("NAME")]


def _file_call_contracts_for(file_layout: dict[str, Any], file_id: str) -> list[dict[str, Any]]:
    contracts = []
    for edge in _file_edges_for(file_layout, file_id, direction="outgoing"):
        contracts.append(
            {
                "NAME": f"{edge.get('consumer_file')}->{edge.get('provider_file')}",
                "CONSUMER_FILE": str(edge.get("consumer_file", "")),
                "PROVIDER_FILE": str(edge.get("provider_file", "")),
                "DEPENDENCY_KIND": str(edge.get("dependency_kind", "")),
                "INCLUDE_SCOPE": str(edge.get("include_scope", "")),
                "REQUIRED_SYMBOLS": list(edge.get("required_symbols", [])),
                "GRAPH_EDGE_IDS": _source_graph_refs(edge),
                "FILE_EDGE_ID": _edge_ref(edge, "file_edge"),
                "ROLE": str(edge.get("reason", "")),
            }
        )
    return contracts


def _topological_generation_order(modules: list[dict[str, Any]], dependency_graph: dict[str, Any]) -> list[str]:
    graph_order = dependency_graph.get("generation_order", [])
    module_names = [str(item.get("name", "")) for item in modules if item.get("name")]
    if isinstance(graph_order, list):
        normalized = [str(item) for item in graph_order if str(item) in module_names]
        if len(normalized) == len(module_names):
            return normalized
    graph = {str(item.get("name")): [str(dep) for dep in item.get("dependencies", [])] for item in modules}
    order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visited or node in visiting:
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            visit(dep)
        visiting.remove(node)
        visited.add(node)
        order.append(node)

    for name in module_names:
        visit(name)
    return order


def _build_modules(
    implementation_plan: dict[str, Any],
    protocol_slug: str,
    decision_refs: list[str],
    profile_id: str,
) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for module in implementation_plan.get("module_graph", []):
        if not isinstance(module, dict):
            continue
        name = str(module.get("name", "module"))
        path = str(module.get("path", f"{protocol_slug}/{name}/{name}"))
        modules.append(
            {
                "name": name,
                "role": str(module.get("role", "")),
                "dependencies": [str(item) for item in module.get("dependencies", [])],
                "files": [str(item) for item in module.get("files", [f"{path}.h", f"{path}.c"])],
                "artifacts": list(module.get("artifacts", [])),
                "dependency_projection": _module_dependency_projection(_dependency_graph(implementation_plan), name),
                "owned_capabilities": [str(item) for item in module.get("owned_capabilities", [])],
                "evidence_refs": list(module.get("evidence_refs", [])),
                "decision_refs": decision_refs,
                "profile_refs": [_profile_ref(profile_id, "module_from_implementation_plan")],
            }
        )
    return modules


def _build_files(
    implementation_plan: dict[str, Any],
    protocol_slug: str,
    decision_refs: list[str],
    profile_id: str,
) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    file_layout = _file_layout(implementation_plan)
    canonical_by_module = {
        str(item.get("owner_module")): str(item.get("type_name"))
        for item in implementation_plan.get("canonical_types", [])
        if isinstance(item, dict) and item.get("owner_module") and item.get("type_name")
    }
    modules_by_name = {str(item.get("name")): item for item in implementation_plan.get("module_graph", []) if isinstance(item, dict)}
    for layout_file in _layout_files(file_layout):
        if not isinstance(layout_file, dict):
            continue
        name = str(layout_file.get("module", "module"))
        module = modules_by_name.get(name, {})
        file_id = str(layout_file.get("file_id", "file"))
        trace_id = _trace_id_for_file(protocol_slug, layout_file)
        public_type = canonical_by_module.get(name, f"{protocol_slug}_{name}_t")
        own_header = str(layout_file.get("owns_header") or layout_file.get("header_path") or "").strip()
        module_header = _module_public_header(file_layout, name)
        outgoing_edges = _file_edges_for(file_layout, file_id, direction="outgoing")
        header_dependencies = [
            _header_for_provider_file(file_layout, str(edge.get("provider_file", "")))
            for edge in outgoing_edges
            if str(edge.get("include_scope", "")).lower() == "header"
        ]
        source_dependencies = [
            own_header or module_header,
            *[
                _header_for_provider_file(file_layout, str(edge.get("provider_file", "")))
                for edge in outgoing_edges
            ],
        ]
        files.append(
            {
                "trace_id": trace_id,
                "file_id": file_id,
                "module": name,
                "lang": "C",
                "role": str(layout_file.get("role") or module.get("role", "")),
                "header_path": own_header,
                "source_path": str(layout_file.get("source_path") or layout_file.get("path") or ""),
                "header_dependencies": _dedupe(header_dependencies),
                "source_dependencies": _dedupe(source_dependencies),
                "header_data": [
                    {
                        "NAME": public_type,
                        "KIND": "TYPE",
                        "VISIBILITY": "PUBLIC",
                        "ROLE": f"Opaque handle for {name} module state",
                    }
                ] if own_header else [],
                "source_data": [
                    {
                        "NAME": f"struct {public_type.rstrip('_t')}",
                        "KIND": "TYPE",
                        "VISIBILITY": "PRIVATE",
                        "ROLE": f"Private state owned by the {name} module",
                    }
                ] if own_header else [],
                "header_interfaces": [],
                "source_interfaces": [],
                "file_dependencies": _file_dependency_projection(file_layout, file_id),
                "dependency_refs": _dedupe([
                    *[ref for edge in outgoing_edges for ref in _source_graph_refs(edge)],
                    *[_edge_ref(edge, "file_edge") for edge in outgoing_edges],
                ]),
                "call_contracts": _file_call_contracts_for(file_layout, file_id),
                "evidence_refs": list(layout_file.get("evidence_refs", [])) or list(module.get("evidence_refs", [])),
                "decision_refs": decision_refs,
                "profile_refs": [_profile_ref(profile_id, "file_from_layout")],
            }
        )
    return files


def _build_lifecycle_functions(
    module_graph: list[dict[str, Any]],
    canonical_by_module: dict[str, str],
    dependency_graph: dict[str, Any],
    protocol_slug: str,
    decision_refs: list[str],
    profile_id: str,
    file_trace_by_function: dict[str, str],
) -> list[dict[str, Any]]:
    functions: list[dict[str, Any]] = []
    for module in module_graph:
        if not isinstance(module, dict):
            continue
        name = str(module.get("name", "module"))
        public_type = canonical_by_module.get(name, f"{protocol_slug}_{name}_t")
        for suffix, return_type, params, role in (
            ("create", f"{public_type}*", [], f"Create and initialize {name} module state"),
            (
                "destroy",
                "void",
                [{"TYPE": f"{public_type}*", "NAME": "self", "NULLABLE": True, "OWNERSHIP": "BORROWED"}],
                f"Release resources owned by {name} module state",
            ),
        ):
            fname = f"{protocol_slug}_{name}_{suffix}"
            file_trace_id = file_trace_by_function.get(fname, f"{protocol_slug}/{name}/{name}_api")
            functions.append(
                {
                    "trace_id": f"{file_trace_id}/{fname}",
                    "module": name,
                    "file_trace_id": file_trace_id,
                    "name": fname,
                    "function_type": "ALGORITHM",
                    "signature": {"RAW": _signature(return_type, fname, params), "NAME": fname, "RETURN": return_type, "PARAMS": params},
                    "role": role,
                    "visibility": "public",
                    "rely": _rely_for(dependency_graph, fname),
                    "logic": {
                        "INPUT": "module construction inputs",
                        "ACTION": role,
                        "OUTPUT": "module handle or cleanup side effect",
                        "INVARIANTS_USED": ["Preserve implementation_plan canonical ownership boundaries"],
                    },
                    "call_contracts": _call_contracts_for(dependency_graph, fname),
                    "dependency_refs": _dedupe([
                        *[str(edge.get("edge_id", "")) for edge in _function_edges_for(dependency_graph, fname)],
                        *[str(edge.get("edge_id", "")) for edge in _data_edges_for(dependency_graph, fname)],
                    ]),
                    "dependency_projection": _call_contracts_for(dependency_graph, fname),
                    "evidence_refs": list(module.get("evidence_refs", [])),
                    "decision_refs": decision_refs,
                    "profile_refs": [_profile_ref(profile_id, "lifecycle_function")],
                }
            )
    return functions


def _capability_function_suffixes(module: dict[str, Any]) -> list[tuple[str, str, str]]:
    owned = {str(item) for item in module.get("owned_capabilities", []) if str(item).strip()}
    suffixes: list[tuple[str, str, str]] = []

    def add(suffix: str, group: str, function_type: str = "ALGORITHM") -> None:
        item = (suffix, group, function_type)
        if item not in suffixes:
            suffixes.append(item)

    if owned.intersection({"transport_io", "connection_lifecycle"}):
        add("start", "runtime")
        add("run", "runtime", "EVENT")
        add("stop", "runtime")
        add("accept_connection", "runtime")
        add("close_connection", "runtime")
    if owned.intersection({"connection_buffering", "incremental_message_framing", "datagram_message_framing"}):
        add("feed", "codec")
    if owned.intersection({"message_decode", "incremental_message_framing", "datagram_message_framing"}):
        add("decode_packet", "codec")
        add("decode_next", "codec")
    if owned.intersection({"message_encode", "error_response_encoding"}):
        add("encode_packet", "codec")
        add("encode_response", "codec")
    if "semantic_dispatch" in owned:
        add("dispatch_packet", "semantic")
    if owned.intersection({"state_machine", "state_transition_validation"}):
        add("validate_transition", "state")
        add("apply_transition", "state")
    if "protocol_error_policy" in owned:
        add("handle_error", "errors")
    if "session_state_ownership" in owned:
        add("add_session", "session")
        add("get_session", "session")
        add("remove_session", "session")
        add("mark_session_connected", "session")
    if "resource_ownership" in owned:
        add("store_resource", "resource")
        add("lookup_resource", "resource")
        add("delete_resource", "resource")
    if "routing_dispatch" in owned:
        add("subscribe", "routing")
        add("unsubscribe", "routing")
        add("publish", "routing")
        add("remove_session_routes", "routing")
    return suffixes


def _capability_params(public_type: str, suffix: str) -> tuple[str, list[dict[str, Any]]]:
    self_param = {"TYPE": f"{public_type}*", "NAME": "self", "NULLABLE": False, "OWNERSHIP": "BORROWED"}
    if suffix == "stop":
        return "void", [self_param]
    if suffix == "run":
        return "int", [self_param]
    if suffix in {"decode_packet", "decode_next", "feed", "dispatch_packet", "validate_transition", "apply_transition", "handle_error"}:
        return "int", [
            self_param,
            {"TYPE": "const void*", "NAME": "input", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
        ]
    if suffix in {"encode_packet", "encode_response"}:
        return "int", [
            self_param,
            {"TYPE": "const void*", "NAME": "message", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
            {"TYPE": "void*", "NAME": "output", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
        ]
    if suffix in {"get_session", "remove_session", "lookup_resource", "delete_resource", "remove_session_routes"}:
        return "int", [
            self_param,
            {"TYPE": "const char*", "NAME": "key", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
        ]
    if suffix in {"add_session", "store_resource", "subscribe", "unsubscribe", "publish"}:
        return "int", [
            self_param,
            {"TYPE": "const void*", "NAME": "item", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
        ]
    return "int", [self_param]


def _build_capability_functions(
    module_graph: list[dict[str, Any]],
    canonical_by_module: dict[str, str],
    dependency_graph: dict[str, Any],
    protocol_slug: str,
    decision_refs: list[str],
    profile_id: str,
    file_trace_by_function: dict[str, str],
) -> list[dict[str, Any]]:
    functions: list[dict[str, Any]] = []
    for module in module_graph:
        if not isinstance(module, dict):
            continue
        name = str(module.get("name", "module"))
        public_type = canonical_by_module.get(name, f"{protocol_slug}_{name}_t")
        for suffix, group, function_type in _capability_function_suffixes(module):
            fname = f"{protocol_slug}_{name}_{suffix}"
            file_trace_id = file_trace_by_function.get(fname, f"{protocol_slug}/{name}/{name}_api")
            return_type, params = _capability_params(public_type, suffix)
            role = f"Implement {group} capability '{suffix}' for {name} module"
            item: dict[str, Any] = {
                "trace_id": f"{file_trace_id}/{fname}",
                "module": name,
                "file_trace_id": file_trace_id,
                "name": fname,
                "function_type": function_type,
                "signature": {"RAW": _signature(return_type, fname, params), "NAME": fname, "RETURN": return_type, "PARAMS": params},
                "role": role,
                "visibility": "public",
                "rely": _rely_for(dependency_graph, fname),
                "call_contracts": _call_contracts_for(dependency_graph, fname),
                "dependency_refs": _dedupe([
                    *[str(edge.get("edge_id", "")) for edge in _function_edges_for(dependency_graph, fname)],
                    *[str(edge.get("edge_id", "")) for edge in _data_edges_for(dependency_graph, fname)],
                ]),
                "dependency_projection": _call_contracts_for(dependency_graph, fname),
                "evidence_refs": list(module.get("evidence_refs", [])),
                "decision_refs": decision_refs,
                "profile_refs": [_profile_ref(profile_id, "capability_function")],
            }
            if function_type == "EVENT":
                item["event"] = {
                    "TRIGGER": f"{group} event reaches {name}",
                    "PRECONDITION": "module state has been initialized",
                    "INPUT": f"{group} runtime input",
                    "ACTION": role,
                    "STATE_CHANGE": "May advance module-owned runtime state according to implementation plan",
                    "RESPONSE": "0 on success, non-zero on failure",
                    "EVENT_TYPE": "RUNTIME_EVENT",
                }
            else:
                item["logic"] = {
                    "INPUT": "module state and function arguments",
                    "ACTION": role,
                    "OUTPUT": "0 or documented side effect on success; non-zero on failure when applicable",
                    "INVARIANTS_USED": ["Preserve implementation_plan canonical ownership boundaries"],
                }
            functions.append(item)
    return functions


def _build_entrypoint_functions(
    module_graph: list[dict[str, Any]],
    dependency_graph: dict[str, Any],
    decision_refs: list[str],
    profile_id: str,
    file_trace_by_function: dict[str, str],
) -> list[dict[str, Any]]:
    functions: list[dict[str, Any]] = []
    for module in module_graph:
        if not isinstance(module, dict):
            continue
        owned = {str(item) for item in module.get("owned_capabilities", []) if str(item).strip()}
        if "role_composition" not in owned:
            continue
        name = str(module.get("name", "module"))
        fname = "main"
        file_trace_id = file_trace_by_function.get(fname, f"protocol/{name}/main")
        params = [
            {"TYPE": "int", "NAME": "argc", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
            {"TYPE": "char**", "NAME": "argv", "NULLABLE": True, "OWNERSHIP": "BORROWED"},
        ]
        role = f"Provide process entrypoint that initializes and runs {name} composition"
        functions.append(
            {
                "trace_id": f"{file_trace_id}/{fname}",
                "module": name,
                "file_trace_id": file_trace_id,
                "name": fname,
                "function_type": "ENTRYPOINT",
                "signature": {"RAW": _signature("int", fname, params), "NAME": fname, "RETURN": "int", "PARAMS": params},
                "role": role,
                "visibility": "private",
                "rely": _rely_for(dependency_graph, fname),
                "logic": {
                    "INPUT": "process arguments",
                    "ACTION": role,
                    "OUTPUT": "process exit status",
                    "INVARIANTS_USED": ["Initialize role composition before entering runtime loop"],
                },
                "call_contracts": _call_contracts_for(dependency_graph, fname),
                "dependency_refs": _dedupe([
                    *[str(edge.get("edge_id", "")) for edge in _function_edges_for(dependency_graph, fname)],
                    *[str(edge.get("edge_id", "")) for edge in _data_edges_for(dependency_graph, fname)],
                ]),
                "dependency_projection": _call_contracts_for(dependency_graph, fname),
                "evidence_refs": list(module.get("evidence_refs", [])),
                "decision_refs": decision_refs,
                "profile_refs": [_profile_ref(profile_id, "role_composition_entrypoint")],
            }
        )
    return functions


def _build_handler_functions(
    handler_matrix: list[dict[str, Any]],
    files: list[dict[str, Any]],
    canonical_by_module: dict[str, str],
    dependency_graph: dict[str, Any],
    protocol_slug: str,
    decision_refs: list[str],
    profile_id: str,
    file_trace_by_function: dict[str, str],
) -> list[dict[str, Any]]:
    functions: list[dict[str, Any]] = []
    for row in handler_matrix:
        if not isinstance(row, dict):
            continue
        module = str(row.get("handler_module", "handler_dispatch"))
        public_type = canonical_by_module.get(module, f"{protocol_slug}_{module}_t")
        surface = str(row.get("surface_unit", "surface"))
        fname = str(row.get("handler_function") or f"{protocol_slug}_{module}_handle_{_safe_slug(surface)}")
        file_trace_id = file_trace_by_function.get(fname, f"{protocol_slug}/{module}/{module}_api")
        params = [
            {"TYPE": f"{public_type}*", "NAME": "self", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
            {"TYPE": "const void*", "NAME": "message", "NULLABLE": False, "OWNERSHIP": "BORROWED"},
        ]
        functions.append(
            {
                "trace_id": f"{file_trace_id}/{fname}",
                "module": module,
                "file_trace_id": file_trace_id,
                "name": fname,
                "function_type": "EVENT",
                "signature": {"RAW": _signature("int", fname, params), "NAME": fname, "RETURN": "int", "PARAMS": params},
                "role": f"Handle protocol surface '{surface}' according to implementation_plan.handler_matrix",
                "visibility": "public",
                "rely": _rely_for(dependency_graph, fname),
                "event": {
                    "TRIGGER": f"decoded protocol surface {surface}",
                    "PRECONDITION": "decoded message and module state are valid",
                    "INPUT": f"decoded {surface} message and module state",
                    "ACTION": str(row.get("path_summary", "decode -> handler -> policy/store")),
                    "STATE_CHANGE": "Apply state/resource updates required by the implementation plan",
                    "RESPONSE": "0 on success, non-zero on protocol or processing failure",
                    "EVENT_TYPE": "PROTOCOL_HANDLER",
                },
                "call_contracts": _call_contracts_for(dependency_graph, fname),
                "dependency_refs": _dedupe([
                    *[str(edge.get("edge_id", "")) for edge in _function_edges_for(dependency_graph, fname)],
                    *[str(edge.get("edge_id", "")) for edge in _data_edges_for(dependency_graph, fname)],
                ]),
                "dependency_projection": _call_contracts_for(dependency_graph, fname),
                "evidence_refs": list(row.get("evidence_refs", [])),
                "decision_refs": decision_refs,
                "profile_refs": [_profile_ref(profile_id, "handler_function_from_handler_matrix")],
            }
        )
    return functions


def _attach_interfaces(files: list[dict[str, Any]], functions: list[dict[str, Any]]) -> None:
    files_by_trace = {str(item.get("trace_id")): item for item in files}
    header_file_by_module = {
        str(item.get("module")): item
        for item in files
        if item.get("module") and item.get("header_path")
    }
    for function in functions:
        file_item = files_by_trace.get(str(function.get("file_trace_id", "")))
        if file_item is None:
            continue
        file_item.setdefault("source_interfaces", []).append(_function_interface(function))
        if str(function.get("visibility", "public")).lower() == "public":
            header_file = header_file_by_module.get(str(function.get("module", ""))) or file_item
            header_file.setdefault("header_interfaces", []).append(_header_interface(function))


def _drop_empty_source_only_files(files: list[dict[str, Any]], modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    valid_paths: set[str] = set()
    for file_item in files:
        source_path = str(file_item.get("source_path", ""))
        header_path = str(file_item.get("header_path", ""))
        has_source_interfaces = bool(file_item.get("source_interfaces"))
        is_main = source_path.endswith("/main.c") or source_path == "main.c"
        if header_path or has_source_interfaces or is_main:
            kept.append(file_item)
            if header_path:
                valid_paths.add(header_path)
            if source_path:
                valid_paths.add(source_path)
    for module in modules:
        module["files"] = [path for path in module.get("files", []) if str(path) in valid_paths]
        module["artifacts"] = [
            artifact
            for artifact in module.get("artifacts", [])
            if not isinstance(artifact, dict)
            or not artifact.get("PATH")
            or str(artifact.get("PATH")) in valid_paths
        ]
    return kept


def _build_plan_driven_blueprint(
    planning_ir: PlanningIR,
    implementation_plan: dict[str, Any],
    target_profile: TargetProfile,
) -> SpecBlueprint:
    protocol_slug = _safe_slug(planning_ir.protocol_name)
    profile_id = GENERIC_C_PROFILE
    decision_refs = _decision_ids(implementation_plan)
    module_graph = [item for item in implementation_plan.get("module_graph", []) if isinstance(item, dict)]
    handler_matrix = [item for item in implementation_plan.get("handler_matrix", []) if isinstance(item, dict)]
    dep_graph = _dependency_graph(implementation_plan)
    file_layout = _file_layout(implementation_plan)
    canonical_by_module = {
        str(item.get("owner_module")): str(item.get("type_name"))
        for item in implementation_plan.get("canonical_types", [])
        if isinstance(item, dict) and item.get("owner_module") and item.get("type_name")
    }
    modules = _build_modules(implementation_plan, protocol_slug, decision_refs, profile_id)
    files = _build_files(implementation_plan, protocol_slug, decision_refs, profile_id)
    trace_by_file_id = {str(item.get("file_id")): str(item.get("trace_id")) for item in files if item.get("file_id")}
    placement = _function_placement(file_layout)
    file_trace_by_function = {function: trace_by_file_id[file_id] for function, file_id in placement.items() if file_id in trace_by_file_id}
    functions = _build_lifecycle_functions(module_graph, canonical_by_module, dep_graph, protocol_slug, decision_refs, profile_id, file_trace_by_function)
    functions.extend(_build_entrypoint_functions(module_graph, dep_graph, decision_refs, profile_id, file_trace_by_function))
    functions.extend(_build_capability_functions(module_graph, canonical_by_module, dep_graph, protocol_slug, decision_refs, profile_id, file_trace_by_function))
    functions.extend(_build_handler_functions(handler_matrix, files, canonical_by_module, dep_graph, protocol_slug, decision_refs, profile_id, file_trace_by_function))
    _attach_interfaces(files, functions)
    files = _drop_empty_source_only_files(files, modules)
    data = {
        "kind": "SPEC_BLUEPRINT",
        "schema_version": BLUEPRINT_SCHEMA,
        "protocol_name": planning_ir.protocol_name,
        "target_profile": target_profile.raw,
        "expansion_profile": profile_id,
        "source": "implementation_plan_v2",
        "implementation_plan_ref": "implementation_plan_v2.json",
        "generation_order": _topological_generation_order(modules, dep_graph),
        "consistency_rules": [
            {"ID": "unique_public_type_owner", "RULE": "Every canonical public type must have one owner only.", "DOC_REF": []},
            {"ID": "acyclic_module_dependencies", "RULE": "Module dependencies must be forward-safe for current coder.", "DOC_REF": []},
            {"ID": "file_layout_function_placement", "RULE": "Every generated function must map to exactly one file layout source unit.", "DOC_REF": []},
            {"ID": "handler_matrix_surface_coverage", "RULE": "Every required surface in scope_decisions must map to a handler function.", "DOC_REF": []},
        ],
        "modules": modules,
        "files": files,
        "functions": functions,
        "coverage": {
            "module_count": len(modules),
            "file_spec_count": len(files),
            "function_spec_count": len(functions),
            "required_surface_count": len(implementation_plan.get("scope_decisions", {}).get("minimum_v1_surface", [])),
            "handler_function_count": len(handler_matrix),
        },
        "traceability": {
            "decision_refs": decision_refs,
            "source_artifacts": ["protocol_facts.json", "target_profile.json", "implementation_plan_v2.json"],
        },
    }
    return SpecBlueprint(data)


def build_spec_blueprint(
    planning_ir: PlanningIR,
    implementation_plan: dict[str, Any],
    target_profile: TargetProfile,
    llm_client: Any | None = None,
) -> tuple[SpecBlueprint, list[dict[str, Any]]]:
    del llm_client
    blueprint = _build_plan_driven_blueprint(planning_ir, implementation_plan, target_profile)
    candidates = [
        {
            "candidate_id": f"profile:{GENERIC_C_PROFILE}",
            "origin": "implementation_plan_v2",
            "selected": True,
            "expansion_profile": blueprint.data["expansion_profile"],
            "selection_score": 1.0,
            "applicable": True,
            "coverage": blueprint.data.get("coverage", {}),
            "notes": [
                "Generated only from protocol_facts.json, target_profile.json, and implementation_plan_v2.json.",
                "No protocol-specific catalog or external baseline is used by the planning flow.",
                "Verifier remains the final acceptance gate for the selected blueprint.",
            ],
        }
    ]
    return blueprint, candidates


def blueprint_to_jsonable(blueprint: SpecBlueprint) -> dict[str, Any]:
    return asdict(blueprint)["data"]
