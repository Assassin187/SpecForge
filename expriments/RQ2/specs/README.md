# MQTT r002 规格副本

本目录保存 RQ2 已选定的 r002 发布规格，以及按实验 Generic 条件转换的
specfs 格式规格。两份规格来自同一个已回修版本。

| 目录或文件 | 内容 |
| --- | --- |
| `specforge_r002/` | 发布清单及其列出的 99 个文件，包含 8 份文件规格、79 份函数规格和 7 个公共 ABI 头文件；逐字节复制。 |
| `specfs_r002/` | `scope.md`、`project.spec`、项目导航、8 份文件 `.spec`、79 份函数 `.spec` 和相同的 7 个 ABI 头文件。 |
| `specfs_r002_transform.json` | 来源、转换器哈希、两份文件清单与哈希、字段删除记录及源字段到目标分区的映射。 |

原始来源为
[历史 r002 发布清单](/home/ljf/SpecForge/runs/paper/round_03/mqtt_01/specs/r002/bundle.json)，
其 SHA-256 为
`8859611d0d3c14189ac52da28713d18c4990cb756880f32d3923567e7c58b954`。

specfs 版本使用 `[PROMPT]`、`[RELY]`、`[GUARANTEE]`、`[SPECIFICATION]`
分区，由 [现有转换器](/home/ljf/SpecForge/expriments/RQ2/spec_views.py)
确定性生成。依赖进入 RELY，函数签名进入 GUARANTEE，行为正文进入
SPECIFICATION。保留设计、接口及 LOGIC/EVENT 正文；按已确定的 Generic
基线定义删除 TEST_VECTORS、WIRE_MAPPING、CALL_CONTRACTS、溯源元数据和
traceability 审查记录。公共头文件保持原字节。

查看入口分别为
[SpecForge 导航](/home/ljf/SpecForge/expriments/RQ2/specs/specforge_r002/SUMMARY.md)
和 [specfs 导航](/home/ljf/SpecForge/expriments/RQ2/specs/specfs_r002/SUMMARY.md)。

这些是已物化的规格资产。实验 `prepare` 仍按既有入口核对历史来源并构造
各条件的隔离视图，使用同一转换器生成 Generic。复制和转换未调用模型，
也未运行代码生成或协议实验。
