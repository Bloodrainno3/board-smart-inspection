#pragma once

#ifndef _LSN_GV_DEFINES_H_
#define _LSN_GV_DEFINES_H_

#include <stddef.h>

namespace lsgv {

/*
* 错误信息缓冲区大小
*/
const size_t ERROR_BUFFER_SIZE = 256;

/*
* 相机基础文字信息
* 字段含义：
* vendor - 生产厂商
* model - 型号
* version - 版本
* serial_number - 序列号
* device.mac_index - 设备唯一索引号（与拨码相关，取值：0xC0 ~ 0xC7）
* device.port_count - 设备占用端口数量（多相机组合成一个逻辑单元）
*/
struct GvCameraDescription
{
	const char* vendor;
	const char* model;
	const char* version;
	const char* serial_number;

	struct {
		unsigned mac_index;
		unsigned port_count;
	} device;
};

/*
* 相机属性ID
* 说明：
*	枚举值用于 GetPropertyFloatValue/SetPropertyFloatValue 函数的第一个参数
*	[W]标记：可读写，[R]标记：只读
*/
enum GvCameraProperties
{
	/* READ-ONLY */
	/* [R] 传感器长度(单位：像素) */
	CP_SENSOR_LENGTH,

	/* [R] 传感器精度(单位：像素/英寸) */
	CP_SENSOR_DPI,

	/* [R] 传感器最大行频率 */
	CP_SENSOR_MAX_LINE_FREQUENCY,

	/* [R] 像素格式(值：PixelFormat 类型常量) */
	CP_PIXEL_FORMAT,

	/* [R] 帧宽度(单位：像素) */
	CP_FRAME_WIDTH,

	/* WRITABLE */
	/* [W] 帧高度(单位：行) */
	CP_FRAME_HEIGHT,

	/* [W] 左右翻转，默认值0，设定为1则使能翻转 */
	CP_FLIP_HORIZONTAL,

	/* [W] 采集频率(单位：Hz) */
	CP_CAPTURE_FREQUENCY,

	/* [W] 红色通道灯光亮度(值：0 ~ 10000) */
	CP_LED_BRIGHTNESS_RED,

	/* [W] 绿色通道灯光亮度(值：0 ~ 10000) */
	CP_LED_BRIGHTNESS_GREEN,

	/* [W] 蓝色通道灯光亮度(值：0 ~ 10000) */
	CP_LED_BRIGHTNESS_BLUE,

	/* [W] 行触发模式(值：LineTriggerMode 类型常量) */
	CP_LINE_TRIGGER_MODE,

	/* [W] 行触发超时(单位：毫秒，默认值：0) */
	CP_LINE_TRIGGER_TIMEOUT,

	/* ENCODER */
	/* [W] 编码器倍频(值：1 ~ 255) */
	CP_ENCODER_MULTIPLY_FACTOR,

	/* [W] 编码器分频(值：1 ~ 255) */
	CP_ENCODER_DIVISION_FACTOR,

	/* [W] 编码器通道(值：EncoderChannel 类型常量) */
	CP_ENCODER_CHANNEL,

	/* [W] 编码器有效方向(值：EncoderValidDirection 类型常量) */
	CP_ENCODER_VALID_DIRECTION,

	/* [W] 编码器反向补偿(默认值：0，设为1时使能反向补偿) */
	CP_ENCODER_REVERSE_COMPENSATION,

	/* [W] 编码器信号过滤宽度(单位：微秒，范围：50 ~ 20'000'000) */
	CP_ENCODER_MIN_VALID_WIDTH,

	/* [W] 采集信号(默认值：0，设置为1则使能该信号 */
	CP_ENABLE_CAPTURE_SIGNAL,

	/* [W] 采集信号识别模式(值：0 - 电平模式(SIG_MODE_LEVEL)，1 - 脉冲模式(SIG_MODE_PULSE) */
	CP_CAPTURE_SIGNAL_MODE,

	/* [W] 采集信号识别极性(值：0 - 正(SIG_POLARITY_POSITIVE)，1 - 负(SIG_POLARITY_NEGATIVE) */
	CP_CAPTURE_SIGNAL_POLARITY,

	/* [W] 分时频闪(间隔亮度)模式使能 */
	CP_ENABLE_INTERVAL_FRAMES,

	/* [W] 分时频闪图像混合模式 */
	CP_INTERVAL_FRAMES_BLENDING_MODE,

	/* [W] 分时频闪：红色灯光亮度比例(数据类型见 IntervalBrightnessValue) */
	CP_INTERVAL_LED_BRIGHTNESS_VALUE_RED,

