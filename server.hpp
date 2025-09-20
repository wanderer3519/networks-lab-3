#ifndef SERVER_HPP
#define SERVER_HPP

#include <iostream>
#include <arpa/inet.h>
#include <memory>
#include "uap.hpp"
#include <set>
#include <map>
#include "client_server_time.hpp"
#include <cctype>
#include <mutex>


enum class State : uint8_t {
    START   = 0,
    RECEIVE = 1,
    DONE    = 2
};

struct SessionData {
    State state;
    uint64_t expiry_time;
    uint32_t prev_client_seq_num;
    int socket;
    sockaddr_in client_addr;
};


std::map<uint32_t, SessionData> session_map;
std::set<std::pair<uint64_t, uint32_t>> expiry_map;
std::mutex session_mutex;

std::atomic<uint32_t> GLOBAL_SEQUENCE_NUMBER{0};
std::atomic<uint64_t> LOGICAL_CLOCK{0};

std::atomic<uint64_t> TOTAL_LATENCY{0};
std::atomic<uint64_t> NUMBER_OF_MESSAGES_RECEIVED{0};

uint32_t get_session_id(ClientRequest& req){
    return ntohl(req.packet.header.session_id);  
}

uint32_t get_prev_seq_num(ClientRequest& req){
    uint32_t session_id = get_session_id(req);
    
    std::lock_guard<std::mutex> lock(session_mutex);

    auto client_session_iterator = session_map.find(session_id);
    return client_session_iterator->second.prev_client_seq_num;
}

State get_server_state(ClientRequest& req){
    uint32_t session_id = get_session_id(req);
    
    std::lock_guard<std::mutex> lock(session_mutex);

    auto client_session_iterator = session_map.find(session_id);
    if (client_session_iterator == session_map.end()){
        // throw error
    }
    return client_session_iterator->second.state;
}

MessagePacket make_message(uint32_t session_id, Command cmd) {
    MessagePacket reply{};
    reply.header.magic           = htons(MAGIC);
    reply.header.version         = VERSION;
    reply.header.command         = static_cast<uint8_t>(cmd);
    reply.header.sequence_number = htonl(GLOBAL_SEQUENCE_NUMBER.load());
    reply.header.session_id      = htonl(session_id);
    reply.header.logical_clock   = my_htonll(LOGICAL_CLOCK.load());
    reply.header.timestamp       = my_htonll(now_ms());

    GLOBAL_SEQUENCE_NUMBER.fetch_add(1);
    return reply;
}

void send_message(uint32_t session_id, Command cmd) {
    std::lock_guard<std::mutex> lock(session_mutex);

    auto session_map_iterator = session_map.find(session_id);
    if (session_map_iterator == session_map.end()) return;

    SessionData& session_data = session_map_iterator->second;
    auto message = make_message(session_id, cmd);

    sendto(
        session_data.socket,
        &message,
        sizeof(message.header),
        0,
        reinterpret_cast<const sockaddr*>(&session_data.client_addr),
        sizeof(session_data.client_addr)
    );

    LOGICAL_CLOCK.fetch_add(1);
}


inline void send_hello(ClientRequest& req){
    uint32_t session_id = get_session_id(req);
    send_message(session_id, Command::HELLO);
}

inline void send_alive(ClientRequest& req){
    uint32_t session_id = get_session_id(req);
    send_message(session_id, Command::ALIVE);
}

inline void send_goodbye(ClientRequest& req){
    uint32_t session_id = get_session_id(req);
    // std::this_thread::sleep_for(std::chrono::milliseconds(100));
    send_message(session_id, Command::GOODBYE);
}

void session_terminate(ClientRequest& req) {
    uint32_t session_id = get_session_id(req);
    send_goodbye(req);
    {
        std::lock_guard<std::mutex> lock(session_mutex);
        // removing session
        auto session_iterator = session_map.find(session_id);
        if (session_iterator != session_map.end()) {
            uint64_t expiry_time = session_iterator->second.expiry_time;
            auto expiry_map_session_iterator = expiry_map.find({expiry_time, session_id});
            if (expiry_map_session_iterator != expiry_map.end()) {
                expiry_map.erase(expiry_map_session_iterator);
            }
            session_map.erase(session_iterator);
        }
    }
    std::cout << "\n" << session_id << " Session closed\n";
}


void refresh_session(ClientRequest& req, State next_state) {
    uint32_t session_id = get_session_id(req);
    
    std::lock_guard<std::mutex> lock(session_mutex);

    auto session_iterator = session_map.find(session_id);
    if (session_iterator == session_map.end()) return;

    session_iterator->second.state = next_state; // changing the the server state as per the FSA

    uint64_t old_expiry = session_iterator->second.expiry_time;

    expiry_map.erase({old_expiry, session_id});

    uint64_t new_expiry = calculate_next_session_timeout();
    session_iterator->second.expiry_time = new_expiry;
    expiry_map.insert({new_expiry, session_id});

    // updating the client sequence number, socket, client_addr stored in the session
    session_iterator->second.prev_client_seq_num = ntohl(req.packet.header.sequence_number);
    session_iterator->second.socket = req.socket;
    session_iterator->second.client_addr = req.client_addr;

}


