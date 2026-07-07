[PROMPT]
Implement function `mqtt_session_manager_get`. Responsibility: 按 session_id 查询并返回会话指针

[RELY]
- STRUCT `struct mqtt_session_manager`
  role: 会话管理器私有结构
```c
struct mqtt_session_manager;
```

- FUNC `mqtt_session_id`
  role: 按 session_id 匹配会话
```c
int mqtt_session_id(const mqtt_session_t* s);
```

[GUARANTEE]
```c
mqtt_session_t* mqtt_session_manager_get(mqtt_session_manager_t* m, int session_id);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: on_data 回调等路径按 fd 查找会话时触发
- Precondition: m 可为空；session_id 为待匹配目标
- Input: 输入为管理器 m 与 session_id

**Post-Condition**:
- State Change: 纯查询，不修改容器状态
- Response: 命中返回会话指针，未命中返回 NULL

**Invariant**:
- None specified.

**System Algorithm**:
- 若 m 为空返回 NULL；否则遍历会话数组，比较 mqtt_session_id，命中即返回
