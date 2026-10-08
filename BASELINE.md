# 唯一运行基线及整理记录

整理日期：2026-10-07（UTC+8）。正式项目：`/home/ljf/SpecForge`，维护分支：`www`。
整理及四协议验证来源：`/home/ljf/.codex/worktrees/2851/SpecForge-Re`。
后续 `www` 以本次清理提交的文件内容为维护基线。

核心生成机制的稳定基线为 **`b8a9130d23cd57c975f2f435bb1b1c2304cfee15`**。以历史 freeze.json
中的 20 个框架/配置/提示词/Schema/测试文件 SHA-256、完整四协议输入及四个验收器核对，
并额外核对独立评测模块。此前未通过验收的工具历史归档实现及其三个测试已移除；
agent.py 和 test_runtime.py 恢复为该提交的原始字节。其他核心文件没有重构或机制调整。

## 三轮四协议统一实验数据

两轮稳定性实验与整理后的一轮验证共同组成论文实验数据集，统一放在
`runs/paper/round_01/`、`round_02/` 和 `round_03/`。共 12 次尝试，
11 次完成生成，7 次同时通过两种独立验收。联合结果与原始位置映射见
[数据集说明](runs/paper/README.md)和[机器汇总](runs/paper/summary.json)。

## 整理时保留的历史三轮记录

| 记录 | 实际版本 | 生成通过 | 独立验收通过 | 当前保留位置 |
|---|---|---:|---:|---|
| R3 | 前序未提交冻结内容；与最终基线四个框架文件不同 | 4/4 | 2/4 | `runs/cost_optimization/20261002T125031Z_json_fields/` |
| B8-1 | b8a9130 | 4/4 | 2/4 | `runs/paper/round_01/` |
| B8-2 | b8a9130 | 3/4 | 2/4 | `runs/paper/round_02/` |

这是原日志所谓“最新三轮”，不是同一版本连续三轮全部通过。没有发现第三轮同版本历史
四协议实验，因此不制造或重新标注缺失的证据。三组全部原始文件保持不变，R3 仅作为
稳定版本形成过程的前序记录，不引入其代码。B8 历史中 MQTT 均 16/16；CoAP 有一次
语义审查响应耗尽；HTTP、SMTP 曾有独立场景失败。这些边界不能因清理而抹掉。

历史 freeze.json、run.json 和报告中的绝对来源路径保持原样，作为原始证据。三轮原始
数据移动到统一目录，原始汇总与审计保存在 `runs/paper/source_records/`；当前位置与
历史位置的映射写入联合索引。不改写旧记录中的命令或哈希，旧编排脚本按原样留作实验记录。

## 完整独立备份

目录：**`/home/ljf/SpecForge-backups/20261007T053821Z/`**。

- `current_workspace.tar.gz`：清理前当前工作区，包括 Token 优化、分析记录和未提交候选。
- `original_history.tar.gz`：`/home/ljf/SpecForge` 全量项目与历史实验，包括 Codex 对照。
- `optimization_history.tar.gz`：`/home/ljf/SpecForge-Re` 全量项目及前两轮 Token 优化。
- 每个归档有逐文件 SHA-256/文件模式/符号链接清单，另存提交、工作区状态和未提交 patch。
- `VERIFIED.json` 记录逐项归档校验结果及压缩文件 SHA-256。清理在全部校验通过后进行。

Git 内部数据库和宿主注入的 `.agents/.aws/.codex` 不属于被清理对象，保留原位置。
同步到正式项目时，再次将待移出或覆盖的文件逐项与备份清单核对；核对及同步记录位于
`/home/ljf/SpecForge-backups/20261007T071859Z_www_sync/`。其他工作区保持原样；正式项目的历史移出由完整备份覆盖。

## 保留、移出和命名

保留所有 `specforge/` 模块、三个提示词、四个 Schema、固定依赖、四协议完整输入、
四套独立验收器和四个原始测试模块。`assets/mqtt_reference/bundle/` 被测试实际引用，
完整保留；不会进入 fresh 生成上下文。

移出正式项目：除上表三轮及本次验证外的全部旧 `runs/` 记录、
`codex_isolated_experiment/`、旧综合报告 `FOUR_PROTOCOL_EXPERIMENT_RESULTS.md`、
根目录两份论文 PDF、`assets/migration.json`、人工规格 `original_specs/`、
旧 `validation.json` 和 `mqtt_reference_facts/`。这些内容均先备份；
不修改其他工作区。Python 缓存同时移出，使用 `PYTHONDONTWRITEBYTECODE=1` 验证。

清理时没有重命名核心目录、案例、测试夹具或既有 CLI。清理后的验证记录现已并入
三轮统一实验数据，现有文档入口随目录整理更新。

原始运行日志和交付目录保持不入库，完整保留在正式项目本地。
远程 `www` 同步代码、必要夹具、基线说明，以及三轮实验的说明、JSON 汇总和 CSV 指标表；
原始运行日志不混入版本化代码树。
历史 Git 提交保留，清理后的分支文件内容作为后续基线。

## 清理后的验证

验证数据：`runs/paper/round_03/`；整理审计和原始汇总：
`runs/paper/source_records/validation/`。

该目录记录清理前后依赖审计、历史复制完整性、现有测试、冻结配置、一轮四协议 fresh
生成、开发构建和隔离的普通/sanitizer 独立验收。仅调用既有有界修复；不追加第二轮，
不向生成阶段提供独立验收反馈，不修改模型产物来制造成功。

状态：已完成唯一一轮验证。生成 **4/4**；全部独立验收 **3/4**；原始框架测试 **52/52**；核心/输入/验收器及历史完整性检查全部通过。

本轮共 66,490,693 Token、1,678 次完成响应，墙钟 3230.011 秒。逐协议生成、构建、行为和异常结果见 [RESULTS.md](runs/paper/source_records/validation/RESULTS.md)，原始账目见同目录 `report.json`、`audit.json` 和 `final-integrity.json`。

本轮 MQTT/CoAP/HTTP 两种独立验收分别为 16/16、10/10、13/13；SMTP 为 12/13。SMTP 的 TURN 回复码仍有已知 502/500 差异，原始 B8 第二轮也有同场景失败。所有历史共同通过场景本轮均通过，未发现清理引入的路径、依赖或核心机制问题。详情及原始记录保留，不在本次清理中修改协议生成机制来修补该行为。
