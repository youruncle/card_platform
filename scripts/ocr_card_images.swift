#!/usr/bin/env swift

import Foundation
import Vision

struct OCRResult: Codable {
    let path: String
    let lines: [String]
    let error: String?
}

var output: [OCRResult] = []
for path in CommandLine.arguments.dropFirst() {
    do {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        request.recognitionLanguages = ["en-US", "zh-Hans"]
        let handler = VNImageRequestHandler(url: URL(fileURLWithPath: path))
        try handler.perform([request])
        let lines = (request.results ?? []).compactMap {
            $0.topCandidates(1).first?.string
        }
        output.append(OCRResult(path: path, lines: lines, error: nil))
    } catch {
        output.append(OCRResult(path: path, lines: [], error: error.localizedDescription))
    }
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
FileHandle.standardOutput.write(try encoder.encode(output))
