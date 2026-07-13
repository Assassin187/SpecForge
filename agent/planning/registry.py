from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable


CANONICAL_FIELDS = {
    "artifact_id",
    "artifact_kind",
    "canonical_name",
    "owner_module_id",
    "owner_file_id",
    "visibility",
    "definition_stage",
}


class RegistryInvariantError(RuntimeError):
    pass


class RegistryBindingError(ValueError):
    def __init__(self, code: str, reference: str, expected_kinds: Iterable[str] = ()) -> None:
        expected = sorted(set(expected_kinds))
        message = f"{code}: cannot bind {reference!r}"
        if expected:
            message += f" as {expected}"
        super().__init__(message)
        self.code = code
        self.reference = reference
        self.expected_kinds = expected


class CanonicalPlanningRegistry:
    def __init__(self, entries: Iterable[dict[str, Any]] = ()) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._aliases: dict[str, set[str]] = {}
        for entry in entries:
            self.register(entry, aliases=entry.get("aliases", []))

    def register(self, entry: dict[str, Any], *, aliases: Iterable[str] = ()) -> dict[str, Any]:
        value = deepcopy(entry)
        missing = (CANONICAL_FIELDS | {"status", "provenance"}) - set(value)
        if missing:
            raise RegistryInvariantError(f"registry_entry_incomplete: missing {sorted(missing)}")
        artifact_id = str(value["artifact_id"])
        value["aliases"] = sorted({artifact_id, str(value["canonical_name"]), *map(str, aliases), *map(str, value.get("aliases", []))} - {""})
        current = self._entries.get(artifact_id)
        if current is not None:
            changed = {field for field in CANONICAL_FIELDS if current.get(field) != value.get(field)}
            if changed:
                raise RegistryInvariantError(f"registry_canonical_conflict: {artifact_id} changes {sorted(changed)}")
            new_aliases = set(value["aliases"]) - set(current["aliases"])
            current["aliases"] = sorted(set(current["aliases"]) | new_aliases)
            for alias in new_aliases:
                self._aliases.setdefault(alias, set()).add(artifact_id)
            return deepcopy(current)
        self._entries[artifact_id] = value
        for alias in value["aliases"]:
            self._aliases.setdefault(alias, set()).add(artifact_id)
        return deepcopy(value)

    def resolve(self, reference: Any, *, expected_kinds: Iterable[str] = ()) -> dict[str, Any]:
        key = str(reference or "")
        ids = set(self._aliases.get(key, set()))
        if key in self._entries:
            ids.add(key)
        kinds = set(expected_kinds)
        if kinds:
            matching = {artifact_id for artifact_id in ids if self._entries[artifact_id]["artifact_kind"] in kinds}
            if not matching and ids:
                raise RegistryBindingError("artifact_kind_mismatch", key, kinds)
            ids = matching
        if not ids:
            raise RegistryBindingError("unknown_artifact_id", key, kinds)
        if len(ids) != 1:
            raise RegistryBindingError("ambiguous_artifact_id", key, kinds)
        return deepcopy(self._entries[next(iter(ids))])

    def typed_view(
        self,
        kinds: Iterable[str],
        *,
        owner_module_id: str | None = None,
        owner_file_id: str | None = None,
    ) -> list[dict[str, Any]]:
        allowed = set(kinds)
        return [
            deepcopy(entry)
            for entry in sorted(self._entries.values(), key=lambda item: item["artifact_id"])
            if entry["artifact_kind"] in allowed
            and (owner_module_id is None or entry["owner_module_id"] == owner_module_id)
            and (owner_file_id is None or entry["owner_file_id"] == owner_file_id)
        ]

    def mark_defined(self, reference: Any, *, stage_id: str, expected_kinds: Iterable[str]) -> None:
        entry = self.resolve(reference, expected_kinds=expected_kinds)
        current = self._entries[entry["artifact_id"]]
        current["status"] = "defined"
        events = current.setdefault("definition_events", [])
        if {"stage_id": stage_id} not in events:
            events.append({"stage_id": stage_id})

    def apply_overlay(self, reference: Any, changes: dict[str, Any]) -> None:
        forbidden = CANONICAL_FIELDS.intersection(changes)
        if forbidden:
            raise RegistryInvariantError(f"registry_canonical_fields_immutable: {sorted(forbidden)}")
        entry = self.resolve(reference)
        self._entries[entry["artifact_id"]].update(deepcopy(changes))

    def register_amendment(
        self,
        *,
        requested_kind: str,
        proposed_name: str,
        requested_owner: str,
        preferred_visibility: str,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        if requested_kind not in {"type", "callback", "function", "constant"}:
            raise RegistryInvariantError(f"artifact_request_kind_unsupported: {requested_kind}")
        owner = self.resolve(requested_owner, expected_kinds={"file"})
        artifact_id = (
            _function_id(owner["canonical_name"], proposed_name)
            if requested_kind == "function"
            else _canonical_id(requested_kind, proposed_name)
        )
        return self.register(
            {
                "artifact_id": artifact_id,
                "artifact_kind": requested_kind,
                "canonical_name": proposed_name,
                "owner_module_id": owner["owner_module_id"],
                "owner_file_id": owner["artifact_id"],
                "visibility": preferred_visibility,
                "definition_stage": "inventory_amendment",
                "status": "requested",
                "provenance": deepcopy(provenance),
            },
            aliases=[proposed_name],
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "kind": "CANONICAL_PLANNING_SYMBOL_REGISTRY",
            "schema_version": 1,
            "status": "active",
            "entries": [deepcopy(item) for item in sorted(self._entries.values(), key=lambda value: value["artifact_id"])],
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> CanonicalPlanningRegistry:
        if snapshot.get("kind") != "CANONICAL_PLANNING_SYMBOL_REGISTRY":
            raise RegistryInvariantError("registry_snapshot_kind_invalid")
        return cls(snapshot.get("entries", []))


def build_registry(stage_artifacts: list[dict[str, Any]], *, protocol_slug: str) -> CanonicalPlanningRegistry:
    registry = CanonicalPlanningRegistry()
    layout = _stage_artifact(stage_artifacts, "module_file_plan")
    inventory = _stage_artifact(stage_artifacts, "public_artifact_inventory")
    type_designs = {
        _name(item): item
        for item in _objects(_stage_artifact(stage_artifacts, "type_and_access_path_design").get("types"))
    }

    for item in _objects(layout.get("modules")):
        name = str(item.get("name") or item.get("id") or "")
        artifact_id = _canonical_id("module", name)
        registry.register(
            _entry(artifact_id, "module", name, None, None, "internal", "module_file_plan", item),
            aliases=[str(item.get("id", "")), name],
        )

    layout_files = _objects(layout.get("files"))
    primary_paths = {
        str(item.get("source_path") or item.get("header_path") or item.get("id") or "file")
        for item in layout_files
    }
    grouped_files: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in layout_files:
        module = str(item.get("module", ""))
        path = str(item.get("source_path") or item.get("header_path") or item.get("id") or "file")
        trace = _trace_from_path(protocol_slug, path)
        grouped_files.setdefault((module, trace), []).append(item)
    for (module, trace), items in grouped_files.items():
        module_entry = registry.resolve(module, expected_kinds={"module"})
        aliases: set[str] = {trace}
        refs: list[str] = []
        for item in items:
            refs.extend(map(str, item.get("trace_refs", [])))
            for key in ("id", "header_path", "source_path"):
                value = str(item.get(key) or "")
                if key == "header_path" and item.get("source_path") and value in primary_paths:
                    continue
                if value:
                    aliases.update({value, Path(value).name})
        artifact_id = _canonical_id("file", trace)
        registry.register(
            _entry(artifact_id, "file", trace, module_entry["artifact_id"], None, "internal", "module_file_plan", {"trace_refs": refs}),
            aliases=aliases,
        )

    for item in _objects(inventory.get("types")):
        name = _name(item)
        kind = "callback" if str(item.get("kind", "")).lower() == "callback" else "type"
        owner = registry.resolve(item.get("owner_file") or item.get("file"), expected_kinds={"file"})
        visibility = str(item.get("visibility") or "private").lower()
        legacy_visibility_source: str | None = None
        if visibility not in {"public", "private"}:
            design = type_designs.get(name, {})
            visibility = "public" if design.get("public_visibility") is True else "private"
            legacy_visibility_source = "type_and_access_path_design.public_visibility" if design else "private_default"
        entry = _entry(
            _canonical_id(kind, name),
            kind,
            name,
            owner["owner_module_id"],
            owner["artifact_id"],
            visibility,
            "public_artifact_inventory",
            item,
        )
        entry["declaration_kind"] = str(item.get("kind") or "type").lower()
        if legacy_visibility_source:
            entry["provenance"]["legacy_visibility_source"] = legacy_visibility_source
        registry.register(
            entry,
            aliases=[str(item.get("id", "")), str(item.get("symbol", "")), name],
        )
    for item in _objects(inventory.get("constants_or_macros")):
        name = _name(item)
        owner = registry.resolve(item.get("owner_file") or item.get("file"), expected_kinds={"file"})
        registry.register(
            _entry(
                _canonical_id("constant", name),
                "constant",
                name,
                owner["owner_module_id"],
                owner["artifact_id"],
                str(item.get("visibility") or "public").lower(),
                "public_artifact_inventory",
                item,
            ),
            aliases=[str(item.get("id", "")), str(item.get("symbol", "")), name],
        )
    for item in _objects(inventory.get("functions")):
        name = _name(item)
        owner = registry.resolve(item.get("owner_file") or item.get("file"), expected_kinds={"file"})
        artifact_id = _function_id(owner["canonical_name"], name)
        registry.register(
            _entry(
                artifact_id,
                "function",
                name,
                owner["owner_module_id"],
                owner["artifact_id"],
                str(item.get("visibility") or "private").lower(),
                "public_artifact_inventory",
                item,
            ),
            aliases=[str(item.get("function_id", "")), str(item.get("id", "")), str(item.get("symbol", "")), name],
        )

    _mark_definitions(registry, stage_artifacts)
    _register_tests(registry, stage_artifacts)
    return registry


def register_semantic_patch_additions(
    registry: CanonicalPlanningRegistry,
    plan: dict[str, Any],
    patch: dict[str, Any],
) -> None:
    collections = {"module": "modules", "file": "files", "type": "types", "callback": "types", "function": "functions"}
    by_kind = {
        kind: {str(item.get("id")): item for item in _objects(plan.get(collection))}
        for kind, collection in collections.items()
    }
    additions = [
        operation
        for operation in patch.get("operations", [])
        if isinstance(operation, dict) and operation.get("op") == "add"
    ]
    rank = {"module": 0, "file": 1, "type": 2, "callback": 2, "function": 3}
    for operation in sorted(additions, key=lambda item: rank.get(str(item.get("artifact_kind", "")), 99)):
        kind = str(operation.get("artifact_kind", ""))
        artifact_id = str(operation.get("artifact_id", ""))
        artifact = by_kind.get(kind, {}).get(artifact_id)
        if artifact is None:
            raise RegistryInvariantError(f"semantic_patch_registry_artifact_missing: {kind} {artifact_id}")
        name = _name(artifact) or artifact_id.rsplit("/", 1)[-1]
        owner_file_id: str | None = None
        owner_module_id: str | None = None
        if kind == "module":
            expected_id = _canonical_id("module", name)
        elif kind == "file":
            owner = registry.resolve(artifact.get("module"), expected_kinds={"module"})
            owner_module_id = owner["artifact_id"]
            slug = str(plan.get("protocol", {}).get("slug") or artifact_id.removeprefix("file:").split("/", 1)[0] or "protocol")
            path = str(artifact.get("source_path") or artifact.get("header_path") or artifact_id.removeprefix("file:"))
            expected_id = _canonical_id("file", _trace_from_path(slug, path))
        else:
            owner = registry.resolve(artifact.get("file") or artifact.get("owner_file"), expected_kinds={"file"})
            owner_file_id = owner["artifact_id"]
            owner_module_id = owner["owner_module_id"]
            expected_id = _function_id(owner["canonical_name"], name) if kind == "function" else _canonical_id(kind, name)
        if kind != "file" and artifact_id != expected_id:
            raise RegistryInvariantError(f"semantic_patch_noncanonical_id: {artifact_id} != {expected_id}")
        registry.register(
            {
                "artifact_id": expected_id,
                "artifact_kind": kind,
                "canonical_name": name if kind != "file" else expected_id.removeprefix("file:"),
                "owner_module_id": owner_module_id,
                "owner_file_id": owner_file_id,
                "visibility": str(artifact.get("visibility") or "private").lower(),
                "definition_stage": "bounded_semantic_patch",
                "status": "defined",
                "provenance": deepcopy(operation.get("provenance", {})),
            },
            aliases=[artifact_id, name, str(artifact.get("trace_id", ""))],
        )


def advance_registry(
    registry: CanonicalPlanningRegistry,
    stage_artifacts: list[dict[str, Any]],
    *,
    stage_id: str,
) -> None:
    if stage_id in {"type_and_access_path_design", "function_interface_design"}:
        _mark_definitions(registry, stage_artifacts, stage_id=stage_id)
    elif stage_id == "function_test_vector_design":
        _register_tests(registry, stage_artifacts)


def _mark_definitions(
    registry: CanonicalPlanningRegistry,
    artifacts: list[dict[str, Any]],
    *,
    stage_id: str | None = None,
) -> None:
    if stage_id in {None, "type_and_access_path_design"}:
        type_stage = _stage_artifact(artifacts, "type_and_access_path_design")
        if "type_definition_overlays" in type_stage:
            references = [item.get("type_id") for item in _objects(type_stage.get("type_definition_overlays"))]
        else:
            references = [_name(item) for item in _objects(type_stage.get("types"))]
        for reference in references:
            registry.mark_defined(reference, stage_id="type_and_access_path_design", expected_kinds={"type", "callback"})
    if stage_id in {None, "function_interface_design"}:
        for item in _objects(_stage_artifact(artifacts, "function_interface_design").get("function_interfaces")):
            registry.mark_defined(
                item.get("function_id") or item.get("id") or item.get("name"),
                stage_id="function_interface_design",
                expected_kinds={"function"},
            )


def _register_tests(registry: CanonicalPlanningRegistry, artifacts: list[dict[str, Any]]) -> None:
    tests = _stage_artifact(artifacts, "function_test_vector_design")
    for owner_ref, vectors in (tests.get("function_test_vectors", {}) or {}).items():
        owner = registry.resolve(owner_ref, expected_kinds={"function"})
        for index, vector in enumerate(vectors if isinstance(vectors, list) else [], 1):
            name = str(vector.get("name") or vector.get("scenario") or f"vector_{index}") if isinstance(vector, dict) else f"vector_{index}"
            artifact_id = _canonical_id("test", f"{owner['artifact_id']}/{index}")
            registry.register(
                _entry(artifact_id, "test", name, owner["owner_module_id"], owner["owner_file_id"], "internal", "function_test_vector_design", vector),
                aliases=[],
            )
    for owner_ref, vectors in (tests.get("file_test_vectors", {}) or {}).items():
        owner = registry.resolve(owner_ref, expected_kinds={"file"})
        for index, vector in enumerate(vectors if isinstance(vectors, list) else [], 1):
            name = str(vector.get("name") or vector.get("scenario") or f"vector_{index}") if isinstance(vector, dict) else f"vector_{index}"
            registry.register(
                _entry(
                    _canonical_id("test", f"{owner['artifact_id']}/{index}"),
                    "test",
                    name,
                    owner["owner_module_id"],
                    owner["artifact_id"],
                    "internal",
                    "function_test_vector_design",
                    vector,
                )
            )
    for index, vector in enumerate(tests.get("runtime_test_vectors", []) or [], 1):
        name = str(vector.get("name") or vector.get("scenario") or f"runtime_{index}") if isinstance(vector, dict) else f"runtime_{index}"
        registry.register(
            _entry(
                _canonical_id("test", f"runtime/{index}"),
                "test",
                name,
                None,
                None,
                "internal",
                "function_test_vector_design",
                vector,
            )
        )


def _entry(
    artifact_id: str,
    kind: str,
    name: str,
    owner_module_id: str | None,
    owner_file_id: str | None,
    visibility: str,
    definition_stage: str,
    source: Any,
) -> dict[str, Any]:
    refs = source.get("trace_refs", source.get("fact_refs", [])) if isinstance(source, dict) else []
    return {
        "artifact_id": artifact_id,
        "artifact_kind": kind,
        "canonical_name": name,
        "owner_module_id": owner_module_id,
        "owner_file_id": owner_file_id,
        "visibility": visibility,
        "definition_stage": definition_stage,
        "status": "declared",
        "provenance": {"kind": "inferred_engineering_decision", "refs": list(map(str, refs or []))},
    }


def _stage_artifact(artifacts: list[dict[str, Any]], stage_id: str) -> dict[str, Any]:
    return next((item["artifact"] for item in reversed(artifacts) if item.get("stage_id") == stage_id and isinstance(item.get("artifact"), dict)), {})


def _objects(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _name(item: dict[str, Any]) -> str:
    return str(item.get("name") or item.get("type_name") or item.get("symbol") or item.get("function_id") or item.get("id") or "")


def _canonical_id(kind: str, value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_./-]+", "_", value.strip().strip("/")).strip("_/.") or "artifact"
    return f"{kind}:{clean}"


def _function_id(owner_trace: str, name: str) -> str:
    return _canonical_id("function", f"{owner_trace}/{name}")


def _trace_from_path(protocol_slug: str, path: str) -> str:
    clean = re.sub(r"\.(c|h)$", "", path.strip())
    clean = re.sub(r"[^A-Za-z0-9_./-]+", "_", clean.strip().strip("/")).strip("_/") or "artifact"
    return clean if clean.startswith(f"{protocol_slug}/") else f"{protocol_slug}/{clean}"
