# GUI-Agent

基于 [Tongyi-MAI/MAI-UI](https://github.com/Tongyi-MAI/MAI-UI)（Apache-2.0）的二次开发项目。

> 本 README 为开发目的占位说明，随开发推进持续补充。

## 当前已公开的能力（上游 MAI-UI 提供）

```
截图 + 指令  →  MAI-UI 模型  →  下一步动作 JSON
```

即：给定一张屏幕截图和一条自然语言指令，模型推理出**单个** GUI 动作（click / long_press / type / swipe / open / drag / system_button 等），输出结构化 JSON。

对应代码：

- `src/mai_grounding_agent.py` —— 单步 grounding：截图 + 指令 → 元素坐标 `[x, y]`
- `src/mai_naivigation_agent.py` —— 带历史的多步动作预测：截图 + 指令 + 历史轨迹 → 下一步动作 JSON

上游只提供了"大脑"的**单步决策**，没有把决策落到真实设备、也没有任务级的闭环。

## 本项目待开发的未公开能力

把单步推理补齐为从"决策"到"真实执行"的完整闭环：

1. **设备接入（Device Connection）**
   接入 Android / iOS / 桌面等真实设备或模拟器，稳定获取实时截图与界面状态（accessibility tree 等）。

2. **动作下发（Action Execution）**
   把模型输出的动作 JSON 翻译并下发为设备上的真实操作——点击、输入、滑动、拖拽、按键、启动 App 等。

3. **App / 系统状态管理（State Management）**
   跟踪应用与系统状态、界面变化，维护环境上下文，为决策提供一致的状态视图。

4. **完整任务编排（Task Orchestration）**
   把单步动作串成多步任务循环：**观察 → 决策 → 执行 → 再观察**，包含任务规划、历史记忆、终止判定、异常恢复、人工介入（ask_user）与工具调用（MCP）。

5. **真实环境 / 评测基础设施（Environment & Evaluation）**
   在真实设备 / 模拟器上跑任务的运行时环境，以及配套的评测、回放与回归体系。

## 开发进度

- **P1 已完成**：Android ADB 的健康检查、截图观测、受限动作下发、动作校验与有界的闭环任务执行。
- **P2 已完成（仅 Android）**：界面稳定等待、可恢复的任务会话、人工确认/接管，以及本地 JSONL 审计回放。
  - `wait` 可组合 `UiStabilityWaiter(backend).wait`，以连续截图指纹和前台 App 一致性为准，并受超时和样本数双重限制。
  - `ask_user` 会暂停同一 `TaskRunner`；调用 `approve_confirmation()` 后可继续，调用 `take_over(reason)` 会取消任务并把设备交还人工。
  - `JsonlAuditRecorder` 写入本地 append-only 事件；`load_replay()` 仅校验和还原事件，绝不重新执行设备动作。
  - 真机 smoke 是显式 opt-in，默认跳过且只读；具体前置条件见 [Android smoke 说明](docs/ANDROID_SMOKE.md)。

P2 不扩展网页、桌面或其他设备平台。

- **P3 已实现（仅 Android，真实跑分待设备/模型前置条件）**：
  - `python -m gui_agent.evaluation.android` 将既有 MAI-UI 真实推理、`TaskRunner`、显式 ADB 设备和 JSONL 审计回放串成一条运行路径。
  - 提供打开设置、返回主屏、稳定等待和人工接管四个低风险任务；每项动作有精确白名单，未允许的模型动作在发送到设备前失败。
  - 每次运行写入独立 `report.json`：场景成功率、原始任务状态、失败原因、稳定超时、人工接管、设备档案和审计回放结论均可复查。
  - 设备档案以只读 ADB 解析 Settings/HOME/前台包，并兼容 Android 14/OEM 的前台窗口输出，可用于后续模拟器与红米的差异对照。
  - 运行命令、真实模型前置条件与红米对照步骤见 [Android P3 端到端评测](docs/ANDROID_P3_EVALUATION.md)。

## 目标

把「截图 + 指令 → 动作 JSON」的单点推理，扩展为能在真实设备上**自主完成完整任务**的 GUI Agent 系统。

## 许可

继承上游 [Apache License 2.0](./LICENSE)，保留原始版权声明（见 [NOTICE](./NOTICE)）。
