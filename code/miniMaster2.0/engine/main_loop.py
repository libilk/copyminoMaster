"""顶层 Planner 主循环。

这是 miniMaster 的第一层循环，负责全局调度：
- 让 Planner 判断下一步该做什么；
- 把 Planner 的控制动作交给本地处理器执行；
- 在预算耗尽、用户已回复、任务全完成等条件下停止。

阅读这个文件时，可以把它当成整个 harness 的"总导演"。
"""

from __future__ import annotations

from bootstrap.stage_context import build_stage_context
from domain.types import AgentAction, AgentRuntime
from engine.guards import ConsecutiveActionGuard, build_repeated_action_feedback
from engine.plan_actions import handle_plan_action
from engine.support import (
    LOGGER,
    execute_runtime_tool,
    has_runtime_time_left,
    mark_unfinished_tasks_blocked,
    push_planner_feedback,
)
from llm.prompting.builders import build_plan_prompt
from llm.runner import request_agent_action
from memory.prompt_context import build_plan_prompt_context
from memory.session import SessionMemoryManager

# Planner 在"侦察阶段"可以使用的工具。这些工具都是只读的，不会修改代码。
# 侦察的目的是让 Planner 先看一眼项目结构，再决定如何拆分任务。
PLANNER_RESEARCH_ACTIONS = {"read", "glob", "grep"}


def _build_planner_research_status(remaining_steps: int) -> str:
    """把剩余侦察步数翻译成一段中文指令，塞进 Prompt 里告诉模型。

    这段文本直接决定模型的行为边界：
    - 还有预算时：模型可以继续看文件，但要优先收口
    - 预算用尽时：模型必须立刻做决定，不能再探索
    """
    if remaining_steps > 0:
        return (
            f"当前还可以再做 {remaining_steps} 步只读侦察。"
            "如果你还没有形成项目主要模块的顶层边界清单，优先补齐它；"
            "如果你已经掌握了足够边界信息，就应直接输出控制动作，而不是继续探索。"
        )
    return (
        "规划侦察预算已用尽。"
        "下一步必须直接输出控制动作（如 init_tasks / add_task / split_task / subagent_tool / respond_to_user），"
        "不能继续调用 glob / grep / read。"
    )


