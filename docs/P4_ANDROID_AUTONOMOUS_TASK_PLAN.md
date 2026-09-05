# P4 规划：通用自主 Android 任务（任意指令 → 真机自主完成）

> 状态：**已实现并在真机验证**（入口 `python -m gui_agent.autonomous.android`，用法与已验证证据见
> [Android 自主任务](ANDROID_AUTONOMOUS_TASK.md)；以下为规划时的历史记录，保留原样）
> 维护者：东篱馆主　｜　上游：[Tongyi-MAI/MAI-UI](https://github.com/Tongyi-MAI/MAI-UI)（Apache-2.0）
> 前置：P1（ADB 观测/执行/校验/有界闭环）、P2（稳定等待/可恢复会话/人工确认接管/JSONL 审计）、
> P3（真实模型评测闭环，已用智谱 glm 在 emulator-5554 跑通 4/4）均已完成。

---

## 1. 背景与目标

P3 证明了「真实模型 + 真实设备 + 审计闭环」整条链路可用，但它只是一个**评测 demo**：
白名单只放行 `open / system_button / wait / ask_user` 这四个**零坐标、零输入**动作，唯一入口是
`python -m gui_agent.evaluation.android`。

P4 的目标是把它推进成「能真正干活的 Agent」：给一条自然语言指令，Agent 在真机上
**观察截图 → 模型决策 → 真实点击/输入/滑动 → 再观察**，自主完成一个多步任务，并全程审计。

本阶段仍**只覆盖 Android**，不扩 iOS / 桌面 / 网页。

---

## 2. 已查明的事实（含代码证据）

### 2.1 底层接缝其实已齐备，缺的是通用装配

- `TaskRunner`（[gui_agent/orchestration/agent_loop.py](../gui_agent/orchestration/agent_loop.py)）
  已是通用闭环：构造参数为 `(backend, brain, compiler, verifier, *, max_steps, wait_seconds,
  sleeper, session, audit_recorder, stability_waiter)`；`run(instruction)` 内做
  observe → `brain.decide` → `validate_action` → 分派 `terminate/ask_user/wait/普通命令`。
- `AndroidActionCompiler`（[gui_agent/actions/compiler.py](../gui_agent/actions/compiler.py)）
  已支持把校验后的动作翻成像素命令：
  `click→tap`、`long_press→长按 swipe`、`drag→swipe`、`swipe→swipe`、`type→text`、
  `open→launch`（仅经 `app_packages` 白名单解析包名）、`system_button→system_key`。
- `MAIUIBrainAdapter`（[gui_agent/brains/mai_ui_adapter.py](../gui_agent/brains/mai_ui_adapter.py)）
  已把上游 predictor 的 dict 结果转成绑定观测 id 的 `ProposedAction`。
- 审计、会话/状态机、稳定等待均已就绪且 P3 在用。
- **结论**：不需要重写闭环，只需新增一个「通用任务装配层」（与 `evaluation/` 平级），
  用比 P3 更宽但仍有界的策略把真实 brain 接进 `TaskRunner`。

### 2.2 坐标管线安全，但模型坐标尺度从未钉死（本阶段最大风险）

数据流：

```
模型原始坐标  →  解析时 ÷999 (SCALE_FACTOR)  →  [0,1]  →  validate_action 校验  →  编译时 ×(屏幕-1)  →  ADB 像素
```

- 上游固定按 **0–999** 处理：`SCALE_FACTOR = 999`（[src/mai_naivigation_agent.py:38](../src/mai_naivigation_agent.py)），
  `parse_action_to_structure_output` 把 `coordinate/start_coordinate/end_coordinate` 一律除以 999；
  历史回灌再 `×999`，自洽。
- 坐标校验只认 **[0,1] 归一化**（[gui_agent/contracts/actions.py:109-115](../gui_agent/contracts/actions.py)）：
  非有限值或越界直接 `ActionValidationError`，**fail-closed、不 clamp**。
- 编译换算用 `×(screen_width-1)/(screen_height-1)`（[compiler.py:60-66](../gui_agent/actions/compiler.py)），
  1.0 映射到屏幕-1，不会越界。
- **关键缺口**：系统提示词 `src/prompt.py` 的动作空间里**通篇没有告诉模型该用什么坐标系**
  （没有 0–999、没有 [0,1]、没有像素说明）。glm 是通用视觉模型，不保证按 0–999 输出。
  - 模型若输出像素值 **>999**（如 y=1200）：÷999=1.20 → 被 `validate_action` 拒绝（安全，但任务失败）。
  - 模型若输出像素值 **≤999**（如想说 y=500px）：÷999≈0.50 → ×2399 落到 ~1200px，
    **静默点错位置**，任何检查都不报错——这是最需要防的错映。

**因此 P4 必须先做只读坐标探测，确认模型实际输出尺度，再决定改法（见步骤 0）。**

### 2.3 其它已确认事实

- **`Observation.ui_tree` 恒为 None**：Android backend 的 `observe()` 从不抓 UIAutomator
  控件树（[gui_agent/platforms/android/backend.py:44-67](../gui_agent/platforms/android/backend.py)），
  brain 传给模型的 `accessibility_tree` 实际为空。即当前是**纯视觉**方案，截图可用、无需树。
- **成功验证器只有两种**：`_ForegroundVerifier`（前台包等于预期包）和 `_AcceptingVerifier`
  （信任模型 terminate），都在 `evaluation/android.py`；`orchestration/` 只定义了
  `SuccessVerifier.verify(instruction, observation) -> bool` 协议。通用任务无廉价自动判据，
  首版用「信任 terminate + 记录末态前台包」，可选前台包校验。
- **两个动作能过校验但无法编译（现有小缺口）**：`double_click` 在 `_KNOWN_ACTIONS` 里但
  编译器无分支；`answer` 能过校验但编排层只拦截 `terminate/ask_user/wait`，落到编译器会抛
  `ActionCompilationError`。通用策略需显式拒绝这两个（或后续补实现）。
- **`open` 受包名白名单约束**：`app_packages` 映射不提供时，任何 `open` 都会报
  “app is not allowlisted”。通用任务需给出可用的 app→包名映射。
- 模型连接已与 `.env` 打通（[gui_agent/evaluation/environment.py](../gui_agent/evaluation/environment.py)，
  `MAI_UI_BASE_URL / MAI_UI_MODEL_NAME / BIGMODEL_API_KEY`），Key 不打印、不进报告；新入口直接复用。

---

## 3. 已确认的决策

| 决策点 | 结论 |
| --- | --- |
| 模型 | 继续用**智谱 BigModel 远端** OpenAI 兼容视觉模型（`.env` 已配 glm-5.3-flash）；本机无 GPU 不做本地权重部署。客户端模型无关，未来可换 vLLM/其它端点。 |
| 安全姿态 | **有界全自动**：`max_steps` 步数上限 + 坐标 [0,1] 强制校验 + app 白名单 + 完整 JSONL 审计，在开发模拟器上自主跑完出报告。 |
| 平台范围 | 仅 Android；不扩 iOS/桌面/网页。 |
| 自动判成功 | 首版默认信任模型 `terminate`，同时记录末态前台包；可选 `--expect-package` 做前台校验。不做重型语义验证。 |
| 坐标问题 | **先只读探测再改**，不盲改上游 prompt/解析。 |

---

## 4. 实施步骤

### 步骤 0｜只读坐标尺度探测（不发送任何设备输入）
- 抓一张 emulator-5554 截图，构造一个需要点击的指令，调用真实 predictor，收集模型**原始坐标**。
- 判定输出尺度：0–999 / [0,1] / 像素。
- 分支：
  - 模型在提示下能稳定输出 0–999 → 在 `src/prompt.py` 动作空间说明里**补一句坐标系约定**
    （坐标用 0–999 整数、原点左上、x 向右 y 向下），复用现有 ÷999 管线。
  - 模型不遵守 → 在 brain/predictor 接缝加**坐标尺度归一化防御**：把 [0,1] / 像素 / 0–999
    统一归一到 [0,1] 再进校验；越界仍 fail-closed。
- 据探测结果取最小改动。

### 步骤 1｜通用任务装配与安全策略（新包 `gui_agent/autonomous/`）
新建 `gui_agent/autonomous/android.py`（业务命名，与 `evaluation/` 平级），全部复用现有接缝：

- **`GeneralActionPolicy`**（Brain 包装器，仿 `evaluation/android.py` 的 `_RestrictedBrain`）：
  放行可真正执行的动作 `click/long_press/drag/swipe/type/open/system_button/wait/terminate/ask_user`；
  **显式拒绝** `double_click/answer` 与未知动作（fail-closed）。它是设备输入前的唯一收口，
  拒绝即抛策略异常、任务失败并记录审计。
- **app 白名单**：`open` 仍走编译器 `app_packages` 映射；映射由 config 提供，默认含 Settings，可扩展。
- **验证器**：默认 `_AcceptingVerifier`；config 可选 `expected_foreground_package` 走前台校验。
- **`AndroidTaskConfig`**（frozen dataclass，仿 `AndroidEvaluationConfig`）字段：
  `serial, instruction, llm_base_url, model_name, api_key, artifact_directory, adb_path,
  max_steps=15, adb_timeout_seconds, runtime_conf, app_packages, expected_foreground_package?, run_id?`。
- **`run_android_task(config, *, backend_factory=None, predictor_factory=None) -> dict`**：
  建**单个** backend（跨多步复用、截图连续编号）、真实 predictor（`_load_existing_mai_ui` 桥接 `src/`）、
  policy 包 brain、compiler、verifier、一个 `JsonlAuditRecorder`；跑一次 `runner.run(instruction)`；
  产出 `report.json`（终态、步数、末前台包、失败分类、审计路径）+ `audit.jsonl` + 截图。
- **CLI**：`python -m gui_agent.autonomous.android --serial emulator-5554 --adb-path ... "<自然语言指令>"`；
  支持 `--env-file/--llm-base-url/--model-name/--max-steps/--artifact-directory/--expect-package`，
  沿用 P3 的 argparse 约定；`.env` 驱动连接，诊断走 stderr，报告不泄密。
- `gui_agent/autonomous/__init__.py` 做懒导出（仿 `evaluation/__init__.py`）。

### 步骤 2｜离线测试（不碰真机/真模型）
仿 `tests/quality/test_android_evaluation.py` 的假件注入（`PngScriptedBackend` + `ScriptedMAIPredictor`）：
- 脚本化一个 `open → click → type → terminate` 多步任务，断言编译出的 tap/text 像素命令正确、
  坐标换算符合预期；
- 断言 policy 拒绝 `double_click/answer`、越界坐标 fail-closed；
- 断言审计 JSONL 可回放、报告结构完整。
- 新增 `tests/quality/test_android_autonomous_task.py` 与 policy 单测；跑全量套件无回归。

### 步骤 3｜真机多步任务（emulator-5554，只读/可逆）
坐标探测确认落点可信后，跑一个**会真正点到坐标、但不改设备持久状态**的低风险任务：
例如「打开设置 → 点搜索框 → 输入查询词（仅填框、不切开关）→ 返回/终止」。
- 用 artifacts 截图**人工核对点击落点**是否命中目标元素；
- 核对 report.json 与审计；落点偏移则回步骤 0 修正坐标方案。

### 步骤 4｜文档与收尾
- 新增 `docs/ANDROID_AUTONOMOUS_TASK.md`（运行方式、安全边界、坐标约定）；README 加 P4 段。
- `.env.example`、`artifacts/` 忽略已就绪。
- 全量测试通过后再按约定提交（提交动作届时单独确认）。

---

## 5. 安全边界（四重）

1. **步数上限** `max_steps`（默认 15），到顶即失败，防止失控循环。
2. **坐标强制校验**：所有点击/滑动坐标必须落在 [0,1]，越界 fail-closed，不会点到屏幕外。
3. **app 启动白名单**：`open` 只能启动 `app_packages` 显式列出的包，模型任意文本不能变成包名调用。
4. **完整 JSONL 审计**：每个观测/提议/校验/执行事件落盘，可无副作用回放；不可逆/危险操作
   （改设置开关、支付、删除、发消息等）不在首版演示任务范围内。

---

## 6. 风险与待验证项

- **坐标错映（最高优先）**：glm 实际输出尺度未知，≤999 像素值会被静默错映。靠步骤 0 探测 + 步骤 3 人工核对落点兜底。
- **多步稳定性**：连续动作间界面需要时间稳定；必要时复用 `UiStabilityWaiter`（P3 已用于 wait 任务）。
- **模型动作格式漂移**：不同步长/温度下模型可能偶发不返回 `<tool_call>`；P3 已修「无 thinking 标签也能解析」，
  但真实多步任务需观察是否还有其它解析失败形态。
- **`open` 包名覆盖**：演示任务用到的 app 必须在 `app_packages` 内，否则编译失败（fail-closed）。
- **成功判定偏宽**：信任 terminate 可能把「模型自称成功」计为成功；首版靠人工核对截图，后续可加更强验证器。

---

## 7. 涉及 / 新增文件清单（实施时）

- 新增：`gui_agent/autonomous/__init__.py`、`gui_agent/autonomous/android.py`
- 可能修改：`src/prompt.py`（补坐标系说明，视步骤 0 结果）；或在 brain 接缝加坐标归一化防御
- 复用不改：`TaskRunner`、`AndroidActionCompiler`、`MAIUIBrainAdapter`、`AndroidDeviceBackend`、
  `AdbTransport`、`UiStabilityWaiter`、`JsonlAuditRecorder`、`evaluation/environment.py`
- 新增测试：`tests/quality/test_android_autonomous_task.py`（+ policy 单测）
- 新增文档：`docs/ANDROID_AUTONOMOUS_TASK.md`；README 增加 P4 段
