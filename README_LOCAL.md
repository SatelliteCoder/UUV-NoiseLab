# UUV 目标辐射噪声 Web 本地原型

## 启动

在 PowerShell 中运行：

```powershell
cd <项目根目录>
python .\backend\server.py
```

浏览器打开：

```text
http://127.0.0.1:8765
```

如果 8765 被旧服务占用，可以换端口：

```powershell
$env:UUV_WEB_PORT='8768'
python .\backend\server.py
```

## 第一版功能

- 前端：参数表单、点源/线源/面源/体源/全部四类选择、结果图展示、WAV 播放、CSV/NPZ/TXT 下载。
- 后端：Python 标准库 HTTP 服务，不依赖 FastAPI/Node。
- 计算内核：纯 Python 半经验 UUV 目标辐射噪声模型，不再依赖 MATLAB。
- 输出目录：`jobs/<任务ID>/`，每次运行单独保存结果。

## 算法组成

生成的是 UUV 目标辐射噪声半经验信号，不是任意白噪声。信号组成包括：

- 螺旋桨高斯脉冲调制包络；
- 空化/螺旋桨宽带连续谱；
- 机械宽带噪声；
- 艇体流噪声；
- 轴频、叶频 BPF、电机线谱；
- 线谱轻微频率抖动。

## 输出文件

`uuv_source_signal.wav` 是 1 m 等效源信号预览。
`uuv_received_target.wav` 是简化传播后的目标信号预览。
`uuv_received_mix.wav` 是目标信号加背景噪声的接收端混合预览。
`uuv_source_model_result.npz` 是 NumPy 压缩结果文件。

## 重要限制

本项目是工程原型。默认参数只用于算法链路验证和可视化演示，未经过真实 UUV 实测数据标定，不能视为特定装备的真实声学指纹。
