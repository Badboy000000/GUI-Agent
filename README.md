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

- **P3 已完成并在真实模型上跑通（仅 Android）**：
  - `python -m gui_agent.evaluation.android` 将既有 MAI-UI 真实推理、`TaskRunner`、显式 ADB 设备和 JSONL 审计回放串成一条运行路径。
  - 模型连接由 git-ignored 的 `.env`（`MAI_UI_BASE_URL` / `MAI_UI_MODEL_NAME` / `BIGMODEL_API_KEY`）驱动，客户端模型无关：已用智谱 BigModel 远端 OpenAI 兼容视觉模型在 `emulator-5554` 上实测 4/4 场景通过；本地 vLLM 自部署 MAI-UI 权重时只改 `.env` 即可。
  - 提供打开设置、返回主屏、稳定等待和人工接管四个低风险任务；每项动作有精确白名单，未允许的模型动作在发送到设备前失败。
  - 每次运行写入独立 `report.json`：场景成功率、原始任务状态、失败原因、稳定超时、人工接管、设备档案和审计回放结论均可复查。
  - 设备档案以只读 ADB 解析 Settings/HOME/前台包，并兼容 Android 14/OEM 的前台窗口输出，可用于后续模拟器与红米的差异对照。
  - 运行命令、真实模型前置条件与红米对照步骤见 [Android P3 端到端评测](docs/ANDROID_P3_EVALUATION.md)。

- **P4 已完成并在真实设备上跑通（仅 Android）**：通用自主任务——给一条自然语言指令，Agent 在真机上自主完成多步任务。
  - `python -m gui_agent.autonomous.android` 把任意指令、显式 ADB 设备、真实模型推理与 JSONL 审计串成一条运行路径；成功退出码为 0，失败为 1。
  - 动作策略放行 click/long_press/drag/swipe/type/open/system_button/wait/terminate/ask_user，其余动作在任何设备输入前被拒绝；`open` 仅启动白名单内的应用包；坐标越界即拒绝，绝不钳位。
  - 步数受 `--max-steps` 限制（默认 15）；`ask_user` 只暂停并以 `pending_confirmation` 呈现在报告中，绝不自动放行。
  - 运行时键 `coordinate_scale` 选择模型坐标约定（`"thousand"` 为真实 MAI-UI 权重，`"pixels"` 为按原始截图像素作答的模型）；自主入口默认 `"pixels"`，已在智谱 glm 视觉模型上验证。
  - 已在 emulator-5554 + 智谱 glm 上端到端跑通「打开设置 → 搜索 wifi → 终止」；运行前置条件、参数说明与已验证证据见 [Android 自主任务](docs/ANDROID_AUTONOMOUS_TASK.md)。

- **P5 已完成并在双设备上验证（仅 Android）**：UI 树采集能力、坐标自标定与漂移检测。
  - `AdbTransport.dump_ui_hierarchy()` + `parse_ui_tree` 产出扁平节点表（text/resource_id/class_name/content_desc/clickable/bounds/depth）；`AndroidDeviceBackend(capture_ui_tree=True)` 按步填充 `Observation.ui_tree`，采集失败降级为 None，审计观测事件带 `ui_tree_available`；因每步观测约增加 2–3s 延迟，默认关闭。
  - `--coordinate-scale auto` 在任务开跑前做一次只读标定（截图 + uiautomator dump + 最多 4 次模型探针，零设备输入），实测模型坐标空间并采纳 pixels/thousand/显式系数；标定失败则任务拒绝启动、不写 report.json。
  - 漂移检测常驻：坐标越出 [0,1] 首次即报带诊断的失败；滑动窗口内坐标全部落入千分可表达带同样判疑似漂移，fail-closed 收场，不自动重标定。
  - 已在模拟器与红米真机（Xiaomi 23078RKD5C，Android 16，1220×2712）各跑一次 auto 标定任务，均标定为 pixels 且任务 succeeded；用法、报告字段与已验证证据见 [P5 坐标自标定](docs/P5_COORDINATE_CALIBRATION.md)。

## 目标

把「截图 + 指令 → 动作 JSON」的单点推理，扩展为能在真实设备上**自主完成完整任务**的 GUI Agent 系统。

## 许可

继承上游 [Apache License 2.0](./LICENSE)，保留原始版权声明（见 [NOTICE](./NOTICE)）。
