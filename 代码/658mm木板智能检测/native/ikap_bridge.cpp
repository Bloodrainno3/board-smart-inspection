// IKapLibrary 1.7.3 Camera Link adapter. Contracts: the vendor IKapBoard headers.
#define NOMINMAX
#include <windows.h>
#include "IKapBoard.h"
#include "IKapBoardInfoType.h"
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <deque>
#include <mutex>
#include <new>
#include <vector>
#include <string>
#define API extern "C" __declspec(dllexport)
struct Info { unsigned width, height, stride, channels, trigger, multiply, divide, capture_signal; };
struct Stats { uint64_t lines, frames, lost, dropped, flagged, queued, peak, broken, failed; };
struct Frame { std::vector<unsigned char> bytes; unsigned lines; };
static thread_local char last_error[256] = "";
static int error(const char* operation) {
    IKAPERRORINFO e{}; IKapGetLastError(&e, false);
    snprintf(last_error, sizeof(last_error), "%s (IKap error 0x%08X, board %u)", operation, e.uErrorCode, e.uBoardIndex);
    return 0;
}
static int invalid(const char* message) {
    snprintf(last_error, sizeof(last_error), "%s", message); return 0;
}
static bool checked(int result, const char* operation) {
    if (result == IK_RTN_OK) return true;
    error(operation); return false;
}
// Public bridge ABI takes UTF-8. IKap's Windows char* filename is a narrow
// filesystem path: never pass UTF-8 Chinese bytes straight to that API.
static bool sdk_filename(const char* utf8, std::string& result) {
    int n=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,utf8,-1,nullptr,0);
    if(!n) return invalid("Configuration path is not valid UTF-8");
    std::vector<wchar_t> wide(n);
    MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,utf8,-1,wide.data(),n);
    DWORD attrs=GetFileAttributesW(wide.data());
    if(attrs==INVALID_FILE_ATTRIBUTES || (attrs&FILE_ATTRIBUTE_DIRECTORY))
        return invalid("Configuration file no longer exists or is not accessible");
    // Short names also avoid SDK path-length limits, when the volume supports them.
    DWORD size=GetShortPathNameW(wide.data(),nullptr,0);
    if(size) {
        std::vector<wchar_t> short_path(size);
        DWORD written=GetShortPathNameW(wide.data(),short_path.data(),size);
        if(written && written<size) wide=std::move(short_path);
    }
    const bool utf8_acp=GetACP()==CP_UTF8;
    BOOL substituted=FALSE;
    DWORD flags=utf8_acp?WC_ERR_INVALID_CHARS:WC_NO_BEST_FIT_CHARS;
    BOOL* used=utf8_acp?nullptr:&substituted;
    n=WideCharToMultiByte(CP_ACP,flags,wide.data(),-1,nullptr,0,nullptr,used);
    if(!n || substituted) return invalid("Path cannot be represented by Windows codepage; move .vlcf to C:\\IKapConfig\\1.vlcf");
    std::vector<char> narrow(n);
    if(!WideCharToMultiByte(CP_ACP,flags,wide.data(),-1,narrow.data(),n,nullptr,used) || substituted)
        return invalid("Cannot encode configuration filename for IKap");
    if(n>MAX_PATH || GetFileAttributesA(narrow.data())==INVALID_FILE_ATTRIBUTES)
        return invalid("SDK cannot access narrow filename; move .vlcf to C:\\IKapConfig\\1.vlcf");
    result=narrow.data(); return true;
}
constexpr int buffers = 8;
struct Session {
    HANDLE board = nullptr;
    Info info{};
    std::mutex mutex;
    std::deque<Frame> queue;
    Stats stats{};
    uint64_t queue_limit = 0, total_limit = 0;
    bool accepting = false, prepared = false, bgr = false, preview = false;
    int next_index = 0;
    unsigned partial_copied[buffers]{};
    std::vector<unsigned> registered;
    std::string diagnostic;
};
static void trace(Session* s,const char* operation,int index,const IKAPBUFFERSTATUS* status=nullptr) noexcept {
    try {
        if(s->diagnostic.size()>6000)return;
        IKAPERRORINFO e{};IKapGetLastError(&e,false);
        char line[256];
        if(status) snprintf(line,sizeof(line),"%s buffer=%d full=%u empty=%u transfer=%u overflow=%u lines=%u; ",operation,index,status->uFull,status->uEmpty,status->uTransfer,status->uOverflow,status->uLineNum);
        else snprintf(line,sizeof(line),"%s buffer=%d SDK=0x%08X; ",operation,index,e.uErrorCode);
        s->diagnostic+=line;
    }catch(...){}
}
// Caller holds mutex. Release full buffers only after making an owned copy.
static void copy_buffer(Session* s, int index, bool callback) noexcept {
    IKAPBUFFERSTATUS status{};
    if (IKapGetBufferStatus(s->board, index, &status) != IK_RTN_OK) { trace(s,"GetBufferStatus failed",index);s->stats.failed++; return; }
    if(!callback)trace(s,"stopped buffer",index,&status);
    if (status.uOverflow) s->stats.flagged++;
    if (status.uEmpty) { if (callback) { trace(s,"FrameReady empty buffer",index,&status);s->stats.failed++; } return; }
    // After stop, an unfilled buffer may have all status flags cleared. Zero
    // valid rows without Full/Overflow is no data, not a malformed image frame.
    // A zero-line FrameReady or a Full buffer with zero lines still fails below.
    if(!callback && !status.uLineNum && !status.uFull && !status.uOverflow)return;
    unsigned lines = status.uLineNum;
    const unsigned prefix=s->partial_copied[index];
    if(lines==prefix && lines>0) return; // Tail already delivered by a stop callback.
    void* ptr = nullptr;
    const unsigned fresh=lines>=prefix?lines-prefix:0;
    const uint64_t size = uint64_t(fresh) * s->info.stride;
    if (!fresh || lines > s->info.height ||
        IKapGetBufferAddress(s->board, index, &ptr) != IK_RTN_OK || !ptr) {
        s->stats.failed++;
        trace(s,"Invalid frame/address",index,&status);
    } else if (size + s->stats.queued > s->queue_limit ||
               (!s->preview && (s->stats.lines + fresh) * s->info.stride > s->total_limit)) {
        s->stats.dropped += fresh;
    } else {
        try {
            Frame frame; frame.lines = fresh;
            auto p = static_cast<unsigned char*>(ptr)+uint64_t(prefix)*s->info.stride;
            frame.bytes.assign(p, p + size);
            // Storage and inference retain the existing RGB8 contract.
            if (s->bgr) for (size_t i = 0; i < frame.bytes.size(); i += 3)
                std::swap(frame.bytes[i], frame.bytes[i+2]);
            s->queue.push_back(std::move(frame));
            s->stats.lines += fresh; s->stats.frames++; s->stats.queued += size;
            s->stats.peak = (std::max)(s->stats.peak, s->stats.queued);
        } catch (...) { s->stats.dropped += fresh; }
    }
    s->partial_copied[index]=status.uFull?0:lines;
    if (status.uFull && IKapReleaseBuffer(s->board, index) != IK_RTN_OK) { trace(s,"ReleaseBuffer failed",index);s->stats.failed++; }
}
static void IKAPBOARD_CC on_frame(void* user) noexcept {
    auto s = static_cast<Session*>(user);
    std::lock_guard<std::mutex> lock(s->mutex);
    if (!s->accepting) return;
    int index = -1;
    // This property is valid only inside FrameReady.
    if (IKapGetInfo(s->board, IKP_CURRENT_BUFFER_INDEX, &index) != IK_RTN_OK || index < 0 || index >= buffers) {
        trace(s,"Current buffer index invalid",index);s->stats.failed++; return;
    }
    if (index != s->next_index) { trace(s,"Unexpected buffer sequence",index);s->stats.lost++; }
    copy_buffer(s, index, true);
    s->next_index = (index + 1) % buffers;
}
static void IKAPBOARD_CC on_lost(void* user) noexcept {
    auto s=static_cast<Session*>(user); std::lock_guard<std::mutex> lock(s->mutex);
    if (s->accepting) { trace(s,"FrameLost event",-1);s->stats.lost++; }
}
static void IKAPBOARD_CC on_timeout(void* user) noexcept {
    auto s=static_cast<Session*>(user); std::lock_guard<std::mutex> lock(s->mutex);
    if (s->accepting) { trace(s,"TimeOut event",-1);s->stats.failed++; }
}
static void IKAPBOARD_CC on_no_clock(void* user) noexcept {
    auto s=static_cast<Session*>(user); std::lock_guard<std::mutex> lock(s->mutex);
    if (s->accepting) { trace(s,"NoPixelClock event",-1);s->stats.broken++; }
}
API int bc_abi() { return 2; }
API int bc_count() {
    last_error[0]=0; unsigned n=0;
    if (!checked(IKapGetBoardCount(IKBoardPCIE, &n), "IKapGetBoardCount")) return -1;
    return static_cast<int>(n);
}
API void bc_error(char* buffer) { snprintf(buffer, 256, "%s", last_error); }
API int bc_description(int index, char* buffer, unsigned capacity) {
    char name[1024]{}; unsigned size=sizeof(name);
    if (!checked(IKapGetBoardName(IKBoardPCIE,index,name,&size),"IKapGetBoardName")) return 0;
    snprintf(buffer,capacity,"IKap PCIe / %s / %d",name,index); return 1;
}
API Session* bc_open(int index) {
    auto s=new(std::nothrow) Session;
    if (!s) { invalid("Cannot allocate session"); return nullptr; }
    s->board=IKapOpen(IKBoardPCIE,index);
    if (!s->board || s->board==reinterpret_cast<HANDLE>(intptr_t(-1))) { error("IKapOpen"); delete s; return nullptr; }
    const unsigned events[]={IKEvent_FrameReady,IKEvent_FrameLost,IKEvent_TimeOut,IKEvent_No_PixelClock};
    HookFnPtr callbacks[]={on_frame,on_lost,on_timeout,on_no_clock};
    for (int i=0;i<4;++i) {
        if (!checked(IKapRegisterCallback(s->board,events[i],callbacks[i],s),"IKapRegisterCallback")) {
            for(auto id:s->registered) IKapUnRegisterCallback(s->board,id);
            IKapClose(s->board); delete s; return nullptr;
        }
        s->registered.push_back(events[i]);
    }
    return s;
}
API int bc_prepare(Session* s,unsigned height,const char* profile,uint64_t queue_limit,uint64_t total_limit,Info* out) {
    if (!s || !out || !height || height>4096) return invalid("Invalid preparation arguments");
    int active=0;
    if (!checked(IKapGetInfo(s->board,IKP_GRAB_STATUS,&active),"IKP_GRAB_STATUS") || active) return 0;
    if (profile && profile[0]) {
        std::string filename;
        if(!sdk_filename(profile,filename)) return 0;
        if (!checked(IKapLoadConfigurationFromFile(s->board,filename.data()),"Load .vlcf configuration")) return 0;
    } else if (!s->prepared) return invalid("A Camera Link .vlcf configuration is required");
    s->prepared=false;
    int scan=0,depth=0,type=0,bits=0,width=0,actual_height=0,bytes=0;
    auto get=[&](unsigned key,int& value){return checked(IKapGetInfo(s->board,key,&value),"IKapGetInfo");};
    auto set=[&](unsigned key,int value){return checked(IKapSetInfo(s->board,key,value),"IKapSetInfo");};
    if (!get(IKP_SCAN_TYPE,scan) || !get(IKP_DATA_FORMAT,depth) || !get(IKP_IMAGE_TYPE,type)) return 0;
    if (scan!=IKP_SCAN_TYPE_VAL_LINEAR || depth!=8 || (type!=0 && type!=1 && type!=3))
        return invalid("Configuration must be line scan Mono8 / RGB8 / BGR8 (no Bayer, packed or 16-bit)");
    // PLC software gates frames. Keep taps, line timing, encoder and CC output
    // from .vlcf, and avoid waiting for a second hardware frame trigger.
    if (!set(IKP_IMAGE_HEIGHT,height) || !set(IKP_FRAME_COUNT,buffers) ||
        !set(IKP_TIME_OUT,-1) || !set(IKP_GRAB_MODE,IKP_GRAB_NON_BLOCK) ||
        !set(IKP_BOARD_TRIGGER_MODE,IKP_BOARD_TRIGGER_MODE_VAL_INNER) ||
        !set(IKP_FRAME_TRANSFER_MODE,IKP_FRAME_TRANSFER_SYNCHRONOUS_NEXT_EMPTY_WITH_PROTECT) ||
        !set(IKP_FRAME_AUTO_CLEAR,IKP_FRAME_AUTO_CLEAR_VAL_DISABLE)) return 0;
    if (!get(IKP_IMAGE_WIDTH,width) || !get(IKP_IMAGE_HEIGHT,actual_height) ||
        !get(IKP_BOARD_BIT,bits) || !get(IKP_FRAME_SIZE,bytes)) return 0;
    const unsigned channels=type==0?1:3;
    const uint64_t frame_bytes=uint64_t(width)*height*channels;
    if (width<1 || width>65535 || actual_height!=height || bits!=channels*8 ||
        bytes<1 || uint64_t(bytes)!=frame_bytes || queue_limit<frame_bytes || total_limit<frame_bytes)
        return invalid("Unsupported image layout, frame size or capture memory limit");
    s->info={unsigned(width),height,unsigned(width)*channels,channels,0,~0U,~0U,~0U};
    s->bgr=type==3; s->queue_limit=queue_limit; s->total_limit=total_limit;
    if (!checked(IKapPrepareGrab(s->board),"IKapPrepareGrab")) return 0;
    s->prepared=true; *out=s->info; return 1;
}
API int bc_start(Session* s) {
    if (!s || !s->prepared) return invalid("Board not prepared");
    {
        std::lock_guard<std::mutex> lock(s->mutex);
        if(s->accepting || s->stats.broken) return invalid("Board active or faulted; reconnect required");
        s->queue.clear(); s->stats={}; s->next_index=0; s->accepting=true;
        s->diagnostic.clear();
        std::fill(std::begin(s->partial_copied),std::end(s->partial_copied),0);
    }
    if (!checked(IKapStartGrab(s->board,0),"IKapStartGrab")) {
        IKapStopGrab(s->board);
        std::lock_guard<std::mutex> lock(s->mutex); s->accepting=false; s->stats.failed++; return 0;
    }
    return 1;
}
API int bc_stop(Session* s) {
    if (!s) return invalid("No board open");
    // Keep callbacks through StopGrab, then copy only valid rows of the partial
    // buffer. Manual release prevents duplicate full frames in this final drain.
    bool ok=checked(IKapStopGrab(s->board),"IKapStopGrab");
    std::string stop_error=ok?"":last_error;
    int active=1;
    ok=checked(IKapGetInfo(s->board,IKP_GRAB_STATUS,&active),"IKP_GRAB_STATUS after stop") && ok;
    std::lock_guard<std::mutex> lock(s->mutex);
    bool was_accepting=s->accepting; s->accepting=false; s->preview=false;
    if (!ok || active) {
        s->stats.failed++;
        std::string detail=stop_error.empty()?last_error:stop_error;
        detail="Acquisition stop not confirmed: "+detail+"; reconnect required";
        s->diagnostic+=detail;
        return invalid(detail.c_str());
    }
    if(was_accepting) for(int n=0;n<buffers;++n) copy_buffer(s,(s->next_index+n)%buffers,false);
    return 1;
}
// Calibration keeps only a rolling image; the memory queue limit still applies.
// A normal stop restores the production single-board byte limit.
API int bc_start_preview(Session* s) {
    if(!s) return invalid("No board open");
    {
        std::lock_guard<std::mutex> lock(s->mutex);
        if(s->accepting) return invalid("Stop acquisition before starting calibration preview");
        s->preview=true;
    }
    if(bc_start(s))return 1;
    std::lock_guard<std::mutex> lock(s->mutex);s->preview=false;return 0;
}
API int bc_pop(Session* s,void* target,uint64_t capacity,unsigned* lines) {
    if(!s || !target || !lines) return -1;
    std::lock_guard<std::mutex> lock(s->mutex);
    if(s->queue.empty()) return 0;
    auto& f=s->queue.front(); if(f.bytes.size()>capacity) return -1;
    memcpy(target,f.bytes.data(),f.bytes.size()); *lines=f.lines;
    s->stats.queued-=f.bytes.size(); s->queue.pop_front(); return 1;
}
API void bc_stats(Session* s,Stats* out) { if(s && out) { std::lock_guard<std::mutex> lock(s->mutex); *out=s->stats; } }
API void bc_diagnostic(Session* s,char* buffer,unsigned capacity) {
    if(!s || !buffer || !capacity)return;
    std::lock_guard<std::mutex> lock(s->mutex);snprintf(buffer,capacity,"%s",s->diagnostic.c_str());
}
API int bc_working(Session* s) {
    if(!s) return 0; int active=0;
    if(IKapGetInfo(s->board,IKP_GRAB_STATUS,&active)!=IK_RTN_OK) {
        std::lock_guard<std::mutex> lock(s->mutex); s->stats.failed++; return 0;
    }
    return active!=0;
}
API void bc_close(Session* s) {
    if(!s) return;
    bc_stop(s);
    for(auto id:s->registered) IKapUnRegisterCallback(s->board,id);
    IKapClose(s->board); delete s;
}
