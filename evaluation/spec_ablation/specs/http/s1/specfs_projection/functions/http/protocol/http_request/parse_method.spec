[PROMPT]
Implement function `parse_method`. Responsibility: 将方法字符串映射为 http_method_t 枚举：GET→HTTP_GET, HEAD→HTTP_HEAD, POST→HTTP_POST，其他→HTTP_UNKNOWN

[RELY]
None.

[GUARANTEE]
```c
static http_method_t parse_method(const char* s);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入方法字符串 s。

**Post-Condition**:
- 返回对应的 http_method_t 枚举值。

**Invariant**:
- 仅支持 GET/HEAD/POST 三种方法
- 比较为大小写敏感精确匹配
- 未知方法统一返回 HTTP_UNKNOWN
- HTTP_UNKNOWN 只表示合法但不支持的方法，不能被调用方直接等同为 malformed request
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。

**System Algorithm**:
- strcmp 依次比较 GET/HEAD/POST 返回对应枚举值；均不匹配返回 HTTP_UNKNOWN。
