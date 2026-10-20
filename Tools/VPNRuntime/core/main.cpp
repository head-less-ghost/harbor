// OpenVPN 3 Core adapter. fd 3 is a private packet socket, never an OS TUN.
// Route and DNS callbacks describe the in-memory stack; none mutate macOS.
#include <arpa/inet.h>
#include <unistd.h>
#include <iostream>
#include <string>
#include <vector>
#include <thread>
#include <chrono>
#include <client/ovpncli.hpp>

using namespace openvpn;

static void event(const char *name) {
    std::cout << "{\"event\":\"" << name << "\"}" << std::endl;
}
static std::string readField() {
    uint32_t size = 0;
    std::cin.read(reinterpret_cast<char *>(&size), sizeof(size));
    size = ntohl(size);
    if (!std::cin || size > 4 * 1024 * 1024) throw std::runtime_error("invalid input");
    std::string value(size, '\0');
    std::cin.read(value.data(), size);
    if (!std::cin) throw std::runtime_error("incomplete input");
    return value;
}

class HarborVPN : public ClientAPI::OpenVPNClient {
    std::vector<std::string> addresses, dns;
    int mtu = 1500;
    bool connected = false;
public:
    bool tun_builder_new() override { addresses.clear(); dns.clear(); return true; }
    bool tun_builder_set_remote_address(const std::string &, bool) override { return true; }
    bool tun_builder_set_session_name(const std::string &) override { return true; }
    bool tun_builder_add_address(const std::string &ip, int, const std::string &, bool, bool) override {
        addresses.push_back(ip); return true;
    }
    bool tun_builder_set_dns_options(const DnsOptions &options) override {
        for (const auto &[priority, server] : options.servers)
            for (const auto &address : server.addresses) dns.push_back(address.address);
        return true;
    }
    bool tun_builder_set_mtu(int value) override { mtu = value; return value >= 576 && value <= 65535; }
    bool tun_builder_reroute_gw(bool, bool, unsigned int) override { return true; }
    bool tun_builder_add_route(const std::string &, int, int, bool) override { return true; }
    bool tun_builder_exclude_route(const std::string &, int, int, bool) override { return true; }
    bool tun_builder_set_allow_family(int, bool) override { return true; }
    bool tun_builder_persist() override { return false; }
    int tun_builder_establish() override {
        std::cout << "{\"event\":\"configuration\",\"mtu\":" << mtu << ",\"addresses\":[";
        for (size_t i=0; i<addresses.size(); ++i) {
            if (i) std::cout << ',';
            std::cout << '"' << addresses[i] << '"';
        }
        std::cout << "],\"dns\":[";
        for (size_t i=0; i<dns.size(); ++i) {
            if (i) std::cout << ',';
            std::cout << '"' << dns[i] << '"';
        }
        std::cout << "]}" << std::endl;
        return dup(3);
    }
    bool socket_protect(openvpn_io::detail::socket_type, std::string, bool) override { return true; }
    bool pause_on_connection_timeout() override { return false; }
    void event(const ClientAPI::Event &e) override {
        if (e.name == "CONNECTED") { connected = true; ::event("connected"); }
        else if (e.fatal || e.name == "AUTH_FAILED" || (connected && e.name == "RECONNECTING")) {
            ::event("error"); stop();
        }
    }
    void acc_event(const ClientAPI::AppCustomControlMessageEvent &) override {}
    void log(const ClientAPI::LogInfo &) override {}
    void external_pki_cert_request(ClientAPI::ExternalPKICertRequest &r) override { r.error = true; r.errorText = "Inline certificates required"; }
    void external_pki_sign_request(ClientAPI::ExternalPKISignRequest &r) override { r.error = true; r.errorText = "External signing is unavailable"; }
};

int main() {
    try {
        ClientAPI::Config config;
        config.content = readField();
        const auto username = readField();
        const auto password = readField();
        config.guiVersion = "Harbor";
        config.connTimeout = 35;
        config.dco = false;
        config.tunPersist = false;
        config.clockTickMS = 1000;
        HarborVPN client;
        const auto parsed = client.eval_config(config);
        if (parsed.error || parsed.externalPki || parsed.privateKeyPasswordRequired) { event("error"); return 1; }
        if (!parsed.autologin) {
            ClientAPI::ProvideCreds credentials;
            credentials.username = username;
            credentials.password = password;
            if (client.provide_creds(credentials).error) { event("error"); return 1; }
        }
        // The parent owns this tunnel. Do not leave a core behind after a crash.
        const auto parent = getppid();
        std::thread([parent] {
            while (getppid() == parent) std::this_thread::sleep_for(std::chrono::seconds(1));
            _exit(0);
        }).detach();
        const auto result = client.connect();
        event("error");
        return result.error ? 1 : 0;
    } catch (...) { event("error"); return 1; }
}
