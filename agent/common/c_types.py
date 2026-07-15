from __future__ import annotations

import re


STANDARD_C_TYPES = {
    "bool",
    "char",
    "double",
    "float",
    "int",
    "int8_t",
    "int16_t",
    "int32_t",
    "int64_t",
    "intptr_t",
    "long",
    "ptrdiff_t",
    "short",
    "signed",
    "size_t",
    "ssize_t",
    "uint8_t",
    "uint16_t",
    "uint32_t",
    "uint64_t",
    "uintptr_t",
    "unsigned",
    "void",
}

SYSTEM_TYPE_HEADERS = {
    "ssize_t": "sys/types.h",
    "socklen_t": "sys/socket.h",
    "struct iovec": "sys/uio.h",
    "struct sockaddr": "sys/socket.h",
    "struct sockaddr_storage": "sys/socket.h",
}

_TAGGED_TYPE_RE = re.compile(r"\b(struct|union|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_TYPEDEF_TYPE_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*_t\b")
_QUALIFIER_RE = re.compile(r"\b(?:const|restrict|volatile|_Atomic)\b")


def c_type_references(text: str) -> list[str]:
    references = [f"{kind} {tag}" for kind, tag in _TAGGED_TYPE_RE.findall(text)]
    references.extend(_TYPEDEF_TYPE_RE.findall(text))
    return list(dict.fromkeys(references))


def required_system_headers(text: str) -> list[str]:
    references = set(c_type_references(text))
    return sorted({header for type_name, header in SYSTEM_TYPE_HEADERS.items() if type_name in references})


def is_system_type(type_name: str) -> bool:
    return type_name in SYSTEM_TYPE_HEADERS


def normalized_c_type(value: str) -> str:
    text = re.sub(r"\[[^\]]*\]", "*", value.strip())
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*\*\s*", "*", text)
    return text.strip()


def compatible_c_type(expected: str, actual: str) -> bool:
    expected_type = normalized_c_type(expected)
    actual_type = normalized_c_type(actual)
    if expected_type == actual_type:
        return True
    if expected_type.count("*") != actual_type.count("*"):
        return False
    expected_unqualified = _QUALIFIER_RE.sub("", expected_type)
    actual_unqualified = _QUALIFIER_RE.sub("", actual_type)
    expected_unqualified = re.sub(r"\s+", " ", expected_unqualified).strip()
    actual_unqualified = re.sub(r"\s+", " ", actual_unqualified).strip()
    if expected_unqualified != actual_unqualified:
        return False
    return (
        expected_type.count("*") == 1
        and "const" in expected_type.split("*")[0]
        and "const" not in actual_type.split("*")[0]
    )


def type_is_by_value(c_type: str, type_name: str) -> bool:
    return type_name in c_type_references(c_type) and "*" not in c_type


def type_use_mode(c_type: str, *, field_storage: bool = False, dereferenced: bool = False) -> str:
    if dereferenced:
        return "requires_complete_representation"
    if field_storage:
        return "field_storage"
    return "pointer_only" if "*" in normalized_c_type(c_type) else "by_value"
