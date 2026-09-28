# 木板智能检测：采图联动与 TCP JSON 接口

版本：2026.09.22 并行联动版，schema_version=1。适用于311mm、658mm两款程序。

## 1. 操作入口与处理时机

主界面上方选择“只采图 / 采图＋板缝识别 / 采图＋木皮分级 / 采图＋板缝＋分级”，分级时再选择红橡、欧橡、色木或山核桃，点击“应用模式”。模式对随后保存完成的板图生效；已排队的板图保留提交时的模型、标定和材种设置。

每张板按原有光电/PLC触发采集，完整图片保存后进入处理队列。采集下一张板时，后台可以识别上一张板。同一张板同时启用的板缝、分级在后台一起运行。在线队列一次处理一张板，避免大量推理进程占用采集资源。这里不是边采集边对局部图像下结论；产线速度超过处理速度时会积压，界面显示等待数量。

原有板缝、分级窗口仍支持手动导入图片/文件夹，不依赖连接相机。本接口推送“采图联动”产生的结果；手动批处理结果仍写在各自的结果目录，不自动加入产线事件流。

分级模型需要人工选对材种，不能自动判断四种木皮。色木模型只有ABCD组/E组，不能进一步区分A、B、C、D。置信度是模型分数，不是准确率。

## 2. 启用服务

打开“采图联动与实时输出”页，勾选“启用 TCP JSON 结果服务”，点击“应用处理与TCP设置”。默认不开放端口。

| 项目 | 311mm版 | 658mm版 |
|---|---|---|
| 默认监听IP | 127.0.0.1 | 127.0.0.1 |
| 默认端口 | 3110 | 6580 |

同一电脑上的接收软件连接127.0.0.1。其他电脑接收时，监听IP改成本机局域网IPv4或0.0.0.0，设置至少16位的接口口令；接收端连接工控机实际IP，不能连接0.0.0.0。如防火墙阻止，请仅放行使用的局域网端口。当前协议为明文TCP，适合受控局域网，不应暴露到互联网。

端口被占用时界面显示错误，并定期重试；不会暂停相机。每个服务最多8个连接；一个client_id同时只能有一个连接。

## 3. 帧格式与订阅

编码为UTF-8，每个JSON对象后加一个换行符`\n`（JSON Lines）。TCP可能拆包/合包，接收端须按换行符组帧，不可把一次recv视为一条完整消息。结果可能较大，不能固定用很小的接收缓冲区截断。客户端命令每帧最多65536字节。

连接后2秒内发送：

```json
{"type":"subscribe","client_id":"cutter-adapter-01","token":"现场设置的口令"}
```

client_id为1～64位ASCII字母、数字、下划线、点或横杠，应在接收端重启后保持不变。本机未设置口令时可省略token。

服务端首先返回（数值仅为示例）：

```json
{"type":"hello","schema_version":1,"device_id":"设备实例ID","after_seq":100,"latest_seq":104,"delivery":"at_least_once"}
```

默认从该client_id上次已确认的序号继续；新的client_id从最早记录开始。需要人工回放时，可以在订阅中加入`"after_seq":0`，从序号0之后回放。after_seq必须为0到latest_seq之间的整数。回放是查看历史结果，不代表这些板还在输送线上。

## 4. ACK与断线补传

事件按seq升序发送，一次发送一条，收到相应ACK后才发下一条：

```json
{"type":"ack","seq":101}
```

接收方应先持久保存事件，再发送ACK。ACK的seq必须等于刚收到的事件seq；30秒未确认会断开连接。断线重连会补传未确认的事件，因此属于“至少一次投递”。必须按`event_id`去重，不能收到重复事件就重复执行动作。不同服务实例用device_id区分，seq只在单个实例内有意义，序号可能不连续。

空闲约5秒会发`{"type":"heartbeat","latest_seq":104}`，不需ACK；hello、error也不需ACK。错误消息有type=error、message字段，之后关闭连接。

补传记录和客户端ACK保存在软件目录`data/online/events.sqlite`。删除该目录将丢失历史与ACK，并产生新的device_id。升级搬迁如果需要延续ACK，应在程序退出后完整迁移同一实例的data目录，不能让两台工控机共用一份在线数据库。自动导入现场设置不搬迁此数据库。

## 5. 事件类型与关联键

| type | 含义 | 额外字段 |
|---|---|---|
| board.saved | 完整原图已保存，已建立处理任务 | enabled_modules |
| seam.completed | 板缝分析运行完成，可能仍需复核 | result.status、result.result |
| seam.failed | 板缝处理失败 | result.status=error、result.error |
| grading.completed | 颜色分级完成 | result.status、result.result |
| grading.failed | 颜色分级失败 | result.status=error、result.error |
| board.completed | 本板所选模块均已结束 | status、results、requires_review、review_reasons、result_latency_ms |

