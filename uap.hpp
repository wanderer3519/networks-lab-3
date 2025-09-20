#ifndef UAP_HPP
#define UAP_HPP

#include <random>
#include <arpa/inet.h>
#include <cstdint>
#include <cstring>


int PORT = 8080;
constexpr std::size_t MAX_DATA_SIZE = 4096;
constexpr uint16_t MAGIC   = 0xC461;
constexpr uint8_t VERSION  = 1;

enum class Command : uint8_t {
    HELLO   = 0,
    DATA    = 1,
    ALIVE   = 2,
    GOODBYE = 3
};

// ---- Message header ----
#pragma pack(push, 1)
struct MessageHeader {
    uint16_t magic;
    uint8_t  version;
    uint8_t  command;
    uint32_t sequence_number;
    uint32_t session_id;
    uint64_t logical_clock;
    uint64_t timestamp;
};
#pragma pack(pop)

// ---- Message packet ----
#pragma pack(push, 1)
struct MessagePacket {
    MessageHeader header{};
    char data[MAX_DATA_SIZE]{};
};
#pragma pack(pop)

// ---- Client request wrapper ----
struct ClientRequest {
    int socket{};
    sockaddr_in client_addr{};
    MessagePacket packet{};
    int msg_len{};
};

uint32_t get_client_seq_num(ClientRequest& req) {
    return ntohl(req.packet.header.sequence_number);
}

bool is_hello_message(ClientRequest& req){
    return static_cast<Command>(req.packet.header.command) == Command::HELLO;
}

bool is_data_message(ClientRequest& req){
    return static_cast<Command>(req.packet.header.command) == Command::DATA;
}

bool is_alive_message(ClientRequest& req){
    return static_cast<Command>(req.packet.header.command) == Command::ALIVE;
}

bool is_goodbye_message(ClientRequest& req){
    return static_cast<Command>(req.packet.header.command) == Command::GOODBYE;
}

// ---- 64-bit endian helpers ----
inline uint64_t my_htonll(uint64_t value) {
    static const int num = 42;
    if (*reinterpret_cast<const char*>(&num) == 42) { // little endian
        return (static_cast<uint64_t>(htonl(value & 0xFFFFFFFFULL)) << 32) |
               htonl(value >> 32);
    } else {
        return value; // already big endian
    }
}

inline uint64_t my_ntohll(uint64_t value) {
    return my_htonll(value); // symmetric
}

inline bool valid_magic_number(const ClientRequest& req) {
    return ntohs(req.packet.header.magic) == MAGIC;
}

inline bool valid_version(const ClientRequest& req) {
    return req.packet.header.version == VERSION;
}

inline bool valid_command(const ClientRequest& req) {
    uint8_t cmd = req.packet.header.command;
    return (cmd >= static_cast<uint8_t>(Command::HELLO) &&
            cmd <= static_cast<uint8_t>(Command::GOODBYE));
}

inline bool valid_header(const ClientRequest& req) {
    return valid_magic_number(req) && valid_version(req) && valid_command(req);
}

#endif
