#pragma once
#ifndef _LSNCLC_API_HEADER_
#define _LSNCLC_API_HEADER_

#ifdef _WIN32

#ifdef LSNCLC_EXPORT
#define LSNCLC_API __declspec(dllexport)
#else
#define LSNCLC_API __declspec(dllimport)
#endif
#define LSNCLC_CC __cdecl

#elif defined(__linux__)

#ifdef LSNCLC_EXPORT
#define LSNCLC_API __attribute__((visibility("default")))
#else
#define LSNCLC_API 
#endif
#define LSNCLC_CC 

#endif


namespace lscl
{

/*
* CameraLink 相机通信端口(串口)简单描述
* 成员：
*   - szVendorName：生产商名称
*   - szIdentifer：串口识别名称
*/
struct PortDescription
{
	char szVendorName[128];
	char szIdentifer[128];
};

/*
* 相机属性(或命令)常量
*/
enum PropertyType
{
	PropertyEnumBegin = 0x100,

	LightValueScale, // [已弃用!] 光源亮度比例（取值: 100/10000）
	                 // - 100: 光源亮度取值范围为 0~100 (百分比)
					 // - 10000: 光源亮度取值范围为 0~10000 (万分比)
	LightValueR, // 光源亮度值(红色), 取值 0~10000
	LightValueG, // 光源亮度值(绿色), 取值 0~10000
	LightValueB, // 光源亮度值(蓝色), 取值 0~10000
	LightPulseR, // (弃用)
	LightPulseG, // (弃用)
	LightPulseB, // (弃用)
	InterleaveLightCount,	// (弃用) 分时频闪(交替灯光)光源亮度数量
	InterleaveLightMode = InterleaveLightCount, // 分时频闪(交替灯光)使能(取值：0, 1)
	InterleaveLightValueR,	// 分时频闪光源亮度值(红色), 使用extra参数作为光源序号(1~4)，取值 0~10000
	                        // 序号1~4分别代表：A灯左光源，A灯右光源，B灯左光源，B灯右光源（A灯、B灯交替执行）
							// InterleaveLightValueG、InterleaveLightValueB 序号含义与之一致
	InterleaveLightValueG,	// 分时频闪光源亮度值(绿色), 使用extra参数作为光源序号(1~4)，取值 0~10000
	InterleaveLightValueB,	// 分时频闪光源亮度值(蓝色), 使用extra参数作为光源序号(1~4)，取值 0~10000
	PixelFormat,		// 像素格式，
	LineRate,			// 行频率(单位: Hz)
	Offset,				// 响应值偏移量(取值: 0 ~ 255)
	Gain,				// 响应值放大系数(32位浮点值，取值: 1.0 ~ 20.0)
	TriggerMode,		// 触发模式(取值: 0 - 自触发, 1 - 外部触发)
	TestPattern,		// 测试图样(取值: 0 - 显示采集数据, 1~4 - 显示模拟数据)
	MirrorEnabled,		// 左右镜像模式(取值: 0 - 关闭, 1 - 打开)
	BinarizationThreshold, // 二值化阈值(取值: 0 - 关闭二值化，1~254: 使用该值作为黑白分界)
	UserSetDefault,		// 设定用户配置集(取值: 0 ~ 8)
	UserSetLoad,		// 加载用户配置集(取值: 0 ~ 8)
	UserSetStore,		// 保存用户配置集(取值: 0 ~ 8)
	FFC_UserSet,		// 平场校准当前用户集
	FFC_Generate,		// 生成平场校准，1：亮场，2：暗场
	FFC_Algorithm,		// 平场校准算法，0：Basic（均值校准），1~255：Vendor（定值校准）
	FFC_Enabled,		// 使能平场校准，0：关闭，1：打开
};

class ICisCLPort;

#ifdef __cplusplus
extern "C" {
#endif

#ifdef _WIN32
/*
* 获取通信端口(串口)信息
* 
* 参数 index[in]: 端口序号，从0开始计数
* 参数 port_desc[out]: 端口信息数据
* 返回值: true/false
* 说明: 循环使用该函数对通信端口进行枚举，参数index从0开始调用。当函数
*      返回true，则index增1并继续枚举；否则枚举结束。
*/
LSNCLC_API bool LSNCLC_CC lsclGetPortInfo(unsigned index, PortDescription& port_desc);

/*
* 创建端口句柄
* 
* 参数 port_desc[in]: 端口信息输入，可以由函数lsclGetPortInfo得到，
*                    也可将名称直接填入相关字段
* 返回值: 非0 - 端口句柄；0 - 创建失败
*/
LSNCLC_API ICisCLPort* LSNCLC_CC lsclCreatePort(const PortDescription& port_desc);

/*
* 创建端口句柄
*
* 参数 port_name[in]: 以 COMx 为形式的 Windows 串口名称，X为数字
* 返回值: 非0 - 端口句柄；0 - 创建失败
*/
LSNCLC_API ICisCLPort* LSNCLC_CC lsclCreatePortFromId(const char* port_name);

#elif defined(__linux__)

/*
* 创建端口句柄
*
* 参数 filepath[in]: Linux设备文件路径
* 返回值: 非0 - 端口句柄；0 - 创建失败
*/

LSNCLC_API ICisCLPort* LSNCLC_CC lsclCreatePort(const char* filepath);

#endif

/*
* 销毁端口句柄
* 
* 参数 port[in/out]: 端口句柄
* 返回值:
*/
LSNCLC_API void LSNCLC_CC lsclDestroyPort(ICisCLPort*& port);

#ifdef __cplusplus
}
#endif

/*
* 相机操作接口类 (端口句柄类型)
*/
class ICisCLPort
{
public:
	/*
	* 封装属性值
	* 包含整数和浮点类型，根据属性确定对应数据类型
	*/
	union PropertyValue {
		int		iValue;
		float	fValue;
	};

