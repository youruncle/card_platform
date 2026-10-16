import Foundation

@main
struct CardCameraTemporaryFilesTests {
    static func main() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let files = CardCameraTemporaryFiles(directory: directory)
        let own = directory.appendingPathComponent("card-camera-\(UUID().uuidString).jpg")
        let other = directory.appendingPathComponent("unrelated.jpg")
        try Data([1,2,3]).write(to: own)
        try Data([4,5,6]).write(to: other)
        try files.remember(own)
        do { try files.release(other.absoluteString); preconditionFailure("Unknown file was accepted") }
        catch CardCameraTemporaryFiles.Failure.unownedPath { }
        precondition(FileManager.default.fileExists(atPath: other.path))
        do { try files.release("https://example.com/image.jpg"); preconditionFailure("Remote URL was accepted") }
        catch CardCameraTemporaryFiles.Failure.unownedPath { }
        try files.release(own.absoluteString)
        precondition(!FileManager.default.fileExists(atPath: own.path))
        do { try files.release(own.absoluteString); preconditionFailure("Released URL still owned") }
        catch CardCameraTemporaryFiles.Failure.unownedPath { }
        let pending = directory.appendingPathComponent("card-camera-\(UUID().uuidString).jpg")
        try Data([7]).write(to: pending)
        try files.remember(pending)
        files.removeAll()
        precondition(!FileManager.default.fileExists(atPath: pending.path))
        precondition(FileManager.default.fileExists(atPath: other.path))
        print("CardCameraTemporaryFiles: release, path isolation and cleanup passed")
    }
}
