# Planning Specs-to-Bounded-Repair Compile 最终分析报告

> 完成日期：2026-07-15
> 对象：MQTT minimum broker profile
> 唯一目标：fresh planning 产出的 protocol specs 经 coder 最多 3 轮 bounded repair 后可完成 clean compile
> 最终状态：任务序列已完成；compile feasibility 已证明，frozen-revision stability 未达到

## 1. 最终结论

本任务已经证明目标在当前 pipeline 中**可以实现**，但尚不能证明它能够**稳定实现**：

- Step 8-R1 首次打通了真实 fresh planning → specs → coder generation → 2/3 轮 repair → final clean compile；独立 `make clean && make` 再次通过。
- 冻结 revision 后，Step 10.2 再次打通同一端到端链路，并且只使用 1/3 轮 repair；独立 clean rebuild 再次通过。
- Step 10 的三次固定 fresh 结果为 `compile failed / compile passed / specs materialization failed`。按全部预声明 run 计，端到端 compile success 为 1/3；按已成功进入 coder 的 run 计为 1/2。
- 因此，“planning specs 经 bounded repair 后可编译”已经从不可达变为可达，但当前 revision 仍不具备稳定产出这一结果的能力。

本任务书规定的 Step 0–10、三次冻结验证和本报告均已完成。按照 Step 10 的固定结束规则，不再补跑、不再扩大 prompt，也不再在本任务内继续修改 execution source。

## 2. 分析边界

本报告只判断以下链路：

```text
fresh planning
-> nonempty protocol specs
-> schema validation
-> coder loader
-> coder generation
-> <= 3 rounds bounded repair
-> final clean compile
```

报告不评价 runtime startup、MQTT behavior、interoperability、多协议泛化或对比实验效果。`qualification_passed`、union diagnostics、definition coverage 和 token usage 只用于解释结果，不替代 final clean compile endpoint。

所有 acceptance artifacts 均禁止人工修改 protocol specs 或 generated source。Step 10 使用冻结的 facts、schema、planning/coder、model、compiler、repair budget 和 token ceiling。

## 3. Step 0–8 的完成轨迹

### 3.1 Step 0–6：建立可诊断、可消费的 planning 基础

Step 0–6 已完成并冻结了以下能力：

- 记录 revision、facts、schema、prompt、toolchain 和 artifact hashes；
- 区分 fatal、non-fatal、fresh、resume 与 replay；
- 保留 plan → specs 的 required inventory、type、wire、dependency、callback 和 `runtime_flow` 信息；
- 对 non-fatal semantic failure 保留 machine-readable diagnostics，并尽可能 materialize coder-loadable candidate specs；
- 建立 type/ABI、direct call、callback binding、argument provider、dependency 和 entrypoint 的 deterministic validation；
- 形成 coder generation、initial compile、bounded repair 和 final compile 的统一 evidence chain。

这些步骤解决的是 pipeline 可观测性和 contract preservation，并不单独证明 compile utility。

### 3.2 Step 7：确定性收尾、repair routing 与 token 记账

Step 7 将 unresolved planning state 继续送入安全的 deterministic completion，并修正 linker diagnostics routing、repair candidate rollback 和 model-call token accounting。

Step 7-R1 的 fresh run 完成 11/11 stages，materialize 1/6/33 module/file/function specs，schema 与 coder loader 通过；planning 总量为 771279 tokens，accounting complete，未超过 783804 hard ceiling。Coder 3/3 轮 repair 后仍因 opaque session access contract 不闭合而 compile failed。

这一步的贡献是让 failure 可归因、candidate 可消费、repair 反馈可信；它没有直接达到 compile endpoint。

### 3.3 Step 8：闭合 callback、ABI、access 与 runtime call flow

Step 8 首次 fresh 仍因缺少 session/connection access services 和 lifecycle functions，在 3/3 轮 repair 后 compile failed。

Step 8-R1 对已观察到的 compile-contract defects 做了最小修复：闭合 callback provider/consumer、decoded data provider、routing lifecycle、transport input-buffer access/consume/identity 和 session state-query obligations。没有修改 `prompts.py`；代表性 Stage 4 请求为 16409 → 16516 tokenizer tokens，增幅 0.65%。

Step 8-R1 的结果为：

