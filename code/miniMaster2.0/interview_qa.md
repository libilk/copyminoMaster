# miniMaster 面试问答集

## 一、项目架构与设计理念

**Q1: 这个项目的整体架构**
**是怎样的？**
A1: 项目采用三层嵌套循环
架构，Planner 负责全局
任务规划与调度，Executor
负责任务逐步执行与取证，
Validator 独立复核执行
结果，三层角色分离确保
执行与评估不由同一思路
负责到底。

**Q2: 为什么 main_agent.py**
**故意保持很薄？**
A2: 入口越薄系统的职责
分层越清楚，main() 只
做两件事：调用 bootstrap
装配运行时依赖，然后把
控制权交给 engine 主循环，
业务逻辑全在 engine 层。

**Q3: AgentRuntime 这个**
**dataclass 的设计意图？**
A3: 它是整个运行期的
共享状态容器，把 LLM
client、工具服务、三种
工作记忆、任务面板、skill
仓库等全部打包在一起，
避免各处传递零散参数。

**Q4: bootstrap 层为什么**
**被称为 composition root？**
A4: 它负责把"可以运行"
之前需要的对象组装起来，
包括 LLM client、ToolService、
SkillStore、WorkingMemory
等，把依赖创建与业务
执行彻底分离。

**Q5: 项目中的三层循环**
**分别是什么？**
A5: 第一层是 Planner 主
循环（run_main_loop），
决定全局调度；第二层是
Executor 重试循环（run_task），
驱动单任务执行；第三层
是 Validator 验证循环
（run_validate_loop），
独立复核任务是否完成。

**Q6: 为什么选择 Function**
**Calling 而非文本解析？**
A6: 文本解析容易出现
格式漂移，模型可能输出
不符合约定的 JSON，而
原生 Function Call 由
SDK 保证结构化输出，
消除了格式解析的不确定性。

**Q7: 项目的模块分层**
**是怎样的？**
A7: bootstrap 启动装配层、
domain 领域数据层、
engine 引擎编排层、
llm 模型调用层、
memory 记忆管理层、
tools 工具系统层、
skills 技能仓库层、
utils 工具层，共八层。

**Q8: 这个框架与 LangGraph**
**的核心区别是什么？**
A8: LangGraph 用图结构
定义状态转移，而 miniMaster
用显式循环+状态机实现
编排，代码更直观可读，
适合教学和理解 Agent
内部协作机制。

## 二、多智能体协作与状态管理

**Q9: Planner 的六种控制**
**动作分别是什么？**
A9: init_tasks 初始化
任务列表、add_task 追加
单个任务、split_task
拆分过粗任务、retry_task
复活失败任务、subagent_tool
派 Executor 执行任务、
respond_to_user 直接
回复用户。

**Q10: 任务状态机定义了**
**哪些合法迁移路径？**
A10: bootstrap 可设任意
初始状态；planner 不能
直接改状态；runner 只能
PENDING→RUNNING→
DONE/FAILED/BLOCKED；
retry 只能 FAILED/BLOCKED
→PENDING；system 做
有限兜底迁移。

**Q11: 终态任务为什么**
**默认不能再修改？**
A11: 终态（DONE/FAILED/
BLOCKED）代表任务已经
有了确定结论，随意修改
会破坏调度一致性，只有
retry 通道可以显式恢复。

**Q12: 如何防止 Executor**
**绕过 Validator 自己**
**标记任务完成？**
A12: Executor 只能调用
update_task_conclusion
提交文本结论，真正"标记
DONE"由 runner 层在
Validator 返回"有效"
后才执行，执行层无权
直接改状态为 DONE。

**Q13: Planner 的两种工作**
**模式是什么？**
A13: 模式 A：任务面板
为空时，强制只能选择
init_tasks 或 respond_
to_user，不开放侦察工具；
模式 B：已有任务时进入
ReAct 侦察→决策小循环，
允许有限步数的只读侦察。

**Q14: 为什么 Planner 模式 A**
**不开放侦察工具？**
A14: 任务面板还是空的，
模型应该先把用户需求
落成任务结构，而不是
先去乱逛文件目录，防止
"先看代码再想任务"导致
偏离用户原始意图。

**Q15: _select_next_task**
**的调度策略是什么？**
A15: 当前策略非常简单：
有 RUNNING 优先续跑它，
否则取第一个 PENDING，
这体现了 miniMaster 的
重点在 harness 结构而非
复杂调度算法。

## 三、工作记忆与上下文管理

**Q16: 为什么三个 Agent**
**各自拥有独立的**
**WorkingMemory？**
A16: Planner 关注侦察
结果和项目边界理解，
Executor 关注当前任务
执行轨迹和反馈，
Validator 关注验证证据
和 checklist 覆盖情况，
混在一起会造成上下文
噪音和角色混淆。

