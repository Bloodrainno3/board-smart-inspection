#pragma once

#ifndef _LSN_GV_CAMERA_H_
#define _LSN_GV_CAMERA_H_

#ifdef __linux__
#ifdef LSN_API_EXPORTS
#define LSNAPI_DLL __attribute__((visibility("default")))
#else
#define LSNAPI_DLL
#endif
#define LSNAPI_CC
#else
#ifdef LSN_API_EXPORTS
#define LSNAPI_DLL __declspec(dllexport)
#else
#define LSNAPI_DLL __declspec(dllimport)
#endif
#define LSNAPI_CC __cdecl
#endif


#include "lsn_gv_defines.h"

namespace lsgv {
	class IGvCamera;
}

#ifdef __cplusplus
extern "C" {
#endif

/*
* 函数名称: lsgvGetLastError
* 说明:	获取上一次库函数调用失败的错误码
* 参数:
*		errBuffer[out] 接收错误信息的缓存地址，该缓存字节数至少为 ERROR_BUFFER_SIZE
* 注释:
*		获取错误信息后，内部将清空消息，因此无法立即再次获取，直到再次产生错误消息
*/
LSNAPI_DLL void LSNAPI_CC lsgvGetLastError(char* errBuffer);


/*
* 函数名称: lsgvGetCamerasCount
* 说明:	枚举并获取当前系统内相机数量
* 参数:
* 返回值:
*		设备数量，若未检索到设备，怎返回0
* 注释:
*		加载库后，此函数必须在调用其他函数之前首先调用
* 
*/
LSNAPI_DLL int LSNAPI_CC lsgvGetCamerasCount();

/*
* 函数名称: lsgvGetCameraDescription
* 说明:	获取设备描述信息
* 参数:
*		index[in] 设备索引号（以0开始），该值必须小于枚举到的设备数量
* 返回值:
*		设备描述信息数据的内存地址, 若失败则为 0
*/
LSNAPI_DLL const lsgv::GvCameraDescription* LSNAPI_CC lsgvGetCameraDescription(int index);

/*
* 函数名称: lsgvReleaseCameraDescription
* 说明:	释放获取设备描述信息内存块
* 参数:
*		inst[in] 设备描述信息数据的内存地址
*/
LSNAPI_DLL void LSNAPI_CC lsgvReleaseCameraDescription(const lsgv::GvCameraDescription* inst);

/*
* 函数名称: lsgvGetCameraInstance
* 说明:	获取设备实例
* 参数:
*		index[in] 设备索引号（以0开始），该值必须小于枚举到的设备数量
* 返回值:
*		成功，为非0的 IGvCamera 类型对象的地址，用于对该相机设备的操作；否则，为0
* 注释:
*		在结束 IGvCamera 对象所有操作后，应当使用 delete 销毁对象，以释放其占用资源
*		也可以使用 lsgvReleaseCameraInstance 函数
*/
LSNAPI_DLL lsgv::IGvCamera* LSNAPI_CC lsgvGetCameraInstance(int index);

/*
* 函数名称: lsgvReleaseCameraInstance
* 说明:	结束相机使用，清理占用资源
* 参数:
*		inst[in] 相机设备句柄
* 返回值:
* 注释:
*		在结束 IGvCamera 对象所有操作后，应当使用该函数，以释放其占用资源
*		此后 cam 参数对应的对象已无效
*/
LSNAPI_DLL void LSNAPI_CC lsgvReleaseCameraInstance(lsgv::IGvCamera* inst);

#ifdef __cplusplus
}
#endif


namespace lsgv 
{

/*
* class IGvCamera
* 相机基本操作函数库
*/
class IGvCamera
{
public:
	/*
	* 函数名称: IGvCamera::LoadConfigurationFile
	* 说明:	从配置文件加载相机属性
	* 参数:
	*		filepath[in] 配置文件的读取路径
	* 返回值:
	*		成功返回 true，否则 false
	* 注释:
	*		从配置文件加载属性导致当前属性更改，不会触发 EVENT_RELATIVE_PROPERTY_CHANGED 事件
	*/
	virtual bool LoadConfigurationFile(const char* filepath) = 0;

	/*
	* 函数名称: IGvCamera::SaveConfigurationFile
	* 说明:	相机属性保存至配置文件
	* 参数:
	*		filepath[in] 配置文件的保存路径
	* 返回值:
	*		成功返回 true，否则 false
	*/
	virtual bool SaveConfigurationFile(const char* filepath) = 0;

