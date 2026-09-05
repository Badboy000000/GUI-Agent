# P5 设计：UI 树采集 + 坐标自标定 + 漂移检测

> 状态：**已实现，双设备验证通过**（2026-09；用户指南见 [P5 坐标自标定](P5_COORDINATE_CALIBRATION.md)）
> 方法论依据：本设计是「螺旋上升回路」的一次完整应用。前置探测圈已出数据（见 §2），
> 本文件的判据在实现前写死（§6），验收按判据执行。
> 关联文档：[P4 规划](P4_ANDROID_AUTONOMOUS_TASK_PLAN.md)、[P4 用户指南](ANDROID_AUTONOMOUS_TASK.md)。

---

## 1. 问题定义

`coordinate_scale` 目前是**人工配置**的静态值（`thousand`/`pixels`）。它的真实语义是
「(模型, 服务端行为, 分辨率) 三元的经验事实」——猜错时的失败形态是不对称的：

- `thousand` 模式 + 像素模型：y>999 → ÷999 后 >1 → 校验 fail-closed 拦截（**可见失败，安全**）。
- `pixels` 模式 + 千分模型：值 ≤999 → ÷屏幕高（2712）后全部 <0.37 → **静默错映到屏幕上方**（危险）。
- 假设性「服务端缩放像素」：原始像素 × 固定比例 → 两种模式都错，且可能静默。

目标：**把坐标尺度从「配置猜测」变成「每次部署自动实测标定」**，并在运行中检测约定漂移。

## 2. 前置探测圈已确认的事实（设计输入）

| 事实 | 证据 |
| --- | --- |
| glm-5.3-flash 原生输出截图像素坐标 | 模拟器 [664,1909]（Chrome 图标）；[921,1512] 与真实 (912,1518) 差 ≤9px |
| pixels 约定跨分辨率稳定（本模型） | 红米 1220×2712 上 [596,1349]、[596,830]，与真实位置差 ≤3px |
| 「服务端缩放破坏像素空间」已否证（n=2） | 两台设备均无缩放失真 |
| HyperOS(Android 16) uiautomator dump 可用 | 63 节点、XML 格式标准 |
| dump bounds 与截图像素空间一致 | 同屏「锁屏画报」：树中心 (212,2008) vs 视觉 (213,2011)，差 1–3px |
| dump 延迟不可忽略 | 完整周期模拟器 ~2.3s、红米 ~3.1s → **不能默认每步采集** |
| 换模型的约定漂移风险仍在 | 未观测过其它模型；静态配置在换模型时静默失效 |

**结论**：树采集的可行性与真值一致性已验证；标定的主要防御对象是「换模型」，
而非「换设备」（glm 跨分辨率已自证稳定）。

## 3. P5a：UI 树采集

- `AdbTransport.dump_ui_hierarchy() -> str`：`uiautomator dump` 到设备临时文件 →
  `exec-out cat` 读回 → `rm` 清理（finally 保证）。dump 失败（如 `null root node`、空输出）
  抛 `UiTreeError`。
- 新模块 `gui_agent/platforms/android/ui_tree.py`：`parse_ui_tree(xml_text) -> dict`，
  产出**扁平节点表**（标定只需查表，不需要嵌套树）：
  `{"package": str|None, "node_count": int, "nodes": [{"index", "text", "resource_id",
  "class_name", "content_desc", "clickable", "bounds": (l, t, r, b), "depth"}]}`。
  非法 XML / 空 hierarchy 抛 `UiTreeError`。
- `AndroidDeviceBackend(..., capture_ui_tree: bool = False)`：默认**关**（3s/步 太贵）；
  开启时 `observe()` 填充 `Observation.ui_tree`；采集失败降级为 `None` 不阻断观测。
- `TaskRunner._record_observation` 的审计 payload 增加 `ui_tree_available: bool`（可观测性）。
- 上游 `src/` 行为不变：树暂不进 prompt（是否喂给模型是后续阶段的独立决策）。

## 4. P5b：坐标自标定（`coordinate_scale="auto"`）

**触发**：`runtime_conf["coordinate_scale"] == "auto"` 时，由 `run_android_task`
在跑任务前执行一次只读标定；标定失败则任务 fail-closed 拒绝启动（不猜）。

**标定流程（零设备输入）**：

1. `observe()` 拿截图（记尺寸 W×H）+ `dump_ui_hierarchy()` 拿树。
2. 从树中挑标定目标：`clickable=true` 且 `text` 非空、在全部节点中**文本唯一**的节点，
   取其 bounds 中心为真值 `(cx, cy)`。无合格目标 → 标定失败。
3. 用**一次性探针 agent**（独立实例，不污染正式 traj_memory）问模型：
   `Click the element labeled "<text>"`，取坐标。**探针固定 thousand 模式**
   （常量除数、与分辨率无关），解析值 ×999 精确还原 raw，再代入 §4 公式；
   探针返回不可解析结果则换下一个候选目标，候选耗尽 → 标定失败。