**Q17: 工作记忆的三级**
**压缩策略是什么？**
A17: 第一级单次结果按
工具类型差异化截断；
第二级当总字符数超阈值
时触发 compact，把较早
步骤压成摘要；第三级
保留最近 N 步原文，
确保当前上下文连贯。

**Q18: compact_for_memory**
**对不同类型的值做了什么**
**差异化处理？**
A18: dict 只保留前若干
字段并对长文本字段裁剪；
list 只保留前若干项；
str 截成预览片段；嵌套
超过最大深度直接转字符
串预览，防止递归爆炸。

**Q19: system_feedback**
**记忆有什么特殊之处？**
A19: 它不来自真实工具
执行,而是系统注入的纠偏
提示，is_feedback_entry
专门识别它，在渲染时会
提升到"当前最优先响应"
的位置让 Agent 优先处理。

**Q20: SessionMemoryManager**
**的 retry_archive 机制**
**是如何工作的？**
A20: 每次重试前 capture_
retry_archive 把上一轮
结论、失败原因、执行/
验证证据摘要压缩归档，
下一轮 Prompt 带上这些
历史经验，且每个任务
独立维护最多 2 轮归档。

**Q21: 为什么 retry 前**
**要 reset_generator_memory？**
A21: 每轮重试应该有一份
干净的 Generator working
memory，避免把上轮细碎
的试错轨迹原封不动带入
下一轮，干扰模型判断。

