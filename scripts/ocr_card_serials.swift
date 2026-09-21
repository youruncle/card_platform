#!/usr/bin/env swift

import Foundation
import Vision
import ImageIO
import CoreImage

struct SerialObservation: Codable {
    let image_index: Int
    let orientation_degrees: Int
    let text: String
    let context: String
    let confidence: Float
    let bbox: [Double]
    let read_pass: String
    let seed_text: String?
    let roi_bbox: [Double]?
    let support_level: String?
}

struct SerialOCRResponse: Codable {
    let observations: [SerialObservation]
    let errors: [String]
    let strategy_version: String
    let detail_seed_count: Int
    let detail_request_count: Int
    let truncated: Bool
}

struct DetailSeed {
    let imageIndex: Int
    let path: String
    let orientationDegrees: Int
    let orientation: CGImagePropertyOrientation
    let text: String
    let context: String
    let confidence: Float
    let bbox: CGRect
    let kind: String
}

// Read the original pixels in four orientations. No region is assumed to be
// the serial location, and no missing slash/digit is invented by this reader.
let fraction = try NSRegularExpression(pattern: #"[0-9]{1,6}\s*[/／⁄∕]\s*[0-9]{1,6}|[0-9]{1,6}\s+of\s+[0-9]{1,6}"#, options: [.caseInsensitive])
let slashFractionParts = try NSRegularExpression(pattern: #"(?<![0-9/／⁄∕])0*([0-9]{1,6})\s*[/／⁄∕]\s*0*([0-9]{1,6})(?![0-9A-Za-z/／⁄∕])"#)
let ofFractionParts = try NSRegularExpression(pattern: #"(?<![0-9A-Za-z])0*([0-9]{1,6})\s+of\s+0*([0-9]{1,6})(?![0-9A-Za-z])"#, options: [.caseInsensitive])
let isolatedDigits = try NSRegularExpression(pattern: #"^\s*[0-9]{3,6}\s*$"#)
let obviousDateOrStatContext = try NSRegularExpression(
    pattern: #"\b(?:born|birth|dob|date|debut|fg|ft|pts|points|career|stats?|card\s*(?:no|number)|no\.)\b"#,
    options: [.caseInsensitive]
)
let rotations: [(Int, CGImagePropertyOrientation)] = [(0, .up), (90, .right), (180, .down), (270, .left)]
let maxDetailSeedsPerImage = 2
let maxDetailRequestsPerImage = 12
let maxDetailLongEdge = 1600.0
let softDeadlineSeconds = 20.0
let startedAt = Date()
var observations: [SerialObservation] = []
var errors: [String] = []
var detailSeeds: [DetailSeed] = []
var detailSeedCount = 0
var detailRequestCount = 0
var truncated = false
let renderer = CIContext(options: [.cacheIntermediates: false])

func containsMatch(_ expression: NSRegularExpression, _ text: String) -> Bool {
    let range = NSRange(text.startIndex..<text.endIndex, in: text)
    return expression.firstMatch(in: text, range: range) != nil
}

func parsedFraction(_ text: String) -> (Int, Int)? {
    for expression in [slashFractionParts, ofFractionParts] {
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let match = expression.firstMatch(in: text, range: range),
              let numberRange = Range(match.range(at: 1), in: text),
              let totalRange = Range(match.range(at: 2), in: text),
              let number = Int(text[numberRange]),
              let total = Int(text[totalRange]),
              0 < number, number <= total, total <= 100_000 else { continue }
        return (number, total)
    }
    return nil
}

func deadlineReached() -> Bool {
    Date().timeIntervalSince(startedAt) >= softDeadlineSeconds
}

func seedRank(_ seed: DetailSeed) -> (Int, Int) {
    let text = seed.text.trimmingCharacters(in: .whitespacesAndNewlines)
    let primary: Int
    if seed.kind == "isolated_digits" {
        primary = text.hasPrefix("0") ? 0 : 1
    } else {
        primary = parsedFraction(seed.text) == nil ? 0 : 1
    }
    let value = Int(text)
    let looksLikeYear = value.map { 1900...2100 ~= $0 } ?? false
    let contextualNoise = containsMatch(obviousDateOrStatContext, seed.context)
    return (primary, looksLikeYear || contextualNoise ? 1 : 0)
}

func preferredSeed(_ left: DetailSeed, _ right: DetailSeed) -> Bool {
    let leftRank = seedRank(left)
    let rightRank = seedRank(right)
    if leftRank.0 != rightRank.0 { return leftRank.0 < rightRank.0 }
    if leftRank.1 != rightRank.1 { return leftRank.1 < rightRank.1 }
    return left.confidence > right.confidence
}

func uniqueOrderedSeeds(_ candidates: [DetailSeed]) -> [DetailSeed] {
    var seen: Set<String> = []
    return candidates.sorted(by: preferredSeed).filter { seed in
        let text = seed.text.trimmingCharacters(in: .whitespacesAndNewlines)
        return seen.insert("\(seed.kind):\(text)").inserted
    }
}

func selectDetailSeeds(_ candidates: [DetailSeed]) -> [DetailSeed] {
    let isolated = uniqueOrderedSeeds(candidates.filter { $0.kind == "isolated_digits" })
    let fractions = uniqueOrderedSeeds(candidates.filter { $0.kind == "fraction" })
    var selected: [DetailSeed] = []
    // Reserve one slot for each signal family. This prevents two ordinary card
    // numbers/years from starving a stronger-but-truncated ``0/20`` fraction.
    if let seed = isolated.first { selected.append(seed) }
    if let seed = fractions.first { selected.append(seed) }
    if selected.count < maxDetailSeedsPerImage {
        let selectedKeys = Set(selected.map {
            "\($0.kind):\($0.text.trimmingCharacters(in: .whitespacesAndNewlines))"
        })
        for seed in uniqueOrderedSeeds(candidates) {
            let key = "\(seed.kind):\(seed.text.trimmingCharacters(in: .whitespacesAndNewlines))"
            if !selectedKeys.contains(key) { selected.append(seed) }
            if selected.count == maxDetailSeedsPerImage { break }
        }
    }
    return Array(selected.prefix(maxDetailSeedsPerImage))
}

if CommandLine.arguments.dropFirst().first == "--seed-policy-fixture" {
    let box = CGRect(x: 0.1, y: 0.1, width: 0.05, height: 0.05)
    let seeds = [
        DetailSeed(imageIndex: 0, path: "", orientationDegrees: 0, orientation: .up, text: "120", context: "120", confidence: 0.9, bbox: box, kind: "isolated_digits"),
        DetailSeed(imageIndex: 0, path: "", orientationDegrees: 0, orientation: .up, text: "2026", context: "copyright year 2026", confidence: 1, bbox: box, kind: "isolated_digits"),
        DetailSeed(imageIndex: 0, path: "", orientationDegrees: 180, orientation: .down, text: "0 /20", context: "0 /20", confidence: 0.3, bbox: box, kind: "fraction"),
    ]
    let selected = selectDetailSeeds(seeds)
    let fixture: [String: Any] = [
        "selected": selected.map { ["kind": $0.kind, "text": $0.text] },
        "max_seeds_per_image": maxDetailSeedsPerImage,
        "max_detail_requests_per_image": maxDetailRequestsPerImage,
    ]
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: fixture, options: [.sortedKeys]))
    exit(0)
}

func requestForText() -> VNRecognizeTextRequest {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.recognitionLanguages = ["en-US", "zh-Hans"]
    request.minimumTextHeight = 0.004
    return request
}

func autoEnhanced(_ image: CIImage) -> CIImage {
    image.autoAdjustmentFilters(options: [.enhance: true, .redEye: false]).reduce(image) {
        current, filter in
        filter.setValue(current, forKey: kCIInputImageKey)
        return filter.outputImage ?? current
    }
}

for (index, path) in CommandLine.arguments.dropFirst().enumerated() {
    if deadlineReached() {
        truncated = true
        break
    }
    for (degrees, orientation) in rotations {
        if deadlineReached() {
            truncated = true
            break
        }
        autoreleasepool {
            do {
                let request = requestForText()
                try VNImageRequestHandler(url: URL(fileURLWithPath: path), orientation: orientation).perform([request])
                let rows = request.results ?? []
                let texts = rows.map { $0.topCandidates(1).first?.string ?? "" }
                for (lineIndex, row) in rows.enumerated() {
                    guard let candidate = row.topCandidates(1).first else { continue }
                    let text = candidate.string
                    let start = max(0, lineIndex - 1)
                    let end = min(texts.count, lineIndex + 2)
                    let box = row.boundingBox
                    let context = texts[start..<end].joined(separator: "\n")
                    let hasFraction = containsMatch(fraction, text)
                    if hasFraction {
                        observations.append(SerialObservation(
                            image_index: index, orientation_degrees: degrees, text: text,
                            context: context, confidence: candidate.confidence,
                            bbox: [Double(box.minX), Double(box.minY), Double(box.width), Double(box.height)],
                            read_pass: "full", seed_text: nil, roi_bbox: nil,
                            support_level: nil
                        ))
                    }
                    // A faint vertical stamp is sometimes read as just one side
                    // of the fraction (for example ``065``). Treat an isolated
                    // 3-6 digit row only as a bounded ROI seed. It is never emitted
                    // as serial evidence unless a later Vision read sees a complete
                    // x/y token in the source pixels.
                    if hasFraction || containsMatch(isolatedDigits, text) {
                        detailSeeds.append(DetailSeed(
                            imageIndex: index, path: path,
                            orientationDegrees: degrees, orientation: orientation,
                            text: text, context: context,
                            confidence: candidate.confidence, bbox: box,
                            kind: hasFraction ? "fraction" : "isolated_digits"
                        ))
                    }
                }
            } catch {
                errors.append("image=\(index),orientation=\(degrees): \(error.localizedDescription)")
            }
        }
    }
}

for (index, _) in CommandLine.arguments.dropFirst().enumerated() {
    if deadlineReached() {
        truncated = true
        break
    }
    let selected = selectDetailSeeds(detailSeeds.filter { $0.imageIndex == index })

    var requestsForImage = 0
    for seed in selected {
        if deadlineReached() {
            truncated = true
            break
        }
        guard requestsForImage + rotations.count <= maxDetailRequestsPerImage else { break }
        guard let source = CIImage(contentsOf: URL(fileURLWithPath: seed.path)) else {
            errors.append("image=\(index),detail: source image unavailable")
            continue
        }
        detailSeedCount += 1
        let oriented = source.oriented(seed.orientation)
        let extent = oriented.extent
        let paddingScale = seed.kind == "fraction" ? 0.65 : 1.5
        let requestedScale = seed.kind == "fraction" ? 3.0 : 4.0
        let padding = max(seed.bbox.width, seed.bbox.height) * paddingScale
        let region = CGRect(
            x: extent.minX + (seed.bbox.minX - padding) * extent.width,
            y: extent.minY + (seed.bbox.minY - padding) * extent.height,
            width: (seed.bbox.width + 2 * padding) * extent.width,
            height: (seed.bbox.height + 2 * padding) * extent.height
        ).intersection(extent).integral
        guard !region.isEmpty else { continue }
        let base = oriented.cropped(to: region)
        let scale = min(
            requestedScale,
            maxDetailLongEdge / max(base.extent.width, base.extent.height, 1)
        )
        let normalizedROI = [
            Double((region.minX - extent.minX) / extent.width),
            Double((region.minY - extent.minY) / extent.height),
            Double(region.width / extent.width),
            Double(region.height / extent.height),
        ]
        var variants: [(String, CIImage)] = [("color", base)]
        if seed.kind == "isolated_digits" {
            variants.append(("autocontrast", autoEnhanced(base)))
        }
        var foundFraction = false
        var acceptedPairs: Set<String> = []
        for (variantName, variant) in variants {
            if deadlineReached() {
                truncated = true
                break
            }
            if foundFraction || requestsForImage + rotations.count > maxDetailRequestsPerImage {
                break
            }
            let crop = variant.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
            guard let pixels = renderer.createCGImage(crop, from: crop.extent) else { continue }
            for (cropDegrees, cropOrientation) in rotations {
                if deadlineReached() {
                    truncated = true
                    break
                }
                let detail = requestForText()
                requestsForImage += 1
                detailRequestCount += 1
                do {
                    try VNImageRequestHandler(cgImage: pixels, orientation: cropOrientation).perform([detail])
                    for detected in detail.results ?? [] {
                        guard let read = detected.topCandidates(1).first,
                              let pair = parsedFraction(read.string) else { continue }
                        if seed.kind == "isolated_digits" {
                            guard let seedValue = Int(seed.text.trimmingCharacters(in: .whitespacesAndNewlines)),
                                  seedValue == pair.0 || seedValue == pair.1 else { continue }
                        }
                        foundFraction = true
                        let pairKey = "\(pair.0)/\(pair.1)"
                        guard acceptedPairs.insert(pairKey).inserted else { continue }
                        observations.append(SerialObservation(
                            image_index: index,
                            orientation_degrees: seed.orientationDegrees,
                            text: read.string,
                            context: seed.context + "\n局部 OCR：" + read.string,
                            confidence: read.confidence,
                            bbox: [Double(seed.bbox.minX), Double(seed.bbox.minY), Double(seed.bbox.width), Double(seed.bbox.height)],
                            read_pass: "expanded_\(seed.kind)_roi_\(variantName)_rotation_\(cropDegrees)",
                            seed_text: seed.text,
                            roi_bbox: normalizedROI,
                            support_level: "single_source_roi"
                        ))
                    }
                } catch {
                    errors.append("image=\(index),detail=\(seed.kind),rotation=\(cropDegrees): \(error.localizedDescription)")
                }
            }
        }
    }
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
FileHandle.standardOutput.write(try encoder.encode(SerialOCRResponse(
    observations: observations,
    errors: errors,
    strategy_version: "bounded-expanded-roi-v2",
    detail_seed_count: detailSeedCount,
    detail_request_count: detailRequestCount,
    truncated: truncated
)))
