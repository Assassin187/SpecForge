[PROMPT]
Implement function `mqtt_session_manager_add`. Responsibility: 向会话管理器添加会话，支持同 ID 会话替换

[RELY]
- STRUCT `struct mqtt_session_manager`
  role: 会话管理器私有结构
```c
struct mqtt_session_manager;
```

- FUNC `mqtt_session_id`
  role: 读取待加入会话 ID
```c
int mqtt_session_id(const mqtt_session_t* s);
```

- FUNC `mqtt_session_destroy`
  role: 替换或扩容失败时释放会话
```c
void mqtt_session_destroy(mqtt_session_t* s);
```

- FUNC `ensure_cap`
  role: 确保数组容量足够
```c
static void ensure_cap(mqtt_session_manager_t* m, size_t need);
```

[GUARANTEE]
```c
void mqtt_session_manager_add(mqtt_session_manager_t* m, mqtt_session_t* s);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 新连接建会话或会话重建时触发
- Precondition: m 与 s 应有效；s 必须可通过 mqtt_session_id 获取 ID
- Input: 输入为管理器 m 和待加入会话 s

**Post-Condition**:
- State Change: 会话集合元素可能新增或替换，count 可能增长
- Response: 无返回值；修改结果体现在容器内部状态

**Invariant**:
- None specified.

**System Algorithm**:
- 先取 sid；若 sid 已存在则销毁旧会话并原位替换；否则 ensure_cap 扩容后追加；扩容失败时销毁 s 防止泄漏
