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

## 目标

把「截图 + 指令 → 动作 JSON」的单点推理，扩展为能在真实设备上**自主完成完整任务**的 GUI Agent 系统。

## 许可

继承上游 [Apache License 2.0](./LICENSE)，保留原始版权声明（见 [NOTICE](./NOTICE)）。
