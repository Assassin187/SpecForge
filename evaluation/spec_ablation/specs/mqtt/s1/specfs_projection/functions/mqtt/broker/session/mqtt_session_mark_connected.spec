[PROMPT]
Implement function `mqtt_session_mark_connected`. Responsibility: 在 CONNECT 报文通过后更新会话握手状态与协商参数

[RELY]
- STRUCT `struct mqtt_session`
  role: 会话私有实现结构
```c
struct mqtt_session;
```

[GUARANTEE]
```c
void mqtt_session_mark_connected(mqtt_session_t* s, const char* client_id, bool clean_session, uint16_t keep_alive);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: broker 在处理 CONNECT 报文并准备发送 CONNACK 前触发
- Precondition: s 应为有效会话；client_id 可为空
- Input: 输入为会话指针、client_id、clean_session 与 keep_alive

**Post-Condition**:
- State Change: 会话由未连接转为已连接，并刷新身份与会话参数
- Response: 无返回值；后续逻辑可基于 connected/client_id 继续处理订阅与发布

**Invariant**:
- None specified.

**System Algorithm**:
- 设置 connected=true；释放旧 client_id；若新 client_id 非空则复制保存；更新 clean_session 与 keep_alive
