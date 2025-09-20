#include <iostream>
#include <thread>
#include <atomic>
#include <csignal>
#include <unistd.h>
#include <arpa/inet.h>
#include "uap.hpp"
#include "server.hpp"

std::atomic<bool> running(true);

void signal_handler(int) {
    running = false;
}

// this thread watch if 'q' is entered from terminal to terminate the server
void stdin_watcher() {
    std::string line;
    while (running && std::getline(std::cin, line)) {
        if (line == "q") {
            running = false;
            LOGICAL_CLOCK.fetch_add(1); // incrementing logical clock for getting 'q' from stdin
            {
                std::lock_guard<std::mutex> lock(session_mutex);
                while (!expiry_map.empty()) {
                    uint64_t expiry_time = expiry_map.begin()->first;
                    uint32_t session_id = expiry_map.begin()->second;
                    expiry_map.erase(expiry_map.begin());
                    auto session_map_iterator = session_map.find(session_id);
                    if (session_map_iterator != session_map.end()) {
                        std::cout << "\n terminating " << session_id << "\n";
                        SessionData& session_data = session_map_iterator->second;
                        auto message = make_message(session_id, Command::GOODBYE);
                        sendto(
                            session_data.socket,
                            &message,
                            sizeof(message.header),
                            0,
                            reinterpret_cast<const sockaddr*>(&session_data.client_addr),
                            sizeof(session_data.client_addr)
                        );
                        LOGICAL_CLOCK.fetch_add(1); // incrementing logical clock for sending message
                        session_map.erase(session_map_iterator);
                    }
                }
            }
            double average_oneway_latency = static_cast<double>(TOTAL_LATENCY.load()) / NUMBER_OF_MESSAGES_RECEIVED.load();
            std::cout << "Average one-way latency: " << average_oneway_latency << " micro-seconds\n";
            break;
        }
    }
}

void session_cleanup_thread() {
    while (running) {
        {
            std::lock_guard<std::mutex> lock(session_mutex);
            uint64_t now = now_ms();
            while (!expiry_map.empty() && expiry_map.begin()->first < now) {
                uint64_t expiry_time = expiry_map.begin()->first;
                uint32_t session_id = expiry_map.begin()->second;
                expiry_map.erase(expiry_map.begin());
                auto session_map_iterator = session_map.find(session_id);
                if (session_map_iterator != session_map.end()) {
                    std::cout << "\n" << session_id << " was quite for too long [SESSION TIMEOUT]\n";
                    LOGICAL_CLOCK.fetch_add(1); // incrementing logical clock for TIMEOUT reach
                    SessionData& session_data = session_map_iterator->second;
                    auto message = make_message(session_id, Command::GOODBYE);
                    sendto(
                        session_data.socket,
                        &message,
                        sizeof(message.header),
                        0,
                        reinterpret_cast<const sockaddr*>(&session_data.client_addr),
                        sizeof(session_data.client_addr)
                    );
                    LOGICAL_CLOCK.fetch_add(1); // incrementing logical clock for sending message
                    session_map.erase(session_map_iterator);
                }
            }
        }
        std::this_thread::sleep_for(std::chrono::seconds(SESSION_TIMEOUT));
    }
}


int main(int argc, char* argv[]) {

    if (argc >= 2) {
        try {
            PORT = std::stoi(argv[1]);
        } catch (const std::exception& e) {
            std::cerr << "Invalid port number. It should be Positive Integer(like 8080)\n";
            return EXIT_FAILURE;
        }
    }

    int server_sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (server_sock < 0) {
        std::perror("Socket creation failed");
        return EXIT_FAILURE;
    }

    sockaddr_in server_addr{};
    server_addr.sin_family = AF_INET;
    server_addr.sin_addr.s_addr = INADDR_ANY;
    server_addr.sin_port = htons(PORT);

    if (bind(server_sock, reinterpret_cast<sockaddr*>(&server_addr), sizeof(server_addr)) < 0) {
        std::perror("Bind failed");
        close(server_sock);
        return EXIT_FAILURE;
    }
    timeval tv{};
    tv.tv_sec = 1;   // timeout = 1 second
    tv.tv_usec = 0;
    if (setsockopt(server_sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv)) < 0) {
        perror("setsockopt failed");
    }

    std::cout << "server listening on port " << PORT << "...\n";

    // started the stdin watching thread
    std::thread input_thread(stdin_watcher);

    // started the cleanup thread
    std::thread cleanup_thread(session_cleanup_thread);


    while (running) {
        auto req = std::make_unique<ClientRequest>();
        socklen_t client_len = sizeof(req->client_addr);

        req->msg_len = recvfrom(server_sock,
                                &req->packet,
                                sizeof(req->packet),
                                0,
                                reinterpret_cast<sockaddr*>(&req->client_addr),
                                &client_len);

        if (req->msg_len < 0) {
            if (!running) break;  // shutting down
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                // timeout, no packet, just loop again
                continue;
            }
            std::perror("recvfrom failed");
            continue;
        }

        req->socket = server_sock;

        std::thread([r = req.release()]() {
            handle_client(r);
        }).detach();
    }

    close(server_sock);

    if(input_thread.joinable()) {
        std::cout << "\nclosing the input thread...\n";
        input_thread.join();
        std::cout << "input thread closed\n";
    }

    if(cleanup_thread.joinable()) {
        std::cout << "\nclosing the cleanup thread...\n";
        cleanup_thread.join();
        std::cout << "cleanup thread closed\n";
    }

    std::cout << "\nServer shutting down...\n";
    return 0;
}
// g++ -std=c++17 server.cpp -o server -pthread