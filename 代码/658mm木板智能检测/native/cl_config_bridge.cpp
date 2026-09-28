// C ABI around the manufacturer's Camera Link configuration C++ interface.
// All calls belong to the capture controller thread, never the GUI thread.
#include <algorithm>
#include <cmath>
#include <cstring>
#include <string>
#include <vector>
#include "lsn_cl_conf.h"

#define API extern "C" __declspec(dllexport)
namespace {
thread_local std::string error;
struct Value { unsigned type; int extra; double value; };
struct Session { lscl::ICisCLPort* port = nullptr; };
void copy(const std::string& s, char* out, unsigned size) {
    if (out && size) { auto n=std::min<size_t>(size-1,s.size()); memcpy(out,s.data(),n);out[n]=0; }
}
int fail(const char* s) { error=s?s:"CLConfigurator returned no error description";return 0; }
bool valid(Session* s) { if(s && s->port)return true;fail("Camera control port is not connected");return false; }
void collect(lscl::PropertyType type,lscl::ICisCLPort::PropertyValue v,int extra,void* user) {
    auto& values=*static_cast<std::vector<Value>*>(user);
    values.push_back({unsigned(type),extra,type==lscl::Gain?double(v.fValue):double(v.iValue)});
}
bool allowed(unsigned t,double v) {
    if(!std::isfinite(v))return false;
    if(t==lscl::Gain)return v>=1 && v<=20;
    if(v!=std::floor(v))return false;
    switch(t) {
    case lscl::LightValueR: case lscl::LightValueG: case lscl::LightValueB:return v>=0 && v<=10000;
    case lscl::Offset: case lscl::FFC_Algorithm:return v>=0 && v<=255;
    case lscl::FFC_Enabled:return v==0 || v==1;
    case lscl::FFC_Generate:return v==1 || v==2;
    case lscl::UserSetDefault:case lscl::UserSetStore:return v>=0 && v<=8;
    default:return false; // no geometry, trigger, arbitrary command or config-load writes
    }
}
}
API int cc_abi() { return 1; }
API void cc_error(char* out,unsigned size) { copy(error,out,size); }
API int cc_port(unsigned index,lscl::PortDescription* out) {
    if(!out)return fail("Null port description");
    try { memset(out,0,sizeof(*out));return lscl::lsclGetPortInfo(index,*out)?1:0; }
    catch(...) { fail("Exception while enumerating Camera Link control ports");return -1; }
}
API void* cc_open(const lscl::PortDescription* desc) {
    if(!desc) { fail("Select a control port first");return nullptr; }
    try {
        auto port=lscl::lsclCreatePort(*desc);
        if(!port) { fail("Cannot open control port; close CLConfigurator and verify camera control connection");return nullptr; }
        auto s=new Session;s->port=port;return s;
    } catch(...) { fail("Exception while opening camera control port");return nullptr; }
}
API void cc_close(Session* s) {
    if(!s)return;
    try { if(s->port)lscl::lsclDestroyPort(s->port); } catch(...) { fail("Exception while closing camera control port"); }
    delete s;
}
API int cc_read(Session* s,char* raw,unsigned size,Value* out,unsigned capacity,unsigned* count) {
    if(!valid(s) || !raw || !out || !count)return fail("Invalid read arguments");
    *count=0;
    try {
        const char* p=s->port->GetParametersInfo();
        if(!p)return fail(s->port->GetErrorText());
        std::string text(p);
        if(text.size()>=size)return fail("Camera parameter text exceeds buffer; no partial read returned");
        std::vector<Value> values;
        s->port->GetPropertiesFromParams(text.c_str(),collect,&values);
        if(values.size()>capacity)return fail("Too many camera parameters; no partial read returned");
        copy(text,raw,size);
        std::copy(values.begin(),values.end(),out);*count=unsigned(values.size());return 1;
    } catch(...) { return fail("Exception while reading camera parameters"); }
}
API int cc_set(Session* s,unsigned type,double value) {
    if(!valid(s))return 0;
    if(!allowed(type,value))return fail("Property or value is not allowed by calibration bridge");
    try {
        lscl::ICisCLPort::PropertyValue v{};
        if(type==lscl::Gain)v.fValue=float(value);else v.iValue=int(value);
        if(!s->port->SetProperty(static_cast<lscl::PropertyType>(type),v,0))return fail(s->port->GetErrorText());
        return 1;
    } catch(...) { return fail("Exception while writing camera property; read back before retrying"); }
}
