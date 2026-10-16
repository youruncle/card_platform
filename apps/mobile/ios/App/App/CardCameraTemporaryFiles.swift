import Foundation

/// Only photos issued by this plugin instance can be released through the JS bridge.
final class CardCameraTemporaryFiles {
    enum Failure: Error { case unownedPath }
    private let directory: URL
    private var issued = Set<URL>()

    init(directory: URL = FileManager.default.temporaryDirectory) {
        self.directory = directory.resolvingSymlinksInPath().standardizedFileURL
    }

    func remember(_ url: URL) throws {
        let normalized = url.standardizedFileURL
        guard url.isFileURL, url.query == nil, url.fragment == nil,
              normalized.deletingLastPathComponent().resolvingSymlinksInPath() == directory,
              normalized.lastPathComponent.hasPrefix("card-camera-"), normalized.pathExtension == "jpg" else {
            throw Failure.unownedPath
        }
        issued.insert(normalized)
    }

    func release(_ path: String) throws {
        guard let url = URL(string: path), url.isFileURL, url.query == nil, url.fragment == nil,
              issued.contains(url.standardizedFileURL) else { throw Failure.unownedPath }
        let normalized = url.standardizedFileURL
        if FileManager.default.fileExists(atPath: normalized.path) {
            try FileManager.default.removeItem(at: normalized)
        }
        issued.remove(normalized)
    }

    func removeAll() {
        for url in issued { try? FileManager.default.removeItem(at: url) }
        issued.removeAll()
    }

    deinit { removeAll() }
}