def run_plan_step(
    runtime: AgentRuntime,
    iteration: int,
    stage_context: dict,
) -> AgentAction:
    """执行一次 Plan-Agent 决策（主循环里的一轮迭代）。

    这个函数的返回值是一个 AgentAction，告诉主循环"这轮要干什么"。
    典型返回值的 tool 字段可能是：
    - "init_tasks"    → 首次创建任务列表
    - "add_task"      → 往现有列表追加一个任务
    - "split_task"    → 把一个太粗的任务拆成多个子任务
    - "retry_task"    → 复活一个失败/阻塞的任务
    - "subagent_tool" → 派 Executor 去执行某个具体任务
    - "respond_to_user" → 直接回复用户（闲聊、或者任务全部完成）

    函数内部根据"任务面板有没有任务"分成两种模式：

    模式 A：任务面板是空的（第一轮）
    ┌──────────────────────────────────────────┐
    │ 不开放侦察工具。                          │
    │ 模型只能选择：init_tasks 或 respond_to_user │
    │ 强制它先把用户需求落成任务，不许先乱逛文件。  │
    └──────────────────────────────────────────┘

    模式 B：任务面板已有任务（后续轮次）
    ┌──────────────────────────────────────────┐
    │ 允许模型在一个小循环里做"侦察 → 记录 → 再侦察或收口" │
    │ 侦察步数有上限（默认 3 步），用完就必须输出控制动作。 │
    │ 这个 ReAct 循环让 Planner 可以先看代码再决策。      │
    └──────────────────────────────────────────┘
    """
    # ==========================================
    # 准备工作：取出 Planner 阶段的配置
    # ==========================================
    # stage_context 是三个角色（Planner/Executor/Validator）的静态配置字典，
    # 里面存了每个角色的动作白名单、OpenAI tools 定义、行为策略文本等。
    # 这里只取 Planner 的那份。
    role_context = stage_context["planner"]

    # Planner 在这个项目里叫 "Plan-Agent"
    agent_name = role_context["agent_name"]

    # 打印日志：第 N 轮 Planner 迭代开始
    LOGGER.agent_iteration(agent_name, iteration + 1)

    # Planner 每轮最多能侦察几步（默认 3）
    max_research_steps = runtime.max_planner_research_steps

    # 当前有没有任务？—— 用来判断走模式 A 还是模式 B
    has_existing_tasks = bool(runtime.todo_list.get_all_tasks())

    # ==========================================
    # 模式 A：任务面板是空的 — 强制立刻建任务
    # ==========================================
    if not has_existing_tasks:
        # 构造 Prompt 上下文。这里会带上用户原始问题、Planner 工作记忆等。
        memory_context = build_plan_prompt_context(runtime)

        # 往上下文里塞一条强约束：你现在不许侦察，只能建任务或回复用户。
        memory_context["planner_research_status"] = (
            "当前还没有任务，因此本轮不开放规划侦察。"
            "请直接输出初始任务列表，或在明显属于闲聊时直接回复用户。"
        )

        # 把"用户问题 + 空任务面板 + 上下文 + 行为策略"拼成最终 Prompt
        plan_prompt = build_plan_prompt(
            user_query=runtime.user_query,
            tasks=runtime.todo_list.get_all_tasks_payload(),
            memory_context=memory_context,
            # control_policy_text：只允许控制动作（建任务/回用户），不允许侦察
            policy_text=role_context["control_policy_text"],
        )

        # 调用 LLM，拿到模型的结构化动作
        plan_action = request_agent_action(
            prompt=plan_prompt,
            system_prompt=stage_context["system_prompt"],
            # control_actions：只有 init_tasks / respond_to_user 两个选项
            actions=role_context["control_actions"],
            # control_openai_tools：对应的 OpenAI function calling 工具定义
            tools=role_context["control_openai_tools"],
            agent_name=agent_name,
            model_name=runtime.model_name,
            client=runtime.client,
            timeout_seconds=runtime.llm_timeout_seconds,
        )

        # 记录日志：Planner 选了哪个动作、带什么参数
        LOGGER.agent_tool_selection(agent_name, plan_action.tool, plan_action.parameters, icon="📋")
        LOGGER.planner_reason(plan_action, agent_name)

        # 直接把动作返回给主循环，本轮结束
        return plan_action

    # ==========================================
    # 模式 B：已有任务 — 进入"侦察 → 决策"小循环
    # ==========================================
    # 这是一个 ReAct（Reasoning + Acting）模式：
    # Planner 可以先 read/glob/grep 看代码，
    # 每看一步就把结果写进工作记忆，
    # 然后决定"继续看"还是"够了，输出控制动作"。

    # 重复动作防护器：检测 Planner 是不是在同一轮里反复读同一个文件
    planner_action_guard = ConsecutiveActionGuard()

    # 循环次数 = max_research_steps + 1
    # 为什么 +1？因为最后一步即使侦察预算耗尽，也必须输出控制动作。
    # 例如 max_research_steps=3，总共可以跑 4 步：
    #   第1步：还剩3次侦察 → 可以继续看
    #   第2步：还剩2次侦察 → 可以继续看
    #   第3步：还剩1次侦察 → 可以继续看
    #   第4步：还剩0次侦察 → 必须输出控制动作
    for planner_decision_step in range(1, max_research_steps + 2):

        # 如果 Planner 记忆（侦察记录）太长，先压缩旧记录为摘要
        runtime.planner_memory.compact_old_memories()

        # 计算还剩几步侦察预算。
        # 第1步：remaining = max(0, 3 - 0) = 3
        # 第2步：remaining = max(0, 3 - 1) = 2
        # ...
        # 第4步：remaining = max(0, 3 - 3) = 0 → 侦察预算耗尽
        remaining_research_steps = max(0, max_research_steps - (planner_decision_step - 1))

        # 还有侦察预算 → 可以用 read/glob/grep
        can_use_research_tools = remaining_research_steps > 0

        # 根据"有没有侦察预算"，切换可用的工具集：
        # - 有预算：完整工具集 = 侦察工具 + 控制动作
        # - 没预算：只有控制动作（init_tasks/add_task/split_task/retry_task/subagent_tool/respond_to_user）
        available_actions = role_context["actions"] if can_use_research_tools else role_context["control_actions"]
        available_tools = role_context["openai_tools"] if can_use_research_tools else role_context["control_openai_tools"]
        policy_text = role_context["policy_text"] if can_use_research_tools else role_context["control_policy_text"]

        # 构造本轮 Prompt 上下文
        memory_context = build_plan_prompt_context(runtime)
        # 把"还剩 X 步侦察"这条约束写进上下文
        memory_context["planner_research_status"] = _build_planner_research_status(remaining_research_steps)

        # 拼 Prompt
        plan_prompt = build_plan_prompt(
            user_query=runtime.user_query,
            tasks=runtime.todo_list.get_all_tasks_payload(),
            memory_context=memory_context,
            policy_text=policy_text,
        )

        # 调用 LLM
        plan_action = request_agent_action(
            prompt=plan_prompt,
            system_prompt=stage_context["system_prompt"],
            actions=available_actions,
            tools=available_tools,
            agent_name=agent_name,
            model_name=runtime.model_name,
            client=runtime.client,
            timeout_seconds=runtime.llm_timeout_seconds,
        )
        LOGGER.agent_tool_selection(agent_name, plan_action.tool, plan_action.parameters, icon="📋")
        LOGGER.planner_reason(plan_action, agent_name)

        # 给工作记忆分配一个 step 编号。
        # 乘 (max+1)*10 是为了让不同轮次的记忆编号拉开差距，
        # 比如第0轮的第1步 → 0*40+1 = 1
        #     第0轮的第4步 → 0*40+4 = 4
        #     第1轮的第1步 → 1*40+1 = 41
        # 这样看日志时一眼就知道是哪轮哪步。
        planner_memory_step = iteration * ((max_research_steps + 1) * 10) + planner_decision_step

        # ── 情况 1：模型选了侦察动作，但这个动作刚才已经做过了 ──
        # 比如连续两次 read(同一个文件)，说明模型卡住了。
        # 不直接报错，而是把一条"不要重复"的反馈写回工作记忆，
        # 让模型在下一步自己修正。
        if plan_action.tool in PLANNER_RESEARCH_ACTIONS and planner_action_guard.is_repeated(plan_action):
            feedback = build_repeated_action_feedback(
                agent_name,
                plan_action,
                "请不要重复同一条侦察；先基于现有 observation 判断是否应 add_task / split_task / retry_task / subagent_tool，"
                "或改用新的侦察动作来补当前缺口。",
            )
            # 把反馈写入 Planner 记忆（作为 system_feedback 类型的条目）
            push_planner_feedback(runtime, planner_memory_step, feedback)
            LOGGER.warning(feedback, indent="  ")
            # continue → 回到循环开头，让模型在下一步看到这条反馈后纠正
            continue

        # ── 情况 2：模型返回的不是侦察动作，是控制动作 ──
        # 说明 Planner 已经收集够了信息，准备好做决定了。
        # 直接把这个控制动作返回给主循环。
        if plan_action.tool not in PLANNER_RESEARCH_ACTIONS:
            return plan_action

        # ── 情况 3：模型选了侦察动作（read/glob/grep），且不是重复 ──
        # 记录这个动作到防护器，防止下次再选同一个
        planner_action_guard.remember(plan_action)

        # 真正执行这个侦察工具（读文件、搜索代码等）
        result = execute_runtime_tool(runtime, plan_action.tool, plan_action.parameters, log_prefix="  ")

        # 把"我用了什么工具 + 拿到了什么结果"记录到 Planner 工作记忆
        # 这样下一步 Prompt 里就会包含这个 observation
        runtime.planner_memory.add_memory(
            planner_memory_step,
            plan_action.tool,
            plan_action.parameters,
            result,
        )
        LOGGER.tool_result(result, indent="  ", label="规划侦察结果")

    # 如果循环全部跑完（侦察预算耗尽）还没返回控制动作，说明模型失控了。
    # 这是一个硬错误，不应该走到这里。
    raise ValueError("Planner 在侦察预算耗尽后仍未返回控制动作")


