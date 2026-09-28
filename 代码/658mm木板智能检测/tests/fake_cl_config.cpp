#include <cstring>
#include <map>
#include "lsn_cl_conf.h"
using namespace lscl;
static int writes=0,fail_key=0,mismatch=0,read_fail=0;
class FakePort: public ICisCLPort {
    std::map<PropertyType,double> values{{LightValueScale,10000},{LightValueR,15},{LightValueG,18},
        {LightValueB,17},{Gain,1.25},{Offset,0},{FFC_Enabled,1},{FFC_Algorithm,0},{UserSetDefault,1}};
public:
    const char* GetParametersInfo() override { return read_fail?nullptr:"fake camera parameter text"; }
    void GetPropertiesFromParams(const char*,PropertyValueCallback* cb,void* user) override {
        for(auto p:values) { PropertyValue v{};if(p.first==Gain)v.fValue=float(p.second);else v.iValue=int(p.second);cb(p.first,v,0,user); }
    }
    bool SetProperty(PropertyType t,PropertyValue v,int) override {
        ++writes;if(int(t)==fail_key)return false;
        if(t!=FFC_Generate && t!=UserSetStore)values[t]=(t==Gain?double(v.fValue):double(v.iValue))+(mismatch?1:0);
        return true;
    }
    bool SendCommand(const char*,const char*) override { return false; }
    const char* SendCommandForResponse(const char*) override { return nullptr; }
    const char* GetErrorText() override { return "injected vendor failure"; }
};
extern "C" {
bool lsclGetPortInfo(unsigned i,PortDescription& p) {
    if(i>0)return false;memset(&p,0,sizeof(p));strcpy_s(p.szVendorName,"Windows");strcpy_s(p.szIdentifer,"COM11");return true;
}
ICisCLPort* lsclCreatePort(const PortDescription& p) {
    if(strcmp(p.szIdentifer,"COM11")!=0)return nullptr;return new FakePort;
}
ICisCLPort* lsclCreatePortFromId(const char*) { return nullptr; }
void lsclDestroyPort(ICisCLPort*& p) { delete p;p=nullptr; }
__declspec(dllexport) void fake_setup_cl(int fail,int wrong,int read) {writes=0;fail_key=fail;mismatch=wrong;read_fail=read;}
__declspec(dllexport) int fake_cl_writes() {return writes;}
}
