# Light - 智能屏幕亮度管理器

一款基于神经网络的 Windows 屏幕亮度自动调整工具，通过摄像头采集环境光线数据，智能调节屏幕亮度。

## 功能特性

- **摄像头环境感知**：通过摄像头实时采集环境图像，分析光线强度、对比度、色彩饱和度等多维度特征
- **智能亮度调节**：基于多层神经网络模型（14维特征输入 → 3层隐藏层 → 输出），自动调整屏幕亮度
- **多显示器支持**：自动检测并支持多显示器独立亮度调节
- **系统托盘**：运行后自动最小化到系统托盘，左键点击打开管理界面，右键快捷操作
- **手动调节**：支持滑动条手动调整屏幕亮度
- **开机自启**：可配置开机自动启动
- **桌面通知**：实时显示亮度调整状态通知
- **模型训练**：内置模型训练功能，支持自定义训练神经网络（Adam优化器）
- **显示器状态检测**：自动检测显示器开关状态，避免在显示器关闭时误调亮度

## 技术栈

| 模块 | 技术 |
|------|------|
| GUI 界面 | Tkinter + ttk + PyQt5 |
| 系统托盘 | pystray |
| 图像处理 | OpenCV + NumPy + Pillow |
| 神经网络 | NumPy（自定义实现，支持ReLU/Tanh激活函数、Adam优化器、LRU缓存） |
| 系统控制 | pywin32 + WMI + ctypes + winreg |

## 项目结构

```
light/
├── source/
│   ├── app.py              # 主应用程序（GUI、托盘、核心逻辑、单实例检测）
│   ├── liangdu.py          # 亮度处理、摄像头捕获、神经网络模型、显示器检测
│   ├── config.py           # 配置管理
│   ├── utils.py            # 系统工具（开机自启、Toast通知等）
│   ├── notification.py     # 桌面通知系统
│   ├── config.json         # 应用配置文件
│   ├── requirements.txt    # Python 依赖
│   ├── app.spec            # PyInstaller 打包配置
│   ├── brightness_manager.spec
│   ├── favicon.ico         # 应用图标
│   └── brightness_model.npz # 预训练神经网络模型
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

```bash
pyinstaller source/app.spec
```

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
4. 管理界面中可手动拖动滑动条调节亮度，或点击训练按钮训练模型

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

## 亮度调节方式

应用按以下顺序尝试调节屏幕亮度：

1. **WMI方法**：通过`WmiMonitorBrightnessMethods`设置（适用于大多数Windows系统）
2. **Windows API**：通过`SetMonitorBrightness`函数
3. **注册表方法**：修改显卡驱动注册表项
4. **PowerShell命令**：通过WMI PowerShell接口

## 许可证

MIT
