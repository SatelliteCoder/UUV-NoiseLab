# UUV-NoiseLab

UUV 目标辐射噪声仿真平台 —— 纯 Python 半经验噪声模型 + 轻量 Web 界面，支持点源/线源/面源/体源四类等效声源仿真与三维可视化。

> 本项目为研究与工程原型。默认参数仅用于算法链路验证与可视化演示，未经真实 UUV 实测数据标定，不应视为特定装备的真实声学指纹。

## 功能概览

- 支持点源、线源、面源、体源四种等效声源类型，可单选或同时运行全部四类
- 合成 UUV 目标辐射噪声半经验信号：螺旋桨高斯脉冲调制包络、空化/螺旋桨宽带连续谱、机械宽带噪声、艇体流噪声、轴频/叶频/电机线谱（含频率抖动）
- 输出 PNG 频谱图/波形图/LOFAR/DEMON/总览、WAV 音频预览、CSV 数据表、NPZ 压缩结果
- **三维交互式几何可视化**：使用 Three.js 渲染等效源空间分布，支持鼠标旋转/缩放/平移
- Web 界面：左侧参数配置 + 右侧结果标签页切换，整屏适配无需滚动

## 项目结构

```text
UUV-NoiseLab/
├── backend/
│   ├── server.py              # Python HTTP 后端（标准库，无需框架）
│   └── uuv_noise/
│       ├── __init__.py
│       └── simulation.py      # 仿真内核（NumPy/SciPy 信号合成）
├── frontend/
│   ├── index.html             # 页面结构
│   ├── styles.css             # 样式
│   └── app.js                 # 前端逻辑 + Three.js 3D 渲染
├── jobs/                      # 仿真任务输出目录（运行时自动生成）
├── requirements.txt           # Python 依赖
├── start_local_web.bat        # Windows 一键启动脚本
└── README.md
```

## 环境要求

- Windows / Linux / macOS
- Python 3.10+
- 依赖：NumPy、SciPy、Pillow（PIL）

安装依赖：

```bash
pip install -r requirements.txt
```

## 快速启动

```bash
python backend/server.py
```

浏览器打开：

```text
http://127.0.0.1:8765
```

Windows 也可双击 `start_local_web.bat` 启动。

如需更换端口：

```bash
# Linux/macOS
export UUV_WEB_PORT=8768
python backend/server.py

# Windows PowerShell
$env:UUV_WEB_PORT='8768'
python backend/server.py
```

## 架构说明

```text
浏览器 (前端)
  │  fetch() 发送 JSON 参数
  ▼
Python 后端 (server.py)
  │  规范化参数 → 创建任务 → 开线程异步计算
  ▼
仿真内核 (simulation.py)
  │  信号合成 → 频谱分析 → 图片/音频/数据文件输出
  ▼
结果回传
  前端轮询任务状态 → 成功后加载图片/音频/3D 数据
```

- 前端与后端通过 REST API（HTTP + JSON）通信
- 后端使用 Python 标准库 `ThreadingHTTPServer`，无需 Flask/Django
- 仿真任务异步执行，前端每 2 秒轮询状态
- 3D 几何渲染由前端 Three.js 完成，后端提供离散源坐标数据

## 界面说明

采用两栏布局，整屏适配：

- **左侧 — 参数配置**：声源类型选择、UUV 几何与推进参数、采样与接收位置、离散源数量、背景噪声开关
- **右侧 — 仿真结果**（顶部标签页切换）：
  - 总览：关键指标卡片 + 总览图
  - 源级谱 / 时域波形 / LOFAR / DEMON：频谱分析图
  - 三维几何：Three.js 交互式 3D 可视化（等效源分布 + UUV 艇体 + 接收器位置）
  - 音频：WAV 在线试听
  - 文件：CSV/NPZ/TXT 下载

## 输出文件

每次仿真在 `jobs/<job_id>/` 下生成：

| 文件 | 说明 |
|------|------|
| `uuv_source_signal.wav` | 1 m 等效源信号预览 |
| `uuv_received_target.wav` | 传播后目标信号预览 |
| `uuv_received_mix.wav` | 目标 + 背景噪声混合预览 |
| `uuv_source_spectrum.png` | 源级谱图 |
| `uuv_waveforms.png` | 时域波形图 |
| `uuv_lofar.png` | LOFAR 低频分析图 |
| `uuv_demon.png` | DEMON 包络谱图 |
| `uuv_source_geometry_3d.png` | 等效源几何分布图（静态版） |
| `uuv_summary.png` | 总览图 |
| `uuv_source_spectrum.csv` | 频谱数据表 |
| `uuv_tonal_lines.csv` | 线谱表 |
| `uuv_source_geometry.csv` | 源单元坐标与权重 |
| `uuv_source_model_result.npz` | NumPy 压缩结果 |
| `uuv_noise_description.txt` | 信号说明 |

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | 原生 HTML/CSS/JavaScript + Three.js (CDN) |
| 后端 | Python 标准库 `http.server` |
| 仿真 | NumPy + SciPy + Pillow |
| 3D 可视化 | Three.js r128 + OrbitControls |

## 重要限制

本项目是工程原型。默认参数仅用于算法链路验证和可视化演示，未经过真实 UUV 实测数据标定，不能视为特定装备的真实声学指纹。未来可扩展为 FastAPI 后端、接入 Bellhop/RAM/Kraken 传播模型、添加用户认证与任务队列。
