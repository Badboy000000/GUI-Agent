# Android 真实设备 Smoke

这组 smoke 测试默认跳过，必须显式选择一台 Android 设备。测试只执行 ADB 的健康检查、屏幕尺寸读取、截图读取和前台包名读取；不会点击、输入、启动应用或发送系统按键。

前置条件：本机可执行 `adb`，手机或模拟器已开启 USB 调试并完成授权，且 `adb devices` 中目标设备状态为 `device`。

PowerShell 中运行：

```powershell
$env:GUI_AGENT_ANDROID_SMOKE = "1"
$env:GUI_AGENT_ANDROID_SERIAL = "你的 adb 序列号"
python -m pytest tests/smoke/test_android_device_smoke.py -m android_smoke -q
```

不设置这两个环境变量时，日常 `pytest` 不会访问真实设备。若连接了多台设备，序列号仍为必填项，避免测试误连到默认设备。
