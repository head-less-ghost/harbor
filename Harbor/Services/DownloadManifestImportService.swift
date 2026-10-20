import Foundation

nonisolated struct DownloadManifest: Sendable {
    var urls: [URL] = []
    var preview: [DownloadSourceImportService.TextEntry] = []
    var skippedCount = 0
    var lineCount = 0
}

nonisolated enum DownloadManifestImportService {
    /// Keep one normalized URL per download and only a small preview. Never put
    /// the manifest contents into a text control or retain every skipped line.
    @concurrent static func read(_ url: URL) async throws -> DownloadManifest {
        var result = DownloadManifest()
        var seenKeys = Set<String>()
        for try await rawLine in url.lines {
            try Task.checkCancellation()
            let line = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
                .trimmingCharacters(in: CharacterSet(charactersIn: "\u{FEFF}"))
            guard !line.isEmpty else { continue }
            let entry = DownloadSourceImportService.textEntry(
                line: line, id: result.lineCount, seenKeys: &seenKeys
            )
            result.lineCount += 1
            if let url = entry.url { result.urls.append(url) }
            else { result.skippedCount += 1 }
            if result.preview.count < 50 { result.preview.append(entry) }
        }
        return result
    }
}
