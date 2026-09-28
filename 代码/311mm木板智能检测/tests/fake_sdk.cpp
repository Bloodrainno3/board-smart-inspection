// Test double for the documented vendor C++ ABI. Never included in distribution.
#include "lsn_gv_camera.h"
#include <cstdio>
#include <vector>
static int bad_calls = 0;
static unsigned fault = 0;
static unsigned burst = 3;
static bool calibration_ok = true;
class Correction : public lsgv::IBrightnessCorrection {
    bool enabled=false;
public:
    bool Load(const char*) override { return calibration_ok; }
    bool Store(const char*) override { return true; }
    bool MakeDarkCorrection(const lsgv::FrameBuffer*) override { return true; }
    bool MakeBrightCorrection(const lsgv::FrameBuffer*) override { return true; }
    bool MakeHalftoneCorrection(const lsgv::FrameBuffer*, unsigned) override { return true; }
    void ClearData() override {}
    void Enable() override { enabled=true; }
    void Disable() override { enabled=false; }
    bool IsEnabled() override { return enabled; }
};
class Fake : public lsgv::IGvCamera {
    unsigned height=64, valid=0;
    bool active=false, inside=false;
    lsgv::GvEventHandler handlers[lsgv::EVENT_ENUM_COUNT]{};
    void* users[lsgv::EVENT_ENUM_COUNT]{};
    std::vector<unsigned char> data;
    Correction correction;
    void emit(unsigned id, unsigned a, unsigned b=0) { if(handlers[id]) handlers[id](a,b,users[id]); }
    void frame(unsigned lines) {
        valid=lines; inside=true; emit(lsgv::EVENT_FRAME_READY,0,fault&1); inside=false;
    }
public:
    bool LoadConfigurationFile(const char*) override {bad_calls++;return false;}
    bool SaveConfigurationFile(const char*) override {bad_calls++;return false;}
    bool GetPropertyValue(unsigned id,unsigned* value) override {
        switch(id) {
            case lsgv::CP_FRAME_WIDTH: *value=4;break;
            case lsgv::CP_FRAME_HEIGHT:*value=height;break;
            case lsgv::CP_PIXEL_FORMAT:*value=1;break;
            case lsgv::CP_LINE_TRIGGER_MODE:*value=1;break;
            case lsgv::CP_ENCODER_MULTIPLY_FACTOR:*value=2;break;
            case lsgv::CP_ENCODER_DIVISION_FACTOR:*value=25;break;
            case lsgv::CP_ENABLE_CAPTURE_SIGNAL:*value=1;break;
            default:*value=0;
        }
        return true;
    }
    bool GetPropertyFloatValue(unsigned,float*) override{return false;}
    bool SetPropertyValue(unsigned id,unsigned value) override {
        if(id!=lsgv::CP_FRAME_HEIGHT){bad_calls++;return false;}
        height=value;data.assign(height*12,42);return true;
    }
    bool SetPropertyFloatValue(unsigned,float) override{bad_calls++;return false;}
    void RegisterEventHandler(lsgv::GvEventType id,lsgv::GvEventHandler h,void* user) override {handlers[id]=h;users[id]=user;}
    void UnregisterEventHandler(lsgv::GvEventType id) override {handlers[id]=nullptr;}
    bool StartCapture(unsigned count) override {
        if(count!=0)bad_calls++;
        active=true;
        for(unsigned i=0;i<burst;i++)frame(height);
        if(fault&2)emit(lsgv::EVENT_FRAME_LINES_LOST,5);
        if(fault&4)emit(lsgv::EVENT_CONNECTION_BROKEN,0);
        if(fault&8)emit(lsgv::EVENT_CAPTURE_FINISHED,0);
        return true;
    }
    bool StopCapture(bool remaining) override {
        if(active&&remaining)frame(2);
        active=false;return true;
    }
    bool IsWorking() override{return active;}
    double GetRealtimeLineFrequency() override{return 6819;}
    unsigned GetFrameValidLinesCount() override {if(!inside)bad_calls++;return valid;}
    void* GetInternalBufferData(unsigned) override {if(!inside)bad_calls++;return data.data();}
    void GetFrameMemoryInfo(lsgv::FrameMemoryInfo* info) override{*info={size_t(height)*12,12,3};}
    bool RegisterUserFrameBuffers(size_t,void*[],unsigned) override{return false;}
    void UnregisterUserFrameBuffers() override{}
    lsgv::IBrightnessCorrection* GetBrightnessCorrectionObject() override{return &correction;}
    lsgv::IOverlapsCorrection* GetOverlapsCorrectionObject() override{return nullptr;}
};
extern "C" {
void __cdecl lsgvGetLastError(char* buffer){snprintf(buffer,256,"fake SDK failure");}
int __cdecl lsgvGetCamerasCount(){return 1;}
const lsgv::GvCameraDescription* __cdecl lsgvGetCameraDescription(int){return new lsgv::GvCameraDescription{"FAKE","TEST-CAMERA","1","0001",{0,1}};}
void __cdecl lsgvReleaseCameraDescription(const lsgv::GvCameraDescription* p){delete p;}
lsgv::IGvCamera* __cdecl lsgvGetCameraInstance(int){return new Fake;}
void __cdecl lsgvReleaseCameraInstance(lsgv::IGvCamera* p){delete p;}
__declspec(dllexport) void fake_setup(unsigned flags,unsigned count,int correction){fault=flags;burst=count;calibration_ok=correction!=0;bad_calls=0;}
__declspec(dllexport) int fake_bad_calls(){return bad_calls;}
}
