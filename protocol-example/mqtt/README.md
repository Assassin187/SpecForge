# MQTT 最小 Broker（C 语言版）

这是一个基于 TCP + epoll 的 **MQTT 3.1.1（子集）** Broker 最小实现，主要用于学习/实验：支持客户端连接、订阅、QoS0 发布转发、心跳。

说明：本项目的最终目标只包含 **Broker 服务端实现**。订阅者/发布者客户端不作为本项目生成代码目标，功能验证使用 `mosquitto-clients` 等外部 MQTT 客户端完成。本实现目标是“最小可运行 + 便于读代码”，不覆盖完整 MQTT 规范（QoS1/2、鉴权、Will、持久化等）。

## 已支持功能（MQTT 3.1.1 子集）

- `CONNECT` → `CONNACK(accepted)`
- `SUBSCRIBE(QoS0)` → `SUBACK(Granted QoS0)`
- `PUBLISH(QoS0)`：按订阅过滤器匹配并转发给所有订阅者
- `PINGREQ` → `PINGRESP`
- `DISCONNECT`：关闭连接

### Topic 过滤器

- 支持 `+`（单层通配）与 `#`（多层通配，且必须位于末尾）

## 目录结构

```
mqtt/
├── Makefile
├── main.c                 # broker 入口
├── network/
│   ├── connection.h/.c    # 连接读缓冲 + 写队列
│   └── tcp_server.h/.c    # epoll 事件循环 + accept/读写分发
├── protocol/
│   ├── mqtt_packet.h/.c   # 数据结构 + 释放函数
│   ├── mqtt_decoder.h/.c  # 流式增量解码（回调每个 packet）
│   └── mqtt_encoder.h/.c  # 编码：CONNACK/SUBACK/PINGRESP/PUBLISH(QoS0)
├── broker/
│   ├── broker.h/.c        # 协议处理中心：CONNECT/SUBSCRIBE/PUBLISH/PINGREQ
│   ├── session.h/.c       # 会话状态（fd、client_id、keepalive 等）
│   └── session_manager.h/.c
├── topic/
│   └── topic_tree.h/.c    # filter -> session_id[] 的订阅表 + 通配匹配
└── router/
    └── message_router.h/.c # 订阅维护 + 发布转发
```

## 构建

环境：Linux（使用 `epoll`）、支持 C11 的编译器（gcc/clang）+ `make`。

在本目录下执行：

```bash
cd ~/SpecForge/protocol-example/mqtt
make
```

生成：

- `./mqtt_broker`

清理：

```bash
make clean
```

## 运行

### 启动 broker

```bash
./mqtt_broker [port]
```

- 默认端口：`1884`
- 监听地址：`0.0.0.0:<port>`

### 外部客户端验证

推荐安装 `mosquitto-clients`，使用 `mosquitto_sub` 订阅、`mosquitto_pub` 发布来验证 broker 的服务端行为。验证时请使用 QoS0。

## 快速验证

1) 终端 1：启动 broker

```bash
cd ~/SpecForge/protocol-example/mqtt
./mqtt_broker 1884
```

2) 终端 2：使用外部客户端订阅

```bash
cd ~/SpecForge/protocol-example/mqtt
mosquitto_sub -h 127.0.0.1 -p 1884 -t a/b -q 0
```

3) 终端 3：使用外部客户端发布一条 QoS0 消息

```bash
mosquitto_pub -h 127.0.0.1 -p 1884 -t a/b -q 0 -m "hello"
```

订阅者终端应看到：

```text
hello
```

如果没有安装 `mosquitto-clients`，也可以用 Python（不依赖第三方库）发一个最小 `CONNECT + PUBLISH(QoS0)`；此方式只验证 broker 接收发布报文，完整发布转发验证仍建议使用 `mosquitto_sub` + `mosquitto_pub`：

```bash
python3 - <<'PY'
import socket, struct

def enc_str(s: str) -> bytes:
    b=s.encode('utf-8')
    return struct.pack('!H',len(b))+b

def enc_rl(n:int)->bytes:
    out=b''
    while True:
        d=n%128
        n//=128
        if n>0: d|=0x80
        out+=bytes([d])
        if n==0: break
    return out

def pkt_connect(client_id: str, keepalive=10, clean=True):
    vh = enc_str('MQTT') + bytes([4])
    flags = 0
    if clean: flags |= 0x02
    vh += bytes([flags]) + struct.pack('!H', keepalive)
    payload = enc_str(client_id)
    rem = vh + payload
    return bytes([0x10]) + enc_rl(len(rem)) + rem

def pkt_publish(topic:str, payload:bytes):
    vh = enc_str(topic)
    rem = vh + payload
    return bytes([0x30]) + enc_rl(len(rem)) + rem

s=socket.create_connection(('127.0.0.1',1884))
s.sendall(pkt_connect('py-pub'))

# 读掉 CONNACK（不做严格校验，够用即可）
b1=s.recv(1)
assert b1 and (b1[0]>>4)==2

# remaining length（最多 4 字节）
mul=1; rl=0
while True:
    d=s.recv(1)[0]
    rl += (d & 0x7f) * mul
    mul *= 128
    if d & 0x80 == 0: break
_ = s.recv(rl)

s.sendall(pkt_publish('a/b', b'hello-from-py'))
s.close()
print('published')
PY
```

## 当前限制

- 仅实现 QoS0；未实现 QoS1/2（PUBACK/PUBREC/PUBREL/PUBCOMP）
- 未实现：用户名/密码鉴权、Will、保留消息持久化、离线会话持久化
- 解析/编码仅覆盖本项目用到的报文类型；遇到未知类型可能直接忽略
