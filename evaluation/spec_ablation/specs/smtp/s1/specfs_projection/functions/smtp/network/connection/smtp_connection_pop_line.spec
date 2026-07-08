[PROMPT]
Implement function `smtp_connection_pop_line`. Responsibility: 从输入缓冲弹出一行，去除结尾 LF 和可选 CR，返回调用方拥有的新字符串

[RELY]
- STRUCT `smtp_connection_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_connection smtp_connection_t;
```

[GUARANTEE]
```c
char* smtp_connection_pop_line(smtp_connection_t* conn);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(smtp_connection_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回堆分配行字符串并消费输入缓冲；尚无完整行、参数无效或分配失败返回 NULL。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 维护 conn->in_buf：查找 LF 并 memmove 剩余字节
- 维护 conn->in_len：消费已弹出的行
- 遵循 SMTP_LINE.CRLF_line 映射规则：输入缓冲必须出现 LF 才返回完整行；返回值不包含 CRLF

**System Algorithm**:
- 从输入缓冲弹出一行，去除结尾 LF 和可选 CR，返回调用方拥有的新字符串。
