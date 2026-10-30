# CardSP USB 相机组件（已安装并完成Redmi实机验证）

为电脑网页提供准确镜头倍率、预览和单次JPEG拍摄。使用Android Camera2，保持前台取景，切到后台立即关闭相机。不申请联网、存储或麦克风权限，不写系统相册，不改变系统默认相机。

`python3 scripts/build-android-camera.py` 只编译与个人开发签名，不安装。APK位于 `.runtime/redmi-zoom-20261030/android-camera/`，`artifact.json`记录hash与检查结果。

安装及CAMERA授权属于新的Redmi设备操作，必须先取得用户直接确认。用户已于2026-10-31直接回复“允许”，已授权安装及相机权限，不再重复请求该授权。用户已在MIUI提示点击“继续安装”，组件已覆盖安装到1.0.3（4），相机权限已授予。安装后不清数据，并验证签名、包版本、启动、权限、实际倍率、预览对应帧与一次新JPEG。

组件入口要求调用方具有DUMP权限（ADB shell），普通应用不能启动或修改USB凭据。通过Android私有abstract LocalServerSocket通信，Mac助手临时ADB转发；每次连接的密钥只经受保护Activity intent交付，socket中不发送密钥明文。请求与响应均用HMAC-SHA256认证，随机nonce防误配/重放；取消或异常后清理临时转发。

倍率经CaptureResult确认；预览等SurfaceTexture显示对应sensor timestamp后才读取；新JPEG与该次onCaptureStarted的timestamp匹配，超时旧图不会作为新图返回。协议/策略/构建及真实Redmi已验证：1.6×/1.5×实际回读、自动约1.4×完整取景、AF锁定帧与预览timestamp一致、一次新JPEG回传。其他型号及异常恢复的实机覆盖仍有限。

生产助手控制接口由 `scripts/android-camera-native.mjs`负责；`android-camera-controls.mjs`内的MIUI触控探索不用于新版控制路径。未安装专用组件时，已有系统快门保留；专用组件失败不得静默切换到另一套相机拍照。
