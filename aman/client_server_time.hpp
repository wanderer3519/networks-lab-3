#ifndef CLIENT_SERVER_TIME_HPP
#define CLIENT_SERVER_TIME_HPP

#include <chrono>
#include <cstdint>

constexpr int SESSION_TIMEOUT = 20;  // in seconds

uint64_t now_s() {
    // returns current time in seconds
    using namespace std::chrono;
    return duration_cast<seconds>(
        system_clock::now().time_since_epoch()
    ).count();
}

uint64_t now_ms() {
    // returns current time in micro seconds
    using namespace std::chrono;
    return duration_cast<microseconds>(
        system_clock::now().time_since_epoch()
    ).count();
}

uint64_t calculate_next_session_timeout() {
    return now_ms() + (SESSION_TIMEOUT * 1000000);
}

#endif
