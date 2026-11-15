# Light - 智能屏幕亮度管理器

一款基于神经网络的 Windows 屏幕亮度自动调整工具，通过摄像头采集环境光线数据，智能调节屏幕亮度。

## 功能特性

- **摄像头环境感知**：通过摄像头实时采集环境图像，分析光线强度、对比度、色彩饱和度等多维度特征
- **摄像头保守选择**：优先物理摄像头、避开手机投屏等虚拟摄像头；设备断开时不静默切换，只提示重新选择
- **扫描不启动摄像头**：设备扫描只用 DirectShow 枚举，不打开任何摄像头，虚拟摄像头（手机投屏 / OBS）不会被意外唤醒
- **按设备拆分参数**：每台摄像头一份相机模型、每块屏幕一条亮度曲线，参数互不干扰
- **设备自动登记**：自动记录接入过的摄像头与屏幕（`devices.json`），并标注是否训练过、样本数
- **多显示器支持**：每块屏幕单独设置亮度；勾选「参与自动调节」的屏才会被自动写入
- **多后端调光**：外接屏优先 DDC/CI、内置屏优先 WMI，失败自动降级到伽马表软件调光；写后回读校验
- **系统托盘**：运行后自动最小化到系统托盘，左键点击打开管理界面，右键快捷操作
- **手动调节**：下拉选择屏幕 + 滑动条单独调整该屏亮度
- **开机自启**：可配置开机自动启动
- **桌面通知**：实时显示亮度调整状态通知（按屏幕定位）
- **模型训练**：内置「训练当前屏」「训练全部勾选屏」，逐屏记录样本并拟合曲线（Adam优化器）
- **显示器状态检测**：自动检测显示器开关状态，避免在显示器关闭时误调亮度

## 技术栈

| 模块 | 技术 |
|------|------|
| GUI 界面 | Tkinter + ttk + PyQt5 |
| 系统托盘 | pystray |
| 图像处理 | OpenCV + NumPy + Pillow |
| 神经网络 | NumPy（自定义实现，支持ReLU/Tanh激活函数、Adam优化器、LRU缓存） |
| 设备识别 | comtypes（DirectShow DevicePath）+ WMI（InstanceName） |
| 系统控制 | pywin32 + WMI + ctypes（dxva2 DDC/CI）+ winreg |

## 项目结构

```
light/
├── source/
│   ├── app.py              # 主应用程序（GUI、托盘、每屏控制、训练入口）
│   ├── devices.py          # 设备枚举与登记（DirectShow / WMI / devices.json）
│   ├── models.py           # 相机模型 + 屏幕曲线 + 样本文件
│   ├── liangdu.py          # 特征提取、神经网络、亮度读写（WMI / DDC）
│   ├── config.py           # 配置管理
│   ├── utils.py            # 系统工具（开机自启、Toast通知等）
│   ├── notification.py     # 桌面通知系统
│   ├── tests/              # 单元测试（mock 设备，无需硬件）
│   ├── tools/              # 实机诊断脚本（device_probe.py）
│   ├── config.json         # 应用配置文件
│   ├── requirements.txt    # Python 依赖
│   ├── app.spec            # PyInstaller 打包配置
│   ├── brightness_manager.spec
│   ├── favicon.ico         # 应用图标
│   └── brightness_model.npz # 相机先验模型
├── brightness_model.npz    # 根目录模型副本
└── .gitignore
```

## 安装与使用

### 环境要求

- Windows 操作系统
- Python 3.8+
- 摄像头设备（用于环境光线采集）

### 安装依赖

```bash
pip install -r source/requirements.txt
```

### 运行

```bash
python source/app.py
```

### 打包为可执行文件

单文件版本（启动时解压到 `%TEMP%`，体积小、便于分发）：

```bash
pyinstaller source/app.spec
```

目录版本（不解压、启动快，部分安全软件会拦截单文件版解压时优先用这个）：

```bash
cd source
pyinstaller --onedir --windowed --name Light --icon favicon.ico ^
  --add-data "favicon.ico;." --add-data "brightness_model.npz;." --add-data "config.json;." ^
  app.py
```

> PyInstaller 6.x 会把 `--onedir` 的资源放进 `_internal/`，程序已按
> `用户数据目录 → exe 同级目录 → 打包资源目录` 的顺序查找模型和配置，
> 两种打包方式都能读到自带的 `brightness_model.npz` / `config.json`。