只采图也有board.saved和board.completed，但enabled_modules为空、results为空，不伪造识别结果。板缝与分级完成事件的先后顺序不固定；整板完成最后发送。

所有事件共同包含：

| 字段 | 含义 |
|---|---|
| schema_version | 当前为1 |
| device_id | 此软件数据实例的稳定ID |
| event_id | 单条事件的唯一ID，用于接收端去重 |
| seq | 此实例内的事件递增序号 |
| board_id | 每张采图任务的唯一ID，同一张板所有事件一致 |
| board_number | 原图文件名去除后缀，例如123；可能在不同批次重复，不可单独作为唯一键 |
| image_path | 工控机本地原图绝对路径，不传输图片字节；异机需另行共享/传输原图 |
| captured_at | 完整图片保存后提交在线队列的时间，含时区；不是光电触发时刻 |
| emitted_at | 本事件首次写入时间，含时区 |
| simulation | true表示模拟采集数据 |
| enabled_modules | []、["seam"]、["grading"]或["seam","grading"] |

board.completed的status为complete或error，complete仅表示所选计算完成。result_latency_ms是从captured_at到整板结果写入的耗时（包括排队），不含本板采图时间。

## 6. 下游读取的实际字段

以board.completed为例：

| JSON字段路径 | 内容 |
|---|---|
| results.seam.status | done或error |
| results.seam.result.status | measured或review_required |
| results.seam.result.seams | 检出的缝数组，可以为空 |
| results.seam.result.seams[i].corners_px | 四角原图像素坐标，顺序TL、TR、BR、BL |
| results.seam.result.seams[i].center_px | 对角线交点[x,y] |
| results.seam.result.seams[i].top_at_center_x_px | 同x位置处的板顶y |
| results.seam.result.seams[i].distance_from_board_top_px | 板顶到交点的纵向像素距离，无效时null |
| results.seam.result.seams[i].distance_from_board_top_mm | 标定后的纵向毫米距离，未标定或坐标无效时null |
| results.seam.result.seams[i].coordinate_valid | 板顶与缝区的几何关系是否有效 |
| results.seam.result.seams[i].boundary_pair_completed | 是否补全了模型上下边界之间的缝区 |
| results.seam.result.calibration_id | 本次使用的现场距离标定ID |
| results.seam.result.warnings | 需要关注的测量原因 |
| results.grading.status | done或error |
| results.grading.result.material_key | hongxiang、ouxiang、semu或shanhetao |
| results.grading.result.grade | 中文颜色类别 |
| results.grading.result.class_id | 训练时的类别标识 |
| results.grading.result.confidence | 最高类别的模型分数，0～1 |
| results.grading.result.probabilities | 全部类别的class_id、label、probability |

模块完成事件的这些数值位于`result.result`下。内部旧板缝结果里的board_id是原图文件名；跨模块关联必须用**事件最外层board_id**。

requires_review=true表示存在以下一个或多个review_reasons：simulation（模拟）、resumed_historical_board（中断后继续的历史板图）、capture_only（仅采图）、seam_failed/grading_failed（处理失败）、seam_measurement_needs_review（板顶/缝区等需复核）、seam_coordinate_or_calibration_needs_review（坐标、标定或边界补全需复核）、grading_low_confidence（低于界面设置阈值）。null不是0，空缝数组是“未检出”，不能据此确认板上没有缝。

程序退出后，未完成的在线任务保留为“待继续”。下次点击“继续未完成板图处理”才恢复；恢复结果标记为历史板图。原图若被替换，任务报告错误，避免旧板号关联新图片。

## 7. 输出文件与对接示例

每张在线板图写入“在线结果目录/board_id”：request.json、seam.json、grading.json、board_result.json、worker.log，以及板缝可视化结果。未启用的模块不生成对应文件。原图目录仍只保存选定格式的图片，不增加JSON等附加文件。服务关闭或没有客户端时，结果照常落盘。

附带`TCP接收示例.py`只用Python标准库，把收到的事件写到接收端SQLite，提交事务后ACK，按event_id防止重复保存。工控机的检测软件本身无需安装Python；接收示例运行环境需要Python。

同机测试命令：

```powershell
python .\TCP接收示例.py --host 127.0.0.1 --port 3110 --client-id receiver311 --database .\received311.sqlite
python .\TCP接收示例.py --host 127.0.0.1 --port 6580 --client-id receiver658 --database .\received658.sqlite
```

局域网连接使用工控机IP，口令可通过BOARD_RESULT_TOKEN环境变量或--token传入。

后续接切割机和分拣轮，还需定义输送方向、每张板的现场跟踪方式、相机到执行机构距离、编码器/速度反馈、执行位置补偿与允许延时。此接口提供检测结果，不直接发送动作命令；requires_review=false也不等于一条可立即执行的动作指令。应由下游结合板位置、结果时效和设备状态决定如何应用。