def run_main_loop(runtime: AgentRuntime, max_iter: int = 30):
    """运行顶层 Plan-Agent 循环 — 整个程序的"总导演"。

    这是最外层循环，每一轮迭代的流程是：

    ┌─────────────────────────────────────────────┐
    │  1. 检查时间预算是否耗尽                       │
    │     ↓ 没耗尽                                  │
    │  2. 调用 run_plan_step，让 Planner 做一个决策    │
    │     ↓ 返回一个控制动作                          │
    │  3. 调用 handle_plan_action，用本地代码落地这个动作 │
    │     ↓                                        │
    │  4. 打印任务面板快照                            │
    │     ↓                                        │
    │  5. 判断是否需要停止：                          │
    │     - respond_to_user → 停止                  │
    │     - 所有任务 DONE → 停止                     │
    │     - 否则 → 回到步骤 1                        │
    └─────────────────────────────────────────────┘

    停止条件汇总（任一满足即停）：
    - 总运行时间超限
    - Planner 轮数超限
    - 用户收到回复（respond_to_user）
    - 所有任务状态都是 DONE
    """

    # ==========================================
    # 初始化阶段
    # ==========================================

    # 构造三阶段（Planner/Executor/Validator）共享的静态配置。
    # 这里面包含每个角色的 system prompt、动作白名单、OpenAI tool 定义等。
    stage_context = build_stage_context(runtime.tool_service)

    # 取"调用方传入的轮数"和"runtime 默认预算"的较小值，
    # 防止模型无限循环。
    plan_iterations = min(max_iter, runtime.max_plan_iterations)

    # SessionMemoryManager 负责在任务重试时保存跨轮摘要。
    # 比如上一轮 Executor 失败了，这些经验会被归档，下一轮重试时带上。
    session_memory = SessionMemoryManager(runtime=runtime)

    # ==========================================
    # 主循环：最多跑 plan_iterations 轮
    # ==========================================
    for iteration in range(plan_iterations):

        # ── 停止条件 1：总运行时间超限 ──
        # has_runtime_time_left 检查从启动到现在过了多少秒，
        # 是否超过 runtime.max_total_runtime_seconds（默认 600 秒）。
        if not has_runtime_time_left(runtime):
            feedback = (
                f"总运行时间已达到预算上限（{runtime.max_total_runtime_seconds} 秒），"
                "未完成任务已标记为 BLOCKED。"
            )
            # 把所有还在 PENDING/RUNNING 的任务标成 BLOCKED，防止悬空
            mark_unfinished_tasks_blocked(runtime, feedback)
            LOGGER.task_report(runtime.todo_list.get_all_tasks(), "运行预算耗尽")
            break

        # ── 第二步：让 Planner 做一次决策 ──
        # action.tool 可能是：
        #   init_tasks, add_task, split_task, retry_task,
        #   subagent_tool, respond_to_user
        action = run_plan_step(runtime, iteration, stage_context)

        # ── 第三步：用本地代码执行这个控制动作 ──
        # handle_plan_action 内部做的事：
        #   - init_tasks → 往 todo_list 填充初始任务
        #   - add_task   → 往 todo_list 追加一个任务
        #   - split_task → 把一个任务替换成多个子任务
        #   - retry_task → 复活 FAILED/BLOCKED 任务
        #   - subagent_tool → 调用 run_task 让 Executor 真正执行任务
        #   - respond_to_user → 打印回复，返回 True 表示"该停了"
        #
        # 返回值 should_stop: True 表示 respond_to_user 被触发，该停了
        should_stop = handle_plan_action(
            runtime,
            action,
            stage_context,
            session_memory,
        )

        # ── 每轮结束后打印任务面板 ──
        # 方便你在终端看到任务状态的演变：
        #   ☐ 调查登录模块 (PENDING)
        #   ▶ 重构用户模块 (RUNNING)
        #   ✓ 写单元测试 (DONE)
        if runtime.todo_list.get_all_tasks():
            LOGGER.task_snapshot(runtime.todo_list.get_all_tasks())

        # ── 停止条件 2：Planner 选择回复用户 ──
        if should_stop:
            break

        # ── 停止条件 3：所有任务都是 DONE ──
        all_tasks = runtime.todo_list.get_all_tasks()
        if all_tasks and all(task.task_status == "DONE" for task in all_tasks):
            LOGGER.task_report(all_tasks, "所有任务已完成")
            break

    else:
        # ── 停止条件 4：Planner 轮数超限 ──
        # Python 的 for...else：当循环没有被 break 打断时执行 else。
        # 这里意味着 Planner 跑满了 plan_iterations 轮还没自然结束，
        # 把剩下的任务标成 BLOCKED。
        feedback = (
            f"Planner 达到最大规划轮数（{plan_iterations} 轮），"
            "仍有任务未进入终态。"
        )
        mark_unfinished_tasks_blocked(runtime, feedback)
        LOGGER.task_report(runtime.todo_list.get_all_tasks(), "规划预算耗尽")
