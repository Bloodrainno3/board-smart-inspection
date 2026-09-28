# 木板智能检测：311mm / 658mm

两款已交付上位机的源码，供项目内部协作。两款程序分别维护，具有自动单板采图、独立板缝测量、四种木皮颜色分级、采图联动与 TCP JSON 结果输出。

| 程序 | 源码目录 | 对应版本 | 相机接口 |
| --- | --- | --- | --- |
| 311mm木板智能检测 | [代码/311mm木板智能检测](代码/311mm木板智能检测) | 2026.09.22-online | 原相机 GigE SDK |
| 658mm木板智能检测 | [代码/658mm木板智能检测](代码/658mm木板智能检测) | 2026.09.23-cl-config | IKap Camera Link；含 CLConfigurator 灯光与校正 SDK |

本次源码整理保留上述版本的运行代码、原生桥接 DLL 和厂商 SDK DLL。补充依赖清单及开发说明。现场串口、采图路径、生产数据、TCP 密钥和本机环境不随仓库上传；首次运行需要配置设备，或使用程序的“导入现场设置”功能。658mm 的灯光、明暗场校正和颜色效果仍需在实际相机上验证。

## 获取源码与模型

这是私有仓库。协作者接受仓库邀请后，使用自己的 GitHub 账号克隆；网页下载源码 ZIP 也可以。

```powershell
git clone https://github.com/Bloodrainno3/board-smart-inspection.git
cd board-smart-inspection
```

