"""Independent, exact-wire ICMP RFC 792 message-construction acceptance (standard library only).

This file also runs directly inside a bubblewrap stage, without importing the
model runtime. The generated project chooses its own public API (eleven RFC 792
message-construction entry points), so this suite discovers the construction
functions from the project's public headers, compiles a small C driver against
the delivery sources, calls every constructor with checker-fixed sentinel
inputs, and compares the produced messages byte-for-byte against wire bytes
computed independently here from RFC 792.

Usage: evaluate.py --binary /work/icmp_selftest --out <dir>
       (the project root defaults to the current working directory;
       pass --project explicitly for standalone calibration runs)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

TEST_IDS = (
    "icmp_selftest_contract",
    "icmp_echo_request_odd_data",
    "icmp_echo_request_even_data",
    "icmp_echo_request_empty_data",
    "icmp_echo_reply_preservation",
    "icmp_destination_unreachable_codes",
    "icmp_error_quotation_with_options",
    "icmp_time_exceeded_codes",
    "icmp_parameter_problem_pointer",
    "icmp_source_quench",
    "icmp_redirect_gateway",
    "icmp_timestamp_request",
    "icmp_timestamp_reply_preservation",
    "icmp_information_request_reply",
    "icmp_checksum_recomputation",
    "icmp_capacity_reporting",
)

KIND_TYPES = {
    "echo_reply": 0, "dest_unreach": 3, "source_quench": 4, "redirect": 5,
    "echo_request": 8, "time_exceeded": 11, "param_problem": 12,
    "timestamp_request": 13, "timestamp_reply": 14,
    "info_request": 15, "info_reply": 16,
}

# Checker-fixed sentinel inputs. Every expected wire image below derives from these.
FIX_ID = 0x1A2B
FIX_SEQ = 0x3C4D
FIX_DATA_ODD = bytes((0x53, 0x46, 0x2D, 0x69, 0x63, 0x6D, 0x70, 0x2D,
                      0x6F, 0x64, 0x64, 0x21, 0x00, 0xFF, 0x80))
FIX_DATA_ALT = FIX_DATA_ODD[:-1] + b"\x81"
FIX_DATA_EVEN = bytes(range(0x20, 0x30))
FIX_QUOTE = bytes((0x45, 0x00, 0x00, 0x3C, 0x12, 0x34, 0x40, 0x00,
                   0x40, 0x06, 0x00, 0x00, 0xC0, 0xA8, 0x00, 0x01,
                   0xC0, 0xA8, 0x00, 0x02)) + b"QUOTED64"
FIX_QUOTE_OPT = bytes((0x46, 0x00, 0x00, 0x40, 0x12, 0x34, 0x40, 0x00,
                       0x40, 0x06, 0x00, 0x00, 0xC0, 0xA8, 0x00, 0x01,
                       0xC0, 0xA8, 0x00, 0x02, 0x01, 0x01, 0x01, 0x00)) + b"QUOTED64"
FIX_GATEWAY = 0xC0A80063
FIX_POINTER = 21
FIX_TS_ORIG = 0x02FAF080
FIX_TS_RECV = 0x02FAF3E4
FIX_TS_XMIT = 0x82FAF7C8  # High bit set: RFC 792's nonstandard-time indication must pass through.

BUF_SIZE = 256
SANITIZER_RE = re.compile(r"AddressSanitizer|LeakSanitizer|runtime error:|UndefinedBehaviorSanitizer")


class EnvironmentBlocked(RuntimeError):
    pass


class HarnessError(RuntimeError):
    """The suite could not harness the delivery (no behavior verdict implied)."""


# ---------------------------------------------------------------------------
# Independent RFC 792 wire model: the expected bytes for every probe.
# ---------------------------------------------------------------------------

def internet_checksum(data: bytes) -> int:
    if len(data) & 1:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def wrap_message(msg_type: int, code: int, rest: bytes) -> bytes:
    head = bytes((msg_type, code, 0, 0)) + rest
    csum = internet_checksum(head)
    return bytes((msg_type, code, csum >> 8, csum & 0xFF)) + rest


def expect_echo(msg_type: int, data: bytes, ident: int = FIX_ID, seq: int = FIX_SEQ) -> bytes:
    return wrap_message(msg_type, 0, ident.to_bytes(2, "big") + seq.to_bytes(2, "big") + data)


def expect_quote_message(msg_type: int, code: int, middle4: bytes, quote: bytes) -> bytes:
    assert len(middle4) == 4
    return wrap_message(msg_type, code, middle4 + quote)


def expect_timestamp(msg_type: int, orig: int, recv: int, xmit: int,
                     ident: int = FIX_ID, seq: int = FIX_SEQ) -> bytes:
    rest = (ident.to_bytes(2, "big") + seq.to_bytes(2, "big") + orig.to_bytes(4, "big")
            + recv.to_bytes(4, "big") + xmit.to_bytes(4, "big"))
    return wrap_message(msg_type, 0, rest)


def expect_info(msg_type: int, ident: int = FIX_ID, seq: int = FIX_SEQ) -> bytes:
    return wrap_message(msg_type, 0, ident.to_bytes(2, "big") + seq.to_bytes(2, "big"))


# ---------------------------------------------------------------------------
# Public-header parsing: struct definitions, typedef aliases and prototypes.
# ---------------------------------------------------------------------------

class CType:
    def __init__(self, base: str, words: str, ptr: int, const: bool, raw: str):
        self.base, self.words, self.ptr, self.const, self.raw = base, words, ptr, const, raw

    def __repr__(self):
        return f"CType({self.words!r} base={self.base} ptr={self.ptr} const={self.const})"


class Param:
    def __init__(self, ctype: CType, name: str):
        self.ctype, self.name, self.cls = ctype, name, "unsupported"


class Proto:
    def __init__(self, name: str, ret: CType, params: list, header: str):
        self.name, self.ret, self.params, self.header = name, ret, params, header
        self.unsupported = False


class StructInfo:
    def __init__(self, typename: str, fields: list):
        self.typename, self.fields = typename, fields  # fields: list of (CType, name, array_len|None)


_STORAGE = {"const", "volatile", "register", "extern", "inline", "static",
            "__inline", "__inline__", "__restrict", "restrict", "auto"}


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def _base_words(words: str, aliases: dict, depth: int = 0):
    """Resolve aliases; return (base_words, extra_pointer_depth)."""
    if words in aliases and depth < 4:
        target_words, target_ptr = aliases[words]
        inner, extra = _base_words(target_words, aliases, depth + 1)
        return inner, extra + target_ptr
    return words, 0


def _classify_base(words: str) -> str:
    exact = {"uint8_t": "u8", "int8_t": "u8", "uint16_t": "u16", "int16_t": "u16",
             "uint32_t": "u32", "int32_t": "u32", "size_t": "size", "ssize_t": "size",
             "uint64_t": "u64", "int64_t": "u64", "bool": "bool", "_Bool": "bool"}
    if words in exact:
        return exact[words]
    if words in ("", "void"):
        return "void"
    parts = words.split()
    if "char" in parts:
        return "u8"
    if "short" in parts:
        return "u16"
    if parts and parts[-1] in ("int", "long") or words in ("unsigned", "signed"):
        return "u32"
    if "float" in parts or "double" in parts:
        return "unsupported"
    if re.fullmatch(r"(struct\s+)?[A-Za-z_]\w*", words) or words.startswith("enum "):
        return "named"
    return "unsupported"


def normalize_type(text: str, aliases: dict) -> CType:
    flat = " ".join(text.replace("*", " * ").split())
    tokens = flat.split()
    const = "const" in tokens
    ptr = tokens.count("*")
    words = " ".join(t for t in tokens if t != "*" and t not in _STORAGE)
    words, extra = _base_words(words, aliases)
    return CType(_classify_base(words), words, ptr + extra, const, text.strip())


_TYPE_ENDINGS = {"int", "char", "short", "long", "void", "unsigned", "signed", "float",
                 "double", "uint8_t", "uint16_t", "uint32_t", "uint64_t", "int8_t",
                 "int16_t", "int32_t", "int64_t", "size_t", "ssize_t", "bool", "_Bool"}


def _split_decl(text: str):
    """Split 'const uint8_t * data[8]' into (type_text, name, array_len)."""
    text = " ".join(text.strip().split())
    if not text or "(" in text:
        return None
    arr = None
    m = re.search(r"\[([^\]]*)\]\s*$", text)
    if m:
        token = m.group(1).strip()
        try:
            arr = int(token, 0) if token else 0
        except ValueError:
            arr = 0
        text = text[:m.start()].rstrip()
    m = re.match(r"^(?P<type>.*?)\s*(?P<name>[A-Za-z_]\w*)$", text)
    if m and m.group("type").strip() and m.group("type").strip() not in ("struct", "enum", "union") \
            and m.group("name") not in _TYPE_ENDINGS:
        return m.group("type"), m.group("name"), arr
    return text, "", arr


def _parse_fields(body: str, aliases: dict) -> list:
    fields = []
    for piece in body.split(";"):
        piece = piece.strip()
        if not piece or "(" in piece or ":" in piece:
            continue
        decl = _split_decl(piece)
        if decl is None:
            continue
        type_text, name, arr = decl
        if not name:
            continue
        fields.append((normalize_type(type_text, aliases), name, arr))
    return fields


def _split_top_commas(text: str) -> list:
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def parse_headers(project: Path):
    """Return (protos, structs, public_header_relpaths) for the delivery."""
    protos, structs, headers = [], {}, {}
    aliases = {}
    header_paths = []
    for path in sorted(project.rglob("*.h")):
        rel = path.relative_to(project)
        if any(part.startswith(".") for part in rel.parts):
            continue
        lowered = "/".join(rel.parts).lower()
        if "test" in lowered or "selftest" in lowered:
            continue
        header_paths.append(str(rel))
    for rel in header_paths:
        text = strip_comments((project / rel).read_text(encoding="utf-8", errors="replace"))
        text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        for m in re.finditer(r"typedef\s+enum(?:\s+\w+)?\s*\{[^{}]*\}\s*(\w+)\s*;", text):
            aliases[m.group(1)] = ("int", 0)
        text = re.sub(r"typedef\s+enum(?:\s+\w+)?\s*\{[^{}]*\}\s*\w+\s*;", " ", text)
        text = re.sub(r"enum\s+\w+\s*\{[^{}]*\}\s*;?", " ", text)
        for m in re.finditer(r"typedef\s+struct(?:\s+(\w+))?\s*\{(.*?)\}\s*(\w+)\s*;", text, re.S):
            info = StructInfo(m.group(3), _parse_fields(m.group(2), aliases))
            structs[m.group(3)] = info
            if m.group(1):
                structs.setdefault("struct " + m.group(1), info)
        for m in re.finditer(r"struct\s+(\w+)\s*\{(.*?)\}\s*;", text, re.S):
            structs.setdefault("struct " + m.group(1),
                               StructInfo("struct " + m.group(1), _parse_fields(m.group(2), aliases)))
        text = re.sub(r"typedef\s+struct(?:\s+\w+)?\s*\{.*?\}\s*\w+\s*;", " ", text, flags=re.S)
        text = re.sub(r"struct\s+\w+\s*\{.*?\}\s*;", " ", text, flags=re.S)
        for m in re.finditer(r"typedef\s+([\w\s\*]+?)\s+(\w+)\s*;", text):
            target, alias = m.group(1), m.group(2)
            if "(" in target or alias in aliases:
                continue
            flat = " ".join(target.replace("*", " * ").split())
            tokens = flat.split()
            aliases[alias] = (" ".join(t for t in tokens if t != "*" and t not in _STORAGE),
                              tokens.count("*"))
        for segment in text.split(";"):
            if "(" not in segment or "(*" in segment:
                continue
            m = re.match(r"^(?P<ret>[\w\s\*]+?)\s*(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[\s\S]*?)\)\s*$",
                         segment.strip())
            if not m:
                continue
            if any(word in m.group("ret").split() for word in ("typedef", "static", "return")):
                continue
            params, unsupported = [], False
            raw_params = m.group("params").strip()
            if raw_params and raw_params != "void":
                for piece in _split_top_commas(raw_params):
                    decl = _split_decl(piece)
                    if decl is None:
                        unsupported = True
                        continue
                    type_text, name, arr = decl
                    ctype = normalize_type(type_text, aliases)
                    if arr is not None:
                        ctype.ptr += 1
                    params.append(Param(ctype, name))
            proto = Proto(m.group("name"), normalize_type(m.group("ret"), aliases), params, rel)
            proto.unsupported = unsupported
            protos.append(proto)
    return protos, structs, header_paths


# ---------------------------------------------------------------------------
# Constructor discovery: classify prototypes into the eleven RFC 792 kinds
# and bind every parameter to a semantic role.
# ---------------------------------------------------------------------------

_NAME_RULES = (
    ("id", ("id", "identifier", "ident", "icmp_id")),
    ("seq", ("seq", "sequence", "sequence_number", "seqno")),
    ("code", ("code",)),
    ("pointer", ("pointer", "ptr")),
    ("gateway", ("gateway", "gateway_address", "gateway_addr", "gw")),
    ("orig", ("originate", "originate_timestamp", "orig", "otime", "orig_ts", "originate_time")),
    ("recv", ("receive", "receive_timestamp", "recv", "rtime", "recv_ts", "receive_time")),
    ("xmit", ("transmit", "transmit_timestamp", "xmit", "tx", "ttime", "xmit_ts", "transmit_time")),
    ("data_len", ("data_len", "data_length", "payload_len", "payload_length", "body_len",
                  "data_size", "datalen")),
    ("quote_len", ("quote_len", "quotation_len", "quote_length", "quotation_length",
                   "original_len", "orig_len", "quotalen")),
    ("data", ("data", "payload", "body")),
    ("quote", ("quotation", "quote", "original", "orig_packet", "ip_header", "iphdr",
               "ip_datagram", "datagram", "original_datagram", "orig_dgram", "quoted")),
    ("request", ("request", "req", "request_message", "echo_request", "original_request")),
    ("cap", ("cap", "capacity", "bufsize", "buf_size", "out_cap", "out_capacity", "max",
             "maxlen", "max_len", "out_size", "buflen", "buf_len", "out_len", "size")),
    ("out", ("out", "buffer", "buf", "dst", "dest", "output", "out_buf", "outbuf",
             "out_buffer", "wire", "frame", "bytes", "msg_out")),
    ("type", ("type", "message_type", "msg_type", "icmp_type")),
)


def semantic_of_name(name: str):
    lowered = name.lower()
    for sem, words in _NAME_RULES:
        if lowered in words:
            return sem
    for sem, words in _NAME_RULES:
        for word in words:
            if lowered.endswith("_" + word) or lowered.startswith(word + "_"):
                return sem
    return None


def name_kinds(func_name: str) -> set:
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", func_name.lower()) if t]
    joined = "_".join(tokens)
    kinds = set()
    reply = "reply" in joined or "response" in joined or "rep" in tokens

    def has(*words):
        return any(w in joined for w in words)

    if has("echo"):
        kinds.add("echo_reply" if reply else "echo_request")
    if has("unreach"):
        kinds.add("dest_unreach")
    if has("quench"):
        kinds.add("source_quench")
    if has("redirect"):
        kinds.add("redirect")
    if has("time") and has("exceed") or has("ttl_exceed"):
        kinds.add("time_exceeded")
    if (has("param") and has("problem")) or has("paramprob"):
        kinds.add("param_problem")
    if has("timestamp"):
        kinds.add("timestamp_reply" if reply else "timestamp_request")
    if has("information") or "info" in tokens:
        kinds.add("info_reply" if reply else "info_request")
    for token in tokens:
        m = re.fullmatch(r"(?:type|t)?0*(\d{1,2})", token)
        if m and (token.isdigit() or token.startswith(("type", "t"))):
            number = int(m.group(1))
            for kind, value in KIND_TYPES.items():
                if value == number:
                    kinds.add(kind)
    return kinds


def classify_params(proto: Proto, structs: dict) -> bool:
    """Assign a low-level class to every parameter; False when unusable."""
    if proto.unsupported or proto.ret.ptr > 0 or proto.ret.base in ("u64", "unsupported"):
        return False
    if proto.ret.base == "named" and proto.ret.words in structs:
        return False
    for param in proto.params:
        c = param.ctype
        if c.ptr == 0:
            if c.base in ("u8", "u16", "u32", "size", "bool"):
                param.cls = c.base
            elif c.base == "named" and c.words in structs:
                param.cls = "structval"
            elif c.base == "named":
                param.cls = "int"
        elif c.ptr == 1:
            if c.base in ("u8", "void"):
                param.cls = "inbytes" if c.const else "out"
            elif c.base == "size" and not c.const:
                param.cls = "outlen"
            elif c.base == "named" and c.words in structs:
                param.cls = "structptr"
        if param.cls == "unsupported":
            return False
    return True


class BoundCall:
    """One callable construction plan: proto + param-index -> semantic bindings."""

    def __init__(self, proto: Proto, kind: str):
        self.proto, self.kind = proto, kind
        self.bindings = {}        # param index -> semantic
        self.outlen_index = None
        self.struct_index = None  # config-struct parameter (struct/fill+serialize APIs)
        self.struct_fills = {}    # semantic -> ("scalar"|"array"|"ptr", field_name)
        self.struct_by_value = False
        self.fill_proto = None    # fill+serialize APIs: separate fill function
        self.fill_bindings = {}
        self.note = ""

    @property
    def ret_class(self) -> str:
        return {"size": "size", "u32": "int", "bool": "bool", "void": "void"}.get(
            self.proto.ret.base, "int")

    @property
    def has_outlen(self) -> bool:
        return self.outlen_index is not None


def _adjacent_size(params, index, used):
    for candidate in (index + 1, index - 1, index + 2, index - 2):
        if 0 <= candidate < len(params) and candidate not in used and params[candidate].cls == "size":
            return candidate
    return None


def _bind_u16_pair(params, indices):
    named = [semantic_of_name(params[i].name) for i in indices]
    first, second = indices
    if named[0] == "id" and named[1] != "id":
        return [{first: "id", second: "seq"}]
    if named[0] == "seq" and named[1] != "seq":
        return [{first: "seq", second: "id"}]
    if named[1] == "id" and named[0] != "id":
        return [{first: "seq", second: "id"}]
    if named[1] == "seq" and named[0] != "seq":
        return [{first: "id", second: "seq"}]
    # Unnamed pair: try both orders; the wire bytes decide.
    return [{first: "id", second: "seq"}, {first: "seq", second: "id"}]


def _permute(items: list) -> list:
    if len(items) <= 1:
        return [items]
    out = []
    for i, item in enumerate(items):
        for rest in _permute(items[:i] + items[i + 1:]):
            out.append([item] + rest)
    return out


def _bind_u32(params, indices, semantics):
    """Bind semantics to u32 params by name; permute only when names are silent."""
    assignment, free_params, free_sems = {}, [], []
    for i, sem in zip(indices, semantics):
        if semantic_of_name(params[i].name) == sem:
            assignment[i] = sem
        else:
            free_params.append(i)
            free_sems.append(sem)
    if not free_params:
        return [assignment]
    named = [semantic_of_name(params[i].name) for i in free_params]
    if any(named):
        return [dict(assignment, **dict(zip(free_params, free_sems)))]
    out = []
    for perm in _permute(free_sems):
        candidate = dict(assignment)
        candidate.update(zip(free_params, perm))
        out.append(candidate)
    return out


_KIND_PATTERNS = {
    "echo_request": [("plain", (2, 0, 0, 1), {"u32": []})],
    "echo_reply": [("direct", (2, 0, 0, 1), {"u32": []}),
                   ("from_request", (0, 0, 0, 1), {"u32": []})],
    "dest_unreach": [("quote", (0, 1, 0, 1), {"u32": []})],
    "source_quench": [("quote", (0, 0, 0, 1), {"u32": []})],
    "redirect": [("quote", (0, 1, 1, 1), {"u32": ["gateway"]})],
    "time_exceeded": [("quote", (0, 1, 0, 1), {"u32": []})],
    "param_problem": [("quote", (0, 1, 0, 1), {"u32": []})],
    "timestamp_request": [("originate_only", (2, 0, 1, 0), {"u32": ["orig"]}),
                          ("full", (2, 0, 3, 0), {"u32": ["orig", "recv", "xmit"]})],
    "timestamp_reply": [("direct", (2, 0, 3, 0), {"u32": ["orig", "recv", "xmit"]}),
                        ("from_request", (0, 0, 2, 1), {"u32": ["recv", "xmit"]})],
    "info_request": [("plain", (2, 0, 0, 0), {"u32": []})],
    "info_reply": [("direct", (2, 0, 0, 0), {"u32": []}),
                   ("from_request", (0, 0, 0, 1), {"u32": []})],
}


def bind_value_params(params, indices, kind):
    """Bind message-value params for a kind; return (variants, pattern_name, reason)."""
    u16s = [i for i in indices if params[i].cls == "u16"]
    u8s = [i for i in indices if params[i].cls == "u8"]
    u32s = [i for i in indices if params[i].cls in ("u32", "int")]
    inbytes = [i for i in indices if params[i].cls == "inbytes"]
    sizes = [i for i in indices if params[i].cls == "size"]
    others = [i for i in indices
              if params[i].cls not in ("u16", "u8", "u32", "int", "inbytes", "size")]
    # One extra small-integer parameter is tolerated as a message-type selector.
    type_select = None
    if others and all(params[i].cls in ("u8", "int") for i in others) and len(others) == 1:
        type_select = others[0]
        others = []
    if others:
        return None, None, "unsupported parameter types"
    matched = extra = None
    for pattern_name, counts, extra in _KIND_PATTERNS[kind]:
        if counts == (len(u16s), len(u8s), len(u32s), len(inbytes)):
            matched = pattern_name
            break
    if matched is None:
        return None, None, f"parameter shape {(len(u16s), len(u8s), len(u32s), len(inbytes))} " \
                           f"fits no {kind} construction signature"
    used, pairs = set(), {}
    for i in inbytes:
        j = _adjacent_size(params, i, used)
        if j is None:
            return None, None, f"byte input {params[i].name or i} lacks a length parameter"
        used.add(j)
        pairs[i] = j
    if len(sizes) != len(inbytes):
        return None, None, "unpaired size parameter(s)"
    variants = [{}]
    if type_select is not None:
        variants = [{**v, type_select: "type"} for v in variants]
    if inbytes:
        if matched == "from_request":
            sem = "request"
        elif kind.startswith("echo"):
            sem = "data"
        else:
            sem = "quote"
        variants = [{**v, i: sem, pairs[i]: sem + "_len"} for i in inbytes for v in variants]
    if u8s:
        sem = "pointer" if kind == "param_problem" else "code"
        variants = [{**v, u8s[0]: sem} for v in variants]
    if len(u16s) == 2:
        combined = []
        for v in variants:
            for add in _bind_u16_pair(params, u16s):
                merged = dict(v)
                merged.update(add)
                combined.append(merged)
        variants = combined
    if u32s:
        combined = []
        for v in variants:
            for add in _bind_u32(params, u32s, extra["u32"]):
                merged = dict(v)
                merged.update(add)
                combined.append(merged)
        variants = combined
    return variants, matched, None


def bind_flat(proto: Proto, kind: str, structs: dict):
    params = proto.params
    outs = [i for i, p in enumerate(params) if p.cls == "out"]
    if not outs:
        return None, "no output buffer parameter"
    if proto.ret.base == "void" and not any(p.cls == "outlen" for p in params):
        return None, "no serialized-length reporting"
    used = set()
    out_i = outs[0]
    cap_i = _adjacent_size(params, out_i, used)
    if cap_i is None:
        return None, "no output capacity parameter"
    used.update((out_i, cap_i))
    outlens = [i for i, p in enumerate(params) if p.cls == "outlen"]
    outlen_i = outlens[0] if outlens else None
    if outlen_i is not None:
        used.add(outlen_i)
    if any(params[i].cls in ("structptr", "structval") for i in range(len(params)) if i not in used):
        return None, "struct-parameter API"
    rest = [i for i in range(len(params)) if i not in used]
    variants, pattern, reason = bind_value_params(params, rest, kind)
    if variants is None:
        return None, reason
    out = []
    for variant in variants:
        call = BoundCall(proto, kind)
        call.bindings = dict(variant)
        call.bindings[out_i] = "out"
        call.bindings[cap_i] = "cap"
        call.outlen_index = outlen_i
        call.note = pattern
        out.append(call)
    return out, None


_STRUCT_REQUIRED = {
    "echo_request": ["id", "seq", "data"], "echo_reply": ["id", "seq", "data"],
    "dest_unreach": ["code", "quote"], "source_quench": ["quote"],
    "redirect": ["code", "gateway", "quote"], "time_exceeded": ["code", "quote"],
    "param_problem": ["pointer", "quote"],
    "timestamp_request": ["id", "seq", "orig"],
    "timestamp_reply": ["id", "seq", "orig", "recv", "xmit"],
    "info_request": ["id", "seq"], "info_reply": ["id", "seq"],
}


def bind_struct(proto: Proto, kind: str, structs: dict):
    """Bind a constructor that takes a caller-filled config struct."""
    params = proto.params
    outs = [i for i, p in enumerate(params) if p.cls == "out"]
    struct_params = [i for i, p in enumerate(params) if p.cls in ("structptr", "structval")]
    if not outs or not struct_params:
        return None, "no struct config parameter"
    used = set()
    out_i = outs[0]
    cap_i = _adjacent_size(params, out_i, used)
    if cap_i is None:
        return None, "no output capacity parameter"
    used.update((out_i, cap_i))
    outlens = [i for i, p in enumerate(params) if p.cls == "outlen"]
    outlen_i = outlens[0] if outlens else None
    if outlen_i is not None:
        used.add(outlen_i)
    struct_i = struct_params[0]
    used.add(struct_i)
    leftovers = [i for i in range(len(params)) if i not in used]
    if leftovers:
        return None, "unrecognized parameters beside the config struct"
    info = structs.get(params[struct_i].ctype.words)
    if info is None:
        return None, f"unknown struct type {params[struct_i].ctype.words}"
    by_semantic = {}
    for ctype, name, arr in info.fields:
        sem = semantic_of_name(name)
        if sem in ("type", "checksum"):
            continue  # produced by the constructor, never caller input
        if arr is not None and ctype.base == "u8" and ctype.ptr <= 1:
            by_semantic.setdefault("data", ("array", name))
            by_semantic.setdefault("quote", ("array", name))
            continue
        if ctype.ptr == 1 and ctype.base == "u8":
            by_semantic.setdefault(sem or "data", ("ptr", name))
            continue
        if sem is None or ctype.ptr:
            continue
        by_semantic.setdefault(sem, ("scalar", name))
    fills = {}
    for sem in _STRUCT_REQUIRED[kind]:
        if sem not in by_semantic:
            return None, f"config struct lacks a recognizable {sem} field"
        fills[sem] = by_semantic[sem]
    for sem in ("data", "quote"):
        if sem in fills and sem + "_len" in by_semantic:
            fills[sem + "_len"] = by_semantic[sem + "_len"]
    call = BoundCall(proto, kind)
    call.bindings = {out_i: "out", cap_i: "cap"}
    call.outlen_index = outlen_i
    call.struct_index = struct_i
    call.struct_fills = fills
    call.struct_by_value = params[struct_i].cls == "structval"
    call.note = "struct:" + params[struct_i].ctype.words
    return [call], None


def bind_fill_serialize(fill: Proto, ser: Proto, kind: str, structs: dict):
    """Bind a fill(struct, values...) + serialize(out, cap, struct) pair."""
    fill_structs = [i for i, p in enumerate(fill.params)
                    if p.cls == "structptr" and not p.ctype.const]
    if len(fill_structs) != 1 or any(p.cls == "out" for p in fill.params):
        return None, "fill signature not recognized"
    ser_structs = [i for i, p in enumerate(ser.params) if p.cls == "structptr"]
    ser_outs = [i for i, p in enumerate(ser.params) if p.cls == "out"]
    if len(ser_structs) != 1 or not ser_outs:
        return None, "serialize signature not recognized"
    if fill.params[fill_structs[0]].ctype.words != ser.params[ser_structs[0]].ctype.words:
        return None, "fill/serialize struct types differ"
    used = set()
    out_i = ser_outs[0]
    cap_i = _adjacent_size(ser.params, out_i, used)
    if cap_i is None:
        return None, "serialize lacks an output capacity parameter"
    used.update((out_i, cap_i))
    struct_i = ser_structs[0]
    used.add(struct_i)
    outlens = [i for i, p in enumerate(ser.params) if p.cls == "outlen"]
    outlen_i = outlens[0] if outlens else None
    if outlen_i is not None:
        used.add(outlen_i)
    if [i for i in range(len(ser.params)) if i not in used]:
        return None, "unrecognized serialize parameters"
    if ser.ret.base == "void" and outlen_i is None:
        return None, "serialize does not report the serialized length"
    fill_rest = [i for i in range(len(fill.params)) if i != fill_structs[0]]
    variants, pattern, reason = bind_value_params(fill.params, fill_rest, kind)
    if variants is None:
        return None, reason
    out = []
    for variant in variants:
        call = BoundCall(ser, kind)
        call.bindings = {out_i: "out", cap_i: "cap", struct_i: "struct"}
        call.outlen_index = outlen_i
        call.struct_index = struct_i
        call.fill_proto = fill
        call.fill_bindings = dict(variant)
        call.note = "fill+serialize:" + pattern
        out.append(call)
    return out, None


def discover_plans(protos, structs):
    """Return (plans, rejections): plans maps kind -> list of BoundCall."""
    candidates = [p for p in protos if classify_params(p, structs)]
    plans, rejections = {}, {}
    claimed = set()

    def attempt(proto, kind, fill_proto=None):
        if fill_proto is not None:
            bound, reason = bind_fill_serialize(fill_proto, proto, kind, structs)
        else:
            bound, reason = bind_flat(proto, kind, structs)
            if bound is None:
                bound, reason = bind_struct(proto, kind, structs)
        if bound:
            plans.setdefault(kind, []).extend(bound)
            return True
        if reason:
            rejections.setdefault(kind, []).append(f"{proto.name}: {reason}")
        return False

    constructors = [p for p in candidates if any(q.cls == "out" for q in p.params)]
    for proto in constructors:
        for kind in sorted(name_kinds(proto.name)):
            if attempt(proto, kind):
                claimed.add(proto.name)
    # fill+serialize pairs: the fill function carries the per-kind values and
    # the serialize function produces the wire bytes for the same struct type.
    fills = [p for p in candidates
             if not any(q.cls == "out" for q in p.params)
             and sum(1 for q in p.params if q.cls == "structptr" and not q.ctype.const) == 1]
    for ser in constructors:
        ser_structs = [q for q in ser.params if q.cls == "structptr"]
        if len(ser_structs) != 1:
            continue
        for fill in fills:
            if fill.params[[i for i, q in enumerate(fill.params)
                            if q.cls == "structptr" and not q.ctype.const][0]].ctype.words \
                    != ser_structs[0].ctype.words:
                continue
            kinds = name_kinds(fill.name) or name_kinds(ser.name)
            for kind in sorted(kinds):
                if attempt(ser, kind, fill_proto=fill):
                    claimed.add(fill.name)
                    claimed.add(ser.name)
    # Fallback: kinds without any named candidate may claim an unlabeled
    # constructor; the produced Type byte still has to match the expectation.
    unclaimed = [p for p in constructors if p.name not in claimed and not name_kinds(p.name)]
    for kind in KIND_TYPES:
        if kind in plans:
            continue
        for proto in unclaimed[:4]:
            bound, _ = bind_flat(proto, kind, structs)
            if bound is None:
                bound, _ = bind_struct(proto, kind, structs)
            if bound:
                plans.setdefault(kind, []).extend(bound[:6])
    return plans, rejections


# ---------------------------------------------------------------------------
# Probe expansion: turn each scenario x plan variant into one C call block
# whose expected wire bytes are already known to the checker.
# ---------------------------------------------------------------------------

class Probe:
    def __init__(self, key, scenario, group, kind, plan, sem_values, expected, cap, chain=None):
        self.key, self.scenario, self.group, self.kind = key, scenario, group, kind
        self.plan = plan
        self.sem_values = sem_values    # semantic -> C expression (fixtures by array name)
        self.expected = expected
        self.cap = cap                  # capacity argument actually passed
        self.chain = chain              # optional (request_plan, request_sem_values)


def _echo_sem_values(data_name: str, data_len: int, ident: int = FIX_ID, seq: int = FIX_SEQ) -> dict:
    return {"id": f"0x{ident:04X}", "seq": f"0x{seq:04X}",
            "data": data_name, "data_len": str(data_len)}


def _quote_sem_values(quote_name: str, quote_len: int) -> dict:
    return {"quote": quote_name, "quote_len": str(quote_len)}


def _request_chain(request_plans, sem_values):
    """Pick a request constructor for reply-from-wire variants."""
    if not request_plans:
        return None
    plan = request_plans[0]
    if plan.proto.ret.base == "void" and not plan.has_outlen:
        return None
    return plan, sem_values


def expand_probes(plans) -> list:
    probes = []

    def add(scenario, group, kind, plan, sem_values, expected, cap=BUF_SIZE, chain=None):
        sem_values = dict(sem_values)
        sem_values.setdefault("type", str(KIND_TYPES[kind]))
        probes.append(Probe(str(len(probes)), scenario, group, kind, plan,
                            sem_values, expected, cap, chain))

    # -- Echo request: odd / even / empty data (R03, R05, R15) ---------------
    for scenario, data_name, data in (
            ("icmp_echo_request_odd_data", "DATA_ODD", FIX_DATA_ODD),
            ("icmp_echo_request_even_data", "DATA_EVEN", FIX_DATA_EVEN),
            ("icmp_echo_request_empty_data", "DATA_ODD", b"")):
        for plan in plans.get("echo_request", []):
            add(scenario, "data", "echo_request", plan,
                _echo_sem_values(data_name, len(data)), expect_echo(8, data))

    # -- Echo reply preserves id/seq/data and recomputes the checksum (R05) ---
    for plan in plans.get("echo_reply", []):
        if plan.note.endswith("from_request"):
            chain = _request_chain(plans.get("echo_request"),
                                   _echo_sem_values("DATA_ODD", len(FIX_DATA_ODD)))
            if chain is None:
                continue
            add("icmp_echo_reply_preservation", "reply", "echo_reply", plan,
                {"request": "reqbuf", "request_len": "reqlen"},
                expect_echo(0, FIX_DATA_ODD), BUF_SIZE, chain)
        else:
            add("icmp_echo_reply_preservation", "reply", "echo_reply", plan,
                _echo_sem_values("DATA_ODD", len(FIX_DATA_ODD)), expect_echo(0, FIX_DATA_ODD))

    # -- Destination Unreachable: every RFC 792 code (R06) --------------------
    for code in range(6):
        for plan in plans.get("dest_unreach", []):
            sems = _quote_sem_values("QUOTE", len(FIX_QUOTE))
            sems["code"] = str(code)
            add("icmp_destination_unreachable_codes", f"code{code}", "dest_unreach", plan,
                sems, expect_quote_message(3, code, b"\x00" * 4, FIX_QUOTE))

    # -- Quotation carrying an IPv4 header with options (R11) ------------------
    for kind, msg_type, code in (("dest_unreach", 3, 1), ("time_exceeded", 11, 0),
                                 ("param_problem", 12, 0)):
        for plan in plans.get(kind, []):
            sems = _quote_sem_values("QUOTE_OPT", len(FIX_QUOTE_OPT))
            middle = b"\x00" * 4
            if kind == "param_problem":
                sems["pointer"] = str(FIX_POINTER)
                middle = bytes((FIX_POINTER, 0, 0, 0))
            else:
                sems["code"] = str(code)
            add("icmp_error_quotation_with_options", kind, kind, plan, sems,
                expect_quote_message(msg_type, code, middle, FIX_QUOTE_OPT))

    # -- Time Exceeded: both codes (R07) ----------------------------------------
    for code in (0, 1):
        for plan in plans.get("time_exceeded", []):
            sems = _quote_sem_values("QUOTE", len(FIX_QUOTE))
            sems["code"] = str(code)
            add("icmp_time_exceeded_codes", f"code{code}", "time_exceeded", plan,
                sems, expect_quote_message(11, code, b"\x00" * 4, FIX_QUOTE))

    # -- Parameter Problem: caller-supplied pointer, zero unused bytes (R08) -----
    for plan in plans.get("param_problem", []):
        sems = _quote_sem_values("QUOTE", len(FIX_QUOTE))
        sems["pointer"] = str(FIX_POINTER)
        add("icmp_parameter_problem_pointer", "pointer", "param_problem", plan, sems,
            expect_quote_message(12, 0, bytes((FIX_POINTER, 0, 0, 0)), FIX_QUOTE))

    # -- Source Quench (R09) ------------------------------------------------------
    for plan in plans.get("source_quench", []):
        add("icmp_source_quench", "quench", "source_quench", plan,
            _quote_sem_values("QUOTE", len(FIX_QUOTE)),
            expect_quote_message(4, 0, b"\x00" * 4, FIX_QUOTE))

    # -- Redirect: all codes plus the gateway address (R10) ------------------------
    for code in range(4):
        for plan in plans.get("redirect", []):
            sems = _quote_sem_values("QUOTE", len(FIX_QUOTE))
            sems["code"] = str(code)
            sems["gateway"] = f"0x{FIX_GATEWAY:08X}u"
            add("icmp_redirect_gateway", f"code{code}", "redirect", plan, sems,
                expect_quote_message(5, code, FIX_GATEWAY.to_bytes(4, "big"), FIX_QUOTE))

    # -- Timestamp request (R12) ----------------------------------------------------
    for plan in plans.get("timestamp_request", []):
        sems = {"id": f"0x{FIX_ID:04X}", "seq": f"0x{FIX_SEQ:04X}",
                "orig": f"0x{FIX_TS_ORIG:08X}u"}
        if plan.note.endswith("full"):
            sems.update({"recv": f"0x{FIX_TS_RECV:08X}u", "xmit": f"0x{FIX_TS_XMIT:08X}u"})
            expected = expect_timestamp(13, FIX_TS_ORIG, FIX_TS_RECV, FIX_TS_XMIT)
        else:
            expected = expect_timestamp(13, FIX_TS_ORIG, 0, 0)
        add("icmp_timestamp_request", "request", "timestamp_request", plan, sems, expected)

    # -- Timestamp reply: id/seq/originate preserved, receive/transmit set (R12) -----
    for plan in plans.get("timestamp_reply", []):
        if plan.note.endswith("from_request"):
            req_plans = plans.get("timestamp_request")
            chain_sems = {"id": f"0x{FIX_ID:04X}", "seq": f"0x{FIX_SEQ:04X}",
                          "orig": f"0x{FIX_TS_ORIG:08X}u",
                          "recv": "0", "xmit": "0"}
            chain = _request_chain(req_plans, chain_sems)
            if chain is None:
                continue
            add("icmp_timestamp_reply_preservation", "reply", "timestamp_reply", plan,
                {"request": "reqbuf", "request_len": "reqlen",
                 "recv": f"0x{FIX_TS_RECV:08X}u", "xmit": f"0x{FIX_TS_XMIT:08X}u"},
                expect_timestamp(14, FIX_TS_ORIG, FIX_TS_RECV, FIX_TS_XMIT), BUF_SIZE, chain)
        else:
            add("icmp_timestamp_reply_preservation", "reply", "timestamp_reply", plan,
                {"id": f"0x{FIX_ID:04X}", "seq": f"0x{FIX_SEQ:04X}",
                 "orig": f"0x{FIX_TS_ORIG:08X}u", "recv": f"0x{FIX_TS_RECV:08X}u",
                 "xmit": f"0x{FIX_TS_XMIT:08X}u"},
                expect_timestamp(14, FIX_TS_ORIG, FIX_TS_RECV, FIX_TS_XMIT))

    # -- Information request/reply (R13) ----------------------------------------------
    for plan in plans.get("info_request", []):
        add("icmp_information_request_reply", "request", "info_request", plan,
            {"id": f"0x{FIX_ID:04X}", "seq": f"0x{FIX_SEQ:04X}"}, expect_info(15))
    for plan in plans.get("info_reply", []):
        if plan.note.endswith("from_request"):
            chain = _request_chain(plans.get("info_request"),
                                   {"id": f"0x{FIX_ID:04X}", "seq": f"0x{FIX_SEQ:04X}"})
            if chain is None:
                continue
            add("icmp_information_request_reply", "reply", "info_reply", plan,
                {"request": "reqbuf", "request_len": "reqlen"}, expect_info(16),
                BUF_SIZE, chain)
        else:
            add("icmp_information_request_reply", "reply", "info_reply", plan,
                {"id": f"0x{FIX_ID:04X}", "seq": f"0x{FIX_SEQ:04X}"}, expect_info(16))

    # -- Checksum: two payloads differing in the final octet (R04) ----------------------
    for group, data_name, data in (("odd", "DATA_ODD", FIX_DATA_ODD),
                                   ("alt", "DATA_ALT", FIX_DATA_ALT)):
        for plan in plans.get("echo_request", []):
            add("icmp_checksum_recomputation", group, "echo_request", plan,
                _echo_sem_values(data_name, len(data)), expect_echo(8, data))

    # -- Capacity reporting: undersized buffers must be rejected without writes (R14) ----
    for kind, sems, full_len in (
            ("echo_request", _echo_sem_values("DATA_ODD", len(FIX_DATA_ODD)),
             8 + len(FIX_DATA_ODD)),
            ("dest_unreach", dict(_quote_sem_values("QUOTE", len(FIX_QUOTE)), code="0"),
             8 + len(FIX_QUOTE)),
            ("timestamp_request", {"id": f"0x{FIX_ID:04X}", "seq": f"0x{FIX_SEQ:04X}",
                                   "orig": f"0x{FIX_TS_ORIG:08X}u"}, 20)):
        for plan in plans.get(kind, []):
            plan_sems = dict(sems)
            if kind == "timestamp_request" and plan.note.endswith("full"):
                plan_sems.update({"recv": f"0x{FIX_TS_RECV:08X}u",
                                  "xmit": f"0x{FIX_TS_XMIT:08X}u"})
            add("icmp_capacity_reporting", kind, kind, plan, plan_sems, b"", full_len - 1)
    return probes


# ---------------------------------------------------------------------------
# C driver generation, build and probe execution.
# ---------------------------------------------------------------------------

_DRIVER_PREAMBLE = """\
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>

