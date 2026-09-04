# Android P3 端到端评测

P3 只覆盖 Android。入口直接构造已有的 `MAIUINaivigationAgent`，通过
`MAIUIBrainAdapter` 连接到 `TaskRunner`、明确选中的 ADB 设备，以及每个任务的
JSONL 审计回放；没有另建模型服务或使用假模型代替生产推理。

## 运行前置条件

- 已明确选择一台状态为 `device` 的 Android 模拟器或真机。
- 可执行 `adb`，并已授权该设备。
- 已有可访问的 OpenAI 兼容 MAI-UI 推理地址和模型名。
- 设备已解锁，且 Settings、HOME 和前台应用都可由只读 ADB 预检识别。

运行入口需要设备序列号，绝不会选择默认设备：

```powershell
python -m gui_agent.evaluation.android `
  --serial emulator-5554 `
  --adb-path C:\\Users\\lwj\\AppData\\Local\\Android\\Sdk\\platform-tools\\adb.exe `
  --llm-base-url http://127.0.0.1:8000/v1 `
  --model-name your-mai-ui-model `
  --artifact-directory artifacts/android-p3/emulator
```

每次运行会在产物目录下创建独立的运行 ID，写入 `report.json`、每个任务的
`audit.jsonl` 和观测截图。审计回放只校验及还原记录，绝不重放设备输入。

## 安全任务集

每个任务都在模型动作进入 `TaskRunner` 前受精确白名单限制：

| 任务 | 仅允许的模型动作 | 通过条件 |
| --- | --- | --- |
| 打开设置 | `open("Settings")`、成功终止 | 前台包为预检解析的 Settings 包 |
| 返回主屏 | `system_button("home")`、成功终止 | 前台包为预检解析的 HOME 包 |
| 稳定等待 | `wait`、成功终止 | 审计含稳定完成事件，且没有设备输入 |
| 人工接管 | `ask_user` | 先暂停，再由评测器接管并取消；没有设备输入 |

点击、输入、滑动、拖拽、其他应用启动和任意未列出的元动作都会在设备输入前被
拒绝，并在报告中分类为 `policy_violation`。设备预检也是只读的：不会重启、清空数据、
重置状态或发送输入。

## 报告与红米对照

`report.json` 区分模型运行的原始状态和场景是否通过（人工接管的预期原始状态为
`cancelled`）。其中包含成功率、每项失败原因及分类、稳定等待超时数、人工接管数、
`audit_jsonl_valid`（JSONL 审计能否被无副作用地还原和校验）、步数、末次前台包和审计路径。
它不会把数据校验误称为设备动作重放。

先用模拟器完成一次，再用相同模型与任务集运行红米，只替换 serial 和产物目录：

```powershell
python -m gui_agent.evaluation.android `
  --serial REDMI_SERIAL `
  --adb-path C:\\Users\\lwj\\AppData\\Local\\Android\\Sdk\\platform-tools\\adb.exe `
  --llm-base-url http://127.0.0.1:8000/v1 `
  --model-name your-mai-ui-model `
  --artifact-directory artifacts/android-p3/redmi `
  --baseline-report artifacts/android-p3/emulator/<run-id>/report.json
```

红米报告会写入 `comparison`，直接列出两台设备在 manufacturer、model、Android
release、screen size、Settings/HOME 包名、前台包、成功率、超时、人工接管及各任务
结果上的差异。启动器包不会写死：模拟器和红米都由实际设备的 HOME intent 解析，因此
MIUI/HyperOS 或第三方启动器的差异会被保留为结果证据。

## 离线回归

```powershell
python -m pytest tests/quality/test_android_evaluation.py tests/unit/android -q
```

这组测试注入本地 fake backend 和 predictor，验证同一公共入口的行为；默认不会访问
ADB、模型服务或真机。
