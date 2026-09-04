# 开源项目借鉴指南（模块 × 平台 对照）

> 本项目基于 [Tongyi-MAI/MAI-UI](https://github.com/Tongyi-MAI/MAI-UI)（Apache-2.0）二开。
> MAI-UI 只提供「截图 + 指令 → 单步动作 JSON」的**大脑**，缺的是把决策落到真实设备的**闭环**。
> 本文档记录：每个缺失模块、每个目标平台（**手机 / 桌面 / 网页**），应该去哪个开源项目借鉴什么。
>
> 维护者：东篱馆主　｜　协议信息以各仓库 LICENSE 文件为准（下表为核实时点的状态）

---

## 1. 我们要补齐的架构（分层）

```
┌─────────────────────────────────────────────────────────┐
│  大脑层（已有 src/）  截图+指令+历史 → 单步动作 JSON        │
│  mai_grounding_agent / mai_naivigation_agent / prompt     │
├─────────────────────────────────────────────────────────┤
│  编排层（待建）  任务循环：观察→决策→执行→再观察、判终、恢复  │
├─────────────────────────────────────────────────────────┤
│  动作层（待建）  动作 JSON → 平台原语；坐标归一化↔像素换算    │
├─────────────────────────────────────────────────────────┤
│  设备/平台层（待建）                                        │
│   ├─ 手机： Android(ADB) / iOS(XCTest) / 鸿蒙(HDC)         │
│   ├─ 桌面： Windows / macOS / Linux                        │
│   └─ 网页： 浏览器（DOM/无障碍树 + 视觉）                    │
├─────────────────────────────────────────────────────────┤
│  状态管理层（待建） 前台 App / 窗口 / 页面 / 界面元素上下文   │
├─────────────────────────────────────────────────────────┤
│  评测 & 基础设施（待建） 真实/虚拟环境、成功率评测、回放回归   │
└─────────────────────────────────────────────────────────┘
```

**借鉴总原则**：设备层 / 动作层 / 编排层与「大脑」**模型无关**——参考项目的这三层可以照搬架构与大量代码，只需把模型 client 换成我们自己的 MAI-UI 推理、把 prompt/动作解析对齐 `src/` 现有的格式与坐标约定（MAI-UI 坐标归一化到 `[0,1]`，部分项目用 0–999）。

---

## 2. 模块 × 平台 借鉴矩阵（速查）

| 缺失模块 | 手机端 | 桌面端 | 网页端 |
|---|---|---|---|
| **设备接入 / 连接** | Open-AutoGLM `adb/` `hdc/` `xctest/` | OSWorld `desktop/`+VM；Agent-S | browser-use；Playwright |
| **截图 / 观测** | Open-AutoGLM `adb/screenshot.py` | Agent-S / OSWorld 截屏 | Playwright `screenshot` |
| **界面元素树（a11y/DOM）** | uiautomator2（控件树） | pywinauto(UIA)；UFO；UIAutomation | browser-use DOM 提取；Playwright |
| **动作执行 / 下发** | Open-AutoGLM `actions/handler.py` + `adb/device.py` | pyautogui / pywinauto；UFO；Agent-S | Playwright（DOM 操作）；pyautogui（视觉兜底） |
| **App/系统状态管理** | Open-AutoGLM `get_current_app()` | OSWorld 状态；pywinauto 窗口树 | browser-use 标签页/DOM 状态 |
| **任务编排闭环（agent loop）** | Open-AutoGLM `agent.py` `PhoneAgent` | Agent-S；UFO；Cradle | browser-use `Agent` |
| **屏幕理解 / 元素定位** | （视觉模型，走大脑） | OmniParser ⚠️CC-BY；UI-TARS | OmniParser ⚠️；Set-of-Mark |
| **底层驱动库（可直接依赖）** | uiautomator2(MIT)、adb | pyautogui(BSD)、pywinauto(BSD) | playwright(Apache-2.0) |
| **评测 / 基准环境** | AndroidWorld | OSWorld | WebArena、VisualWebArena |
| **全栈架构蓝图（看设计不抄码）** | — | UI-TARS-desktop（TypeScript） | UI-TARS-desktop |

---

## 3. 手机端 —— 首选：Open-AutoGLM

**[zai-org/Open-AutoGLM](https://github.com/zai-org/Open-AutoGLM)**　`Apache-2.0`　Python　26k★
> "An Open Phone Agent Model & Framework"。**它不是只给模型，而是给了一整套手机控制框架**，分层和我们要建的几乎一致，是手机端的首要参考。

重点读 `phone_agent/`：

| 路径 | 对应我们的模块 | 借鉴什么 |
|---|---|---|
| `phone_agent/agent.py` `PhoneAgent.run()/step()` | **编排层** | `while step < max_steps` 循环：截图观测 → 模型决策 → `parse_action` → 执行 → 判 `finish`；含异常兜底、敏感动作确认 |
| `phone_agent/actions/handler.py` `ActionHandler` | **动作层** | 动作名→处理函数映射（Tap/Type/Swipe/Back/Home/Launch/LongPress/Wait…）、**相对坐标→绝对像素换算**、`confirmation_callback`/`takeover_callback`（登录/验证码交还人，正好对上我们已有的 `ask_user_response` 钩子） |
| `phone_agent/adb/` | **设备层-Android** | `connection.py`（设备连接）、`device.py`（`tap/double_tap/long_press/swipe/back/home/launch_app/get_current_app`，全走 `adb shell input`）、`screenshot.py`、`input.py` |
| `phone_agent/hdc/` | **设备层-鸿蒙** | HarmonyOS 的 hdc 后端 |
| `phone_agent/xctest/` + `agent_ios.py` | **设备层-iOS** | iOS XCTest 后端 |
| `phone_agent/device_factory.py` | **设备层抽象** | 多后端工厂，统一 `get_screenshot()/tap()/swipe()/type_text()/back()/home()` 接口——**这就是我们 `DeviceBackend` 抽象基类的样板** |
| `phone_agent/model/client.py` | 大脑接入 | OpenAI 兼容 client 写法（替换成 MAI-UI 即可） |

**配套参考**
- **[openatx/uiautomator2](https://github.com/openatx/uiautomator2)**　`MIT`　Python　8.3k★：Android 原生自动化库，比裸 ADB 更强——能直接拿**控件树/resource-id/text**，适合做 a11y 观测与稳定点击，可作为 Android 后端的增强底座。
- **[google-research/android_world](https://github.com/google-research/android_world)**　`Apache-2.0`　Python：Android 自主 agent 的**环境 + 评测基准**，用于手机端成功率评测。

> 平台范围提醒：Open-AutoGLM 只覆盖**手机**（Android/iOS/鸿蒙），桌面与网页需另找。

---

## 4. 桌面端（Windows / macOS / Linux）

桌面端没有单一"标准答案"，按用途分三类参考：

### 4.1 闭环框架（编排 + 设备 + 动作，首选看这两个）
- **[simular-ai/Agent-S](https://github.com/simular-ai/Agent-S)**　`Apache-2.0`　Python　12k★
  开源 agentic 框架，"uses computers like a human"，**跨平台**（Windows/macOS/Linux）。它的 agent 循环、屏幕观测、动作到 OS 原语的映射，是桌面端最接近 Open-AutoGLM 角色的 Python 项目，**首选**。
- **[microsoft/UFO](https://github.com/microsoft/UFO)**　`MIT`　Python　9.6k★
  **Windows 专用**，基于 UI Automation（UIA）控件树，双 Agent（HostApp + 控件选择）架构。做 Windows 深度自动化时，它的**控件树遍历 + 元素定位**思路很值得借鉴；强依赖 Windows、偏 Office/Win32 应用。
- **[BAAI-Agents/Cradle](https://github.com/BAAI-Agents/Cradle)**　`MIT`　Python　2.6k★
  General Computer Control（GCC）框架，含**技能库（skill curation）、自我改进**的组织方式，适合参考"可复用操作技能"怎么沉淀。
- **[OthersideAI/self-operating-computer](https://github.com/OthersideAI/self-operating-computer)**　`MIT`　Python　10k★
  纯视觉 + pyautogui 的极简闭环，代码短，**适合快速理解"截图→坐标→pyautogui 点击"最小链路**，但能力浅、生产可用性弱。

### 4.2 底层驱动库（可直接作为依赖 import）
- **[asweigart/pyautogui](https://github.com/asweigart/pyautogui)**　`BSD-3-Clause`　Python　12.7k★：跨平台鼠标/键盘/截屏，纯坐标操作（视觉流的执行底座）。
- **[pywinauto/pywinauto](https://github.com/pywinauto/pywinauto)**　`BSD-3-Clause`　Python　6.2k★：**Windows** GUI 自动化，能按控件属性（title/auto_id）操作 Win32/UWP 应用，比盲点坐标稳。
- macOS 可参考 `pyautogui` + AppleScript / Accessibility API；Linux 可参考 `pyautogui` + `xdotool`（OSWorld 里有封装）。

### 4.3 真实环境 + 评测基础设施
- **[xlang-ai/OSWorld](https://github.com/xlang-ai/OSWorld)**　`Apache-2.0`　Python　3.1k★（NeurIPS 2024）
  桌面多模态 agent 的**真实计算机环境基准**。它的价值不只是打分：里面有 **VM 管理、跨 OS（Ubuntu/Windows/macOS）环境初始化、应用启动、状态校验/重置**这套基础设施——正是我们"真实环境/评测基础设施"模块在桌面端最完整的参考。

---

## 5. 网页 / 网站端

网页端比桌面简单，因为有 DOM 这一结构化通道（不必纯靠视觉坐标）：

- **[browser-use/browser-use](https://github.com/browser-use/browser-use)**　`MIT`　Python　**112k★（首选）**
  "Make websites accessible for AI agents"。把网页的 **DOM / 可交互元素抽取成 LLM 易读的索引**，agent 输出元素索引 + 动作，框架转成 Playwright 调用；也支持视觉/截图。它的**元素提取与编号、动作到浏览器操作的映射、agent 循环**是网页端最成熟的 Python 实现。
- **[microsoft/playwright-python](https://github.com/microsoft/playwright-python)**　`Apache-2.0`　Python　15k★
  浏览器自动化底座（导航、点击、填表、等待、截图、拿 DOM/aria 树）。**直接作为依赖**，browser-use 底层也是它。
- **[web-arena-x/webarena](https://github.com/web-arena-x/webarena)**　`Apache-2.0`　Python　1.6k★：自托管、可重置的**真实网站环境**（电商/Reddit/GitLab/地图等）+ 任务评测，网页端评测基准。
- **[web-arena-x/visualwebarena](https://github.com/web-arena-x/visualwebarena)**　`MIT`　Python：偏**视觉**的网页 agent 基准（含截屏、Set-of-Mark）。

> ⚠️ **[Skyvern-AI/skyvern](https://github.com/Skyvern-AI/skyvern)**　`AGPL-3.0`　Python　22.9k★：AI 浏览器工作流自动化，功能很强，但协议是 **AGPL-3.0（见第 7 节红线）**——**不要把它的代码拷进本项目**，可在线体验/读思路，或作为独立服务隔离调用。

---

## 6. 跨端"大脑"与全栈蓝图

- **[bytedance/UI-TARS](https://github.com/bytedance/UI-TARS)**　`Apache-2.0`　Python　11.4k★
  字节原生 GUI Agent **模型**仓库（权重 + 系统提示词 + 动作空间 + 推理/训练方案）。我们大脑用 MAI-UI，但可借鉴它的**提示词设计、统一动作空间、desktop/mobile 动作约定**。
- **[bytedance/UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop)**　协议需逐文件核对（仓库标注 NOASSERT，主体 Apache-2.0）　**TypeScript**　38.8k★
  "Open-Source Multimodal AI Agent Stack"：桌面 + 移动（ADB/云手机）+ 浏览器、多模型接入、设备配置、operator 交互的**产品级全栈**。语言是 TS，**不适合直接搬代码**，但它是**功能划分与架构蓝图**最全的参考——我们设计多端统一抽象、设备管理 UI、云手机接入时，对照它想"要做哪些功能"。
- **[microsoft/OmniParser](https://github.com/microsoft/OmniParser)**　`CC-BY-4.0`　25k★
  纯视觉屏幕解析（检测可交互元素 + 输出标号，Set-of-Mark）。**协议不是软件开源协议**（见第 7 节），用作"视觉元素定位"服务可参考其思路，但权重/代码的商用与分发条款需单独确认。

---

## 7. 协议合规红线（重要）

借鉴/拷贝代码前先看协议，分三档：

| 档位 | 协议 | 项目举例 | 能否拷代码进本项目 |
|---|---|---|---|
| ✅ **宽松可用** | Apache-2.0 / MIT / BSD-3-Clause | Open-AutoGLM、Agent-S、OSWorld、UFO、Cradle、browser-use、playwright、pyautogui、pywinauto、uiautomator2、AndroidWorld、WebArena、UI-TARS | **可以**。需保留原版权头，并在 `NOTICE` 追加该项目署名 |
| ⚠️ **谨慎 / 仅限服务化调用** | CC-BY-4.0（非软件协议） | OmniParser（模型权重/数据） | 不直接拷进分发代码；可作为**独立服务**调用，商用/分发前逐条确认条款并署名 |
| ❌ **不要拷入本项目** | **AGPL-3.0**（强 copyleft） | **Skyvern**、**OpenInterpreter/open-interpreter** | 若把其代码整合进本项目并对外（含网络）提供服务，**必须开源整个项目**。只"读思路"，或严格隔离成独立进程/服务通过网络调用 |

> UI-TARS-desktop 仓库 GitHub 识别为 `NOASSERT`（多协议混合），引用其代码前务必打开具体文件头与 LICENSE 逐一确认。

**统一合规动作**：
1. 从上述项目**拷贝或改写**任何文件 → 保留该文件原版权头，在本项目 `NOTICE` 追加对应项目的名称、协议、来源链接。
2. 我们**原创**的文件 → 用 `Copyright (c) 2026, 东篱馆主` 头。
3. 新增第三方依赖 → 优先选 Apache-2.0/MIT/BSD，引入 AGPL 依赖前先评估。

---

## 8. 建议的落地目录结构（借鉴 Open-AutoGLM 分层）

```
src/                       # 已有：大脑层（MAI-UI 推理、prompt、记忆）
platforms/                 # 设备/平台层（对应 phone_agent）
  base.py                  #   DeviceBackend 抽象基类：screenshot/tap/swipe/type/back/home/launch/get_state
  phone/
    android/               #   借鉴 Open-AutoGLM adb/；可叠加 uiautomator2
    ios/                   #   借鉴 xctest/
    harmony/               #   借鉴 hdc/
  desktop/
    windows/               #   pywinauto + pyautogui；借鉴 UFO 控件树
    macos/  linux/         #   借鉴 Agent-S / OSWorld
  web/
    browser/               #   playwright 底座 + browser-use 的 DOM 提取思路
actions/
  handler.py               # 动作 JSON→平台原语、坐标换算（借鉴 actions/handler.py）
orchestration/
  agent_loop.py            # 观察→决策→执行→判终 闭环（借鉴 agent.py PhoneAgent）
state/
  state_manager.py         # 前台 App/窗口/页面、界面元素上下文
evaluation/                # 真实环境与评测：借鉴 OSWorld / AndroidWorld / WebArena
```

---

## 9. 一句话优先级

1. **手机端**：直接精读并复刻 **Open-AutoGLM 的 `phone_agent/`**（agent.py + actions/handler.py + adb/ + device_factory.py）。
2. **桌面端**：编排/跨平台看 **Agent-S**，Windows 深做看 **UFO + pywinauto/pyautogui**，环境与评测看 **OSWorld**。
3. **网页端**：直接用 **Playwright 做底座 + browser-use 的元素提取/动作映射思路**；评测用 **WebArena / VisualWebArena**。
4. **大脑与动作空间**：参考 **UI-TARS** 的 prompt/动作约定；**全栈功能蓝图**对照 **UI-TARS-desktop**（TS，只看设计）。
5. **协议**：只把 Apache-2.0/MIT/BSD 代码拷进来；**AGPL（Skyvern、open-interpreter）不拷**，**OmniParser（CC-BY）服务化**。