	/* [W] 分时频闪：绿色灯光亮度比例(数据类型见 IntervalBrightnessValue) */
	CP_INTERVAL_LED_BRIGHTNESS_VALUE_GREEN,

	/* [W] 分时频闪：蓝色灯光亮度比例(数据类型见 IntervalBrightnessValue) */
	CP_INTERVAL_LED_BRIGHTNESS_VALUE_BLUE,

	/* [W] 采集值偏移量(值：0 ~ 255，默认值：128) */
	CP_CAPTURE_VALUE_OFFSET,

	/* [W] 采集值增益量(值：0 ~ 511，默认值：32) */
	CP_CAPTURE_VALUE_GAIN,

	/* [W] 内部行触发信号频率(单位：Hz) */
	CP_INTERNAL_SIGNAL_FREQUENCY,
};

/* 像素格式 */
enum PixelFormat
{
	/* 灰度图像(8bit) */
	PIX_FORMAT_MONO8 = 0,

	/* 彩色图像(24bit, RGB三通道) */
	PIX_FORMAT_RGB24 = 1,
};

/* 采集触发模式 */
enum LineTriggerMode
{
	/* 自触发，按照行频率连续产生内部信号触发 */
	FREERUN = 0,

	/* 编码器信号触发 */
	ENCODER = 1,

	/* 内部信号触发 */
	INTERNAL_SIGNAL = 2,
};

/* 采集信号识别模式 */
enum SignalMode
{
	/* 识别电平，电平持续时采集继续，电平改变时采集停止 */
	SIG_MODE_LEVEL = 0,

	/* 识别脉冲，开始采集并收到脉冲信号则采集立即生效 */
	SIG_MODE_PULSE = 1,
};

/* 采集信号识别极性 */
enum SignalPolarity
{
	/* 正(高电平/上升沿) */
	SIG_POLARITY_POSITIVE = 0,

	/* 负(低电平/下降沿) */
	SIG_POLARITY_NEGATIVE = 1,
};

/* 编码器有效方向 */
enum EncoderValidDirection
{
	/* 前进单向有效 */
	ENCODER_DIR_FORWARD = 1,

	/* 后退单向有效 */
	ENCODER_DIR_BACKWARD = 2,

	/* 前进和后退双向有效 */
	ENCODER_DIR_BOTH = 3,
};

/* 编码器通道 */
enum EncoderChannel
{
	/* A通道有效 */
	ENCODER_CHANNEL_A = 1,

	/* B通道有效 */
	ENCODER_CHANNEL_B = 2,

	/* AB双通道同时生效 */
	ENCODER_CHANNEL_AB = 3,
};

/* 分时频闪图像混合模式 */
enum FramesBlendingMode
{
	/* 不混合 */
	NO_BLENDING = 0,
	/* 分离图像 */
	NO_BLENDING_SEPARATED = 1,

	/* 混合加深，保留各通道较暗灰阶 */
	BLENDING_DARKEN,
	/* 混合加深，仅保留低灰阶像素，抛弃高灰阶像素 */
	BLENDING_MULTIPLY,
	/* 混合加深，使颜色变暗并增加对比度 */
	BLENDING_COLOR_BURN,
	/* 混合加深，降低亮度使颜色变暗 */
	BLENDING_LINEAR_BURN,
	/* 混合加深，保留亮度较暗的像素 */
	BLENDING_DARKER_COLOR,

	/* 混合减淡，保留各通道较亮灰阶 */
	BLENDING_LIGHTEN,
	/* 混合减淡，仅保留高灰阶像素，抛弃低灰阶像素 */
	BLENDING_SCREEN,
	/* 混合减淡，增加亮度并降低对比度，使颜色变亮 */
	BLENDING_COLOR_DODGE,
	/* 混合减淡，增加亮度使颜色变亮 */
	BLENDING_LINEAR_DODGE,
	/* 混合减淡，保留亮度较亮的像素 */
	BLENDING_LIGHTER_COLOR,

	/* 混合合成，主要调节中间色调，使图像融合 */
	BLENDING_OVERLAY,
	/* 混合合成，将高低色调调节至中间色调，使图像以较柔和的方式融合 */
	BLENDING_SOFT_LIGHT,
	/* 混合合成，扩大高低色调灰阶差异，使图像以较强烈的方式融合 */
	BLENDING_HARD_LIGHT,
	/* 混合合成，提高高灰阶亮度但降低对比度，减弱低灰阶亮度并提高对比度，使图像融合 */
	BLENDING_VIVID_LIGHT,
	/* 混合合成，以较高对比度融合图像 */
	BLENDING_LINEAR_LIGHT,
	/* 混合合成，高灰阶保留高亮度像素，低灰阶保留低亮度像素，可能产生颗粒感 */
	BLENDING_PIN_LIGHT,

