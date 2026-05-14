# 当前分支是进行planning重构之前的分支，目前已将specs -> coder 的流程大致完成，但planning的流程较混乱，具体描述如下：

Planning Agent
Preprocessing
初始化日志和输出目录，验证planning模块需要的 protocol_facts.json 和 target_profile.json 是否存在。target_profile 为手工制定，简要描述实现的目标。protocol_facts 即为 Facts Agent 抽取出的协议事实。

Planning IR
复制并规则结构化 facts 文件，当前还将target_profile也拼了进去。相当于给 planning agent 制作标准化的输入，这里是假定上一步抽取的协议事实是松散的信息。
**关键点**：我对其的设计为作为上一阶段facts的完整语义表示，planning agent阶段将使用 IR 作为唯一协议事实来源。

Protocol Profile
对当前的 IR 进行压缩摘要，当前使用的是规则方式进行推理，描述当前要实现的协议边界和大体情况。字段包含protocol_name、target_role（broker、client、server）、transport_shape、required_capabilities等等。
**关键点**：这一步使用的是规则推理，后续面对更多的协议事实应该调整为LLM总结推理比较好，这就需要固定Profile的结构。

Experts Knowledge
专家知识激活，即手工插入包含人类工程经验的条款，例如“出现流传输的特性时，需要在设计中加入缓冲区”。在Profile的字段满足条件时加入后续规划prompt。
**关键点**：这里应该包含人类专家对于实现应用层协议所具备的工程经验。

Architecture & Rank
LLM根据当前的 Protocol_Profile + Experts_Knowledge + Target_Profile（来自 IR ） 生成出三个候选架构，然后用另一个模型进行评分挑选。架构包含对于该协议应该包含：协议包含哪些工程模块、每个模块实现的角色、每个模块所覆盖的职责（即 Protocol_Profile 中的 required_capabilities 字段）。
**关键点**：这里得到整个协议工程的具体模块结构划分

Implement Plan
负责决定“这个协议实现工程应该长什么样”，包括模块边界、能力归属、状态/资源/错误策略、handler 覆盖、依赖图和文件布局。但目前还没有细化到最终 specs 所需的 packet field、ACCESS_PATHS、WIRE_MAPPING、具体 helper 逻辑那一层。
**关键点**：这一步理应作为最后一步由llm进行生成的一步，目前在上一步中做了模块间的划分，当前这一步做了文件的划分，却把文件中函数的规划放在了第六步中，最好是将其整并进当前的Implement Plan阶段，

Basic Plan
从planning_ir、protocol_profile、Architecture中确定性生成各个字段，包括：schema_version、planning_authority、module_budget、target_profile、protocol_name、protocol_description、scope_decisions、module_graph、canonical_types、state_design、handler_matrix、resource_lifecycle、error_strategy、test_plan、traceability、unresolved_questions。随后让LLM去refine部分字段
**关键点**：这一步又是从前面几个步骤中提取信息来生成，我觉得会造成信息冗余的情况，并且这一步没有明显的信息增量，LLM通常不知道要在哪些地方进行提升信息密度，可以在此处进行优化。

Dependency Graph 
这一步生成三级依赖图，包括模块间、文件间和函数间的相互调用关系。LLM使用 selected architecture、module graph、handler matrix、canonical types 和 `implementation_plan.traceability.decision_ids`作为输入，生成策略是 LLM 提议加 deterministic normalization。LLM 输出只作为候选边；规则层会补齐 capability/handler 推导出的必要边，随后过滤掉不存在的模块、自依赖和成环边。最终把结果填回到Implement Plan中。
**关键点**：选择的架构中已经包含了模块图的信息，并且我不是很信任这里推导的依赖图到底对不对。

File Layout
这一步生成每个模块具体划分哪些文件出来以及文件的布局，同样也是LLM使用selected architecture、module graph、handler matrix、canonical types 和 `dependency_graph` 作为输入，然后把结果填回到Implement Plan中。目前设计是每个源文件都应该有其对应头文件，main文件除外，这样设计的原因是我使用这一套方案在coder agent中已经测试通了。
**关键点**：这一步放在了依赖图生成的后面，理论上应该先做文件分布再去规划依赖问题。

Spec Blueprint
这一步生成用于specs确定性编译的蓝图，它将作为最终specs生成的唯一来源。这一步同样依赖planning_ir、implementation_plan、target_profile来进行生成，并且仍采用了规则性的生成，没有LLM参与。
**关键点**：当前这一步中还涉及了每个函数的展开，却是规则性的展开，最好放在上一步去展开。我对Spec Blueprint的预期定位应该是从工程计划到最终specs之间的机械展开，这也意味着当前Implement Plan存在着冗余信息，有些内容是编译specs不需要的。

Complie Specs
根据Blueprint进行确定性编译，生成模块级规范、文件级规范和函数级规范，作为coder agent的直接输入。
