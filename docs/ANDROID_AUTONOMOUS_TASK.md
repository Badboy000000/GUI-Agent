# Android 自主任务

`python -m gui_agent.autonomous.android` 是通用自主任务入口：给一条自然语言指令，
Agent 在显式指定的 Android 设备上**观察截图 → 模型决策 → 真实点击/输入/滑动 →
再观察**，自主完成多步任务，并全程写入可回放的 JSONL 审计。仍只覆盖 Android。

## 运行前置条件

- 已明确选择一台状态为 `device` 的 Android 模拟器或真机，设备已解锁。
- 可执行 `adb`，并已授权该设备。
- 仓库根目录有 git-ignored 的 `.env`，提供 `MAI_UI_BASE_URL`、`MAI_UI_MODEL_NAME`、
  `BIGMODEL_API_KEY`（远端托管服务如智谱 BigModel 需要真实 Key；本地 vLLM 类服务
  可填占位符）。Key 只在进程内使用，绝不打印、写日志或进入报告/审计产物。

## 快速开始

```powershell
python -m gui_agent.autonomous.android `
  "Open the Settings app, tap the search box, type wifi, terminate when results appear" `
  --serial emulator-5554 `
  --adb-path C:\\Users\\lwj\\AppData\\Local\\Android\\Sdk\\platform-tools\\adb.exe
```

入口需要显式设备序列号，绝不会选择默认设备。模型连接默认从仓库根目录 `.env`
读取（进程环境变量优先于 `.env`）。成功时退出码为 0，否则为 1；stderr 只打印
endpoint/model/device 三元组。报告 JSON 同时打到 stdout，便于管道处理。

## 命令行参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `instruction`（位置参数） | 必填 | 自然语言任务指令 |
| `--serial` | 必填 | 显式 ADB 序列号 |
| `--env-file` | 仓库根目录 `.env` | 指定其他 dotenv 文件 |
| `--llm-base-url` | `.env` 的 `MAI_UI_BASE_URL` | 覆盖 OpenAI 兼容端点 |
| `--model-name` | `.env` 的 `MAI_UI_MODEL_NAME` | 覆盖模型名 |
| `--adb-path` | `adb` | platform-tools 不在 PATH 时指定 |
| `--max-steps` | 15 | 步数上限，到达后以失败收尾 |
| `--artifact-directory` | `artifacts/android-tasks` | 产物根目录 |
| `--expect-package` | 无 | 要求任务结束时处于前台的包名 |

## 运行产物

每次运行创建 `artifacts/android-tasks/<run_id>/`，包含：

- `report.json`：运行 ID、指令、模型名、设备档案、最终状态、步数、失败原因、
  `pending_confirmation`、末次前台包、`audit_jsonl_valid`、审计与截图路径。
- `audit.jsonl`：append-only 事件流（观察 / 提议 / 校验 / 执行），`load_replay()`
  只校验及还原记录，绝不重放设备输入。
- `screenshots/`：每步观测截图。

## 安全边界（四层）

1. **步数上限**：`--max-steps`（默认 15）硬性封顶，循环不会失控。
2. **坐标校验**：动作坐标必须是 [0,1] 归一化值，越界即拒绝（fail-closed），
   绝不做钳位修正。
3. **动作策略**：只放行 `click` / `long_press` / `drag` / `swipe` / `type` /
   `open` / `system_button` / `wait` / `terminate` / `ask_user`；
   `double_click`、`answer` 及任何未列出动作在任何设备输入前被拒绝。其中
   `open` 只启动应用包白名单内的应用（默认经只读设备预检解析出 Settings，
   可经配置扩展）。
4. **全程审计与人工暂停**：审计可无副作用回放；`ask_user` 只暂停运行并在
   `report.json` 的 `pending_confirmation` 中呈现提示文本，CLI 绝不自动放行。

## 坐标约定（选模型时必读）

MAI-UI 流水线历史上假设模型按 0–999 作答（`SCALE_FACTOR=999`）。运行时键
`coordinate_scale` 现在显式选择坐标约定：

- `"thousand"`：真实 MAI-UI 权重的 0–999 约定（上游 agent 默认值）。
- `"pixels"`：模型按原始截图像素作答。自主任务入口默认使用它，因为智谱
  BigModel 的 glm 视觉模型按像素回答——探测时模型在 1080×2400 截图上返回
  例如 `[921,1512]`，与目标图标真实中心偏差约 10px 以内。

解析与历史回显遵守同一约定，多步上下文中的坐标始终一致。换用本地 vLLM 部署的
MAI-UI 权重时改回 `"thousand"`（经 `AndroidTaskConfig.runtime_conf`）即可。

## 已验证

- 离线：完整测试套件 `python -m pytest -q` → 130 passed, 1 skipped
  （`android_smoke` 为显式 opt-in）。
- 真机：`emulator-5554` + 智谱 BigModel `glm-5.3-flash`，指令
  "Open the Settings app, tap the search box, type wifi, terminate when results
  appear" 端到端完成：全新运行 `36d40965-ac96-4140-aa70-304770a85366`，
  8 步，状态 `succeeded`，审计回放有效。此前一次运行（`530220d6-…`）还演示了
  Agent 跨步自我修正拼音输入法组词错误（`wififi` → 删除回退为 `wifi`）。

## 使用注意

- `--expect-package` 必须写**任务结束时**的前台包。例如设置的搜索结果页运行在
  `com.google.android.settings.intelligence`，不是 `com.android.settings`。
  拿不准就省略该参数：默认校验器信任模型的 `terminate`。
- ADB `input text` 会经过输入法组词（拼音键盘可能打出错误文本）；Agent 可以
  删除重输自我恢复，但要为多走的步骤留出步数预算。
- `open` 只对白名单应用生效；其他应用应让 Agent 通过点击图标导航。
- 模型只能看到截图，没有 accessibility tree。

## 离线回归

```powershell
python -m pytest tests/quality/test_android_autonomous_task.py tests/unit/test_autonomous_android_runtime.py -q
```

这组测试注入本地 fake backend 和 predictor，验证同一公共入口的行为；默认不会
访问 ADB、模型服务或真机。
