# P5 坐标自标定与漂移检测

`python -m gui_agent.autonomous.android --coordinate-scale auto` 让自主任务在开跑前
**自动实测**模型的坐标约定，取代人工猜测：Agent 先做一次只读标定（截图 + uiautomator
dump + 最多 4 次模型探针调用，全程零设备输入），拟合出模型的坐标空间并采纳
`pixels` / `thousand` 或显式系数；标定失败则任务拒绝启动（fail-closed，不猜、不回退）。
运行期间另有常驻漂移检测：坐标约定一旦与配置不符，任务以带诊断的失败收场。
仅覆盖 Android 自主任务入口。

## 用法

```powershell
python -m gui_agent.autonomous.android `
  --serial emulator-5554 `
  --coordinate-scale auto `
  "Open the Settings app, tap the search box, type wifi, terminate when results appear"
```

`--coordinate-scale` 三个取值：

| 取值 | 适用场景 |
| --- | --- |
| `pixels`（默认） | 模型按原始截图像素作答。智谱 BigModel glm 视觉模型已验证为此约定。 |
| `thousand` | 真实 MAI-UI 权重的 0–999 约定（上游 agent 默认值）。 |
| `auto` | 不确定约定、或切换模型/端点时：开跑前实测一次，流程见下节。 |

其余参数与运行前置条件见 [Android 自主任务](ANDROID_AUTONOMOUS_TASK.md)。

## auto 标定流程（只读）

1. `observe()` 拿截图（记尺寸 W×H）+ `dump_ui_hierarchy()` 拿 UI 树。
2. 从树中挑标定目标：`clickable` 且文本在全部节点中**唯一**、bounds 距屏幕四边
   ≥40px 的节点，取其中心为真值。无合格目标 → 标定失败。
3. 对每个候选目标用一次性探针 agent（固定 thousand 解析、独立实例，不污染正式任务
   历史）问模型 `Click the element labeled "<text>"`，还原 raw 坐标，反推模型坐标
   空间 extent：S_x = W·rx/cx，S_y = H·ry/cy。
4. 采纳规则：实测 S 与某约定（pixels / thousand / normalized）双轴相对偏差 <10%
   → 吸附该约定；否则进入显式系数模式，但须过 sanity 界 0.25 ≤ S/屏幕维度 ≤ 4。
   既不吸附又越界的样本视为模型答错目标，直接作废。
5. 一致性复核：最多探测 4 个样本，第一组双轴相对差 <5% 的样本对即被采纳（取均值）。
   候选耗尽仍无一致样本对 → 标定失败。

标定成本为 2–4 次模型调用 + 1 次 dump + 1 次截图。标定在审计记录器创建之前完成，
且只调用观测 / dump / 模型推理，结构上没有任何设备输入路径。

**失败行为**：标定失败抛 `CalibrationError`，任务不启动、不写 `report.json`
（运行目录已创建但为空），进程以非零退出码结束。

## report.json 标定块

使用 auto 的运行在 `report.json` 中带 `calibration` 块（未用 auto 时为 `null`）：

```json
"calibration": {
  "coordinate_scale": "pixels",
  "scale_x": null,
  "scale_y": null,
  "samples": 2,
  "detail": "model answers in pixels coordinates; discarded 0 implausible sample(s)"
}
```

| 字段 | 说明 |
| --- | --- |
| `coordinate_scale` | 标定采纳并实际用于本次任务的约定：`pixels` / `thousand` / `explicit` |
| `scale_x` / `scale_y` | 仅 `explicit` 时有值（自由拟合为实测 extent；吸附 normalized 时为 1.0），否则为 `null` |
| `samples` | 被采纳的一致性样本数（≥2） |
| `detail` | 人类可读结论，含作废样本计数（`discarded N implausible sample(s)`） |

## 漂移检测（运行期间，常驻）

`CoordinateDriftMonitor` 位于 brain 与动作策略之间，对每次运行生效（不限 auto），
检查每个提议动作的坐标字段（`coordinate` / `start_coordinate` / `end_coordinate`）。
两种信号都 fail-closed，只报警，不自动重标定：

- **信号 A（越界）**：任一坐标点越出 [0,1]，首次即抛 `CoordinateDriftError`，诊断
  「坐标约定可能漂移，请用 auto 重新标定」。它把「thousand 配置 + 像素模型」原本的
  通用校验拒绝升级为带诊断的失败。
- **信号 B（千分带聚集）**：滑动窗口（最近 5 个坐标点）内所有 y 都落在 0–999 可表达
  带之下（y < 999/屏幕高，仅当屏幕高度 >999 时启用）→ 判定疑似「pixels 配置 +
  千分模型」的静默错映，同样抛错。

错误经 `TaskRunner` 收尾为 `state=failed`：诊断文本写入 `report.json` 的 `detail`，
审计写入 `TASK_ERROR`，CLI 退出码为 1。

> **已知误报面**：在正确配置的 thousand 运行中，若任务恰好连续 5 个坐标点都点击屏幕
> 顶部窄带（归一化 y < 999/屏幕高），信号 B 会误判为漂移。遇到时重跑任务，或改用
> `--coordinate-scale auto`。

## 已知边界与使用注意

- 标定需要屏幕上至少有可点击且文本唯一的元素：一致性复核要求两个合格样本，因此实际
  需要**至少两个**这样的元素。主屏、设置首页都可以；纯启动页/闪屏可能没有合格目标，
  此时标定失败、任务拒绝启动——换到内容更丰富的界面再启动，或显式指定约定。
- 标定按开跑时的分辨率固定系数；任务中途旋转屏幕或改分辨率会使 explicit 系数失效，
  由漂移检测兜底报警。
- UI 树采集能力已具备但**默认关闭**：`AndroidDeviceBackend(capture_ui_tree=True)` 会
  在每步 `observe()` 填充 `Observation.ui_tree`（扁平节点表，含 text / resource_id /
  class_name / content_desc / clickable / bounds / depth），采集失败降级为 `None` 不
  阻断观测，审计观测事件带 `ui_tree_available` 字段。代价是每步观测延迟约从 0.9s 升至
  3.1s（模拟器实测；dump 完整周期模拟器 ~2.3s、红米 ~3.1s），因此默认不开。auto 标定
  自行 dump 一次，与该开关无关。UI 树暂不进入模型 prompt。

## 已验证

- 离线：完整测试套件 `python -m pytest -q` → 193 passed, 1 skipped
  （`android_smoke` 为显式 opt-in）。
- 模拟器：`emulator-5554`（1080×2400，Android 14）+ 智谱 BigModel `glm-5.3-flash`，
  auto 标定为 `pixels`（2 样本），任务 5 步 `succeeded`，审计回放有效，运行
  `d5043004-addb-4474-add5-847e85c4615d`。
- 真机：红米 Xiaomi 23078RKD5C（1220×2712，Android 16）+ 同一模型，auto 标定为
  `pixels`（2 样本，0 作废），任务 13 步 `succeeded`，审计回放有效，运行
  `6bb8a250-ace7-4064-930f-b8d5732ad371`。
- 两次运行的标定阶段均零设备输入。

## 离线回归

```powershell
python -m pytest tests/quality/test_coordinate_calibration.py tests/unit/test_coordinate_drift.py tests/unit/android/test_ui_tree.py -q
```

这组测试注入假 backend、假树与假 predictor，验证标定拟合、采纳/失败路径与漂移信号；
默认不访问 ADB、模型服务或真机。设计判据与取舍记录见
[P5 设计](P5_COORDINATE_CALIBRATION_DESIGN.md)。
