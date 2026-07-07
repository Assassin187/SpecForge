[PROMPT]
Implement function `main`. Responsibility: MQTT broker 可执行程序入口，负责端口解析与 broker 生命周期编排

[RELY]
- STRUCT `mqtt_broker_t`
  role: Broker 运行时对象
```c
typedef struct mqtt_broker mqtt_broker_t;
```

- FUNC `mqtt_broker_create`
  role: 按端口创建 broker 对象
```c
mqtt_broker_t* mqtt_broker_create(uint16_t port);
```

- FUNC `mqtt_broker_start`
  role: 启动 TCP 监听服务
```c
bool mqtt_broker_start(mqtt_broker_t* b);
```

- FUNC `mqtt_broker_run`
  role: 进入 broker 事件循环
```c
void mqtt_broker_run(mqtt_broker_t* b);
```

- FUNC `mqtt_broker_destroy`
  role: 释放 broker 资源
```c
void mqtt_broker_destroy(mqtt_broker_t* b);
```

[GUARANTEE]
```c
int main(int argc, char** argv);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入为进程命令行参数；argv[1] 可选表示 broker 监听端口。

**Post-Condition**:
- 创建或启动失败返回 1；正常完成生命周期后返回 0。

**Invariant**:
- 端口必须保持在 uint16_t 可表达范围内
- broker 创建成功后，任何启动失败路径都必须销毁 broker

**System Algorithm**:
- 默认端口为 1884；当 argv[1] 解析为 1..65535 时覆盖默认端口；随后创建 broker，启动监听，运行事件循环，退出后销毁 broker。
