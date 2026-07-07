[PROMPT]
Implement function `mqtt_session_manager_destroy`. Responsibility: 销毁会话管理器及其持有的全部会话对象

[RELY]
- STRUCT `struct mqtt_session_manager`
  role: 会话管理器私有结构
```c
struct mqtt_session_manager;
```

- FUNC `mqtt_session_destroy`
  role: 销毁每个会话对象
```c
void mqtt_session_destroy(mqtt_session_t* s);
```

[GUARANTEE]
```c
void mqtt_session_manager_destroy(mqtt_session_manager_t* m);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: broker 销毁时统一回收会话容器资源
- Precondition: m 可为空；非空时应是合法管理器对象
- Input: 输入为会话管理器 m

**Post-Condition**:
- State Change: 容器与其中会话全部失效并释放
- Response: 无返回值；完成资源回收

**Invariant**:
- None specified.

**System Algorithm**:
- m 为空直接返回；否则遍历 [0,count) 调用 mqtt_session_destroy，随后释放 sessions 数组和管理器本体
