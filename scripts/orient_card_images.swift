#!/usr/bin/env swift

import Foundation
import Vision

struct OrientationResult: Codable {
    let path: String
    let rotationDegrees: Int?
    let confidence: Double
    let textCount: Int
    let faceRotationDegrees: Int?
    let faceConfidence: Double
    let faceCount: Int
    let error: String?
}

func normalizedDegrees(_ value: Double) -> Double {
    let result = value.truncatingRemainder(dividingBy: 360)
    return result < 0 ? result + 360 : result
}

var output: [OrientationResult] = []
for path in CommandLine.arguments.dropFirst() {
    do {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        request.recognitionLanguages = ["en-US", "zh-Hans"]
        request.minimumTextHeight = 0.012
        let faceRequest = VNDetectFaceRectanglesRequest()
        let handler = VNImageRequestHandler(url: URL(fileURLWithPath: path), orientation: .up)
        try handler.perform([request, faceRequest])

        var votes = [0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0]
        var textCount = 0
        for observation in request.results ?? [] {
            guard let candidate = observation.topCandidates(1).first else { continue }
            let text = candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
            guard text.count >= 2, candidate.confidence >= 0.2 else { continue }
            let dx = Double(observation.topRight.x - observation.topLeft.x)
            let dy = Double(observation.topRight.y - observation.topLeft.y)
            guard abs(dx) + abs(dy) > 0.0001 else { continue }
            let baseline = normalizedDegrees(atan2(dy, dx) * 180 / Double.pi)
            let nearest = Int((baseline / 90).rounded()) * 90 % 360
            // Vision orders the semantic text-box corners, so this angle also
            // distinguishes upright from upside-down text. `nearest` is the
            // clockwise correction required in image coordinates.
            let weight = Double(candidate.confidence) * Double(min(text.count, 80))
            votes[nearest, default: 0] += weight
            textCount += 1
        }
        let ranked = votes.sorted { $0.value > $1.value }
        let total = votes.values.reduce(0, +)
        let best = ranked.first
        let second = ranked.dropFirst().first?.value ?? 0
        let confidence = total > 0 && best != nil
            ? max(0, min(1, (best!.value - second) / total))
            : 0

        var faceVotes = [0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0]
        var faceConfidenceTotal = 0.0
        var faceWeightTotal = 0.0
        var faceCount = 0
        for face in faceRequest.results ?? [] {
            guard face.confidence >= 0.55, let roll = face.roll?.doubleValue else { continue }
            let rollDegrees = roll * 180 / Double.pi
            let nearestSigned = (rollDegrees / 90).rounded() * 90
            let residual = abs(rollDegrees - nearestSigned)
            guard residual <= 25 else { continue }
            let nearest = Int(normalizedDegrees(nearestSigned)) % 360
            let area = Double(face.boundingBox.width * face.boundingBox.height)
            guard area >= 0.002 else { continue }
            let alignment = max(0, 1 - residual / 45)
            let weight = Double(face.confidence) * alignment
            faceVotes[nearest, default: 0] += weight
            faceConfidenceTotal += Double(face.confidence) * weight
            faceWeightTotal += weight
            faceCount += 1
        }
        let rankedFaces = faceVotes.sorted { $0.value > $1.value }
        let faceTotal = faceVotes.values.reduce(0, +)
        let bestFace = rankedFaces.first
        let secondFace = rankedFaces.dropFirst().first?.value ?? 0
        let faceConsensus = faceTotal > 0 && bestFace != nil
            ? max(0, min(1, (bestFace!.value - secondFace) / faceTotal))
            : 0
        let averageFaceConfidence = faceWeightTotal > 0
            ? faceConfidenceTotal / faceWeightTotal
            : 0
        let faceConfidence = faceConsensus * averageFaceConfidence
        output.append(
            OrientationResult(
                path: path,
                rotationDegrees: (best?.value ?? 0) > 0 ? best?.key : nil,
                confidence: confidence,
                textCount: textCount,
                faceRotationDegrees: (bestFace?.value ?? 0) > 0 ? bestFace?.key : nil,
                faceConfidence: faceConfidence,
                faceCount: faceCount,
                error: nil
            )
        )
    } catch {
        output.append(
            OrientationResult(
                path: path,
                rotationDegrees: nil,
                confidence: 0,
                textCount: 0,
                faceRotationDegrees: nil,
                faceConfidence: 0,
                faceCount: 0,
                error: error.localizedDescription
            )
        )
    }
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
FileHandle.standardOutput.write(try encoder.encode(output))
