// Test double for vendor C API. Never shipped with the application.
#include "IKapBoard.h"
#include "IKapBoardInfoType.h"
#include <map>
#include <vector>
#include <cstring>
#include <cstdio>
#include <thread>
#include <chrono>
#define TEST_API extern "C" __declspec(dllexport)
static unsigned fault=0,burst=3;
static bool load_ok=true;
static int bad=0,depth=8,type=1,width=4;
struct Callback {HookFnPtr fn=nullptr;void* user=nullptr;};
struct Fake {
    bool active=false,inside=false;
    int current=0;
    std::map<unsigned,int> p;
    std::map<unsigned,Callback> callbacks;
    std::vector<std::vector<unsigned char>> data;
    std::vector<IKAPBUFFERSTATUS> status;
    int channels(){return type==0?1:3;}
    void emit(unsigned id){auto c=callbacks[id];if(c.fn)c.fn(c.user);}
    void frame(int index,unsigned lines,bool callback) {
        auto& st=status[index];if(!st.uEmpty)bad++;
        st={unsigned(lines==unsigned(p[IKP_IMAGE_HEIGHT])),0,0,fault&1,lines};
        auto& bytes=data[index];std::fill(bytes.begin(),bytes.end(),42);
        if(fault&512) for(size_t i=0;i<bytes.size();++i)bytes[i]=(i%3+1)*10;
        current=index;
        if(callback){inside=true;emit(IKEvent_FrameReady);inside=false;}
    }
};
TEST_API void fake_setup(unsigned f,unsigned b,int good){fault=f;burst=b;load_ok=good!=0;bad=0;depth=8;type=1;width=4;}
TEST_API void fake_format(int d,int t,int w){depth=d;type=t;width=w;}
TEST_API int fake_bad_calls(){return bad;}
int IKAPBOARD_CC IKapGetBoardCount(unsigned,unsigned* n){*n=(fault&2048)?2:1;return (fault&4096)?0:1;}
int IKAPBOARD_CC IKapGetBoardName(unsigned,unsigned,char* name,unsigned* size){const char* s="Fake CL";if(*size<8)return 0;strcpy_s(name,*size,s);return 1;}
HANDLE IKAPBOARD_CC IKapOpen(unsigned,unsigned){return new Fake;}
int IKAPBOARD_CC IKapClose(HANDLE h){auto f=static_cast<Fake*>(h);if(f->active)bad++;delete f;return 1;}
void IKAPBOARD_CC IKapGetLastError(PIKAPERRORINFO e,bool){*e={2,0,9};}
int IKAPBOARD_CC IKapLoadConfigurationFromFile(HANDLE,char* path){
    if(!load_ok || !path || !path[0])return 0;
    FILE* stream=nullptr;
    // Real Windows narrow-file I/O: the old UTF-8 forwarding fails this check
    // for Chinese filenames on a non-UTF8 Windows ACP.
    if(fopen_s(&stream,path,"rb")!=0 || !stream)return 0;
    fclose(stream);return 1;
}
int IKAPBOARD_CC IKapRegisterCallback(HANDLE h,unsigned id,HookFnPtr fn,void* user){static_cast<Fake*>(h)->callbacks[id]={fn,user};return 1;}
int IKAPBOARD_CC IKapUnRegisterCallback(HANDLE h,unsigned id){auto f=static_cast<Fake*>(h);if(f->active)bad++;f->callbacks.erase(id);return 1;}
int IKAPBOARD_CC IKapSetInfo(HANDLE h,unsigned key,int value){
    switch(key){
      case IKP_IMAGE_HEIGHT: case IKP_FRAME_COUNT: case IKP_TIME_OUT: case IKP_GRAB_MODE:
      case IKP_FRAME_TRANSFER_MODE: case IKP_FRAME_AUTO_CLEAR: case IKP_BOARD_TRIGGER_MODE: break;
      default:bad++;return 0; // Changing width/taps/line timing/encoder is a regression.
    }
    static_cast<Fake*>(h)->p[key]=value;return 1;
}
int IKAPBOARD_CC IKapGetInfo(HANDLE h,unsigned key,int* value){
    auto f=static_cast<Fake*>(h);
    switch(key){
      case IKP_IMAGE_WIDTH:*value=width;break;
      case IKP_DATA_FORMAT:*value=depth;break;
      case IKP_IMAGE_TYPE:*value=type;break;
      case IKP_SCAN_TYPE:*value=(fault&32)?1:0;break;
      case IKP_BOARD_BIT:*value=f->channels()*(depth==8?8:16);break;
      case IKP_FRAME_SIZE:*value=width*f->p[IKP_IMAGE_HEIGHT]*f->channels()+(fault&64?1:0);break;
      case IKP_GRAB_STATUS:*value=f->active?1:0;break;
      case IKP_CURRENT_BUFFER_INDEX:if(!f->inside){bad++;return 0;}*value=f->current;break;
      default:*value=f->p[key];
    }return 1;
}
int IKAPBOARD_CC IKapPrepareGrab(HANDLE h){auto f=static_cast<Fake*>(h);if(fault&128)return 0;
    auto n=f->p[IKP_FRAME_COUNT];f->data.assign(n,std::vector<unsigned char>(width*f->p[IKP_IMAGE_HEIGHT]*f->channels()));
    f->status.assign(n,{0,1,0,0,0});return 1;
}
int IKAPBOARD_CC IKapStartGrab(HANDLE h,int n){auto f=static_cast<Fake*>(h);
    if(n!=0||f->active||f->p[IKP_FRAME_AUTO_CLEAR]!=0||f->p[IKP_BOARD_TRIGGER_MODE]!=0){bad++;return 0;}
    if(fault&256)return 0;
    f->active=true;for(auto& s:f->status)s={0,1,0,0,0};
    for(unsigned i=0;i<burst;++i)f->frame(i%f->data.size(),f->p[IKP_IMAGE_HEIGHT],true);
    if(fault&2)f->emit(IKEvent_FrameLost);
    if(fault&4)f->emit(IKEvent_No_PixelClock);
    if(fault&8)f->emit(IKEvent_TimeOut);
    return 1;
}
int IKAPBOARD_CC IKapStopGrab(HANDLE h){auto f=static_cast<Fake*>(h);
    if(!f->active)return 1;
    // Mimic the callback racing with the disk writer's empty pop at stop.
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    if(fault&8192)f->status.at(burst%f->data.size())={0,0,0,0,0};
    else if(fault&16384)f->status.at(burst%f->data.size())={1,0,0,0,0};
    else f->frame(burst%f->data.size(),2,(fault&16)!=0);
    f->active=false;return (fault&1024)?0:1;
}
int IKAPBOARD_CC IKapGetBufferStatus(HANDLE h,int i,PIKAPBUFFERSTATUS s){auto f=static_cast<Fake*>(h);*s=f->status.at(i);return 1;}
int IKAPBOARD_CC IKapGetBufferAddress(HANDLE h,int i,void** p){*p=static_cast<Fake*>(h)->data.at(i).data();return 1;}
int IKAPBOARD_CC IKapReleaseBuffer(HANDLE h,int i){auto f=static_cast<Fake*>(h);if(!f->status.at(i).uFull)bad++;
    f->status.at(i)={0,1,0,0,0};return 1;
}