### 运行时文件位置

| 文件 | 读取 | 写入 |
|------|------|------|
| `config.json` | 用户目录优先，其次 exe 同级，最后打包内置 | `%LOCALAPPDATA%\BrightnessManager\config.json` |
| `brightness_model.npz` | 用户目录优先，其次 exe 同级，最后打包内置 | 只读，作为新摄像头的先验 |
| `devices.json` | 用户目录 | `%LOCALAPPDATA%\BrightnessManager\devices.json` |
| `cameras/<camera_id>.npz` | 用户目录 | 每台摄像头的模型参数 |
| `screens/<screen_id>.json` | 用户目录 | 每块屏幕的亮度曲线 |
| `samples/<camera>__<screen>.npz` | 用户目录 | 该「摄像头 × 屏幕」的样本 |
| `training_samples.npz` | 用户目录（旧版） | 作为 legacy 样本参与所有设备训练（权重 0.5） |

自训练/预训练得到的模型始终写入用户数据目录，因此程序放在
`Program Files` 等只读目录下也能正常保存。

## 配置说明

配置文件 `source/config.json`：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `auto_brightness` | 是否启用自动亮度调节 | `true` |
| `startup` | 是否开机自启 | `false` |
| `model_path` | 神经网络模型路径 | `brightness_model.npz` |
| `log_level` | 日志级别 | `info` |
| `check_interval` | 自动检查间隔（秒） | `1800` |

## 使用说明

1. 启动应用后，窗口会自动最小化到系统托盘
2. **左键点击**托盘图标：打开管理界面
3. **右键点击**托盘图标：
   - 打开管理界面
   - 切换自动亮度开关
   - 立即调整亮度
   - 退出应用
4. 管理界面里：
   - 「当前屏幕」下拉框选择要调整的屏，滑动条只作用于这块屏；
   - 「显示器选择」复选框决定哪些屏参与自动调节（勾选才会被自动写入）；
   - 「获取亮度」采集一次环境画面，为每块屏算出建议值；
   - 「应用」只写入当前屏；「训练当前屏」/「训练全部勾选屏」会记录样本并更新曲线。

摄像头默认选择是保守的：首次运行优先物理摄像头，避开手机投屏等虚拟摄像头；上次使用的摄像头未接入时
不会自动切换到其它设备（避免悄悄改用手机摄像头），而是暂停自动调节并提示重新选择。
摄像头打不开、被占用或断开时只记录日志并提示，不会影响其它功能。

扫描/刷新设备列表只做 DirectShow 枚举，不会打开摄像头；只有点「获取亮度」或自动调整时才会打开当前选中的摄像头。

## 模型训练

模型参数拆成两层：

| 部分 | 文件 | 作用 |
| ---- | ---- | ---- |
| 相机模型 | `cameras/<camera_id>.npz` | 14 维画面特征 → 参考亮度（每台摄像头独立） |
| 屏幕曲线 | `screens/<screen_id>.json` | 参考亮度 → 实际亮度（每块屏独立的 5 节点单调曲线） |
| 样本 | `samples/<camera>__<screen>.npz` | 该组合下用户确认过的「环境 → 亮度」 |

- 新摄像头首次使用会从 `brightness_model.npz` 复制一份独立模型，之后各训各的。
- 新屏幕默认恒等曲线，行为与改造前一致；训练后用「缩放 + 偏移」的仿射拟合更新，单条样本也能立刻生效，且保持单调。
- `训练当前屏`：只记录下拉选中屏的一条样本并更新它的曲线。
- `训练全部勾选屏`：为每块「勾选参与自动、且手动设置过亮度」的屏各记一条样本，更新全部曲线，并在样本 ≥ 20 条时用合成先验混合微调相机模型。
- 旧版 `training_samples.npz` 作为 legacy 样本参与所有设备训练，权重 0.5。

训练改为批量向量化实现（与原来逐样本 SGD 的目标函数、Adam 公式一致），1000 轮训练从数分钟降到 1-2 秒，并加入验证集早停。

## 亮度调节方式

按屏幕类型选择后端，失败会自动降级到下一个：

