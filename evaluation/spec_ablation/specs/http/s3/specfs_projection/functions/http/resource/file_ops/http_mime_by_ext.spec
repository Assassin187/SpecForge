[PROMPT]
Implement function `http_mime_by_ext`. Responsibility: 根据文件路径扩展名在 24 条目 MIME 表中匹配；未匹配返回 application/octet-stream

[RELY]
- VAR `mime_table`
  role: 静态 MIME 类型映射表
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
const char* http_mime_by_ext(const char* path);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入文件路径。

**Post-Condition**:
- 返回静态 MIME 字符串字面量。

**Invariant**:
- 返回字符串为静态字面量，调用方不得释放
- 未匹配时统一返回 application/octet-stream
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- strrchr 查找最后一个 '.'；遍历 mime_table 用 strcasecmp 匹配扩展名；匹配则返回对应 MIME 字符串。
