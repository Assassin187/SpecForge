[PROMPT]
Implement function `http_status_text`. Responsibility: 将 HTTP 状态码映射为 RFC 标准状态文本；未知码返回 Unknown

[RELY]
None.

[GUARANTEE]
```c
const char* http_status_text(int code);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入状态码。

**Post-Condition**:
- 返回静态字符串字面量。

**Invariant**:
- 返回字符串为静态字面量，不可被调用方释放
- 未知状态码统一返回 Unknown
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- code 为有效的 HTTP 状态码整数
- 返回指向静态字符串字面量的指针，调用方无需释放

**System Algorithm**:
- switch 映射：200→OK, 201→Created, 400→Bad Request, 403→Forbidden, 404→Not Found, 405→Method Not Allowed, 413→Payload Too Large, 500→Internal Server Error, 501→Not Implemented, default→Unknown。