| 屏幕类型 | 尝试顺序 |
| --- | --- |
| 外接屏（HDMI / DisplayPort / DVI / VGA…） | DDC/CI → WMI → 伽马 |
| 内置屏（`内置屏` / `LVDS` / `DisplayPort(内置)`） | WMI → DDC/CI → 伽马 |

1. **DDC/CI**：`dxva2.dll` 的 `GetVCPFeatureAndVCPFeatureReply` / `SetVCPFeature(0x10)`。
   按显示器返回的 VCP 最大值缩放目标值（有的屏不是 0-100），每次写入重试两次并回读；
   这是 Twinkle Tray、Monitorian 等工具控制外接屏的主要方式。
2. **WMI**：`WmiMonitorBrightnessMethods`，按 `InstanceName` 精确匹配，找不到时按序号回退。
   Windows 对外接屏经常「列得出来但写不进去」，所以外接屏不把 WMI 作为首选。
3. **伽马表软件调光**：`SetDeviceGammaRamp`，DDC/WMI 都不可用时兜底。只能压暗、不能提亮，
   100% 表示恢复原样；HDR 模式下部分驱动会拒绝。程序退出时会自动把伽马表恢复。

物理后端写入后会回读校验，偏差超过 2 就判为失败并继续尝试下一个后端；全部失败才报错。
通知按 `\\.\DISPLAYn` 映射到对应屏幕。

## 屏幕参数档案（含 HDMI 外接屏）

每块屏幕都会建档，HDMI 外接屏通过 EDID 补全厂商/型号等信息，不再只显示 `Generic PnP Monitor`：

| 字段 | 来源 | 说明 |
| ---- | ---- | ---- |
| `manufacturer` / `manufacturer_id` | EDID / WMI | 厂商（PNP ID 解码，`DEL`→`DELL`、`BOE`→`BOE` 等） |
| `model` / `product_code` | EDID / WMI | 型号与产品代码 |
| `serial` / `serial_number` | EDID | 序列号 |
| `year` / `week` | EDID | 生产年份与周 |
| `size_cm` | EDID | 物理尺寸（厘米） |
| `preferred_resolution` / `resolution` / `refresh_hz` | EDID / 显示模式 | 首选分辨率、当前分辨率与刷新率 |
| `connection` | WMI `VideoOutputTechnology` | HDMI / DisplayPort / DVI / 内置屏等 |
| `ddc` / `brightness_max` | DDC/CI | 是否走 DDC、亮度上限 |
| `edid` | 注册表 | 是否成功读到 EDID |

参数写入两处：

- `devices.json` 的 `screens.<id>.params`（设备登记）；
- 训练保存曲线时同步写入 `screens/<id>.json` 的 `device` 字段（随屏幕档案一起备份）。

「已登记设备」区会显示每块屏的连接类型、分辨率和物理尺寸。

## 测试

```bash
cd source
python -m unittest discover -s tests -v
python tools/device_probe.py            # 实机诊断（只读）
python tools/device_probe.py --set 60   # 写入 60% 并回读校验
```

## 亮度计算原理

应用通过摄像头采集20帧图像，提取以下14维特征向量：

| 特征 | 说明 |
|------|------|
| 平均亮度 | 灰度图像平均值 |
| 平均对比度 | 灰度图像标准差 |
| 亮度变化率 | 帧间亮度差异 |
| 移动平均亮度 | 平滑后的亮度趋势 |
| 平均R/G/B值 | 色彩通道均值 |
| 色彩饱和度 | HSV色彩空间饱和度 |
| 感知亮度 | Gamma校正后的亮度 |
| 亮度标准差 | 亮度分布离散程度 |
| 亮度峰度/偏度 | 亮度分布形态特征 |
| 清晰度 | 拉普拉斯算子方差 |
| 空间加权亮度 | 中心区域加权亮度 |

综合评分公式为各特征加权求和，最终映射到1-100的亮度值。

**目标亮度只由环境光照决定。** 摄像头读数被拆成「环境光贡献」和「屏幕发光贡献」
两部分：生成训练数据时，屏幕亮度与屏幕内容亮度作为独立的干扰变量随机采样，
目标值只随环境光照变化，因此从深色主题切到白底页面不会改变标签。改造前那条
`screen_darkness`（屏幕越暗 → 推荐亮度越高）的加权项已经删除，避免形成
「屏幕越暗 → 推荐越亮」的正反馈。

## 许可证

MIT
