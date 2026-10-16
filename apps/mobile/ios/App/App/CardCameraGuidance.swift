import Foundation
import CoreGraphics

/// Device-side framing hints only. No frame or inferred geometry leaves the device.
struct CardCameraGuidance {
    enum Hint: String {
        case findCard = "将一张卡片放入画面"
        case multipleCards = "画面中有多个候选，请只保留目标卡片"
        case closer = "靠近一点，让卡片更清晰"
        case includeEdges = "稍微后退，保留完整边缘"
        case steady = "保持稳定"
        case ready = "可以拍摄 · 拍后请确认裁切"
    }

    struct Result {
        let hint: Hint
        let quad: [CGPoint]?
    }

    private var previous: [CGPoint]?
    private var stableFrames = 0

    mutating func update(candidates: [[CGPoint]]) -> Result {
        guard candidates.count == 1, let quad = candidates.first, quad.count == 4 else {
            previous = nil
            stableFrames = 0
            return Result(hint: candidates.isEmpty ? .findCard : .multipleCards, quad: nil)
        }
        let xs = quad.map(\.x), ys = quad.map(\.y)
        let area = (xs.max()! - xs.min()!) * (ys.max()! - ys.min()!)
        let movement = previous.map { old in
            zip(old, quad).map { hypot($0.x - $1.x, $0.y - $1.y) }.max() ?? 1
        } ?? 1
        stableFrames = movement < 0.025 ? stableFrames + 1 : 1
        previous = quad
        if xs.min()! < 0.025 || xs.max()! > 0.975 || ys.min()! < 0.025 || ys.max()! > 0.975 {
            return Result(hint: .includeEdges, quad: quad)
        }
        if area < 0.12 { return Result(hint: .closer, quad: quad) }
        return Result(hint: stableFrames >= 3 ? .ready : .steady, quad: quad)
    }

    /// Vision coordinates have a bottom-left origin. Both buffers and preview are portrait.
    static func previewPoint(_ point: CGPoint, imageSize: CGSize, viewSize: CGSize) -> CGPoint {
        guard imageSize.width > 0, imageSize.height > 0 else { return .zero }
        let scale = max(viewSize.width / imageSize.width, viewSize.height / imageSize.height)
        let offsetX = (viewSize.width - imageSize.width * scale) / 2
        let offsetY = (viewSize.height - imageSize.height * scale) / 2
        return CGPoint(x: point.x * imageSize.width * scale + offsetX,
                       y: (1 - point.y) * imageSize.height * scale + offsetY)
    }
}
