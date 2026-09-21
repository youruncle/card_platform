#!/usr/bin/env swift
import Foundation
import ImageCaptureCore
import ImageIO
import UniformTypeIdentifiers

final class Bridge: NSObject, ICDeviceBrowserDelegate, ICScannerDeviceDelegate {
    let browser = ICDeviceBrowser()
    let command: String
    let output: URL?
    let dpi: Int
    let timeout: TimeInterval
    let areaWidth: Double?
    let areaHeight: Double?
    var target: ICScannerDevice?
    var timer: Timer?
    var finishing = false
    var opening = false
    var closing = false
    var scanning = false
    var scanScheduled = false
    var sessionOpened = false
    var readySeen = false
    var configured = false
    var pendingExitCode: Int32 = 0
    var cancelling = false
    var signalSource: DispatchSourceSignal?

    init(command: String, output: URL?, dpi: Int, timeout: TimeInterval, areaWidth: Double?, areaHeight: Double?) {
        self.command = command; self.output = output; self.dpi = dpi; self.timeout = timeout; self.areaWidth = areaWidth; self.areaHeight = areaHeight
        super.init()
        browser.delegate = self
        browser.browsedDeviceTypeMask = ICDeviceTypeMask(rawValue: ICDeviceType.scanner.rawValue | ICDeviceLocationTypeMask.local.rawValue)!
    }

    func run() {
        signal(SIGINT, SIG_IGN)
        signalSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
        signalSource?.setEventHandler { [weak self] in self?.cancel() }
        signalSource?.resume()
        browser.start()
        timer = Timer.scheduledTimer(withTimeInterval: timeout, repeats: false) { _ in self.fail("timeout", 124) }
        RunLoop.current.run()
    }

