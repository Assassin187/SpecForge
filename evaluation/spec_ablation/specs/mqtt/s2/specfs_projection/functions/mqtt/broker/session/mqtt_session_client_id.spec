[PROMPT]
Implement function `mqtt_session_client_id`. Responsibility: 读取会话保存的 MQTT client_id 字符串

[RELY]
- STRUCT `struct mqtt_session`
  role: 会话私有实现结构
```c
struct mqtt_session;
```

[GUARANTEE]
```c
const char* mqtt_session_client_id(const mqtt_session_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 日志打印、会话识别或路由诊断需要 client_id 时触发
- Precondition: 允许 s 为空，允许 client_id 尚未设置
- Input: 输入为会话指针 s

**Post-Condition**:
- State Change: 只读访问，不修改会话状态
- Response: 返回非 NULL 的字符串指针，便于调用方免空指针判断

**Invariant**:
- None specified.

**System Algorithm**:
- 若 s 与 s->client_id 有效则返回该字符串，否则返回空字符串 ""