void take_action_start(ClientRequest& req){
    if(is_hello_message(req)){
        // the message is a HELLO message.
        refresh_session(req, State::RECEIVE);
        send_hello(req);
    }
    else{
        // need to terminate session
        std::cout << "\n expected HELLO message but recieved else. \n";
        session_terminate(req);
    }
}

void print_client_data(const ClientRequest& req, uint32_t session_id, uint32_t client_seq_num) {
    std::cout << "\n" << session_id << "[" << client_seq_num << "] ";

    // length of payload after the header
    int data_len = req.msg_len - sizeof(MessageHeader);
    if (data_len <= 0) {
        std::cout << "[No data in packet]" << std::endl;
        return;
    }

    for (int i = 0; i < data_len; i++) {
        char c = req.packet.data[i];
        if (std::isprint(static_cast<unsigned char>(c)))
            std::cout << c;
    }
    std::cout << "\n";
}

void take_action_receive(ClientRequest& req){
    if(is_data_message(req)){
        // the message is a DATA message.
        uint32_t client_seq_num = get_client_seq_num(req);
        uint32_t prev_seq_num = get_prev_seq_num(req);
        
        if(client_seq_num < prev_seq_num){
            std::cout << "\nreceived packet of sequence number less than expected!!!\n";
            session_terminate(req);
            return;
        }
        else if(client_seq_num == prev_seq_num){
            std::cout << "\nduplicate packet!!!\n";
            send_alive(req);
            return;
        }

        uint32_t session_id = get_session_id(req);
        for(int sq_num=prev_seq_num+1; sq_num<client_seq_num; sq_num++){
            // printing Lost packet for the missing packets
            std::cout << "\n" << session_id << "[" << sq_num << "] Lost Packet!\n";
        }
        print_client_data(req, session_id, client_seq_num);
        
        refresh_session(req, State::RECEIVE);
        send_alive(req);
    }
    else if(is_goodbye_message(req)){
        // need to terminate session
        uint32_t session_id = get_session_id(req);
        std::cout << "\nreceived goodbye from " << session_id << "\n";
        session_terminate(req);
        return;
    }
    else{
        // need to terminate session
        session_terminate(req);
        return;
    }
}

void take_action(ClientRequest& req){
    State server_state = get_server_state(req);
    switch (server_state) {
        case State::START:
            take_action_start(req);
            break;
        case State::RECEIVE:
            take_action_receive(req);
            break;
        case State::DONE:
            std::cout << "Session completed\n";
            break;
    }
}

bool valid_packet_detail(ClientRequest& req){
    uint32_t session_id = get_session_id(req);

    std::lock_guard<std::mutex> lock(session_mutex);

    if(session_map.find(session_id) == session_map.end()){
        // creating session for the new client
        uint64_t expiry_time = calculate_next_session_timeout();
        session_map[session_id] = {
            State::START,
            expiry_time,
            0,
            req.socket,
            req.client_addr
        };
        expiry_map.insert({expiry_time, session_id});
        std::cout << "\n" << session_id << "[0] Session created!\n";
    }
    else if(session_map[session_id].expiry_time < now_ms()) {
        std::cout << "client sent at: " << session_map[session_id].expiry_time << "\n";
        std::cout << "server now: " << now_ms() << "\n";
        return false;
    }
    
    return true;
}

inline void handle_client(ClientRequest* raw_req) {
    std::unique_ptr<ClientRequest> req(raw_req); // RAII for cleanup

    if(!valid_header(*req)){
        std::cout << "\nincorrect packet header!!! \n";
        session_terminate(*req);
        return;
    }
    if(!valid_packet_detail(*req)){
        std::cout << "\ninvalid packet detail !!! \n";
        session_terminate(*req);
        return;
    }

    // updating the 'logical clock' to the client's 'logical clock' if it is larger than server's 'logical clock'
    uint64_t recv_clock = my_ntohll(req->packet.header.logical_clock);
    uint64_t current_clock = LOGICAL_CLOCK.load();
    LOGICAL_CLOCK.store(std::max(current_clock, recv_clock) + 1);

    uint64_t client_sending_time = my_ntohll(req->packet.header.timestamp);
    uint64_t server_receive_time = now_ms();
    uint64_t client_sending_latency = server_receive_time - client_sending_time;
    TOTAL_LATENCY.fetch_add(client_sending_latency);
    NUMBER_OF_MESSAGES_RECEIVED.fetch_add(1);

    take_action(*req);
}

#endif