	/*
	* 函数名称: IGvCamera::GetPropertyFloatValue
	*          IGvCamera::GetPropertyFloatValue
	* 说明:	获取相机属性值
	* 参数:
	*		id[in] 属性ID，使用 GvCameraProperties 类型枚举值
	*		value[out] 就收属性值的32位整数缓存地址
	*		fValue[out] 就收属性值的32位浮点数缓存地址
	* 返回值:
	*		成功返回 true，否则 false
	*/
	virtual bool GetPropertyValue(unsigned id, unsigned* value) = 0;
	virtual bool GetPropertyFloatValue(unsigned id, float* fValue) = 0;

	/*
	* 函数名称: IGvCamera::SetPropertyFloatValue
	*          IGvCamera::SetPropertyFloatValue
	* 说明:	设定相机属性值
	* 参数:
	*		id[in] 属性ID，使用 GvCameraProperties 类型枚举值
	*		value[in] 设定的32位整数属性值
	*		fValue[in] 设定的32位浮点数属性值
	* 返回值:
	*		成功返回 true，否则 false
	*/
	virtual bool SetPropertyValue(unsigned id, unsigned value) = 0;
	virtual bool SetPropertyFloatValue(unsigned id, float fValue) = 0;

	/*
	* 函数名称: IGvCamera::RegisterEventHandler
	* 说明:	注册通知事件
	* 参数:
	*		eventID[in] 事件ID，使用 GvCameraProperties 类型枚举值
	*		handler[in] 对应的事件处理函数地址
	*		userData[in] 用户自定义数据地址，将被记录，并作为 handler 的最后一个参数值
	* 返回值:
	*		成功返回 true，否则 false
	* 注释:
	*		当 handler 为 0 时，其作用相当于调用 UnregisterEventHandler 函数
	*/
	virtual void RegisterEventHandler(GvEventType eventID,
		GvEventHandler handler, void* userData = 0) = 0;

	/*
	* 函数名称: IGvCamera::UnregisterEventHandler
	* 说明:	注销通知事件
	* 参数:
	*		eventID[in] 事件ID，使用 GvEventType 类型枚举值
	*/
	virtual void UnregisterEventHandler(GvEventType eventID) = 0;

	/*
	* 函数名称: IGvCamera::StartCapture
	* 说明:	启动采集任务
	* 参数:
	*		count[in] 指定的采集帧数，0值代表不限制帧数
	* 返回值:
	*		成功返回 true，否则 false
	* 注释:
	*		若 count 为非0值，应当提前注册 EVENT_CAPTURE_FINISHED 事件以接收停止通知
	*		若采集任务
	*/
	virtual bool StartCapture(unsigned count = 0) = 0;

	/*
	* 函数名称: IGvCamera::StopCapture
	* 说明:	停止采集任务
 	* 参数:
		fetchRemaining[in] 指定是否抓取剩余行，若为 true，则收到最后不完整帧
	* 返回值:
	*		成功返回 true，否则 false
	*/
	virtual bool StopCapture(bool fetchRemaining = false) = 0;

	/*
	* 函数名称: IGvCamera::IsWorking
	* 说明:	查询工作状态
	* 返回值:
	*		若正在进行采集任务，则返回 true，否则 false
	*/
	virtual bool IsWorking() = 0;

	/*
	* 函数名称: IGvCamera::GetRealtimeLineFrequency
	* 说明:	查询实时行频率
	* 返回值:
	*		行频率，单位: 行/秒
	*/
	virtual double GetRealtimeLineFrequency() = 0;

	/*
	* 函数名称: IGvCamera::GetFrameValidLinesCount
	* 说明:	查询图像有效行数，仅在 EVENT_FRAME_READY 事件处理函数中调用
	* 返回值:
	*		帧内有效行数
	*/
	virtual unsigned GetFrameValidLinesCount() = 0;

	/*
	* 函数名称: IGvCamera::GetInternalBufferData
	* 说明:	获取内部缓存地址
	* 参数:
	*		index[in] 以0开始的内部缓存索引
	* 返回值:
	*		成功，则返回非0的内部缓存地址值，否则为0
	* 注释:
	*		仅当用户未调用 RegisterUserFrameBuffers 时（即使用库内部缓存时），才有必要使用此函数
	*		该函数只能在 EVENT_FRAME_READY 事件的处理函数中调用
	*/
	virtual void* GetInternalBufferData(unsigned index) = 0;

