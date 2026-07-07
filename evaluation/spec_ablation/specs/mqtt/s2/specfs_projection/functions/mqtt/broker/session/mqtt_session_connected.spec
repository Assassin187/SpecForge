[PROMPT]
Implement function `mqtt_session_connected`. Responsibility: 判断会话是否已完成 CONNECT 并进入可交互状态

[RELY]
- STRUCT `struct mqtt_session`
  role: 会话私有实现结构
```c
struct mqtt_session;
```

[GUARANTEE]
```c
bool mqtt_session_connected(const mqtt_session_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 处理 SUBSCRIBE/PUBLISH/PINGREQ 前的会话合法性检查时触发
- Precondition: 允许 s 为空
- Input: 输入为会话指针 s

**Post-Condition**:
- State Change: 只读查询，不改变 session
- Response: 返回 true 表示已连接，false 表示未连接或会话无效

**Invariant**:
- None specified.

**System Algorithm**:
- 若 s 非空返回 s->connected，否则返回 false
