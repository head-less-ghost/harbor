import AppKit
import Observation
import Security
import UniformTypeIdentifiers

@Observable @MainActor
final class TorrentVPNController {
    private(set) var profileName: String?
    private(set) var isConnecting = false
    private(set) var route: TorrentVPNRoute = .disabled
    private(set) var message = String(localized: "Disabled")
    var username = ""
    var password = ""
    @ObservationIgnored var routeDidChange: ((TorrentVPNRoute) -> Void)?
    @ObservationIgnored private var profile: TorrentVPNProfile?
    @ObservationIgnored private var process: Process?
    @ObservationIgnored private var input: Pipe?
    @ObservationIgnored private var output: Pipe?
    @ObservationIgnored private var buffer = Data()
    @ObservationIgnored private var generation = UUID()
    private let defaults: UserDefaults
    private var enabled = false
    private static let keychainService = "co.hapy.harbor.torrent-vpn"

    init(defaults: UserDefaults) {
        self.defaults = defaults
        if defaults.bool(forKey: "experimental.vpn.hasProfile") {
            do {
                if let data = try readProfile() {
                    let saved = try JSONDecoder().decode(TorrentVPNProfile.self, from: data)
                    profile = saved
                    profileName = saved.name
                    username = saved.username
                    password = saved.password
                }
            } catch { message = String(localized: "The saved VPN profile could not be read. Import it again.") }
        }
    }

    func setEnabled(_ enabled: Bool) {
        self.enabled = enabled
        disconnect()
        if enabled, profile != nil { connect() }
    }

    func importProfile() {
        guard enabled else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [UTType(filenameExtension: "ovpn") ?? .data,
                                     UTType(filenameExtension: "conf") ?? .data]
        panel.prompt = String(localized: "Import VPN Profile")
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let data = try Data(contentsOf: url, options: .mappedIfSafe)
            guard data.count <= 4 * 1_024 * 1_024,
                  let config = String(data: data, encoding: .utf8), !config.isEmpty else {
                throw CocoaError(.fileReadInapplicableStringEncoding)
            }
            let next = TorrentVPNProfile(
                name: url.lastPathComponent,
                protocol: url.pathExtension.lowercased() == "ovpn" ? "openvpn" : "wireguard",
                config: config, username: "", password: ""
            )
            try saveProfile(next)
            disconnect()
            profile = next
            profileName = next.name
            username = ""
            password = ""
            message = String(localized: "Ready to connect. Torrents are blocked until the VPN connects.")
        } catch { message = String(localized: "Could not import the VPN profile. Select a UTF-8 .ovpn or WireGuard .conf file.") }
    }

    func connect() {
        guard enabled, var profile else { return }
        disconnect()
        profile.username = username
        profile.password = password
        do {
            try saveProfile(profile)
            self.profile = profile
            guard let executable = HarborApplicationSupport.bundledResourceRoots()
                .map({ $0.appendingPathComponent("VPNRuntime/\(HarborApplicationSupport.architectureName)/bin/harbor-vpn") })
                .first(where: { FileManager.default.isExecutableFile(atPath: $0.path) }) else {
                message = String(localized: "The bundled VPN engine is missing. Reinstall Harbor.")
                return
            }
            let process = Process()
            let input = Pipe(), output = Pipe()
            let identifier = generation
            process.executableURL = executable
            process.standardInput = input
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            output.fileHandleForReading.readabilityHandler = { [weak self] handle in
                guard let self else { return }
                let data = handle.availableData
                Task { @MainActor in self.receive(data, generation: identifier) }
            }
            process.terminationHandler = { [weak self] _ in
                guard let self else { return }
                Task { @MainActor in
                    guard self.generation == identifier else { return }
                    self.disconnect()
                    self.message = String(localized: "The VPN stopped. Torrents remain blocked.")
                }
            }
            try process.run()
            self.process = process
            self.input = input
            self.output = output
            isConnecting = true
            message = String(localized: "Connecting… Torrents are blocked.")
            var data = try JSONEncoder().encode(profile)
            data.append(0x0A)
            try input.fileHandleForWriting.write(contentsOf: data)
        } catch {
            disconnect()
            message = String(localized: "Could not start the VPN. Check the profile and saved credentials.")
        }
    }

    func disconnect() {
        generation = UUID()
        output?.fileHandleForReading.readabilityHandler = nil
        try? input?.fileHandleForWriting.close()
        try? output?.fileHandleForReading.close()
        let previous = process
        process = nil
        input = nil
        output = nil
        buffer.removeAll()
        if previous?.isRunning == true { previous?.terminate() }
        isConnecting = false
        publish(enabled ? .blocked : .disabled)
        message = enabled ? String(localized: "Disconnected. Torrents are blocked.") : String(localized: "Disabled")
    }

    private func receive(_ data: Data, generation: UUID) {
        guard self.generation == generation else { return }
        buffer.append(data)
        guard buffer.count <= 64 * 1_024 else { disconnect(); return }
        while let newline = buffer.firstIndex(of: 0x0A) {
            let line = Data(buffer[..<newline])
            buffer.removeSubrange(...newline)
            guard let event = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { continue }
            if event["event"] as? String == "ready",
               let port = event["port"] as? Int, (1...65_535).contains(port),
               let username = event["username"] as? String,
               let password = event["password"] as? String {
                var endpoint = URLComponents()
                endpoint.scheme = "socks5"
                endpoint.host = "127.0.0.1"
                endpoint.port = port
                endpoint.user = username
                endpoint.password = password
                guard let uri = endpoint.string else { disconnect(); return }
                isConnecting = false
                publish(.connected(proxyURI: uri))
                message = String(localized: "Connected. Torrent traffic uses the VPN.")
            } else if event["event"] as? String == "error" {
                let detail = event["message"] as? String
                disconnect()
                message = detail ?? String(localized: "The VPN failed. Torrents remain blocked.")
                return
            }
        }
    }

    private func publish(_ route: TorrentVPNRoute) {
        guard self.route != route else { return }
        self.route = route
        routeDidChange?(route)
    }

    private func readProfile() throws -> Data? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.keychainService, kSecAttrAccount as String: "profile",
            kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
        return result as? Data
    }

    private func saveProfile(_ profile: TorrentVPNProfile) throws {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.keychainService, kSecAttrAccount as String: "profile"]
        let attributes: [String: Any] = [kSecValueData as String: try JSONEncoder().encode(profile)]
        var status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var item = query.merging(attributes) { _, value in value }
            item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            status = SecItemAdd(item as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
        defaults.set(true, forKey: "experimental.vpn.hasProfile")
    }
}
