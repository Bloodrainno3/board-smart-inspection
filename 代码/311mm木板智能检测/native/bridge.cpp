// C ABI over the vendor's documented C++ interface. No CLR / pythonnet.
#include "lsn_gv_camera.h"
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <deque>
#include <mutex>
#include <new>
#include <vector>

#define API extern "C" __declspec(dllexport)
struct Info { unsigned width, height, stride, channels, trigger, multiply, divide, capture_signal; };
struct Stats { uint64_t lines, frames, lost, dropped, flagged, queued, peak, broken, failed; };
struct Frame { std::vector<unsigned char> bytes; unsigned lines; };
struct Session {
    lsgv::IGvCamera* camera = nullptr;
    Info info{};
    lsgv::FrameMemoryInfo memory{};
    std::mutex mutex;
    std::deque<Frame> queue;
    Stats stats{};
    uint64_t queue_limit = 512ULL * 1024 * 1024;
    uint64_t total_limit = 1500000000;
    std::atomic<bool> accepting{false};
};

static void __stdcall on_frame(unsigned index, unsigned flags, void* user) noexcept {
    auto s = static_cast<Session*>(user);
    if (!s->accepting) return;
    // Vendor permits these two calls only while inside EVENT_FRAME_READY.
    const unsigned lines = s->camera->GetFrameValidLinesCount();
    auto ptr = s->camera->GetInternalBufferData(index);
    const uint64_t size = uint64_t(lines) * s->info.stride;
    std::lock_guard<std::mutex> lock(s->mutex);
    s->stats.flagged += flags != 0;
    if (!ptr || !lines || lines > s->info.height || size > s->memory.buffer_size) {
        s->stats.failed++;
        return;
    }
    if (size + s->stats.queued > s->queue_limit ||
        (s->stats.lines + lines) * s->info.stride > s->total_limit) {
        s->stats.dropped += lines;
        return;
    }
    try {
        Frame frame;
        frame.lines = lines;
        frame.bytes.assign(static_cast<unsigned char*>(ptr), static_cast<unsigned char*>(ptr) + size);
        s->queue.push_back(std::move(frame));
        s->stats.queued += size;
        s->stats.peak = (std::max)(s->stats.peak, s->stats.queued);
        s->stats.lines += lines;
        s->stats.frames++;
    } catch (...) { s->stats.dropped += lines; }
}
static void __stdcall on_lost(unsigned lines, unsigned, void* user) noexcept {
    auto s = static_cast<Session*>(user);
    std::lock_guard<std::mutex> lock(s->mutex);
    s->stats.lost += lines;
}
static void __stdcall on_broken(unsigned, unsigned, void* user) noexcept {
    auto s = static_cast<Session*>(user);
    std::lock_guard<std::mutex> lock(s->mutex);
    s->stats.broken++;
}
static void __stdcall on_finished(unsigned ok, unsigned, void* user) noexcept {
    auto s = static_cast<Session*>(user);
    if (!ok) {
        std::lock_guard<std::mutex> lock(s->mutex);
        s->stats.failed++;
    }
}
API int bc_abi() { return 1; }
API int bc_count() { return lsgvGetCamerasCount(); }
API void bc_error(char* buffer) { lsgvGetLastError(buffer); }
API int bc_description(int index, char* buffer, unsigned capacity) {
    auto p = lsgvGetCameraDescription(index);
    if (!p) return 0;
    snprintf(buffer, capacity, "%s / %s / %s", p->vendor ? p->vendor : "",
             p->model ? p->model : "", p->serial_number ? p->serial_number : "");
    lsgvReleaseCameraDescription(p);
    return 1;
}
API Session* bc_open(int index) {
    auto s = new(std::nothrow) Session;
    if (!s) return nullptr;
    s->camera = lsgvGetCameraInstance(index);
    if (!s->camera) { delete s; return nullptr; }
    s->camera->RegisterEventHandler(lsgv::EVENT_FRAME_READY, on_frame, s);
    s->camera->RegisterEventHandler(lsgv::EVENT_FRAME_LINES_LOST, on_lost, s);
    s->camera->RegisterEventHandler(lsgv::EVENT_CONNECTION_BROKEN, on_broken, s);
    s->camera->RegisterEventHandler(lsgv::EVENT_CAPTURE_FINISHED, on_finished, s);
    return s;
}
API int bc_prepare(Session* s, unsigned height, const char* calibration,
                   uint64_t queue_limit, uint64_t total_limit, Info* out) {
    if (!s || height < 1 || s->camera->IsWorking()) return 0;
    auto c = s->camera;
    if (calibration && calibration[0]) {
        auto correction = c->GetBrightnessCorrectionObject();
        if (!correction || !correction->Load(calibration)) return 0;
        correction->Enable();
        if (!correction->IsEnabled()) return 0;
    }
    // Deliberately preserve the encoder, line-trigger and EN settings stored in the camera.
    if (!c->SetPropertyValue(lsgv::CP_FRAME_HEIGHT, height) ||
        !c->GetPropertyValue(lsgv::CP_FRAME_WIDTH, &s->info.width) ||
        !c->GetPropertyValue(lsgv::CP_FRAME_HEIGHT, &s->info.height) || s->info.height != height) return 0;
    c->GetFrameMemoryInfo(&s->memory);
    s->info.stride = s->memory.bytes_per_line;
    s->info.channels = s->memory.bytes_per_pixel;
    if (!s->info.width || (s->info.channels != 1 && s->info.channels != 3) ||
        s->info.stride < uint64_t(s->info.width) * s->info.channels ||
        s->memory.buffer_size < uint64_t(height) * s->info.stride) return 0;
    s->info.trigger = s->info.multiply = s->info.divide = s->info.capture_signal = ~0U;
    c->GetPropertyValue(lsgv::CP_LINE_TRIGGER_MODE, &s->info.trigger);
    c->GetPropertyValue(lsgv::CP_ENCODER_MULTIPLY_FACTOR, &s->info.multiply);
    c->GetPropertyValue(lsgv::CP_ENCODER_DIVISION_FACTOR, &s->info.divide);
    c->GetPropertyValue(lsgv::CP_ENABLE_CAPTURE_SIGNAL, &s->info.capture_signal);
    s->queue_limit = queue_limit;
    s->total_limit = total_limit;
    *out = s->info;
    return 1;
}
API int bc_start(Session* s) {
    if (!s || s->camera->IsWorking()) return 0;
    {
        std::lock_guard<std::mutex> lock(s->mutex);
        if (s->stats.broken) return 0;
        s->queue.clear();
        s->stats = {};
    }
    s->accepting = true; // callbacks may arrive before StartCapture returns
    if (!s->camera->StartCapture(0)) { s->accepting = false; return 0; }
    return 1;
}
API int bc_stop(Session* s) {
    if (!s) return 0;
    // true flushes the final partial frame through the registered callback.
    int ok = s->camera->StopCapture(true);
    s->accepting = false;
    return ok;
}
API int bc_pop(Session* s, void* target, uint64_t capacity, unsigned* lines) {
    std::lock_guard<std::mutex> lock(s->mutex);
    if (s->queue.empty()) return 0;
    auto& frame = s->queue.front();
    if (frame.bytes.size() > capacity) return -1;
    memcpy(target, frame.bytes.data(), frame.bytes.size());
    *lines = frame.lines;
    s->stats.queued -= frame.bytes.size();
    s->queue.pop_front();
    return 1;
}
API void bc_stats(Session* s, Stats* out) {
    std::lock_guard<std::mutex> lock(s->mutex);
    *out = s->stats;
}
API int bc_working(Session* s) { return s && s->camera->IsWorking(); }
API void bc_close(Session* s) {
    if (!s) return;
    if (s->camera->IsWorking()) s->camera->StopCapture(true);
    s->accepting = false;
    for (unsigned id = 0; id < lsgv::EVENT_ENUM_COUNT; ++id)
        s->camera->UnregisterEventHandler(static_cast<lsgv::GvEventType>(id));
    lsgvReleaseCameraInstance(s->camera);
    delete s;
}