| 指标 | 结果 |
| --- | --- |
| Planning | 11/11 stages；1/6/34 module/file/function specs |
| Specs | schema validation passed；coder loader passed |
| Planning tokens | 746305；accounting complete；低于 749726 target |
| Initial compile | failed |
| Bounded repair | 2/3 rounds；`compile_succeeded` |
| Final clean compile | passed；独立 clean rebuild 再次 passed |
| Manual edits | 0 |

这一结果首次证明当前 planning specs 可以让 coder 在 bounded repair 内产出可编译代码，因此仅用于重复证明可达性的 Step 9 被取消。

## 4. Step 10 冻结稳定性结果

冻结信息：

```text
HEAD = 5891e2449a0854c5b7fbe274a69088416c1f17f1
execution-source tree SHA256 = 9e6abdf442162bb96065e847dcdcb071d73b2e3c99233f58417bc0f32b6fa4fc
model = qwen3-max-2026-01-23
compiler = cc 12.3.0
max_repair_rounds = 3
planning target/hard ceiling = 749726 / 783804 tokens
```

三次 run 均为独立 fresh，没有 resume、replay、fixed-spec 替代或失败补跑：

| Run | Planning | Specs/loader | Tokens | Repair | Final outcome |
| --- | --- | --- | ---: | ---: | --- |
| Step 10.1 | 11/11 stages；15/16 partitions | 1/5/33；passed | 602042 | 3/3 | compile failed |
| Step 10.2 | 11/11 stages；16/19 partitions | 1/7/38；passed | 727172 | 1/3 | compile passed |
| Step 10.3 | 11/11 stages；17/20 partitions | materialization failed；coder not run | 726377 | 0/3 | not run |

聚合结果：

| Gate | Step 10 结果 |
| --- | ---: |
| Fixed fresh sequence completed | 3/3 |
| 11/11 structured stages executed | 3/3 |
| Planning token accounting complete | 3/3 |
| Planning total <= 749726 target | 3/3 |
| Specs materialized | 2/3 |
| Schema + coder loader passed | 2/3 |
| Coder generation entered | 2/3 |
| Bounded-repair final compile passed | 1/3 overall；1/2 coder-started |
| Manual artifact/source edits | 0（3/3 runs） |

Step 10.1 最终错误集中于 session/router ABI：`mqtt_session_manager_is_connected` 和 `mqtt_session_manager_mark_connected` 的 caller/callee 参数契约不一致，并且 generated code 调用了 specs 未声明的 `mqtt_session_manager_get_connection`。三轮 repair 改善了代码，但没有在预算内恢复一致 contract。

Step 10.2 虽然 `qualification_passed=false`，Stage 9 也因 projected whole-fresh usage 触及 hard ceiling 而采用 non-fatal fallback，但 candidate specs 仍可被 coder 消费，并在一轮 repair 后 compile passed。这说明 qualification 或 Stage 9 完整执行不是当前 compile endpoint 的必要条件，同时也说明 budget preflight 能阻止超限而不必然破坏 compile utility。

Step 10.3 在 structured stages 全部执行后，compiler/materialization 遇到：

```text
RegistryBindingError: unknown_artifact_id: cannot bind
'type:lowering/mqtt/mqtt_transport_t' as ['callback', 'type']
```

manifest 将其记录为 `fatal_reason_code=deterministic_internal_invariant`，没有产生 candidate specs，因此不能把该 run 计为 coder compile failure；它是 planning compiler/registry 的 deterministic materialization failure。

## 5. 已经解决的核心问题

相较于任务开始时长期卡在 Step 7 的状态，本轮工作已经取得四项可复核进展：

1. Non-fatal planning failure 不再默认导致无 specs；最后可序列化 state 能经过 deterministic completion 并保留 diagnostics。
2. Linker diagnostics 可以进入正确的 repair routing；bounded repair 不再因 `no_repairable_sources` 等错误归因提前停止。
3. Callback、runtime access、lifecycle 和 transport/session call obligations 已足以在两个独立 fresh run 中支撑 bounded-repair compile success。
4. Whole-fresh token accounting 和 preflight ceiling 已生效；Step 10 三次运行均在 target 内完成记账，没有通过扩大 prompt 换取成功。

这些改动把目标从“0 次真实 fresh compile success”推进到“至少 2 次独立 fresh compile success”，其中一次发生在最终冻结 revision 下。

## 6. 尚未解决的稳定性瓶颈

### 6.1 Fresh planning 的 contract 形态仍有较大方差

