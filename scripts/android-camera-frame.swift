import Foundation
import AppKit
import Vision
import ImageIO

// Local-only preview crop and rectangle proposals. No image persistence or network.
do {
    let args = CommandLine.arguments.dropFirst().compactMap(Double.init)
    guard args.count == 4, args.allSatisfy({ $0.isFinite }), args[0] >= 0, args[1] >= 0, args[2] >= 16, args[3] >= 16 else { throw NSError(domain:"frame",code:1) }
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard input.count <= 25 * 1024 * 1024, let source = CGImageSourceCreateWithData(input as CFData,nil), let image = CGImageSourceCreateImageAtIndex(source,0,nil) else { throw NSError(domain:"image",code:2) }
    let rect = CGRect(x:args[0],y:args[1],width:args[2],height:args[3])
    guard CGRect(x:0,y:0,width:image.width,height:image.height).contains(rect), let frame = image.cropping(to:rect) else { throw NSError(domain:"crop",code:3) }
    let request = VNDetectRectanglesRequest()
    // Broad native proposals, then measure aspect in actual pixel space. Native
    // aspect/confidence prefilters miss the real transparent-holder regression.
    request.minimumAspectRatio = 0.1; request.maximumAspectRatio = 1.0
    request.minimumSize = 0.05; request.minimumConfidence = 0.0
    request.quadratureTolerance = 45; request.maximumObservations = 24
    try VNImageRequestHandler(cgImage:frame,options:[:]).perform([request])
    let document = VNDetectDocumentSegmentationRequest()
    try VNImageRequestHandler(cgImage:frame,options:[:]).perform([document])
    let documentResults = (document.results ?? []).filter { $0.confidence >= 0.8 }
    let candidates: [[String:Double]] = (documentResults + (request.results ?? [])).compactMap { r in
        let b = r.boundingBox
        func length(_ a: CGPoint,_ c: CGPoint) -> Double { hypot(Double(a.x-c.x)*Double(frame.width),Double(a.y-c.y)*Double(frame.height)) }
        let w = (length(r.topLeft,r.topRight)+length(r.bottomLeft,r.bottomRight))/2
        let h = (length(r.topLeft,r.bottomLeft)+length(r.topRight,r.bottomRight))/2
        let aspect = min(w,h)/max(w,h)
        guard aspect >= 0.5, aspect <= 0.88, b.width*b.height >= 0.04 else { return nil }
        let isDocument = documentResults.contains { $0 === r }
        let padX = isDocument ? b.width*0.12 : 0, padY = isDocument ? b.height*0.12 : 0
        let left = max(0,b.minX-padX), right = min(1,b.maxX+padX)
        let top = max(0,1-b.maxY-padY), bottom = min(1,1-b.minY+padY)
        return ["x":left,"y":top,"width":right-left,"height":bottom-top,"confidence":Double(r.confidence),"document":isDocument ? 1 : 0]
    }
    let scale = min(1,720 / Double(max(frame.width,frame.height)))
    let w = Int(Double(frame.width)*scale), h = Int(Double(frame.height)*scale)
    guard let context = CGContext(data:nil,width:w,height:h,bitsPerComponent:8,bytesPerRow:0,space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGImageAlphaInfo.noneSkipLast.rawValue) else { throw NSError(domain:"context",code:4) }
    context.interpolationQuality = .high
    context.draw(frame,in:CGRect(x:0,y:0,width:w,height:h))
    guard let small = context.makeImage(), let jpeg = NSBitmapImageRep(cgImage:small).representation(using:.jpeg,properties:[.compressionFactor:0.75]) else { throw NSError(domain:"jpeg",code:5) }
    let result: [String:Any] = ["preview":"data:image/jpeg;base64,"+jpeg.base64EncodedString(),"width":frame.width,"height":frame.height,"rectangles":candidates]
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:result))
} catch {
    FileHandle.standardError.write(Data("无法处理本地相机预览，请保持相机完整显示。\n".utf8))
    exit(1)
}
