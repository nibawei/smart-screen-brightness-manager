# Light - 智能屏幕亮度管理器

一款基于神经网络的 Windows 屏幕亮度自动调整工具，通过**摄像头采集**环境光线数据，智能调节屏幕亮度。

## 功能特性

- **摄像头环境感知**：通过摄像头实时采集环境图像，分析光线强度与对比度
- **智能亮度调节**：基于神经网络模型处理摄像头采集的数据，自动调整屏幕亮度
- **系统托盘**：运行后自动最小化到系统托盘，左键点击打开管理界面，右键快捷操作
- **手动调节**：支持滑动条手动调整屏幕亮度
- **开机自启**：可配置开机自动启动
- **桌面通知**：实时显示亮度调整状态通知
- **模型训练**：内置模型训练功能，可自定义训练神经网络

## 技术栈

| 模块 | 技术 |
|------|------|
| GUI 界面 | Tkinter + ttk |
| 系统托盘 | pystray |
| 图像处理 | OpenCV + Pillow |
| 神经网络 | NumPy |
| 系统控制 | pywin32 + WMI |

## 项目结构

```
light/
├── source/
│   ├── app.py              # 主应用程序（GUI、托盘、核心逻辑）
│   ├── liangdu.py          # 亮度处理、摄像头捕获、神经网络模型
│   ├── config.py           # 配置管理
│   ├── utils.py            # 系统工具（开机自启、单实例等）
│   ├── notification.py     # 桌面通知系统
│   ├── config.json         # 应用配置文件
│   ├── requirements.txt    # Python 依赖
│   ├── app.spec            # PyInstaller 打包配置
│   ├── brightness_manager.spec
│   ├── favicon.ico         # 应用图标
│   ├── brightness_model.npz # 预训练神经网络模型
│   └── documents/          # 项目文档
├── brightness_model.npz    # 根目录模型副本
└── .gitignore
```

## 安装与使用

### 环境要求

- Windows 操作系统
- Python 3.8+

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

## 许可证

MIT