	/*
	* 函数名称: IGvCamera::GetFrameMemoryInfo
	* 说明:	获取帧内存大小信息
	* 参数:
	*		info[out] 输出内存大小信息的 FrameMemoryInfo 类型缓存地址
	* 注释:
	*		当用户进行改变帧高或修改重叠等配置操作时，可能导致缓存大小变化
	*		应当在做完所有配置后，开始采集任务前，调用此函数获取准确的缓存信息，以分配并注册缓存
	*/
	virtual void GetFrameMemoryInfo(FrameMemoryInfo* info) = 0;

	/*
	* 函数名称: IGvCamera::RegisterUserFrameBuffers
	* 说明:	注册用户缓存地址
	* 参数:
	*		bufSize[in] 缓存占用字节数
	*		bufAddresses[in] 缓存数组的地址，该数组元素为: 用户自行分配的，大小为 bufSize 的内存地址
	*		bufAddressesCount[in] 缓存数组的元素数量
	* 返回值:
	*		成功返回 true，否则 false
	* 注释:
	*		当用户调用 RegisterUserFrameBuffers 后，即开启“用户缓存模式”
	*		用户缓存模式时，在 EVENT_FRAME_READY 事件的处理函数中接收到的缓存索引，即为参数 bufAddresses 的元素索引
	*/
	virtual bool RegisterUserFrameBuffers(size_t bufSize,
		void* bufAddresses[], unsigned bufAddressesCount) = 0;

	/*
	* 函数名称: IGvCamera::UnregisterUserFrameBuffers
	* 说明:	注销用户缓存地址
	* 参数:
	* 返回值:
	* 注释:
	*		用户先前调用了 RegisterUserFrameBuffers 后，注销用户缓存区，即开启“内部缓存模式”
	*		用内部存模式时，在 EVENT_FRAME_READY 事件的处理函数中接收到的缓存索引，即为内部缓存区的索引
	*/
	virtual void UnregisterUserFrameBuffers() = 0;

	/*
	* 函数名称: IGvCamera::GetBrightnessCorrectionObject
	* 说明:	获取校准操作对象
	* 返回值:
	*		返回非0的 IBrightnessCorrection 类型对象实例
	*/
	virtual class IBrightnessCorrection* GetBrightnessCorrectionObject() = 0;

	/*
	* 函数名称: IGvCamera::GetOverlapsCorrectionObject
	* 说明:	获取重叠操作对象
	* 返回值:
	*		返回非0的 IOverlapsCorrection 类型对象实例
	*/
	virtual class IOverlapsCorrection* GetOverlapsCorrectionObject() = 0;

public:
	virtual ~IGvCamera() {}
};

/*
* class IBrightnessCorrection
* 说明: 校准配置函数库
* 注释: 所有操作在采集任务进行中无效
*/
class IBrightnessCorrection
{
public:
	/*
	* 函数名称: IBrightnessCorrection::Load
	* 说明:	加载校准文件
	* 参数:
	*		filepath[in] 文件路径字符串地址
	* 返回值:
	*		成功返回 true，否则 false
	* 注释:
	*		在采集任务进行时，总是会返回 false
	*		若失败，使用 IGvCamera::GetLastError 获取错误信息
	*/
	virtual bool Load(const char* filepath) = 0;

	/*
	* 函数名称: IBrightnessCorrection::Store
	* 说明:	存储校准文件
	* 参数:
	*		filepath[in] 文件路径字符串地址
	* 返回值:
	*		成功返回 true，否则 false
	* 注释:
	*		在采集任务进行时，总是会返回 false
	*		若失败，使用 IGvCamera::last_error 获取错误信息
	*/
	virtual bool Store(const char* filepath) = 0;

	/*
	* 函数名称: IBrightnessCorrection::MakeDarkCorrection
	* 说明:	生成暗场校准数据
	* 参数:
	*		image[in] 图像缓存描述信息，详见 FrameBuffer
	* 返回值:
	*		成功返回 true，否则 false
	* 注释:
	*		生成前，请确保校准已禁用，以避免叠加校准
	*		必须使用“用户缓存模式”，填入 image->data 字段的地址为接收图像的用户缓存地址之一
	*		image 的各字段值，需通过其他库函数获取并填入
	*		重新生成亮/暗场数据会清除“灰度均衡数据”
	*/
	virtual bool MakeDarkCorrection(const FrameBuffer* image) = 0;