static void print_hex(const uint8_t *bytes, size_t count)
{
    static const char digits[] = "0123456789abcdef";
    size_t i;
    fputs("HEX ", stdout);
    for (i = 0; i < count; i++) {
        putchar(digits[bytes[i] >> 4]);
        putchar(digits[bytes[i] & 0x0F]);
    }
    putchar('\\n');
}

"""


def _c_array(name: str, data: bytes) -> str:
    body = ", ".join(f"0x{b:02X}" for b in data) or "0"
    return f"static uint8_t {name}[] = {{{body}}};\n"


def _value_expr(sem: str, value: str, param) -> str:
    if param.cls == "inbytes":
        return f"(void *){value}"
    return value


def emit_construct(lines, plan, sem_values, buf, capexpr, retvar, outlenvar, cfgname):
    """Append the C statements that invoke one construction plan."""
    if plan.fill_proto is not None:
        struct_type = plan.proto.params[plan.struct_index].ctype.words
        lines.append(f"{struct_type} {cfgname};")
        lines.append(f"memset(&{cfgname}, 0, sizeof {cfgname});")
        fill_args = []
        for i, param in enumerate(plan.fill_proto.params):
            sem = plan.fill_bindings.get(i)
            if sem is None:
                if param.cls == "structptr":
                    fill_args.append(f"&{cfgname}")
                    continue
                raise HarnessError(f"internal: unbound fill parameter {i} in {plan.fill_proto.name}")
            if sem not in sem_values:
                raise HarnessError(f"internal: no value for fill semantic {sem}")
            fill_args.append(_value_expr(sem, sem_values[sem], param))
        lines.append(f"{plan.fill_proto.name}({', '.join(fill_args)});")
    elif plan.struct_index is not None:
        struct_type = plan.proto.params[plan.struct_index].ctype.words
        lines.append(f"{struct_type} {cfgname};")
        lines.append(f"memset(&{cfgname}, 0, sizeof {cfgname});")
        for sem, (style, field) in plan.struct_fills.items():
            if sem not in sem_values:
                raise HarnessError(f"internal: no value for struct field {field}")
            value = sem_values[sem]
            if style == "array":
                length = sem_values.get(sem + "_len", f"sizeof {value}")
                lines.append(f"if (sizeof {cfgname}.{field} >= (size_t)({length}))")
                lines.append(f"    memcpy({cfgname}.{field}, {value}, (size_t)({length}));")
            elif style == "ptr":
                lines.append(f"{cfgname}.{field} = {value};")
            else:
                lines.append(f"{cfgname}.{field} = {value};")
    args = []
    for i, param in enumerate(plan.proto.params):
        if i == plan.outlen_index:
            args.append(f"&{outlenvar}")
        elif i == plan.struct_index:
            args.append(("" if plan.struct_by_value else "&") + cfgname)
            continue
        else:
            sem = plan.bindings.get(i)
            if sem == "out":
                args.append(f"(void *){buf}")
            elif sem == "cap":
                args.append(str(capexpr))
            elif sem is not None and sem in sem_values:
                args.append(_value_expr(sem, sem_values[sem], param))
            else:
                raise HarnessError(f"internal: unbound parameter {i} in {plan.proto.name}")
    call = f"{plan.proto.name}({', '.join(args)})"
    if plan.proto.ret.base == "void":
        lines.append(f"{call};")
    else:
        lines.append(f"{plan.proto.ret.raw} {retvar} = {call};")


def generate_driver(probes, header_rel_paths) -> str:
    out = [_DRIVER_PREAMBLE]
    for rel in header_rel_paths:
        out.append(f'#include "{rel}"\n')
    out.append("\n")
    for name, data in (("DATA_ODD", FIX_DATA_ODD), ("DATA_ALT", FIX_DATA_ALT),
                       ("DATA_EVEN", FIX_DATA_EVEN), ("QUOTE", FIX_QUOTE),
                       ("QUOTE_OPT", FIX_QUOTE_OPT)):
        out.append(_c_array(name, data))
    out.append("\n")
    for probe in probes:
        plan = probe.plan
        lines = [f"static void probe_{probe.key}(void)", "{",
                 f"    uint8_t buf[{BUF_SIZE}];",
                 "    size_t outlen_v = 0;",
                 "    memset(buf, 0xAA, sizeof buf);",
                 f'    puts("BEGIN {probe.key}");']
        inner = []
        if probe.chain is not None:
            req_plan, req_sems = probe.chain
            inner.append("uint8_t reqbuf[256];")
            inner.append("memset(reqbuf, 0, sizeof reqbuf);")
            if req_plan.has_outlen:
                inner.append("size_t reqlen_out = 0;")
            emit_construct(inner, req_plan, req_sems, "reqbuf", "sizeof reqbuf",
                           "reqret", "reqlen_out", "reqcfg")
            if req_plan.has_outlen:
                inner.append("size_t reqlen = reqlen_out;")
            else:
                inner.append("size_t reqlen = (size_t)reqret;")
        emit_construct(inner, plan, probe.sem_values, "buf", probe.cap,
                       "result", "outlen_v", "cfg")
        lines.extend("    " + line for line in inner)
        if plan.proto.ret.base != "void":
            lines.append('    printf("RET %lld\\n", (long long)result);')
        if plan.has_outlen:
            lines.append('    printf("OUTLEN %llu\\n", (unsigned long long)outlen_v);')
        lines.append("    print_hex(buf, sizeof buf);")
        lines.append(f'    puts("END {probe.key}");')
        lines.append("}")
        out.append("\n".join(lines) + "\n\n")
    cases = "\n".join(f"        case {p.key}: probe_{p.key}(); return 0;" for p in probes)
    out.append(f"""int main(int argc, char **argv)
{{
    long index;
    if (argc != 2)
        return 2;
    index = strtol(argv[1], NULL, 10);
    switch (index) {{
{cases}
        default: return 3;
    }}
}}
""")
    return "".join(out)


def find_sources(project: Path, exclude_tests: bool):
    sources = []
    for path in sorted(project.rglob("*.c")):
        rel = path.relative_to(project)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if exclude_tests and "test" in str(rel).lower():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\bint\s+main\s*\(", text):
            continue
        sources.append(path)
    return sources


def compile_driver(project: Path, out: Path, probes, headers, sanitize: bool):
    driver_c = out / "driver.c"
    driver_c.write_text(generate_driver(probes, headers), encoding="utf-8")
    include_dirs = sorted({str((project / h).parent) for h in headers} | {str(project)})
    base_sources = find_sources(project, exclude_tests=False)
    if not base_sources:
        raise HarnessError("no implementation sources found (every .c file defines main)")
    source_sets = [base_sources]
    no_tests = find_sources(project, exclude_tests=True)
    if no_tests and no_tests != base_sources:
        source_sets.append(no_tests)
    flags = ["-Wall", "-Wextra", "-O1"]
    if sanitize:
        flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g"]
    attempts = []
    for std in ("c99", "gnu11"):
        for sources in source_sets:
            argv = (["gcc", f"-std={std}"] + flags
                    + [f"-I{d}" for d in include_dirs]
                    + [str(driver_c)] + [str(s) for s in sources]
                    + ["-o", str(out / "driver")])
            result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
            attempts.append(f"$ {' '.join(argv)}\n{result.stdout}{result.stderr}")
            if result.returncode == 0:
                (out / "driver_build.log").write_text("\n\n".join(attempts), encoding="utf-8")
                return out / "driver"
    (out / "driver_build.log").write_text("\n\n".join(attempts), encoding="utf-8")
    raise HarnessError("could not compile the generated driver against the delivery sources; "
                       "see driver_build.log")


def run_probe(driver: Path, key: str, sanitize: bool) -> dict:
    env = dict(os.environ)
    if sanitize:
        env["ASAN_OPTIONS"] = "detect_leaks=0:halt_on_error=1"
        env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
    try:
        result = subprocess.run([str(driver), str(key)], capture_output=True, text=True,
                                timeout=20, env=env)
    except subprocess.TimeoutExpired:
        return {"complete": False, "error": "probe timed out"}
    output = result.stdout + result.stderr
    block = {"exit": result.returncode, "raw": output[-2000:]}
    if SANITIZER_RE.search(output):
        block["sanitizer"] = True
    m = re.search(r"RET (-?\d+)", output)
    if m:
        block["ret"] = int(m.group(1))
    m = re.search(r"OUTLEN (\d+)", output)
    if m:
        block["outlen"] = int(m.group(1))
    m = re.search(r"HEX ([0-9a-f]+)", output)
    if m:
        block["hex"] = m.group(1)
    block["complete"] = f"END {key}" in output
    return block


# ---------------------------------------------------------------------------
# Judgment: interpret return conventions, compare exact wire bytes.
# ---------------------------------------------------------------------------

def conventions(plan) -> list:
    ret, has = plan.ret_class, plan.has_outlen
    if ret == "size":
        convs = ["ret_len"]
        if has:
            convs += ["status_nz_outlen", "status_z_outlen"]
        return convs
    if ret == "bool":
        return ["status_nz_outlen"] if has else []
    if ret == "void":
        return ["outlen"] if has else []
    convs = []
    if has:
        convs += ["status_z_outlen", "status_nz_outlen"]
    return convs + ["ret_len"]


def interpret(conv: str, block: dict):
    ret, outlen = block.get("ret"), block.get("outlen")
    if conv == "ret_len":
        return None if ret is None else (ret > 0, ret)
    if conv == "status_z_outlen":
        return None if ret is None or outlen is None else (ret == 0, outlen)
    if conv == "status_nz_outlen":
        return None if ret is None or outlen is None else (ret != 0, outlen)
    if conv == "outlen":
        return None if outlen is None else (outlen > 0, outlen)
    return None


def match_probe(probe, block):
    """Return (matched_convention or None, detail)."""
    if not block:
        return None, "probe did not run"
    if block.get("sanitizer"):
        return None, "sanitizer diagnostics during probe: " + block.get("raw", "")[-200:]
    if not block.get("complete"):
        return None, f"probe did not complete (exit {block.get('exit')}, " \
                     f"{block.get('error', 'see raw output')})"
    if "hex" not in block:
        return None, "probe produced no wire bytes"
    buf = bytes.fromhex(block["hex"])
    detail = "constructor reported failure for a valid construction request"
    for conv in conventions(probe.plan):
        reading = interpret(conv, block)
        if reading is None:
            continue
        accepted, length = reading
        if not accepted:
            continue
        if length != len(probe.expected):
            detail = f"reported length {length}, expected {len(probe.expected)}"
            continue
        if buf[:length] != probe.expected:
            offset = next((i for i, pair in enumerate(zip(probe.expected, buf))
                           if pair[0] != pair[1]), min(length, len(probe.expected)))
            want = probe.expected[offset] if offset < len(probe.expected) else None
            got = buf[offset] if offset < len(buf) else None
            detail = (f"byte {offset}: expected {want}, got {got}; "
                      f"expected {probe.expected.hex()}, produced {buf[:length].hex()}")
            continue
        return conv, ""
    return None, detail


class Ctx:
    def __init__(self, project: Path, binary: Path, out: Path, sanitize: bool):
        self.project, self.binary, self.out, self.sanitize = project, binary, out, sanitize
        self.headers = []
        self.plans, self.rejections = {}, {}
        self.probes, self.blocks = [], {}
        self.matched = {}          # kind -> set of (id(plan), convention)
        self.matched_probes = {}   # (scenario, group) -> (probe, convention)
        self.harness_error = None
        self.harness_info = {}


_SCENARIO_KINDS = {
    "icmp_echo_request_odd_data": ["echo_request"],
    "icmp_echo_request_even_data": ["echo_request"],
    "icmp_echo_request_empty_data": ["echo_request"],
    "icmp_echo_reply_preservation": ["echo_reply"],
    "icmp_destination_unreachable_codes": ["dest_unreach"],
    "icmp_error_quotation_with_options": ["dest_unreach", "time_exceeded", "param_problem"],
    "icmp_time_exceeded_codes": ["time_exceeded"],
    "icmp_parameter_problem_pointer": ["param_problem"],
    "icmp_source_quench": ["source_quench"],
    "icmp_redirect_gateway": ["redirect"],
    "icmp_timestamp_request": ["timestamp_request"],
    "icmp_timestamp_reply_preservation": ["timestamp_reply"],
    "icmp_information_request_reply": ["info_request", "info_reply"],
    "icmp_checksum_recomputation": ["echo_request"],
    "icmp_capacity_reporting": ["echo_request", "dest_unreach", "timestamp_request"],
}


def judge_wire(ctx: Ctx, scenario: str):
    probes = [p for p in ctx.probes if p.scenario == scenario]
    if not probes:
        missing = ", ".join(_SCENARIO_KINDS[scenario])
        reasons = []
        for kind in _SCENARIO_KINDS[scenario]:
            reasons += ctx.rejections.get(kind, [])
        detail = f"; rejected candidates: {'; '.join(reasons[:6])}" if reasons else ""
        raise AssertionError(f"no harnessable public constructor for {missing}{detail}")
    groups = {}
    for probe in probes:
        groups.setdefault(probe.group, []).append(probe)
    failures = []
    for group, members in groups.items():
        detail = "no probe"
        for probe in members:
            conv, detail = match_probe(probe, ctx.blocks.get(probe.key, {}))
            if conv:
                ctx.matched.setdefault(probe.kind, set()).add((id(probe.plan), conv))
                ctx.matched_probes[(scenario, group)] = (probe, conv)
                break
        else:
            failures.append(f"[{group}] {detail}")
    if failures:
        raise AssertionError("; ".join(failures))


def judge_checksum(ctx: Ctx):
    scenario = "icmp_checksum_recomputation"
    judge_wire(ctx, scenario)
    checksums = []
    for group in ("odd", "alt"):
        probe, conv = ctx.matched_probes[(scenario, group)]
        block = ctx.blocks[probe.key]
        buf = bytes.fromhex(block["hex"])
        checksums.append(buf[2:4])
    if checksums[0] == checksums[1]:
        raise AssertionError("identical checksums for payloads differing in the final octet")


def judge_capacity(ctx: Ctx):
    probes = [p for p in ctx.probes if p.scenario == "icmp_capacity_reporting"]
    if not probes:
        raise AssertionError("no harnessable public constructor for the capacity checks")
    for kind in _SCENARIO_KINDS["icmp_capacity_reporting"]:
        matched = ctx.matched.get(kind)
        if not matched:
            raise AssertionError(f"no accepted {kind} construction baseline; "
                                 "cannot evaluate capacity reporting")
        for plan_id, conv in matched:
            probe = next((p for p in probes if id(p.plan) == plan_id), None)
            if probe is None:
                continue
            block = ctx.blocks.get(probe.key, {})
            label = f"{kind} ({probe.plan.proto.name})"
            if block.get("sanitizer"):
                raise AssertionError(f"{label}: sanitizer diagnostics for an undersized buffer")
            if not block.get("complete"):
                raise AssertionError(f"{label}: constructor crashed on an undersized buffer")
            reading = interpret(conv, block)
            if reading is None or reading[0]:
                raise AssertionError(
                    f"{label}: insufficient {probe.cap}-byte output storage was not reported")
            buf = bytes.fromhex(block.get("hex", ""))
            if any(byte != 0xAA for byte in buf[probe.cap:]):
                raise AssertionError(
                    f"{label}: wrote beyond the supplied {probe.cap}-byte output capacity")


# ---------------------------------------------------------------------------
# Suite driver.
# ---------------------------------------------------------------------------

def detect_sanitize(binary: Path) -> bool:
    try:
        result = subprocess.run(["nm", "--undefined-only", str(binary)],
                                capture_output=True, text=True, timeout=15)
    except OSError:
        return False
    return "__asan_init" in result.stdout or "__ubsan_handle" in result.stdout


def scenario_selftest(ctx: Ctx):
    if not ctx.binary.is_file():
        raise AssertionError(f"missing executable {ctx.binary}")
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = "detect_leaks=1:halt_on_error=1"
    env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
    try:
        result = subprocess.run([str(ctx.binary)], cwd=str(ctx.project), capture_output=True,
                                text=True, timeout=60, env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise AssertionError("icmp_selftest did not finish within 60 seconds "
                             "(it must exit without waiting for input)")
    output = result.stdout + result.stderr
    if SANITIZER_RE.search(output):
        raise AssertionError("sanitizer diagnostics from icmp_selftest: " + output[-300:])
    if result.returncode != 0:
        raise AssertionError(f"icmp_selftest exited {result.returncode}: {output[-300:]}")
    if not output.strip():
        raise AssertionError("icmp_selftest reported no development-test results")


def prepare(ctx: Ctx):
    """Discover constructors, build the driver, and run every probe once."""
    try:
        if shutil.which("gcc") is None:
            raise HarnessError("gcc is required to harness a message-construction delivery")
        protos, structs, headers = parse_headers(ctx.project)
        ctx.headers = headers
        ctx.plans, ctx.rejections = discover_plans(protos, structs)
        ctx.probes = expand_probes(ctx.plans)
        ctx.harness_info = {
            "headers": headers,
            "plans": {kind: [f"{c.proto.name} ({c.note})" for c in calls]
                      for kind, calls in sorted(ctx.plans.items())},
            "rejections": {kind: reasons[:4] for kind, reasons in sorted(ctx.rejections.items())},
            "probe_count": len(ctx.probes),
        }
        if not ctx.probes:
            return
        driver = compile_driver(ctx.project, ctx.out, ctx.probes, headers, ctx.sanitize)
        ctx.harness_info["driver"] = "built"
        for probe in ctx.probes:
            ctx.blocks[probe.key] = run_probe(driver, probe.key, ctx.sanitize)
        audit = [{
            "key": probe.key, "scenario": probe.scenario, "group": probe.group,
            "kind": probe.kind, "function": probe.plan.proto.name, "note": probe.plan.note,
            "capacity": probe.cap, "expected_hex": probe.expected.hex(),
            "block": {k: v for k, v in ctx.blocks[probe.key].items() if k != "raw"},
        } for probe in ctx.probes]
        (ctx.out / "probes.json").write_text(json.dumps(audit, indent=2) + "\n",
                                             encoding="utf-8")
    except HarnessError as exc:
        ctx.harness_error = str(exc)


def judge_scenario(ctx: Ctx, name: str):
    if ctx.harness_error:
        raise EnvironmentBlocked(ctx.harness_error)
    if name == "icmp_capacity_reporting":
        judge_capacity(ctx)
    elif name == "icmp_checksum_recomputation":
        judge_checksum(ctx)
    else:
        judge_wire(ctx, name)


def run_suite(binary: Path, out: Path, project: Path, *, reference: bool = False) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    if not binary.is_absolute():
        binary = project / binary
    sanitize = detect_sanitize(binary)
    ctx = Ctx(project, binary, out, sanitize)
    results = []

    def record(name, fn):
        started = time.monotonic()
        entry = {"id": name, "status": "not_executed"}
        try:
            fn()
            entry["status"] = "passed"
        except EnvironmentBlocked as exc:
            entry.update(status="environment_blocked", error=str(exc))
        except Exception as exc:
            entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
        results.append(entry)
        print(f"{name}: {entry['status']}"
              + (f" ({entry['error']})" if "error" in entry else ""), flush=True)

    record(TEST_IDS[0], lambda: scenario_selftest(ctx))
    prepare(ctx)
    for name in TEST_IDS[1:]:
        record(name, lambda n=name: judge_scenario(ctx, n))
    report = {
        "passed": all(r["status"] == "passed" for r in results),
        "reference_calibration": reference,
        "required_count": len(TEST_IDS),
        "phase": "sanitize" if sanitize else "normal",
        "harness": ctx.harness_info,
        "scenarios": results,
        "elapsed_seconds": round(sum(r["elapsed_seconds"] for r in results), 3),
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--project", type=Path, default=None,
                        help="delivery project root (default: current directory)")
    parser.add_argument("--reference", action="store_true")
    args = parser.parse_args()
    project = (args.project or Path.cwd()).resolve()
    report = run_suite(args.binary, args.out, project, reference=args.reference)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
