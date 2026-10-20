import Foundation

nonisolated enum TorrentVPNRoute: Equatable, Sendable {
    case disabled
    case blocked
    case connected(proxyURI: String)

    var permitsTorrents: Bool {
        if case .blocked = self { return false }
        return true
    }
}

nonisolated struct TorrentVPNProfile: Codable {
    let name: String
    let `protocol`: String
    let config: String
    var username: String
    var password: String
}
