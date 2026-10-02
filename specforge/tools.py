"""Shared native tools and a small, explicit stage filesystem view."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

import jsonschema

from .documents import ROOT, read_json, save_json
from .specs import SCHEMAS

OUTPUT_LIMIT = 8000


def parameters(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOL_PARAMETERS = {
    "list_files": parameters({"path": {"type": "string"}}, ["path"]),
    "read_file": parameters({"path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1},
                              "line_count": {"type": "integer", "minimum": 1, "maximum": 320}}, ["path"]),
    "search": parameters({"path": {"type": "string"}, "keyword": {"type": "string", "minLength": 1}}, ["path", "keyword"]),
    "write_file": parameters({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    "edit_file": parameters({"path": {"type": "string"}, "old": {"type": "string", "minLength": 1},
                              "new": {"type": "string"}}, ["path", "old", "new"]),
    "run_command": parameters({"command": {"type": "string"}, "cwd": {"type": "string"},
                                "timeout": {"type": "integer", "minimum": 1, "maximum": 180}}, ["command"]),
    "check": parameters({}, []),
    "report_spec_gap": parameters({"kind": {"enum": ["interface", "behavior", "scope"]},
                                    "spec_refs": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                                    "problem": {"type": "string", "minLength": 1},
                                    "evidence": {"type": "string", "minLength": 1}},
                                   ["kind", "spec_refs", "problem", "evidence"]),
}
DESCRIPTIONS = {
    "list_files": "List files in an allowed logical directory, recursively.",
    "read_file": "Read text with 1-based line numbers. Default 160 lines; output bounded.",
    "search": "Literal case-insensitive keyword search; returns file paths and 1-based lines.",
    "write_file": "Write an entire UTF-8 file in /work. Public interfaces may be read-only.",
    "edit_file": "Replace exactly one occurrence; zero or multiple occurrences fail.",
    "run_command": "Run bash in an isolated stage view, with timeout. Full output is saved in /logs.",
    "check": "Run the controller-owned current-stage gate. Its result, not an agent claim, determines success.",
    "report_spec_gap": "Submit a concrete Spec defect with an existing log/report as evidence. Stops this coding job.",
}


@dataclass
class ToolRuntime:
    work: Path
    inputs: dict[str, Path]
    logs: Path
    check_callback: Callable[[], dict]
    readonly: list[Path] = field(default_factory=list)
    coder: bool = False
    gap: dict | None = None
    command_count: int = 0

    def __post_init__(self):
        self.work = self.work.resolve()
        self.logs = self.logs.resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        existing = [int(p.stem.split("_")[1]) for p in self.logs.glob("command_*.json")]
        self.command_count = max(existing, default=0)
        self.inputs = {alias: root.resolve() for alias, root in self.inputs.items()}
        self.readonly = [p.resolve() for p in self.readonly]

    @property
    def mounts(self) -> dict[str, Path]:
        return {"/work": self.work, "/logs": self.logs, **self.inputs}

    def resolve(self, path: str, write: bool = False) -> Path:
        logical = PurePosixPath(path)
        if not logical.is_absolute() or ".." in logical.parts:
            raise ValueError("Use an absolute stage path, without '..'")
        for alias, root in self.mounts.items():
            try:
                relative = logical.relative_to(alias)
            except ValueError:
                continue
            result = (root / str(relative)).resolve()
            if not result.is_relative_to(root):
                raise ValueError("Path escapes its stage root")
            if write and (alias != "/work" or any(result == p or result.is_relative_to(p) for p in self.readonly)):
                raise ValueError("Path is read-only in this stage")
            return result
        raise ValueError("Path is outside the stage view")

    def tool_definitions(self) -> list[dict]:
        return [{"type": "function", "function": {"name": name, "description": DESCRIPTIONS[name], "parameters": schema}}
                for name, schema in TOOL_PARAMETERS.items() if self.coder or name != "report_spec_gap"]

    def dispatch(self, name: str, args: dict) -> dict:
        try:
            if name not in TOOL_PARAMETERS or (name == "report_spec_gap" and not self.coder):
                raise ValueError(f"Unknown tool: {name}")
            jsonschema.Draft202012Validator(TOOL_PARAMETERS[name]).validate(args)
            if name == "check":
                return self.check_callback()
            if name == "run_command":
                return self.command(args["command"], args.get("cwd", "/work"), args.get("timeout", 120))
            if name == "report_spec_gap":
                evidence = self.resolve(args["evidence"])
                if not evidence.is_file():
                    raise ValueError("Evidence must be an existing stage log/report file")
                self.gap = args
                save_json(self.logs / "spec_gap.json", args)
                return {"reported": True, "job_stops": True}
            path = self.resolve(args["path"], write=name in ("write_file", "edit_file"))
            if name == "list_files":
                files = [str(PurePosixPath(args["path"]) / p.relative_to(path).as_posix())
                         for p in sorted(path.rglob("*")) if p.is_file() and not p.is_symlink()]
                return {"files": files[:300], "truncated": len(files) > 300}
            if name == "read_file":
                lines = path.read_text(encoding="utf-8").splitlines()
                start, count = args.get("start_line", 1), args.get("line_count", 160)
                selected, size, next_line = [], 0, start
                oversized = None
                for i, line in enumerate(lines[start - 1:start - 1 + count], start):
                    entry = f"L{i:04d}: {line}"
                    if size + len(entry) + 1 > OUTPUT_LIMIT:
                        if not selected:
                            selected.append(entry[:OUTPUT_LIMIT])
                            oversized = i
                            next_line = i + 1
                        break
                    selected.append(entry)
                    size += len(entry) + 1
                    next_line = i + 1
                result = {"text": "\n".join(selected), "total_lines": len(lines),
                          "next_line": next_line if next_line <= len(lines) else None,
                          "truncated": oversized is not None or next_line < min(start + count, len(lines) + 1)}
                if oversized:
                    result["hint"] = f"Line {oversized} exceeds the output limit; use run_command to extract selected JSON fields or text."
                return result
            if name == "search":
                matches = []
                files = sorted(path.rglob("*")) if path.is_dir() else [path]
                for p in files:
                    if not p.is_file() or p.is_symlink():
                        continue
                    try:
                        text = p.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        continue
                    for number, line in enumerate(text.splitlines(), 1):
                        if args["keyword"].casefold() in line.casefold():
                            label = str(PurePosixPath(args["path"]) / p.relative_to(path).as_posix()) if path.is_dir() else args["path"]
                            matches.append({"path": label, "line": number, "text": line[:400]})
                            if len(matches) >= 40:
                                return {"matches": matches, "truncated": True}
                return {"matches": matches, "truncated": False}
            if name == "write_file":
                content = args["content"]
            else:
                content = path.read_text(encoding="utf-8")
                if content.count(args["old"]) != 1:
                    raise ValueError("old text must occur exactly once")
                content = content.replace(args["old"], args["new"], 1)
            if path.suffix == ".json":
                value = json.loads(content)
                if not self.coder and isinstance(value, dict) and value.get("KIND") in SCHEMAS:
                    validator = jsonschema.Draft202012Validator(read_json(ROOT / "schemas" / SCHEMAS[value["KIND"]]))
                    error = next(validator.iter_errors(value), None)
                    if error:
                        field = "/".join(map(str, error.absolute_path))
                        raise ValueError(f"Invalid Spec {args['path']}:{field}: {error.validator}={error.validator_value}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {"written": args["path"]}
        except (OSError, ValueError, jsonschema.ValidationError) as exc:
            return {"error": str(exc)[:OUTPUT_LIMIT]}

    def sandbox_argv(self, command: str, cwd: str = "/work") -> list[str]:
        actual_cwd = self.resolve(cwd)
        if not actual_cwd.is_dir():
            raise ValueError("cwd must be an existing stage directory")
        argv = ["bwrap", "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
                "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
                "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                "--unshare-user", "--unshare-pid", "--unshare-net", "--die-with-parent",
                "--clearenv", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "LC_ALL", "C.UTF-8"]
        # Debian/Ubuntu compiler names are symlinks through this system directory.
        argv += ["--ro-bind", "/etc/alternatives", "/etc/alternatives"]
        for alias, root in self.mounts.items():
            argv += ["--bind" if alias == "/work" else "--ro-bind", str(root), alias]
        for path in self.readonly:
            argv += ["--ro-bind", str(path), "/work/" + path.relative_to(self.work).as_posix()]
        argv += ["--chdir", cwd, "--", "/bin/bash", "-c", command]
        return argv

    def command(self, command: str, cwd: str = "/work", timeout: int = 120) -> dict:
        self.command_count += 1
        stem = f"command_{self.command_count:04d}"
        output_path = self.logs / (stem + ".log")
        started = time.monotonic()
        with output_path.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(self.sandbox_argv(command, cwd), stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True, env={"PATH": "/usr/bin:/bin"})
            timed_out = False
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        text = output_path.read_text(encoding="utf-8", errors="replace")
        result = {"exit_code": process.returncode, "timed_out": timed_out, "elapsed_seconds": round(time.monotonic() - started, 3),
                  "log": "/logs/" + output_path.name, "output": text[:OUTPUT_LIMIT], "truncated": len(text) > OUTPUT_LIMIT}
        save_json(self.logs / (stem + ".json"), {**result, "command": command, "cwd": cwd})
        return result

