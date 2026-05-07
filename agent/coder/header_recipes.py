from __future__ import annotations

from .models import FileSpec


def _type_tag(name: str) -> str:
    return name[:-2] if name.endswith("_t") else name


def _typedef_callback(signature: str) -> str:
    normalized = signature.strip().rstrip(";")
    if normalized.startswith("typedef "):
        return normalized + ";"
    return f"typedef {normalized};"


def _render_member(item: dict[str, object], indent: str = "    ") -> list[str]:
    member_name = str(item.get("NAME", "")).strip()
    member_type = str(item.get("TYPE", "")).strip()
    nested_spec = item.get("TYPE_SPEC")
    if not member_name:
        return []
    if isinstance(nested_spec, dict):
        nested_kind = str(nested_spec.get("TYPE_KIND", "")).upper()
        if nested_kind in {"STRUCT", "UNION"}:
            keyword = "struct" if nested_kind == "STRUCT" else "union"
            nested_key = "FIELDS" if nested_kind == "STRUCT" else "VARIANTS"
            lines = [f"{indent}{keyword} {{"]
            rendered_nested = False
            nested_members = nested_spec.get(nested_key, [])
            if isinstance(nested_members, list):
                for nested_item in nested_members:
                    if isinstance(nested_item, dict):
                        child_lines = _render_member(nested_item, indent + "    ")
                        if child_lines:
                            lines.extend(child_lines)
                            rendered_nested = True
            if not rendered_nested:
                lines.append(f"{indent}    unsigned char reserved;")
            lines.append(f"{indent}}} {member_name};")
            return lines
    if not member_type:
        return []
    return [f"{indent}{member_type} {member_name};"]


def _render_type_spec(name: str, type_spec: dict[str, object]) -> str:
    type_kind = str(type_spec.get("TYPE_KIND", "")).upper()
    if type_kind == "OPAQUE":
        return f"typedef struct {_type_tag(name)} {name};"
    if type_kind == "ALIAS":
        alias_of = str(type_spec.get("ALIAS_OF", "")).strip()
        return f"typedef {alias_of} {name};" if alias_of else ""
    if type_kind == "CALLBACK":
        callback_signature = str(type_spec.get("CALLBACK_SIGNATURE", "")).strip()
        return _typedef_callback(callback_signature) if callback_signature else ""
    if type_kind == "ENUM":
        values = type_spec.get("ENUM_VALUES", [])
        lines = [f"typedef enum {_type_tag(name)} {{"]
        rendered_value = False
        if isinstance(values, list):
            for item in values:
                if not isinstance(item, dict):
                    continue
                enum_name = str(item.get("NAME", "")).strip()
                enum_value = item.get("VALUE")
                if not enum_name:
                    continue
                rendered_value = True
                if enum_value in (None, ""):
                    lines.append(f"    {enum_name},")
                else:
                    lines.append(f"    {enum_name} = {enum_value},")
        if not rendered_value:
            lines.append("    PLACEHOLDER_ENUM_VALUE = 0,")
        lines.append(f"}} {name};")
        return "\n".join(lines)
    if type_kind in {"STRUCT", "UNION"}:
        keyword = "struct" if type_kind == "STRUCT" else "union"
        member_key = "FIELDS" if type_kind == "STRUCT" else "VARIANTS"
        members = type_spec.get(member_key, [])
        lines = [f"typedef {keyword} {_type_tag(name)} {{"]
        rendered_member = False
        if isinstance(members, list):
            for item in members:
                if not isinstance(item, dict):
                    continue
                rendered_lines = _render_member(item)
                if not rendered_lines:
                    continue
                lines.extend(rendered_lines)
                rendered_member = True
        if not rendered_member:
            lines.append("    unsigned char reserved;")
        lines.append(f"}} {name};")
        return "\n".join(lines)
    return ""


def _render_macro(item: dict[str, object]) -> str:
    name = str(item.get("NAME", "")).strip()
    value = str(item.get("VALUE", "")).strip()
    return f"#define {name} {value}" if name and value else ""


def _render_const(item: dict[str, object]) -> str:
    name = str(item.get("NAME", "")).strip()
    value = str(item.get("VALUE", "")).strip()
    c_type = str(item.get("TYPE", "")).strip()
    if not name or not value or not c_type:
        return ""
    return f"static const {c_type} {name} = {value};"


def _render_data_item(item: dict[str, object]) -> str:
    kind = str(item.get("KIND", "")).upper()
    name = str(item.get("NAME", "")).strip()
    if kind == "TYPE":
        type_spec = item.get("TYPE_SPEC")
        if isinstance(type_spec, dict):
            return _render_type_spec(name, type_spec)
        role = str(item.get("ROLE", ""))
        lowered_role = role.lower()
        if "opaque" in lowered_role or "不透明" in role or "句柄" in role or "隐藏" in role:
            struct_name = name[:-2] if name.endswith("_t") else name
            return f"typedef struct {struct_name} {name};"
    if kind == "MACRO":
        return _render_macro(item)
    if kind == "CONST":
        return _render_const(item)
    return ""


def render_public_declarations(file_spec: FileSpec) -> str:
    lines: list[str] = []
    for item in file_spec.header_data:
        if str(item.get("VISIBILITY", "")).upper() != "PUBLIC":
            continue
        rendered = _render_data_item(item)
        if rendered:
            lines.append(rendered)
    return "\n".join(lines)