	/* 
	* 属性值反馈回调函数
	* 
	* 参数 type: 属性类型
	* 参数 val: 属性值
	* 参数 extra: 属性值附加参数
	* 参数 user: 返回注册回调时用户设定的数据地址，
	*           如函数 GetPropertiesFromParams 传入的最后一个参数
	*/
	typedef void (PropertyValueCallback)(lscl::PropertyType type, PropertyValue val, int extra, void* user);

	/*
	* 获取相机参数信息
	* 
	* 返回值: 相机参数文字信息。返回为0则失败，使用 GetErrorText 函数查看错误信息
	*/
	virtual const char* GetParametersInfo() = 0;

	/*
	* 从相机参数文字信息获取相机属性
	* 
	* 参数 params[in]: 相机参数文字信息，由函数 GetParametersInfo 获取并由用户自行存储
	* 参数 callback[in]: 注册接收解析后属性值的回调函数
	* 参数 user[in]: 注册回调函数关联的用户数据地址。在回调函数被调用时，作为最后一个实参。
	*/
	virtual void GetPropertiesFromParams(const char* params, PropertyValueCallback* callback, void* user) = 0;

	/*
	* 设置相机属性（或命令相机开始某一操作）
	* 
	* 参数 type[in]: 属性(命令)类型
	* 参数 val[in]: 相关的属性值
	* 参数 extra[in]: 额外参数
	* 返回值: true - 设定成功，false - 设定失败, 使用 GetErrorText 函数查看错误信息
	*/
	virtual bool SetProperty(PropertyType type, PropertyValue val, int extra = 0) = 0;

	/*
	* 以下均为便捷函数，功能参考内部实现
	*/

	bool SetProperty(PropertyType type, int value, int extra = 0) {
		PropertyValue val;
		val.iValue = value;
		return SetProperty(type, val, extra);
	}

	bool SetProperty(PropertyType type, float value, int extra = 0) {
		PropertyValue val;
		val.fValue = value;
		return SetProperty(type, val, extra);
	}

	bool set_ffc_start(int value) { return SetProperty(FFC_Generate, value); }

	bool set_ffc_mode(int value) { return SetProperty(FFC_Enabled, value); }

	bool set_ffc_selector(int value) { return SetProperty(FFC_UserSet, value); }

	bool set_ffc_algorithm(int value) { return SetProperty(FFC_Algorithm, value); }

	bool set_light_scale(int value) { return SetProperty(LightValueScale, value); }

	bool set_light_red(int value) { return SetProperty(LightValueR, value); }

	bool set_light_green(int value) { return SetProperty(LightValueG, value); }

	bool set_light_blue(int value) { return SetProperty(LightValueB, value); }

	bool set_pixel_format(int value) { return SetProperty(PixelFormat, value); }

	bool set_line_rate(int value) { return SetProperty(LineRate, value); }

	bool set_offset(int value) { return SetProperty(Offset, value); }

	bool set_gain(float value) { return SetProperty(Gain, value); }

	bool set_trigger_mode(int value) { return SetProperty(TriggerMode, value); }

	bool set_test_pattern(int value) { return SetProperty(TestPattern, value); }

	bool set_mirror_mode(int value) { return SetProperty(MirrorEnabled, value); }

	bool user_set_set_default(int value) { return SetProperty(UserSetDefault, value); }

	bool user_set_load(int value) { return SetProperty(UserSetLoad, value); }

	bool user_set_store(int value) { return SetProperty(UserSetStore, value); }

	bool set_binarization_threshold(int value) { return SetProperty(BinarizationThreshold, value); }

	bool set_multi_light_num(int value) { return SetProperty(InterleaveLightCount, value); }

	bool set_multi_light_red(int index, int value) { return SetProperty(InterleaveLightValueR, value, index); }

	bool set_multi_light_green(int index, int value) { return SetProperty(InterleaveLightValueG, value, index); }

	bool set_multi_light_blue(int index, int value) { return SetProperty(InterleaveLightValueB, value, index); }

	/*
	* 发送字符串命令
	* 
	* 参数 cmd[in]: 命令内容
	* 参数 suffix: 命令后缀，一般默认为 " \r"
	* 返回值: true - 操作成功；false - 操作失败，使用 GetErrorText 函数查看错误信息
	*/
	virtual bool SendCommand(const char* cmd, const char* suffix = " \r") = 0;

	/*
	* 发送字符串命令，等待并获取响应信息
	*
	* 参数 cmd[in]: 命令内容
	* 返回值: 非0 - 命令响应字符串信息；0 - 操作失败，使用 GetErrorText 函数查看错误信息
	*/
	virtual const char* SendCommandForResponse(const char* cmd) = 0;

	/*
	* 获取操作失败后的错误信息
	* 
	* 返回值: 错误提示文本信息
	*/
	virtual const char* GetErrorText() = 0;

	virtual ~ICisCLPort() {}
};

} // namespace lscl


#endif // !_LSNCLC_API_HEADER_