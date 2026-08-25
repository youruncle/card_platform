#!/usr/bin/env swift
import AppKit
import Foundation
import Vision

guard CommandLine.arguments.count == 2 else {
    fputs("usage: ocr_card_region.swift IMAGE\n", stderr)
    exit(64)
}
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let image = NSImage(contentsOf: url),
      let data = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: data),
      let cgImage = bitmap.cgImage
else { fputs("invalid image\n", stderr); exit(65) }

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["en-US", "zh-Hans"]
try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
let payload: [String: Any] = ["lines": lines, "text": lines.joined(separator: "\n")]
let encoded = try JSONSerialization.data(withJSONObject: payload)
print(String(data: encoded, encoding: .utf8)!)
