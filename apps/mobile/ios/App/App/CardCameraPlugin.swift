import AVFoundation
import Capacitor
import UIKit
import Vision

@objc(CardCameraPlugin)
public class CardCameraPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "CardCameraPlugin"
    public let jsName = "CardCamera"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "capture", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "release", returnType: CAPPluginReturnPromise)
    ]
    private let temporaryFiles = CardCameraTemporaryFiles()
    private var pending = false
    private var camera: CardCameraViewController?

    @objc func capture(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            guard !self.pending else { call.reject("相机正在使用中", "BUSY"); return }
            self.pending = true
            switch AVCaptureDevice.authorizationStatus(for: .video) {
            case .authorized:
                self.presentCamera(call)
            case .notDetermined:
                AVCaptureDevice.requestAccess(for: .video) { granted in
                    DispatchQueue.main.async {
                        if granted { self.presentCamera(call) }
                        else { self.permissionDenied(call) }
                    }
                }
            default:
                self.permissionDenied(call)
            }
        }
    }

    @objc func release(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            guard let path = call.getString("path") else {
                call.reject("照片路径无效", "INVALID_PATH")
                return
            }
            do {
                try self.temporaryFiles.release(path)
                call.resolve()
            } catch CardCameraTemporaryFiles.Failure.unownedPath {
                call.reject("只能清理本次相机产生的临时照片", "INVALID_PATH")
            } catch {
                call.reject("临时照片清理失败，请重试", "RELEASE_FAILED")
            }
        }
    }

    private func permissionDenied(_ call: CAPPluginCall) {
        pending = false
        call.reject("未获得相机权限。请在系统设置中允许相机，或返回选择相册照片。", "CAMERA_PERMISSION")
    }

    private func presentCamera(_ call: CAPPluginCall) {
        guard UIApplication.shared.applicationState != .background,
              let presenter = bridge?.viewController,
              presenter.presentedViewController == nil else {
            pending = false
            call.reject("相机暂时无法打开，请返回后重试", "CAMERA_UNAVAILABLE")
            return
        }
        let controller = CardCameraViewController()
        controller.modalPresentationStyle = .fullScreen
        controller.completion = { [weak self] result in
            self?.pending = false
            self?.camera = nil
            switch result {
            case .success(let path):
                do {
                    guard let self else {
                        try? FileManager.default.removeItem(at: path)
                        call.reject("相机会话已结束", "CANCELLED")
                        return
                    }
                    try self.temporaryFiles.remember(path)
                    call.resolve(["path": path.absoluteString])
                } catch {
                    try? FileManager.default.removeItem(at: path)
                    call.reject("照片保存失败", "CAPTURE_FAILED")
                }
            case .failure(let failure): call.reject(failure.message, failure.code)
            }
        }
        camera = controller
        presenter.present(controller, animated: true)
    }
}

private struct CardCameraFailure: Error {
    let code: String
    let message: String
}