	BLENDING_MODE_ENUM_END
};

#pragma pack(4)
/*
* 分时频闪亮度设置
* 字段：
*	group		- 灯光组索引 (0, 1)
*	part		- 光源选择 (0: LED_A, 1: LED_B)
*	brightness	- 亮度值 (0 ~ 10000)
*/
union IntervalBrightnessValue
{
	struct {
		unsigned char group;
		unsigned char part;
		unsigned short brightness;
	} t;
	unsigned u32_value;
};
#pragma pack()

/*
* 图像缓存内存信息
* 字段：
*	buffer_size - 缓存占用内存字节数
*	bytes_per_line - 单行数据字节数
*	bytes_per_pixel - 一个像素字节数（灰度图像为1字节，彩色图像为3字节）
*/
struct FrameMemoryInfo
{
	size_t		buffer_size;
	unsigned	bytes_per_line;
	unsigned	bytes_per_pixel;
};

/*
* 图像缓存总体描述
* 字段：
*	memory - 内存信息
*	data - 图像数据地址
*	image_width - 图像宽度(像素)，需要从 CP_FRAME_WIDTH 属性获得
*	image_height - 图像高度(像素)，需要从 CP_FRAME_HEIGHT 属性获得
*	pixel_format - 像素格式(像素)，需要从 CP_PIXEL_FORMAT 属性获得
*/
struct FrameBuffer
{
	FrameMemoryInfo memory;
	void*		data;
	unsigned	image_width;
	unsigned	image_height;
	unsigned	pixel_format;
};

/*
* 事件通知回调函数
* 参数：
*	arg0/arg1 - 事件参数，与事件通知类型(GvEventType)相关联
*	user_data - 用户自定义的地址类型的数据，该值由 RegisterEventHandler 函数的最后一个参数指定
*/
#ifdef __linux__
typedef void(*GvEventHandler)(unsigned arg0, unsigned arg1, void* user_data);
#else
typedef void(_stdcall* GvEventHandler)(unsigned arg0, unsigned arg1, void* user_data);
#endif

/* 
* 事件通知ID
*/
enum GvEventType
{
	/*
	* 事件：帧图像
	* 说明：
	*		采集开始后，相机接收到一帧完整图像时发出此事件。
	* 参数：
	*		arg0: 缓存索引
	*		arg1: 数据丢失标记，数据完整（无丢失）为 0，帧内数据丢失为 1，
			      与上一帧之间丢失为 2，帧内帧间都有丢失为 3
	* 注释：
	*		当参数 arg0 代表内部缓存索引时，只能在该事件处理函数中获取内部缓冲地址。
	*		其他任何时候获取内部缓冲地址，可能造成未定义的行为或程序崩溃。
	*		用户应当始终检查 arg1 参数，若出现非 0 值，则图像可能不完整，可能需要特殊处理
	*/
	EVENT_FRAME_READY = 0,

	/*
	* 事件：行数据丢失
	* 说明：
	*		采集开始后，如果系统性能不足，图像数据有可能会丢失，此时事件被触发。
	* 参数：
	*		arg0: 丢失的行数量
	*		arg1: 0
	*/
	EVENT_FRAME_LINES_LOST,

	/*
	* 事件：采集结束
	* 说明：
	*		单/多帧采集完成时，或者采集异常中止时，发出此事件。
	* 参数：
	*		arg0: 1 - 采集任务成功完成；0 - 采集失败，采集异常停止。
	*		arg1: 0
	* 注释：
	*		当采集失败时，使用 last_error 获取错误描述
	*/
	EVENT_CAPTURE_FINISHED,

	/*
	* 事件：相关属性值变更
	* 说明：
	*		用户做配置操作时，相关属性发生变化，发出此事件以通知用户
	* 参数：
	*		arg0: 发生变更的属性ID
	*		arg1: 变更后的值
	* 注释：
	*		当用户使用 SetPropertyFloatValue 设置时，被设置的属性值变更，不会发出此通知。
	*		SetPropertyFloatValue 可能导致其他属性变更，而发出其他属性的变更通知。
	*/
	EVENT_RELATIVE_PROPERTY_CHANGED,

	/*
	* 事件：连接断开
	* 说明：
	*		当网络配置被外部更改，或其他原因导致通讯失败，发出此事件通知
	* 参数：
	*		arg0: 0
	*		arg1: 0
	* 注释：
	*		用户收到此事件后，应当结束正在进行的采集任务
	*/
	EVENT_CONNECTION_BROKEN,

	EVENT_ENUM_COUNT,
};


} // namespace lsgv

#endif // _LSN_GV_DEFINES_H_