4. **自由系数拟合**：统一形式 `truth = raw / S × dim`，解析出模型坐标空间 extent
   **S_x = W·rx/cx，S_y = H·ry/cy**（`rx`/`ry` 为 0 则该样本作废换目标）；
   - 候选约定都是 S 的特例：`pixels`（S=(W,H)）、`thousand`（S=(999,999)）、`normalized`（S=(1,1)）；
   - 实测 S 与某约定相对偏差 < 10% → 吸附该约定；否则自由模式，sanity 界
     **0.25 ≤ S_x/W ≤ 4 且 0.25 ≤ S_y/H ≤ 4**（模型空间不得离谱），通过则采纳显式系数。
5. **双样本复核**：换第二个目标重复一次；两次采纳的约定/系数须一致
   （自由系数相对差 < 5%），不一致 → 标定失败。标定成本 = 2 次模型调用 + 1 次 dump。

**落点**：标定产出写入正式 agent 的 runtime_conf：
- 约定命中 → `coordinate_scale` 置为 `pixels`/`thousand`（复用现有解析路径）；
- 自由模式 → `coordinate_scale="explicit"` + `coordinate_scale_x/_y`（新增支持，
  解析时直接除以该系数；`normalized` 等价于 explicit 1.0/1.0）。

**上游 `src/mai_naivigation_agent.py` 最小扩展**：
- `coordinate_scale` 合法值增加 `"explicit"`，配合 `coordinate_scale_x`/`coordinate_scale_y`
  （缺省即抛 ValueError）；
- 历史回灌 `_denormalize_point` 在 explicit 模式下乘以系数（保持与解析互逆）。
- 现有 `thousand`/`pixels` 行为零变化。

**已知边界**（写进文档，不在本期解决）：标定按当时分辨率进行，任务中途旋转/改分辨率
会使 explicit 系数失效 → 由漂移检测（§5）兜底报警。

## 5. P5c：漂移检测（fail-closed 信号）

在 `MAIUIBrainAdapter` 与 policy 之间的薄监控层（`gui_agent/brains/coordinate_drift.py`）：

- **信号 A（thousand 模式下模型输出像素）**：解析后坐标 >1 会被 `validate_action` 拒绝。
  **实施勘定（评审后修正）**：监控层在**首次**越界即抛 `CoordinateDriftError` 并给出
  「坐标约定可能漂移，请用 auto 重新标定」的诊断——原设计「连续 N=3 次」不可观测，
  因为首次越界本就会经 validate_action 终止任务；监控层的价值是把通用校验失败转成
  带诊断的失败，而不是计数。
- **信号 B（pixels 模式下模型输出千分）**：静默错映，无越界。监控层对坐标动作做滑动窗口
  （W=5）：若窗口内所有 y 都 < `999/屏幕高`（千分模型的理论上限带），判定疑似漂移 →
  同样 fail-closed 报警。
- v1 **不做**自动重标定（报警即停，人工或上层决定是否重标）。自动回路留待后续。

## 6. 判据（验收前写死）

**离线（CI）**：
1. 解析器：标准/嵌套/畸形/空 XML、缺失属性、bounds 变体 → 结构正确或抛 `UiTreeError`。
2. 标定拟合（假 predictor + 假树）：0–999 模型 → 采纳 `thousand`；像素模型 → `pixels`；
   [0,1] 模型 → explicit 1.0；恒定比例 1.5× 的「缩放像素」模型 → explicit 自由系数；
   乱答模型（坐标随机）→ 标定失败拒绝开跑。
3. 漂移监控：信号 A 首次越界即抛带诊断的错误（见 §5 勘定说明）；信号 B 窗口内千分带聚集触发报警；
   正常轨迹不触发。
4. 全量 `python -m pytest -q` 绿。

**真机（双设备验收）**：
5. 模拟器 + 红米各跑一次 `coordinate_scale="auto"` 的真实任务（打开设置→搜索→输入 wifi），
   标定结果应为 `pixels`，任务成功，落点截图人工复核。
6. 标定过程零设备输入（审计中无 command_executed 于标定阶段）。

## 7. 范围红线（反过度设计）

- 不做自动重标定回路；不做每步默认树采集；不把树喂进 prompt；不动 P3 评测链路；
  不做多目标加权拟合/鲁棒回归（双样本一致性已够，多余复杂度不要）。
- 除 `src/mai_naivigation_agent.py` 的 explicit 模式扩展外，不改上游文件。

## 8. 文件清单（实施时）

- 新增：`gui_agent/platforms/android/ui_tree.py`、`gui_agent/brains/coordinate_calibration.py`、
  `gui_agent/brains/coordinate_drift.py`
- 修改：`gui_agent/platforms/android/adb_transport.py`（dump 方法）、
  `gui_agent/platforms/android/backend.py`（capture_ui_tree 开关）、
  `gui_agent/platforms/android/__init__.py`（导出）、
  `gui_agent/orchestration/agent_loop.py`（审计 payload 一字段）、
  `gui_agent/autonomous/android.py`（auto 标定接线 + drift 监控接线）、
  `src/mai_naivigation_agent.py`（explicit 模式）
- 测试：`tests/unit/android/test_ui_tree.py`、`tests/quality/test_coordinate_calibration.py`、
  `tests/unit/test_coordinate_drift.py` 及既有测试适配
- 文档：`docs/P5_COORDINATE_CALIBRATION.md`（用户向）、README P5 段