	/*
	* 函数名称: IBrightnessCorrection::MakeBrightCorrection
	* 说明:	生成亮场校准数据
	* 参数:
	*		image[in] 图像缓存描述信息，详见 FrameBuffer
	* 返回值:
	*		成功返回 true，否则 false
	* 注释:
	*		略(参考 IBrightnessCorrection::MakeDarkCorrection)
	*/
	virtual bool MakeBrightCorrection(const FrameBuffer* image) = 0;

	/*
	* 函数名称: IBrightnessCorrection::MakeHalftoneCorrection
	* 说明：	生成亮度均衡数据
	*		（采集色彩均衡的物体，使图像整体亮度均衡）
	* 参数：
	*		image[in] 图像缓存描述信息，详见 FrameBuffer
	*		target[in] 灰度均衡后的目标值，在 2 ~ 254 之间, 若使用程序默认，则将此值设置为0
	* 返回值:
	*		成功返回 true，否则 false
	* 注释:
	*		亮度均衡校准的操作，需在亮/暗场校准数据生成后才进行
	*		亮/暗场校准后，以前的亮度均衡数据将失效
	*		其他注意事项，请参考 IBrightnessCorrection::MakeDarkCorrection
	*/
	virtual bool MakeHalftoneCorrection(const FrameBuffer* image, unsigned target = 0) = 0;

	/*
	* 函数名称: IBrightnessCorrection::ClearData
	* 说明:	清除校准数据
	* 注释:
	*		清除从 MakeDarkCorrection，MakeBrightCorrection 和 Load 等函数产生的数据
	*/
	virtual void ClearData() = 0;

	/*
	* 函数名称: IBrightnessCorrection::Enable
	* 说明:	启用校准数据，使校准生效（应用到图像采集）
	*/
	virtual void Enable() = 0;

	/*
	* 函数名称: IBrightnessCorrection::Disable
	* 说明:	禁用校准数据，使校准数据无效（不会清除当前校准数据）
	*/
	virtual void Disable() = 0;

	/*
	* 函数名称: IBrightnessCorrection::IsEnabled
	* 说明:	查询当前校准数据是否生效
	* 返回值:
	*		如果校准生效，返回 true，否则 false
	*/
	virtual bool IsEnabled() = 0;
};

/*
* class IOverlapsCorrection
* 说明: 重叠配置函数库
* 注释: 所有操作在采集任务进行中无效
*/
class IOverlapsCorrection
{
public:
	/*
	* 函数名称: IOverlapsCorrection::Load
	* 说明:	加载重叠文件
	* 参数:
	*		filepath[in] 文件路径字符串地址
	* 返回值:
	*		成功返回 true，否则 false
	* 注释:
	*		在采集任务进行时，总是会返回 false
	*		若失败，使用 IGvCamera::last_error 获取错误信息
	*/
	virtual bool Load(const char* filepath) = 0;

	/*
	* 函数名称: IOverlapsCorrection::Store
	* 说明:	保存重叠文件
	* 参数:
	*		filepath[in] 文件路径字符串地址
	* 返回值:
	*		成功返回 true，否则 false
	* 注释:
	*		在采集任务进行时，总是会返回 false
	*		若失败，使用 IGvCamera::last_error 获取错误信息
	*/
	virtual bool Store(const char* filepath) = 0;

	/*
	* 函数名称: IOverlapsCorrection::ClearData
	* 说明:	清除重叠数据
	* 返回值:
	*		成功返回 true，否则 false
	* 注释:
	*		在采集任务进行时，总是会返回 false
	*		若失败，使用 IGvCamera::last_error 获取错误信息
	*/
	virtual bool ClearData() = 0;

	/*
	* 函数名称: IOverlapsCorrection::SetVerticalDPI
	* 说明:	设定竖向DPI，以使重叠数据与图像竖向DPI适应
	* 参数:
	*		dpi[in] 图像实际的竖向DPI
	* 返回值:
	*		成功返回 true，否则 false
	* 注释:
	*		在采集任务进行时，总是会返回 false
	*		若失败，使用 IGvCamera::last_error 获取错误信息
	*/
	virtual bool SetVerticalDPI(double dpi) = 0;
};

} // namespace lsgv

#endif // _LSN_GV_CAMERA_H_