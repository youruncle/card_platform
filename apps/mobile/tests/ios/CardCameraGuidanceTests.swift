import Foundation
import CoreGraphics

@main
struct CardCameraGuidanceTests {
    static func main() {
        var tracker = CardCameraGuidance()
        let card = [CGPoint(x: 0.2, y: 0.9), CGPoint(x: 0.8, y: 0.9), CGPoint(x: 0.8, y: 0.1), CGPoint(x: 0.2, y: 0.1)]
        precondition(tracker.update(candidates: []).hint == .findCard)
        precondition(tracker.update(candidates: [card, card]).hint == .multipleCards)
        precondition(tracker.update(candidates: [card]).hint == .steady)
        precondition(tracker.update(candidates: [card]).hint == .steady)
        precondition(tracker.update(candidates: [card]).hint == .ready)
        let moved = card.map { CGPoint(x: $0.x + 0.06, y: $0.y) }
        precondition(tracker.update(candidates: [moved]).hint == .steady)
        let small = [CGPoint(x: 0.4, y: 0.6), CGPoint(x: 0.6, y: 0.6), CGPoint(x: 0.6, y: 0.4), CGPoint(x: 0.4, y: 0.4)]
        precondition(tracker.update(candidates: [small]).hint == .closer)
        let clipped = [CGPoint(x: 0.01, y: 0.9), CGPoint(x: 0.8, y: 0.9), CGPoint(x: 0.8, y: 0.1), CGPoint(x: 0.01, y: 0.1)]
        precondition(tracker.update(candidates: [clipped]).hint == .includeEdges)
        let point = CardCameraGuidance.previewPoint(CGPoint(x: 0, y: 1), imageSize: CGSize(width: 100, height: 200), viewSize: CGSize(width: 200, height: 200))
        precondition(point == CGPoint(x: 0, y: -100))
        let center = CardCameraGuidance.previewPoint(CGPoint(x: 0.5, y: 0.5), imageSize: CGSize(width: 100, height: 200), viewSize: CGSize(width: 200, height: 200))
        precondition(center == CGPoint(x: 100, y: 100))
        print("CardCameraGuidance: 10 behavioral checks passed")
    }
}