    func deviceBrowser(_ browser: ICDeviceBrowser, didAdd device: ICDevice, moreComing: Bool) {
        guard let scanner = device as? ICScannerDevice else { return }
        let serial = scanner.serialNumberString ?? ""
        let name = scanner.name ?? "Canon Scanner"
        guard name.localizedCaseInsensitiveContains("LiDE 400") || serial == "4706E5" else { return }
        target = scanner; scanner.delegate = self
        if command == "list" {
            emit(["event":"device", "name":name, "serial":serial, "persistent_id":scanner.persistentIDString ?? ""]); finish(0)
        } else {
            opening = true
            scanner.requestOpenSession(options: nil) { error in
                DispatchQueue.main.async { self.opened(scanner, error) }
            }
        }
    }
    func deviceBrowser(_ browser: ICDeviceBrowser, didRemove device: ICDevice, moreGoing: Bool) { if !finishing && device === target { fail("device_disconnected", 69) } }
    func device(_ device: ICDevice, didOpenSessionWithError error: Error?) {
        // Kept for macOS versions that deliver the legacy delegate callback.
        guard !device.hasOpenSession || error != nil else { return }
        opened(device as! ICScannerDevice, error)
    }
    func opened(_ scanner: ICScannerDevice, _ error: Error?) {
        if !opening { return }
        opening = false
        if let error { fail("busy_or_open_failed: \(error.localizedDescription)", 75); return }
        sessionOpened = true
        if readySeen { proceed(scanner) }
    }
    func deviceDidBecomeReady(_ device: ICDevice) {
        readySeen = true
        if sessionOpened, let scanner = device as? ICScannerDevice { proceed(scanner) }
    }
    func proceed(_ scanner: ICScannerDevice) {
        if configured { return }
        configured = true
        scannerDevice(scanner, didSelect: scanner.selectedFunctionalUnit, error: nil)
    }
    func scannerDevice(_ scanner: ICScannerDevice, didSelect functionalUnit: ICScannerFunctionalUnit, error: Error?) {
        if !scanner.hasOpenSession { return }
        if let error { fail(error.localizedDescription, 70); return }
        if command == "capabilities" {
            emit(["event":"capabilities", "type":"flatbed", "dpi":functionalUnit.supportedResolutions.map { $0 }, "preferred_dpi":functionalUnit.preferredResolutions.map { $0 }, "color":true, "preview":functionalUnit.canPerformOverviewScan, "width":functionalUnit.physicalSize.width, "height":functionalUnit.physicalSize.height, "measurement_unit":functionalUnit.measurementUnit.rawValue]); close(scanner, 0); return
        }
        configureAndScan(scanner)
    }
    func configureAndScan(_ scanner: ICScannerDevice) {
        if scanScheduled || scanning { return }
        scanScheduled = true
        guard let unit = scanner.selectedFunctionalUnit as? ICScannerFunctionalUnitFlatbed else { scanner.requestSelect(.flatbed); return }
        unit.measurementUnit = .inches
        unit.pixelDataType = .RGB
        unit.resolution = dpi
        unit.scaleFactor = 100
        if unit.supportedDocumentTypes.contains(Int(ICScannerDocumentType.typeA4.rawValue)) { unit.documentType = .typeA4 }
        unit.scanArea = NSRect(
            x: 0, y: 0,
            width: min(unit.documentSize.width, areaWidth ?? 8.27),
            height: min(unit.documentSize.height, areaHeight ?? 11.69)
        )
        scanner.transferMode = .fileBased
        scanner.documentUTI = "public.png"
        scanner.downloadsDirectory = output!.deletingLastPathComponent()
        scanner.documentName = output!.deletingPathExtension().lastPathComponent
        emit(["event":"scan_configured", "dpi":dpi, "preview":command == "preview"])
        Timer.scheduledTimer(withTimeInterval: 1.0, repeats: false) { _ in
            if self.finishing { return }
            self.scanning = true
            self.emit(["event":"scan_started", "dpi":self.dpi, "preview":self.command == "preview"])
            if self.command == "preview" { scanner.requestOverviewScan() } else { scanner.requestScan() }
        }
    }
    func scannerDevice(_ scanner: ICScannerDevice, didScanTo url: URL) {
        scanning = false
        var event: [String:Any] = ["event":"scan_complete", "path":url.path, "preview":false]
        if let source = CGImageSourceCreateWithURL(url as CFURL, nil),
           let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String:Any],
           let width = properties[kCGImagePropertyPixelWidth as String] as? Int,
           let height = properties[kCGImagePropertyPixelHeight as String] as? Int {
            event["source_resolution"] = [
                "capture_kind":"scan", "pixel_width":width, "pixel_height":height,
                "actual_dpi":scanner.selectedFunctionalUnit.resolution,
                "source":"image_capture_core_resolution"
            ]
        }
        emit(event)
        close(scanner, 0)
    }
    func scannerDevice(_ scanner: ICScannerDevice, didCompleteOverviewScanWithError error: Error?) {
        scanning = false
        if cancelling { return }
        if let error { fail(error.localizedDescription, 70); return }
        guard let unit = scanner.selectedFunctionalUnit as? ICScannerFunctionalUnitFlatbed,
              let image = unit.overviewImage,
              let destination = CGImageDestinationCreateWithURL(output! as CFURL, UTType.png.identifier as CFString, 1, nil)
        else { fail("overview_image_missing", 70); return }
        let properties: [CFString:Any] = [
            kCGImagePropertyDPIWidth:unit.overviewResolution,
            kCGImagePropertyDPIHeight:unit.overviewResolution
        ]
        CGImageDestinationAddImage(destination, image, properties as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { fail("overview_write_failed", 74); return }
        emit(["event":"scan_complete", "path":output!.path, "preview":true,
              "source_resolution":[
                "capture_kind":"preview", "pixel_width":image.width, "pixel_height":image.height,
                "actual_dpi":unit.overviewResolution,
                "source":"image_capture_core_overview_resolution"
              ]])
        close(scanner, 0)
    }
    func scannerDevice(_ scanner: ICScannerDevice, didCompleteScanWithError error: Error?) { scanning = false; if cancelling { return }; if let error { fail(error.localizedDescription, 70) } }
    func device(_ device: ICDevice, didEncounterError error: Error?) {
        if cancelling { return }
        let message = error?.localizedDescription ?? "device_error"
        if opening { emit(["event":"device_warning", "warning":message]); return }
        fail(message, 70)
    }
    func device(_ device: ICDevice, didCloseSessionWithError error: Error?) {
        if closing { closing = false; if let error { emit(["event":"close_warning", "warning":error.localizedDescription]) }; finish(pendingExitCode) }
    }
    func didRemove(_ device: ICDevice) { if !finishing && device === target { fail("device_disconnected", 69) } }
    func close(_ scanner: ICScannerDevice, _ code: Int32) { if closing { return }; closing = true; pendingExitCode = code; scanner.requestCloseSession() }
    func cancel() { if cancelling || finishing { return }; cancelling = true; emit(["event":"error", "error":"cancelled"]); if let scanner = target, scanner.hasOpenSession { if scanning { scanner.cancelScan() }; close(scanner, 130) } else { finish(130) } }
    func fail(_ message: String, _ code: Int32) { emit(["event":"error", "error":message]); if let scanner = target, scanner.hasOpenSession { if scanning { scanner.cancelScan() }; close(scanner, code) } else { finish(code) } }
    func finish(_ code: Int32) { finishing = true; timer?.invalidate(); signalSource?.cancel(); fflush(stdout); browser.stop(); exit(code) }
    func emit(_ value: [String:Any]) { let data = try! JSONSerialization.data(withJSONObject:value); print(String(data:data, encoding:.utf8)!) }
}

let args = CommandLine.arguments
guard args.count >= 2, ["list","capabilities","preview","scan"].contains(args[1]) else { fputs("usage: canon_lide400_bridge.swift list|capabilities|preview|scan [--output PATH] [--dpi 300|600|4800] [--area-width INCHES --area-height INCHES] [--timeout SEC]\n", stderr); exit(64) }
func value(_ flag: String) -> String? { guard let i = args.firstIndex(of:flag), i + 1 < args.count else { return nil }; return args[i+1] }
let command = args[1], dpi = Int(value("--dpi") ?? "300") ?? 300, timeout = Double(value("--timeout") ?? "60") ?? 60
let areaWidth = value("--area-width").flatMap(Double.init), areaHeight = value("--area-height").flatMap(Double.init)
guard [300,600,4800].contains(dpi) else { fputs("dpi must be 300, 600, or 4800\n", stderr); exit(64) }
guard (areaWidth == nil) == (areaHeight == nil), (areaWidth ?? 1) > 0, (areaHeight ?? 1) > 0 else { fputs("area width and height must be supplied together and positive\n", stderr); exit(64) }
guard dpi != 4800 || ((areaWidth ?? 99) <= 1 && (areaHeight ?? 99) <= 1) else { fputs("4800 DPI is limited to a 1x1 inch test area\n", stderr); exit(64) }
let output = value("--output").map { URL(fileURLWithPath:$0).standardizedFileURL }
if ["preview","scan"].contains(command) && output == nil { fputs("--output is required\n", stderr); exit(64) }
Bridge(command:command, output:output, dpi:dpi, timeout:timeout, areaWidth:areaWidth, areaHeight:areaHeight).run()