同一冻结输入下，两个可 materialize run 分别产生 1/5/33 和 1/7/38 的 module/file/function specs；第三个 run 又形成了不同的 canonical type lowering 路径。模块边界、type ownership、access service 和 function inventory 的变化会直接改变 coder 接收到的 ABI 与 repair 难度。

### 6.2 Session/router access contract 仍可能不闭合

Step 10.1 表明 planning 仍可能同时给出不一致的 session manager API、topic router argument ordering 和不存在的 connection accessor。Coder repair 能修局部 C code，但很难在三轮内从互相冲突的 specs 中恢复唯一正确的跨模块 contract。

### 6.3 Materialization 对合法 canonical type 组合覆盖不足

Step 10.3 不是模型服务或 token exhaustion，而是 callback/type 双重 binding 触发 registry invariant。只要这一 deterministic failure 仍可由 fresh planning 触发，pipeline 就不能保证每次都把已完成的 structured state 转换为 coder-loadable specs。

### 6.4 Token ceiling 可控，但 Stage 9 仍长期贴近预算边界

Step 10.2 和 Step 10.3 都在下一次 Stage 9 model call 前被 hard-ceiling preflight 阻止。该机制正确控制了总成本；同时，它使最终 candidate 更依赖 fallback 和最后已提交 state。Step 10.2 成功而 Step 10.3 失败，说明预算压力是稳定性风险的放大因素，但现有证据不足以把 materialization failure 单独归因于 token ceiling。

## 7. 对最终目标的判定

| 问题 | 判定 | 依据 |
| --- | --- | --- |
| 当前 pipeline 能否产出可编译代码？ | 能 | Step 8-R1 与 Step 10.2 均在 bounded repair 内通过 clean compile |
| 成功是否依赖人工修改 specs/source？ | 否 | acceptance runs 的 manual edit count 为 0 |
| Token 是否保持在修订后的范围？ | 是 | Step 10 为 602042 / 727172 / 726377，均低于 749726 target |
| 冻结 revision 是否稳定？ | 否 | Step 10 仅 1/3 端到端 compile pass，且 1/3 未 materialize specs |
| 是否应继续当前任务书的长时间循环？ | 否 | 固定三次验证已完成，继续补跑会违反结束规则且不能区分 sampling variance 与 deterministic defect |

因此，本任务的准确收尾表述是：

> Planning specs 经 coder bounded repair 后可编译的 feasibility 已经证明；当前实现仍存在 ABI/access contract variance 与 deterministic materialization 缺口，尚未达到 frozen-revision stability。

## 8. 后续处理建议

当前任务应在此结束，不再追加 fresh 或 prompt 扩充。如果以后开启独立的新任务，优先级应为：

1. 用 protocol-agnostic fixture 复现并修复 `mqtt_transport_t` callback/type dual-binding 的 registry/materialization defect；
2. 对 session manager、router 和 connection access obligations 增加 deterministic cross-contract consistency gate；
3. 在不增加 prompt tokens 的前提下，减少同一 facts 下 module/type ownership 与 API inventory 的变体；
4. 修改通过完整 regressions 后，只做预先声明的最小 fresh 验证，不延续当前任务书或补跑 Step 10。

这些建议属于未来独立工作，不是本任务的未完成项。

## 9. 证据索引

- 任务书与 compact ledger：`agent/planning/PLANNING_RQ1_SEMANTIC_UTILITY_GOAL.md`
- Step 8-R1 planning：`agent/planning/out/mqtt_step8_r1_fresh_20260714_204807/`
- Step 8-R1 coder/repair：`evaluation/planning_utility/out/step8_r1_fresh_20260714_204807/`
- Step 10.1 planning/coder：`agent/planning/out/mqtt_step10_1_fresh_20260714_213220/` 与 `evaluation/planning_utility/out/step10_1_fresh_20260714_213220/`
- Step 10.2 planning/coder：`agent/planning/out/mqtt_step10_2_fresh_20260714_215355/` 与 `evaluation/planning_utility/out/step10_2_fresh_20260714_215355/`
- Step 10.3 planning：`agent/planning/out/mqtt_step10_3_fresh_20260714_221315/`
- Frozen-sequence hashes：`agent/planning/out/step10_sequence_20260714/EVIDENCE.md`

## 10. 最终状态

任务书后续步骤已经全部执行：Step 10 固定验证完成，mixed outcomes 已归档，本总结分析报告完成。任务以“执行完成、feasibility achieved、stability not achieved”结束。
