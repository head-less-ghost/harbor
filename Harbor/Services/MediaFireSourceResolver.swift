import Foundation

/// Keeps the public share URL as the source; expiring download URLs are resolved per attempt.
nonisolated enum MediaFireSourceResolver {
    struct ResolvedFile {
        let url: URL
        let filename: String?
    }
    static func isFileURL(_ url: URL) -> Bool { sourceKey(url, kind: "file") != nil || sourceKey(url, kind: "view") != nil }
    static func isFolderURL(_ url: URL) -> Bool { sourceKey(url, kind: "folder") != nil }

    private static func sourceKey(_ url: URL, kind: String) -> String? {
        guard ["mediafire.com", "www.mediafire.com"].contains(url.host?.lowercased() ?? ""),
              ["http", "https"].contains(url.scheme?.lowercased() ?? "") else { return nil }
        let components = url.pathComponents.filter { $0 != "/" }
        guard components.count >= 2, components[0] == kind, validKey(components[1]) else { return nil }
        return components[1]
    }

    private static func validKey(_ key: String) -> Bool {
        !key.isEmpty && key.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber) }
    }

    private static func session(proxy: NetworkProxySettings) throws -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 120
        try proxy.apply(to: configuration)
        return URLSession(configuration: configuration)
    }

    static func downloadURL(for source: URL, proxy: NetworkProxySettings) async throws -> ResolvedFile? {
        let session = try session(proxy: proxy)
        defer { session.finishTasksAndInvalidate() }
        let (data, response) = try await session.data(from: source)
        guard let response = response as? HTTPURLResponse, response.statusCode == 200,
              let html = String(data: data, encoding: .utf8) else { return nil }
        // Match the actual download button, not advertising links elsewhere on the page.
        let anchors = try NSRegularExpression(pattern: "<a\\b[^>]*>", options: [.caseInsensitive])
        let href = try NSRegularExpression(pattern: "\\bhref\\s*=\\s*[\"']([^\"']+)[\"']", options: [.caseInsensitive])
        let button = try NSRegularExpression(pattern: "\\bid\\s*=\\s*[\"']downloadButton[\"']", options: [.caseInsensitive])
        for match in anchors.matches(in: html, range: NSRange(html.startIndex..., in: html)) {
            let tag = (html as NSString).substring(with: match.range)
            let range = NSRange(tag.startIndex..., in: tag)
            guard button.firstMatch(in: tag, range: range) != nil,
                  let link = href.firstMatch(in: tag, range: range),
                  let url = URL(string: (tag as NSString).substring(with: link.range(at: 1))
                    .replacingOccurrences(of: "&amp;", with: "&")),
                  url.scheme == "https", let host = url.host?.lowercased(),
                  host.hasSuffix(".mediafire.com") else { continue }
            // MediaFire's button uses form-style escaping; its response filename can decode UTF-8 as Latin-1.
            let filename = URLComponents(url: url, resolvingAgainstBaseURL: false)?.percentEncodedPath
                .split(separator: "/").last.map(String.init)?
                .replacingOccurrences(of: "+", with: " ").removingPercentEncoding
            return ResolvedFile(url: url, filename: filename)
        }
        // A protected or challenged page still uses Harbor's normal browser continuation.
        return nil
    }

    static func fileURLs(in source: URL, proxy: NetworkProxySettings) async throws -> [URL] {
        guard let root = sourceKey(source, kind: "folder") else { return [] }
        let session = try session(proxy: proxy)
        defer { session.finishTasksAndInvalidate() }
        let selectedFolder = source.fragment.flatMap { validKey($0) ? $0 : nil } ?? root
        var folders = [selectedFolder]
        var visited = Set<String>()
        var files: [URL] = []
        var seenFiles = Set<String>()
        while !folders.isEmpty {
            let key = folders.removeFirst()
            guard visited.insert(key).inserted else { continue }
            for type in ["files", "folders"] {
                var chunk = 1
                var hasMore = true
                while hasMore {
                    try Task.checkCancellation()
                    var components = URLComponents(string: "https://www.mediafire.com/api/1.5/folder/get_content.php")!
                    components.queryItems = [
                        .init(name: "folder_key", value: key), .init(name: "content_type", value: type),
                        .init(name: "chunk", value: String(chunk)), .init(name: "response_format", value: "json")
                    ]
                    let (data, response) = try await session.data(from: components.url!)
                    guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw FolderError.unavailable }
                    let result = try JSONDecoder().decode(APIResult.self, from: data).response
                    guard result.result == "Success", let content = result.folder_content else {
                        throw FolderError.unavailable
                    }
                    for file in content.files ?? [] where validKey(file.quickkey) && seenFiles.insert(file.quickkey).inserted {
                        files.append(URL(string: "https://www.mediafire.com/file/\(file.quickkey)")!
                            .appendingPathComponent(file.filename).appendingPathComponent("file"))
                    }
                    folders.append(contentsOf: (content.folders ?? []).map(\.folderkey).filter(validKey))
                    hasMore = content.more_chunks == "yes"
                    chunk += 1
                }
            }
        }
        guard !files.isEmpty else { throw FolderError.empty }
        return files
    }

    private struct APIResult: Decodable {
        let response: Response
        struct Response: Decodable {
            let result: String
            let folder_content: Content?
        }
        struct Content: Decodable {
            let files: [File]?
            let folders: [Folder]?
            let more_chunks: String
        }
        struct File: Decodable {
            let quickkey: String
            let filename: String
        }
        struct Folder: Decodable { let folderkey: String }
    }

    private enum FolderError: LocalizedError {
        case unavailable, empty
        var errorDescription: String? {
            switch self {
            case .unavailable: String(localized: "MediaFire could not list this folder. Check that it is public and try again.")
            case .empty: String(localized: "No files were found in this MediaFire folder.")
            }
        }
    }
    // TODO: Preserve subfolder paths when Harbor supports folder-aware batch destinations.
}
