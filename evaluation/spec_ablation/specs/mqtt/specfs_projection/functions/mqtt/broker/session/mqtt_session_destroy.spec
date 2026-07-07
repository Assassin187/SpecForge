[PROMPT]
Implement function `mqtt_session_destroy`. Responsibility: 销毁会话对象并释放其动态字段

[RELY]
- STRUCT `struct mqtt_session`
  role: 会话私有实现结构
```c
struct mqtt_session;
```

[GUARANTEE]
```c
void mqtt_session_destroy(mqtt_session_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 会话被替换、移除或管理器整体销毁时触发
- Precondition: s 可为空；非空时应指向合法 session 对象
- Input: 输入为待销毁会话 s

**Post-Condition**:
- State Change: 对应会话对象从内存中移除，不再可访问
- Response: 无返回值；完成资源回收

**Invariant**:
- None specified.

**System Algorithm**:
- 若 s 为空直接返回；否则释放 s->client_id 并置空，再释放 s
