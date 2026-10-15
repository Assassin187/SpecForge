# RQ1：SpecForge / APG 的 ICMP 生成输入

协议版本采用 APG 论文使用的 **ICMPv4 / RFC 792（1981 年 9 月）**。
论文 [LLMs Unleashed: Generating Protocol Code from RFC Specifications](https://ojs.aaai.org/index.php/AAAI/article/download/37048/41010)
第 4.1 节明确选择 RFC 792；第 3.3 节的生成对象是消息结构及填充函数，
第 5.1 节和 Figure 3 按 8 类消息统计。请求和响应分别计数时，共 11 个 Type 值。

| 论文的消息类别 | RFC 792 Type | 对应需求 |
|---|---|---|
| Echo | 8 请求、0 响应 | R05 |
| Timestamp | 13 请求、14 响应 | R12 |
| Destination Unreachable | 3 | R06 |
| Redirect | 5 | R10 |
| Time Exceeded（图中 Timeout） | 11 | R07 |
| Information Request/Reply（图中 Info Req） | 15 请求、16 响应 | R13 |
| Parameter Problem | 12 | R08 |
| Source Quench | 4 | R09 |

保留原始 RFC 792 的历史消息类型；不改用 ICMPv6，也不叠加后续 RFC 的扩展。
本次先准备消息构造输入，便于测试 SpecForge 当前的生成效果。
论文第 5.1 节另外进行了 ping 互通；这里的离线开发自测不能算作该互通结果。

## 输入文件

- `TASK.md`：生成任务和运行约定。
- `REQUIREMENTS.md`：消息构造范围、15 条可追踪需求及开发自测要求。
- `spec/rfc792.txt`：完整原始规范，不做摘要、摘选或改写。
- `icmp_check.py`：独立行为验收套件（见下文"独立验收套件"），**不是**生成输入。
- `calibration/`：验收套件自身的标定资产（参考实现与变异体），**不是**生成输入。

原文来源：[RFC Editor 的 RFC 792](https://www.rfc-editor.org/rfc/rfc792.txt)。
该文件与本地 `/home/ljf/APG/rfc/rfc792.txt` 逐字节一致，共 29,186 字节。
SHA-256：`58714393ded142bacf188d7e8977eef98f4110c4c87ac94595f750df5664c2c6`。
APG 论文公开代码：[Assassin187/APG](https://github.com/Assassin187/APG)。
本地 `icmp2code.py` 使用 `rfc792.txt`；`expriment/gold_spe/icmp_gold.json`
列出上述 11 个 Type。该金标准、已提取的 Guidebook 和参考 C 实现不作为
SpecForge 的生成输入。

## 运行 SpecForge

使用已安装 SpecForge 依赖的 Python 3.11+ 环境，以及现有模型配置。
完整生成需要在运行前设置现有的 `DS_API` 环境变量。
每次 `--out` 必须指定尚不存在的新目录。

```sh
cd /home/ljf/SpecForge
python3 -m specforge run \
  --task expriments/RQ1/icmp/TASK.md \
  --requirements expriments/RQ1/icmp/REQUIREMENTS.md \
  --protocol expriments/RQ1/icmp/spec/rfc792.txt \
  --out expriments/RQ1/icmp/runs/specforge_01
```

如需先检查输入冻结和分块，使用独立输出目录：

```sh
python3 -m specforge run \
  --task expriments/RQ1/icmp/TASK.md \
  --requirements expriments/RQ1/icmp/REQUIREMENTS.md \
  --protocol expriments/RQ1/icmp/spec/rfc792.txt \
  --out expriments/RQ1/icmp/runs/prepare_01 \
  --until prepare
```

`--until prepare` 不调用模型。完整生成使用现有 Facts → Design → Specs →
Code → Verify 流程；结果和用量记录在运行目录中的 `run.json`、`reports/`
和 `logs/`，生成项目在 `project/`。生成后可运行：

```sh
cd /home/ljf/SpecForge/expriments/RQ1/icmp/runs/specforge_01/project
./icmp_selftest
```

`icmp_selftest` 是有限的离线开发测试入口，用来满足 SpecForge 现有的可执行
交付及普通/ASan/UBSan 开发关卡；它不监听网络、不需要端口或 root 权限。

## 独立验收套件

`icmp_check.py` 是与 `evaluation/` 下四个应用层协议验收器同一契约的独立验收器
（`--binary` / `--out`，写 `report.json`，仅依赖 Python 标准库，可直接在
bubblewrap 阶段内运行）。ICMP 任务交付的是消息构造库而非监听服务，因此验收
方式与 socket 交互不同：验收器解析交付项目的公共头文件，发现 11 个 Type 值
各自的构造函数，生成一个 C driver 与交付源码一同编译，以固定哨兵输入调用
构造函数，并把产出的完整报文与验收器内部按 RFC 792 独立计算的期望字节逐字节
比对。接口形状在合理范围内自适应：平直参数、配置结构体、fill+serialize 拆分、
reply 从请求报文构造、`size_t` 长度返回或 status+out-length 约定均可；
无法 harness 时按 `environment_blocked` 报告，不计入通过。

16 个场景覆盖：`icmp_selftest` 构建契约（R01/R15）、Echo 奇/偶/空数据（R05）、
Echo Reply 字段保持与校验和重算（R05）、Destination Unreachable 全部 6 个
Code（R06）、Time Exceeded 两个 Code（R07）、Parameter Problem 指针（R08）、
Source Quench（R09）、Redirect 全部 4 个 Code 及网关地址（R10）、含 IPv4 选项的
引用报文（R11）、Timestamp 请求/应答及 Originate 保持（R12）、Information
Request/Reply（R13）、网络字节序/保留字段归零/奇数长度校验和（R03/R04）、
输出容量不足时的拒绝与越界写入检查（R14）。

对一次完成的生成运行执行：

```sh
python3 -m specforge evaluate \
  --run expriments/RQ1/icmp/runs/specforge_01 \
  --evaluator expriments/RQ1/icmp/icmp_check.py
```

### 套件自身的标定

`calibration/run_calibration.py` 验证验收套件本身的正确性：

- `calibration/reference/`：手工编写的 RFC 792 正确实现（平直参数 API），
  普通与 ASan/UBSan 两阶段各 16/16 场景通过；
- `calibration/reference_alt/`：同一行为的另一种公共 API 风格（status+outlen
  返回、reply 从请求报文构造、fill+serialize 拆分、配置结构体、参数乱序），
  两阶段各 16/16 场景通过；
- 13 个单点变异体（校验和字节序、主机字节序、Reply Type 错误、校验和漏算
  数据、引用截短丢失 IPv4 头、保留字段非零、奇数长度补字节误并入报文、网关
  地址被忽略、Originate Timestamp 未保持、Pointer 差一、容量不足不报错且
  越界写、Time Exceeded Code 被忽略、Information Reply Type 错误），
  全部必须被检出且检出场景与注入缺陷对应。

最近一次标定：18/18 个目标行为符合预期。标定输出（`calibration/output/`）
为可再生中间产物，不保留在目录中；重新运行
`python3 calibration/run_calibration.py` 即可复现，汇总写入
`calibration/output/report.json`。此外校验和例程单独通过 RFC 1071 第 3 节
标准向量（00 01 f2 03 f4 f5 f6 f7 → 0x220d）锚定，并在 bubblewrap 隔离环境
（与 `specforge evaluate` 相同挂载）下以普通和 sanitizer 构建各通过 16/16。

## 对比边界

消息结构和填充逻辑覆盖范围按论文对齐。接口命名和项目布局交给 SpecForge
设计，不预先提供 APG 的结构体或函数签名。
本地 APG 的 `icmp2code.py` 在生成提示中假定 `common.h` 已提供手写校验和函数；
本输入要求 SpecForge 自行生成校验和，以便独立构建。后续比较时应单独说明
这一辅助代码差异；当前输入准备不代表已复现论文的全部实验条件。

同模型生成及独立检查已完成：SpecForge 的完整结果为普通/ASan/UBSan 各 16/16；
在 14 个共同协议场景中，SpecForge 各 14/14，APG 各 5/14。两者均记录了必要的
API/格式适配，不能将 APG 未提供的容量接口及自测程序计入失败。详细结果见
[runs/specforge_01/RESULTS.md](runs/specforge_01/RESULTS.md) 和
[runs/apg_01/RESULTS.md](runs/apg_01/RESULTS.md)。

APG 已克隆至 `APG/`，上游提交为 `6642c58ccd533ce2a1bf4d2dfe33b8dc72016122`。
分类模型直接使用 `/home/ljf/models/all-MiniLM-L6-v2`；此次 ICMP 专用流程不调用它。
同模型复现入口为 `runs/apg_01/reproduce_with_format_adapter.py`，传入一个尚不存在的
运行输出目录即可完整生成。该入口保留 APG 原生提示词及生成/编译修复流程，
并使用 `specforge_01/run.json` 中冻结的 `deepseek-flash` 配置；具体命令及差异
记录在 APG 的结果报告中。