**Q22: 三种 Prompt 视图**
**（planner/generator/**
**validation）各侧重什么？**
A22: Planner 视图突出
最新侦察 observation 和
阶段状态；Generator 视图
强调当前最紧急反馈和最近
执行步骤；Validation 视图
区分已验证证据与仍待补充
线索。

## 四、Prompt 工程与模型调用

**Q23: build_plan_prompt**
**中为什么有 39 条**
**instructions？**
A23: 通过大量具体约束
来弥补模型对任务编排
的理解偏差，每一条都是
针对实际测试中观察到的
失败模式写的防御性规则。

**Q24: 为什么 Planner 的**
**instructions 强调**
**"先广后深"策略？**
A24: 防止 Planner 一开始
就钻进少数子目录做深入
侦察，而忽略了项目整体
的顶层模块边界，导致
后续任务拆分遗漏关键
模块。

**Q25: _build_planner_**
**research_status 的**
**作用是什么？**
A25: 把剩余侦察步数翻译
成明确的中文指令塞进
Prompt，有预算时鼓励
收口，预算用尽时强制
必须输出控制动作，不能
继续 read/glob/grep。

**Q26: 为什么 protocol.py**
**中要自行实现 schema**
**校验而不是用 JSON**
**Schema 库？**
A26: 教学项目只覆盖当前
真正需要的几类校验（必填、
类型、枚举），代码更短
更容易读懂，不需要引入
额外依赖。

**Q27: decode_agent_tool_**
**call 做了哪些安全校验？**
A27: 检查 tool_calls 数量
必须为 1；解析 JSON 参数；
校验 tool 名在允许列表中；
校验参数 schema 的类型
和枚举约束；全部通过才
标记为可执行的 AgentAction。

**Q28: llm/runner.py 中**
**tool_choice 降级策略**
**是为什么设计的？**
A28: 某些模型/模式下
tool_choice="required"
可能与服务端约束冲突，
此时降级为 "auto" 提升
在不同后端上的兼容性。

**Q29: 三层 Agent 各自的**
**system prompt 来源**
**是什么？**
A29: 共享同一份 system_
prompt（来自 build_
execution_context_block），
包含工作目录和运行环境
信息，角色差异全部放在
各自的 user prompt 中。

**Q30: 为什么 Prompt 构造**
**与角色策略解耦？**
A30: 不同阶段按 Agent
角色动态注入差异化上下
文，而非加载静态 Prompt，
这样同一份底层状态可以
为不同角色重组出不同
视图。

## 五、工具系统设计

**Q31: 工具系统的抽象**
**层次是怎样的？**
A31: ToolSpec 声明静态
元信息，ToolContext 提供
共享运行时上下文，BaseTool
封装参数校验和结果归一化，
ToolService 负责注册、
渲染和执行调度。

**Q32: BaseTool.execute()**
**的标准流程是什么？**
A32: validate(params)
校验参数 → run(params)
执行具体逻辑 →
normalize_result(result)
统一整理返回格式，三步
收敛成同一控制流。

**Q33: resolve_path 方法**
**为什么先展开用户目录**
**和环境变量？**
A33: 允许模型传入 ~/ 和
%USERPROFILE% 这类
贴近用户习惯的写法，
同时保证所有工具对相对
路径的理解以 workspace
为统一基准。

**Q34: GrepTool 为什么**
**设计了 rg 和 Python**
**两级后端？**
A34: 有 rg 时优先获得
更快速度和更接近开发者
习惯的 ignore 语义；没
rg 时仍能用 Python 保底，
保证项目在任何环境都可
运行。

**Q35: BashTool 如何做**
**跨平台适配？**
A35: 通过 os.name 判断
平台，Windows 使用
PowerShell 执行命令，
类 Unix 使用 bash，同时
把命令 shell 名称暴露给
Prompt 层供模型感知。

**Q36: EditTool 的替换**
**为什么是顺序执行的？**
A36: 后一个 replacement
会基于前一个的结果继续
处理，这样可以用多个
替换规则链式修改文件，
每次可以选择 replace_all
还是只替换第一处。

**Q37: _CONTROL_ACTIONS**
**的作用是什么？**
A37: 在 execute_runtime_
tool 中拦截控制动作
（如 init_tasks、subagent_
tool），防止它们混入
工具执行链，因为控制
动作应该由本地代码处理
而非通过工具系统。

**Q38: ToolService 每次**
**get_tool 都新建实例**
**是为什么？**
A38: 避免工具对象之间
共享可变状态，每次执行
都是干净独立的实例。

## 六、验证与纠错机制

**Q39: Validator 的**
**validate_tool 输出**
**包含哪些字段？**
A39: status（有效/无效）、
reason（判定原因）、
covered_requirements
（已覆盖的完成项列表）、
missing_requirements
（仍缺失的完成项列表）。

**Q40: 验证器如何防止**
**模型"口头上说覆盖了"**
**但实际没分类对？**
A40: 本地代码做确定性
检查：统计 covered 和
missing 的集合，检查
是否有重叠项、是否有
checklist 外的未知条目、
是否有未分类遗漏项。

**Q41: 当 Validator 判定**
**"有效"与"仍有缺项"**
**同时出现时怎么处理？**
A41: 直接视为验证失败，
因为这在逻辑上冲突，
把不一致信息翻译成
Generator 能执行的纠偏
提示写回工作记忆。

**Q42: Validator 最后一步**
**的强制收口机制是什么？**
A42: 最后一步时切换为
只包含 validate_tool 的
动作集合，不允许继续
read/glob/grep/bash，
强制 Validator 基于现有
证据给出判定。

**Q43: 验证失败后的反馈**
**如何驱动 Generator**
**重试？**
A43: 把失败原因和仍缺
完成项翻译成结构化反馈，
通过 push_generator_
feedback 写入 Generator
工作记忆，并用 LOGGER.
retry_focus 高亮下一轮
需要直接回应的焦点。

**Q44: run_task 中重试**
**循环的终止条件有哪些？**
A44: 验证通过→done 返回，
总运行时间超限→blocked
返回，任务意外丢失→abort
返回，重试次数超过 max_
task_retries→标记 FAILED。

## 七、任务管理与调度

**Q45: Task 数据类包含**
**哪些关键字段？**
A45: task_name 任务标题、
goal 目标、scope 边界、
done_when 完成条件、
deliverable 交付物、
task_status 生命周期状态、
task_conclusion 结论、
attempt_count 尝试次数、
last_feedback 最近反馈、
recovery_reason 恢复原因。

**Q46: ToDoList.replace_**
**task_with_subtasks**
**如何防止命名冲突？**
A46: 先收集列表中其他
任务的名称做黑名单，
再用 seen_names 防止
子任务内部重名，用切片
拼接实现原地替换，保留
原任务在面板中的位置。

**Q47: init_tasks 和**
**add_task 的核心区别？**
A47: init_tasks 整体替换
任务面板，在已有未完成
任务时会被本地拦截；
add_task 只追加单个任务，
同名任务静默忽略而非
覆盖。

**Q48: split_task 对**
**子任务有什么约束？**
A48: 子任务名不能与父
任务完全相同、子任务
列表不能为空、子任务名
不能互相重复或与现有
任务名冲突，只允许拆分
PENDING/FAILED/BLOCKED
状态的任务。

**Q49: task_requirements.py**
**的完成清单归一化做了**
**哪些处理？**
A49: 处理多行文本拆分、
行首列表符号去除、行内
序号拆分（①②③等）、
过长逗号串联句子的再次
拆分、去重、最大条目数
限制为 8 条。

**Q50: _handle_terminal_task**
**对不同终态的拦截提示**
**有什么区别？**
A50: DONE 任务提示"无需
再次执行"；FAILED/BLOCKED
任务提示"如需恢复请先
调用 retry_task 并说明
恢复原因"。

## 八、重复动作防护与兜底

**Q51: ConsecutiveActionGuard**
**的滑动窗口机制是什么？**
A51: 跟踪最近 max_history
（默认 4）次动作签名，
当同一签名在窗口中出现的
次数达到 repeat_threshold
（默认 2）时判定为重复。

**Q52: 为什么动作签名要把**
**工具名和参数一起纳入？**
A52: 否则 read(a.py) 和
read(b.py) 会被误判成
同一个动作，必须工具名
加完整参数才能准确判断
是否真的重复。

**Q53: 重复动作被检测到后**
**的处理策略是什么？**
A53: 不直接报错终止，而是
构造一条结构化纠偏反馈
写回工作记忆，让模型在
下一步看到反馈后自己修正。

**Q54: build_generator_stall_**
**feedback 和 build_validation_**
**stall_feedback 的区别？**
A54: 前者针对执行阶段
未收口，引导 Executor
优先判断是否该提交结论；
后者针对验证阶段未收口，
引导 Validator 判断现有
证据是否已覆盖全部条件。

**Q55: 总运行时间超限时**
**的兜底处理是什么？**
A55: mark_unfinished_
tasks_blocked 把全部
未进入终态的任务标记为
BLOCKED，避免悬空状态，
同时记录超限原因到
last_feedback。

**Q56: Planner 侦察预算**
**耗尽后仍未返回控制动作**
**会怎样？**
A56: 抛出 ValueError 硬
错误，因为这意味着模型
在约束条件下失控了，属于
不应出现的异常情况。

## 九、Skill 系统

**Q57: Skill package 的**
**目录结构约定是什么？**
A57: 每个 skill 是一个
独立子目录，必须有 SKILL.md
作为入口，可选包含 scripts/、
references/、assets/ 三个
资源目录，遵循"约定优于
配置"的目录规范。

**Q58: frontmatter 解析**
**为什么不用完整 YAML 库？**
A58: 只支持当前真正需要
的最小子集（name、description、
tags、license），代码更短
更容易读懂，同时显式拒绝
未支持字段保证格式稳定。

**Q59: SkillStore.load_all**
**和 find 的加载策略**
**有什么不同？**
A59: load_all 只加载元数据
不含 instructions 正文，
避免一次性把所有指令全文
塞进上下文；find 命中后
才二次加载完整正文。

**Q60: Executor 如何**
**自主决定是否使用 skill？**
A60: task 本身不绑定 skill，
Executor 从 available_skills
列表中判断是否有某个 skill
对当前 task 有帮助，若有
则先 read 它的 SKILL.md
再按需使用内部资源。

## 十、跨切面问题

**Q61: 项目对 LangSmith 的**
**集成做了什么？**
A61: 用 wrap_openai 包裹
OpenAI client，让后续所有
模型调用都能被 LangSmith
追踪，同时在 call_agent_
function 上加 @traceable
装饰器做函数级追踪。

**Q62: 为什么用 time.monotonic**
**而非 time.time 做超时**
**统计？**
A62: monotonic 时钟不受
系统时间被用户手动修改
的影响，保证超时判断在
任何情况下都是准确的。

**Q63: read_user_query 中**
**为什么不做复杂的交互**
**界面？**
A63: miniMaster 的关注点
在 harness 框架本身，而不是
交互界面，因此保持最简单
的终端 input() 读取方式。

**Q64: 工具结果过大时**
**prepare_memory_result**
**做了哪些兜底处理？**
A64: 先按工具类型差异化
压缩，再检查是否超过硬
上限，若仍超限则只保留
success 信号和一个预览
片段，标记"结果过长已
压缩为摘要"。

**Q65: 这个项目的 use_case**
**目录存放了什么？**
A65: 存放了三个测试用例，
每个用例包含 query.txt
（用户输入）、log.txt
（运行日志）、res.md
（结果输出），用于验证
框架在不同场景下的表现。

**Q66: ConsoleLogger 为什么**
**不维护复杂状态？**
A66: 它只负责把主循环中
的事件输出整理成统一格式，
让 orchestration 层专注于
"发生了什么"而非"怎么
打印出来"，职责单一。

**Q67: 如果你要扩展这个**
**框架支持并行任务执行，**
**你会改哪些模块？**
A67: 修改 engine/runner.py
的 run_task 支持并发调度，
修改 todo.py 增加任务依赖
图，修改 state_machine.py
增加 WAITING 状态，修改
Planner 的 subagent_tool
动作支持批量派发。

**Q68: 为什么 Planner 指令**
**中说"不要额外创建整体**
**分析整个项目的总括任务"？**
A68: 这类任务会吞掉大量
执行步数预算却产出模糊，
应该把大任务拆成具体的、
边界清晰的子任务，每个
任务只解决一个核心问题。