private final class CardCameraViewController: UIViewController,
    AVCaptureVideoDataOutputSampleBufferDelegate, AVCapturePhotoCaptureDelegate {
    var completion: ((Result<URL, CardCameraFailure>) -> Void)?
    private let session = AVCaptureSession()
    private let sessionQueue = DispatchQueue(label: "card.camera.session")
    private let analysisQueue = DispatchQueue(label: "card.camera.analysis", qos: .userInitiated)
    private let photoOutput = AVCapturePhotoOutput()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let previewView = UIView()
    private lazy var preview = AVCaptureVideoPreviewLayer(session: session)
    private let outline = CAShapeLayer()
    private let hint = UILabel()
    private let shutter = UIButton(type: .system)
    private var tracker = CardCameraGuidance()
    private var lastAnalysisTime: CFTimeInterval = 0 // analysisQueue only
    private var stopped = false // sessionQueue only
    private var finished = false // main queue only

    override var supportedInterfaceOrientations: UIInterfaceOrientationMask { .portrait }
    override var preferredInterfaceOrientationForPresentation: UIInterfaceOrientation { .portrait }
    override var shouldAutorotate: Bool { false }
    override var prefersStatusBarHidden: Bool { true }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        previewView.clipsToBounds = true
        preview.videoGravity = .resizeAspectFill
        previewView.layer.addSublayer(preview)
        outline.strokeColor = UIColor.systemYellow.cgColor
        outline.fillColor = UIColor.clear.cgColor
        outline.lineWidth = 3
        previewView.layer.addSublayer(outline)
        previewView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(previewView)

        hint.text = "将一张卡片放入画面 · 取景提示仅供参考"
        hint.textColor = .white
        hint.font = .preferredFont(forTextStyle: .body)
        hint.numberOfLines = 0
        hint.textAlignment = .center
        hint.accessibilityTraits = .updatesFrequently
        hint.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(hint)

        shutter.setTitle("拍摄", for: .normal)
        shutter.accessibilityLabel = "拍摄当前卡片"
        shutter.titleLabel?.font = .boldSystemFont(ofSize: 20)
        shutter.backgroundColor = .white
        shutter.setTitleColor(.black, for: .normal)
        shutter.layer.cornerRadius = 36
        shutter.isEnabled = false
        shutter.translatesAutoresizingMaskIntoConstraints = false
        shutter.addTarget(self, action: #selector(takePhoto), for: .touchUpInside)
        view.addSubview(shutter)

        let cancel = UIButton(type: .system)
        cancel.setTitle("取消", for: .normal)
        cancel.setTitleColor(.white, for: .normal)
        cancel.translatesAutoresizingMaskIntoConstraints = false
        cancel.addTarget(self, action: #selector(cancelCapture), for: .touchUpInside)
        view.addSubview(cancel)
        NSLayoutConstraint.activate([
            previewView.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            previewView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            previewView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            previewView.bottomAnchor.constraint(equalTo: hint.topAnchor, constant: -16),
            hint.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
            hint.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -24),
            hint.bottomAnchor.constraint(equalTo: shutter.topAnchor, constant: -20),
            hint.heightAnchor.constraint(greaterThanOrEqualToConstant: 48),
            shutter.widthAnchor.constraint(equalToConstant: 100),
            shutter.heightAnchor.constraint(equalToConstant: 72),
            shutter.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            shutter.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -20),
            cancel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
            cancel.centerYAnchor.constraint(equalTo: shutter.centerYAnchor),
            cancel.heightAnchor.constraint(greaterThanOrEqualToConstant: 44)
        ])
        NotificationCenter.default.addObserver(self, selector: #selector(backgrounded), name: UIApplication.didEnterBackgroundNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(sessionInterrupted), name: .AVCaptureSessionWasInterrupted, object: session)
        NotificationCenter.default.addObserver(self, selector: #selector(sessionInterrupted), name: .AVCaptureSessionRuntimeError, object: session)
        sessionQueue.async { [weak self] in self?.configureSession() }
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        preview.frame = previewView.bounds
        outline.frame = previewView.bounds
        if preview.connection?.isVideoOrientationSupported == true {
            preview.connection?.videoOrientation = .portrait
        }
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        if !finished { finish(.failure(CardCameraFailure(code: "CANCELLED", message: "已关闭相机"))) }
    }

    deinit { NotificationCenter.default.removeObserver(self) }

    private func configureSession() {
        guard !stopped else { return }
        guard AVCaptureDevice.authorizationStatus(for: .video) == .authorized else {
            fail("CAMERA_PERMISSION", "相机权限已关闭，请在系统设置中允许相机")
            return
        }
        session.beginConfiguration()
        session.sessionPreset = .photo
        do {
            guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
                throw CardCameraFailure(code: "CAMERA_UNAVAILABLE", message: "没有可用的后置相机，请选择相册照片")
            }
            let input = try AVCaptureDeviceInput(device: device)
            guard session.canAddInput(input), session.canAddOutput(photoOutput), session.canAddOutput(videoOutput) else {
                throw CardCameraFailure(code: "CAMERA_UNAVAILABLE", message: "此设备无法启动取景，请使用系统相机或相册")
            }
            session.addInput(input)
            session.addOutput(photoOutput)
            session.addOutput(videoOutput)
            videoOutput.alwaysDiscardsLateVideoFrames = true
            videoOutput.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarFullRange]
            videoOutput.setSampleBufferDelegate(self, queue: analysisQueue)
            if videoOutput.connection(with: .video)?.isVideoOrientationSupported == true {
                videoOutput.connection(with: .video)?.videoOrientation = .portrait
            }
            if photoOutput.connection(with: .video)?.isVideoOrientationSupported == true {
                photoOutput.connection(with: .video)?.videoOrientation = .portrait
            }
            photoOutput.maxPhotoQualityPrioritization = .quality
            session.commitConfiguration()
            guard !stopped else { return }
            session.startRunning()
            DispatchQueue.main.async { [weak self] in
                guard let self, !self.finished else { return }
                self.shutter.isEnabled = true
            }
        } catch {
            session.commitConfiguration()
            let failure = error as? CardCameraFailure
            fail(failure?.code ?? "CAMERA_UNAVAILABLE", failure?.message ?? "相机启动失败，请使用系统相机或相册")
        }
    }

    @objc private func takePhoto() {
        guard !finished, shutter.isEnabled else { return }
        shutter.isEnabled = false
        hint.text = "正在保存照片…"
        sessionQueue.async { [weak self] in
            guard let self, !self.stopped else { return }
            guard self.session.isRunning else {
                self.fail("CAMERA_UNAVAILABLE", "相机已停止，请重新打开后拍摄")
                return
            }
            guard AVCaptureDevice.authorizationStatus(for: .video) == .authorized else {
                self.fail("CAMERA_PERMISSION", "相机权限已关闭")
                return
            }
            let settings = AVCapturePhotoSettings(format: [AVVideoCodecKey: AVVideoCodecType.jpeg])
            settings.photoQualityPrioritization = .quality
            self.photoOutput.capturePhoto(with: settings, delegate: self)
        }
    }

    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
        guard error == nil, let data = photo.fileDataRepresentation() else {
            fail("CAPTURE_FAILED", "拍摄失败，请重试")
            return
        }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("card-camera-\(UUID().uuidString).jpg")
        do {
            try data.write(to: url, options: [.atomic, .completeFileProtection])
            DispatchQueue.main.async { [weak self] in
                guard let self, !self.finished else {
                    try? FileManager.default.removeItem(at: url)
                    return
                }
                self.finish(.success(url))
            }
        } catch { fail("CAPTURE_FAILED", "照片保存失败，请检查设备剩余空间后重试") }
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        let now = CACurrentMediaTime()
        guard now - lastAnalysisTime >= 0.25,
              let pixels = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        lastAnalysisTime = now
        // Portrait buffers are requested above. No video frame is sent through the bridge.
        let request = VNDetectRectanglesRequest()
        request.maximumObservations = 3
        request.minimumConfidence = 0.6
        request.minimumAspectRatio = 0.3
        request.maximumAspectRatio = 1.0
        request.minimumSize = 0.12
        do {
            try VNImageRequestHandler(cvPixelBuffer: pixels, orientation: .up).perform([request])
            let candidates = (request.results ?? []).map { [$0.topLeft, $0.topRight, $0.bottomRight, $0.bottomLeft] }
            let guidance = tracker.update(candidates: candidates)
            let imageSize = CGSize(width: CVPixelBufferGetWidth(pixels), height: CVPixelBufferGetHeight(pixels))
            DispatchQueue.main.async { [weak self] in
                guard let self, !self.finished, self.shutter.isEnabled else { return }
                self.hint.text = guidance.hint.rawValue
                self.outline.strokeColor = (guidance.hint == .ready ? UIColor.systemGreen : UIColor.systemYellow).cgColor
                guard let quad = guidance.quad else { self.outline.path = nil; return }
                let path = UIBezierPath()
                for (index, point) in quad.enumerated() {
                    let mapped = CardCameraGuidance.previewPoint(point, imageSize: imageSize, viewSize: self.previewView.bounds.size)
                    if index == 0 { path.move(to: mapped) } else { path.addLine(to: mapped) }
                }
                path.close()
                self.outline.path = path.cgPath
            }
        } catch {
            DispatchQueue.main.async { [weak self] in
                guard let self, !self.finished, self.shutter.isEnabled else { return }
                self.outline.path = nil
                self.hint.text = "取景提示暂不可用，仍可拍摄后手动确认裁切"
            }
        }
    }

    @objc private func cancelCapture() { finish(.failure(CardCameraFailure(code: "CANCELLED", message: "已取消拍摄"))) }
    @objc private func backgrounded() { finish(.failure(CardCameraFailure(code: "CANCELLED", message: "应用已进入后台，相机已关闭"))) }
    @objc private func sessionInterrupted() { fail("CAMERA_UNAVAILABLE", "相机会话已中断，请重试或选择相册照片") }

    private func fail(_ code: String, _ message: String) {
        DispatchQueue.main.async { [weak self] in self?.finish(.failure(CardCameraFailure(code: code, message: message))) }
    }

    private func finish(_ result: Result<URL, CardCameraFailure>) {
        guard !finished else { return }
        finished = true
        shutter.isEnabled = false
        outline.path = nil
        NotificationCenter.default.removeObserver(self)
        // start/stop/configure share a serial queue; a pending start cannot survive close.
        sessionQueue.async { [self] in
            stopped = true
            videoOutput.setSampleBufferDelegate(nil, queue: nil)
            if session.isRunning { session.stopRunning() }
            DispatchQueue.main.async { [self] in
                dismiss(animated: UIApplication.shared.applicationState == .active) {
                    self.completion?(result)
                    self.completion = nil
                }
            }
        }
    }
}