模型单独放在 [配套 Release](https://github.com/Bloodrainno3/board-smart-inspection/releases/tag/v2026.09.23-source)。下载 `model-assets-20260923.zip` 放到仓库根目录，不用手动解压。它包含板缝 ONNX / best.pt，以及四种木皮分级 ONNX。仓库中的模型 JSON 描述与这些权重配套。模型包约 520 MiB，恢复到两款程序共需约 1.1 GiB 空间。

拥有 GitHub CLI 的协作者也可在登录后执行：

```powershell
gh release download v2026.09.23-source --repo Bloodrainno3/board-smart-inspection --pattern model-assets-20260923.zip
```

## PyCharm 开发和运行

环境基线：**Windows x64、Python 3.13 x64**。在仓库根目录打开 PowerShell：

```powershell
py -3.13 -m venv .\软件\venv
& '.\软件\venv\Scripts\python.exe' -m pip install -r '.\代码\658mm木板智能检测\requirements-dev.txt'
& '.\软件\venv\Scripts\python.exe' '.\tools\restore_models.py' --archive '.\model-assets-20260923.zip'
```

两款程序使用相同的 Python 依赖，只需安装一次。恢复脚本先校验 ZIP 和每个模型的 SHA-256，再放入两款程序各自的 `models/`，已有相同文件会跳过；已有不同权重会拒绝覆盖。

PyCharm 打开整个仓库，解释器选择 `<仓库目录>\软件\venv\Scripts\python.exe`，运行所需相机目录中的 `main.py`；工作目录设置为该 `main.py` 所在目录。也可以双击对应目录中的 `从源码运行.cmd`，或者执行：

```powershell
& '.\软件\venv\Scripts\python.exe' '.\代码\311mm木板智能检测\main.py'
# 或者运行 658mm 版：
& '.\软件\venv\Scripts\python.exe' '.\代码\658mm木板智能检测\main.py'
```

仅运行推理无需 PyTorch、pythonnet 或在线下载预训练参数。重新导出模型时才使用各项目的 `requirements-export.txt`、`export_model.py` 和 `export_grading.py`。四套分级训练工程和原始分级 checkpoint 不在此仓库，分级 ONNX 已提供。

### 实际设备

- 已带应用调用的相机 DLL；USB 串口驱动和 658mm 的 IKap 采集卡系统驱动仍需在工控机安装。DLL 不等于采集卡驱动安装包。
- PLC 的 COM 口、波特率、校验位以及相机选择按现场设置；658mm 需选择与现场相机对应的 `.vlcf` 文件。
- 311mm 目录包含原版 `calibration/default.calib`，使用现场校正结果时在界面选择现场文件。相机明暗场校正与测量用的毫米标定是不同功能。
- [658mm 灯光与校正说明](代码/658mm木板智能检测/相机灯光与校正使用说明.md)；生产用原图编号、保存路径及已应用的距离标定需现场确认。

## 师兄从哪些文件看起

以下路径均在所选相机项目下：

| 功能 | 主要文件 |
| --- | --- |
| 程序入口与主界面 | `main.py`、`boardcapture/gui.py` |
| 采图状态机、PLC、相机和存图 | `boardcapture/engine.py`、`plc.py`、`triggers.py`、`camera.py`、`storage.py` |
| 原生相机 SDK 桥接 | 311mm：`native/bridge.cpp`；658mm：`native/ikap_bridge.cpp` |
| 658mm 灯光、明暗场校正、预览 | `boardcapture/cl_config.py`、`cl_config_gui.py`、`cl_preview.py`、`native/cl_config_bridge.cpp` |
| 板缝分割、区域四角及距离 | `boardcapture/vision.py`、`boundary_regions.py`、`geometry.py`、`vision_gui.py` |
| 四种木皮颜色分级 | `boardcapture/grading.py`、`grading_gui.py`、`models/grading/manifest.json` |
| 采图后并行处理、TCP 输出 | `boardcapture/processing.py`、`processing_config.py`、`result_stream.py` |

主界面可选只采图、采图＋板缝、采图＋分级、采图＋两者。完整板图保存后进入处理队列，与下一张板采集并行；独立识别/分级窗口也可以手动导入图片或文件夹。四种木皮由操作员选择，程序不自动判定材种。

TCP 协议及确认/重连规则见各项目的 [接口说明](代码/658mm木板智能检测/TCP接口说明.md)，接收示例见 [对接示例/TCP接收示例.py](对接示例/TCP接收示例.py)。端口默认 3110 / 6580，是否启用和网络地址在界面设置。后续切割机与分选设备的动作控制由接收端按实际协议实现。

## 检查与打包

基础检查示例（不连接设备）：

```powershell
$repo = (Get-Location).Path
$python = Join-Path $repo '软件\venv\Scripts\python.exe'
Set-Location '.\代码\658mm木板智能检测'
$env:QT_QPA_PLATFORM = 'offscreen'
& $python -m pytest tests/test_core.py tests/test_plc_ladder.py tests/test_gui.py -q
& $python main.py --smoke-test
Remove-Item Env:QT_QPA_PLATFORM
Set-Location $repo
```

完整测试中的 `tests/test_native.py` 和 658mm `tests/test_cl_config.py` 依赖测试 DLL，先运行项目下的 `tests/build_fake.cmd` / `tests/build_fake_cl.cmd`。这些脚本及原生编译脚本使用 VS2022 Community 的 C++ x64 工具链默认路径，安装位置不同需调整脚本。运行已有桥接 DLL 或修改 Python 时无需重新编译 C++。

`tests/test_field_wide_masks.py` 依赖未上传的现场图像，默认不能直接运行；需要将获准共享的对应图像放回它的 fixtures 目录。`test_grading.py` 的训练预处理对照测试另需 PyTorch；模型相关测试须先恢复模型。测试源码保留用于后续维护，不把未运行的测试视为通过。

在仓库根目录执行对应命令打包：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File '.\代码\311mm木板智能检测\build.ps1'
powershell -NoProfile -ExecutionPolicy Bypass -File '.\代码\658mm木板智能检测\build.ps1'
```

打包输出在 `软件/待发布/`，构建缓存和环境不上传 Git。发布到工控机时保留整个输出文件夹，并导入同款相机的现场设置。

## 协作与版本

`SOURCE_VERSIONS.json` 记录原版文件指纹与本次分享整理的差异；`model-assets.json` 记录配套模型指纹。首次上传标签为 `v2026.09.23-source`，之后的修改建议另建分支并提交 Pull Request，便于对照已交付版本。

两套目录各自独立；修改共用算法时，需要检查是否也要同步到另一款相机版本。厂商 SDK 的头文件、导入库和 DLL 保持原文件，仓库不另行声明它们为开源代码。
