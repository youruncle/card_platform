> 当前状态（2026-10-31）：专用USB相机组件1.0.3（4）已安装并完成Redmi实机验证，网页及本机助手已发布。手动范围1–3×、±0.1×，自动取景同时对准检测到的卡片；下文保留初版系统相机及MIUI探索记录。

# 电脑触发 Android / Redmi 拍卖照片

电脑网页的单卡拍卖草稿可通过 USB 调试连接 Android 手机。点击网页拍照时，电脑助手启动手机相机并发送一次相机快门键，将本次新照片回传到网页，再由网页上传至当前账户的私有草稿。

本助手运行在电脑，依赖 Node.js 18+ 和 Android platform-tools，无第三方 npm 依赖。运行：

```sh
node scripts/android-camera-helper.mjs
```

默认 ADB 路径是 `/Users/song/Library/Android/sdk/platform-tools/adb`；其他电脑可通过 `ANDROID_ADB_PATH` 指向本机 adb 可执行文件。助手固定监听 `127.0.0.1:8767`，结束进程即关闭连接并取消正在等待的拍摄。

用户需自行将手机通过 USB 连接电脑、启用 USB 调试并确认电脑授权；拍摄前手动点亮并解锁手机、完成相机权限确认。助手不会替用户更改手机设置、自动解锁或接受授权。可先只读检查：

```sh
/Users/song/Library/Android/sdk/platform-tools/adb devices -l
```

`device` 表示已授权；`unauthorized` 需要手机确认；`offline` 需要检查 USB 连接。多台设备同时连接时，必须在网页中选择目标设备。照片须保存到 `/sdcard/DCIM/Camera/`，格式为 JPEG 或 PNG，单张最大 25 MB；设备若默认保存 HEIC、使用不同目录、需要额外相机确认或不响应 Android 相机快门键，会明确失败或超时，需要实际设备验证兼容性。不要在等待期间手动拍其他照片或开启连拍。

## 本机接口

允许来源与现有 Canon 助手一致：`https://cardsp.cn`、`https://www.cardsp.cn`、`http://localhost:3000`、`http://127.0.0.1:3000`。所有请求必须携带 `X-Camera-Client: cardsp-web-v1`；Origin 和 Host 均校验，支持已授权来源的 CORS / Private Network Access 预检，不接受任意来源或 `Authorization` 头。浏览器自身仍可能要求允许网页访问本地网络。

- `GET /v1/devices`：返回 `{devices:[{serial,name}],token,message}`；只有已授权在线设备进入 `devices`。`message` 提示未授权、离线或未连接状态。
- `POST /v1/capture`：携带上述客户端头、`X-Camera-Token` 和 JSON `{serial}`，返回 `image/jpeg` 或 `image/png`。串号必须来自本助手最近一次枚举且拍摄前仍在线。令牌仅在本助手进程内有效，重启助手后需重新连接。

助手只处理硬件传输，不接收云端登录 cookie、密码或账号凭据。照片仅在内存中短暂传输，不产生本地临时文件，不删除或修改手机已有照片；拍摄产生的新照片保留在手机中。

每次拍摄先检查屏幕锁定状态，启动系统相机，再记录快门前文件列表，只发送一次 `KEYCODE_CAMERA`。快门后轮询新文件，连续两次大小和修改时间相同才读取，并在读取后再检查稳定性、字节长度和图片签名。已有文件即使被更新也不作为本次拍摄结果。找不到新照片绝不回退到“最新旧照片”。检测到多个新文件、锁屏、相机启动失败、格式不支持、断连或超时均返回错误；单次请求最多等待35秒，失败和网页取消后释放忙状态，允许重试。

## 验证记录（2026-10-30）

```sh
node --test scripts/tests/android-camera-helper.test.mjs
```

13项自动测试通过，覆盖 HTTP 来源/Host/客户端头/令牌、PNA预检、设备串号绑定、单次快门、新文件稳定性、旧图与危险文件名拒绝、锁屏与未知状态、相机失败、断连、超时、取消、超大文件及多图歧义；ADB 子进程执行器另测参数原样传递、输出上限、超时及终止。HTTP 和文件选择逻辑实际执行，USB/相机响应使用注入的 ADB 假设备；不等于实机验收。

初次开发时ADB设备列表为空，未进行实机拍摄。随后用户于2026-10-30连接Redmi K30 Pro Zoom Edition，真实手机助手HTTP接口完成枚举、相机启动、一次快门和本次新照片回传。JPEG为6,705,582字节，4624×3472，校验及解码成功，实际画面为支架上的卡片。此次正常路径实机通过；锁屏、断连、超时等异常尚未实机回归，不能用自动测试代替。原图与结果位于 `.runtime/auction-drafts-20261030/redmi-live/`。没有上传卡淘、修改真实卡库、部署生产或更改手机设置。

## 网页取景与缩放（2026-10-30追加）

连接 USB 手机后可「刷新取景」「自动调整」或手动选择0.6–3×目标倍率。预设0.6/1/3×，支持±0.1×微调；倍率以手机UI实际回读为准。取景是手动刷新的屏幕预览，不是实时视频，不上传云端；「旋转预览」只改变网页显示。确认取景后仍需单独点击快门。

新增POST `/v1/camera`，请求 `{serial,action:'preview'|'auto'|'zoom',zoom?}`，`zoom`只随zoom操作提供。Origin/Host/client/token/serial校验及解锁检查与快门共用；全部硬件操作互斥，关闭编辑器取消请求。响应包含实际zoom、presets、width/height、JPEG dataURL预览、矩形候选、提示；自动调整附`adjusted`（实际是否改变倍率）和`verified`（最终边距及目标一致性是否确认）。

缩放使用动态读取的MIUI控件，不只是放大网页图片。MIUI相机待机页和已展开标尺会先恢复到取景；短滑动可能被手机吞掉，因此细调使用越过手势阈值、回到目标点、静止释放的触控序列，并以实际读回值校准。取消时通过独立、限时ADB通道释放触点。无法调到目标时明确报告实际倍率，不把目标值冒充成功。

本地框检测依赖Mac自带Vision/AppKit框架；首次使用通过Xcode命令行工具编译 `scripts/android-camera-frame.swift`，按源码hash缓存可执行文件到 `~/Library/Caches/CardPlatform/camera/`。没有命令行工具时提示安装，原有快门仍可用。原始PNG在本机内存中先进行文档分割/矩形候选检测，再按真实像素边长比例筛选、生成最多720px的JPEG预览；高置信文档候选外扩12%，避免透明卡壳边缘被内部图案框遗漏。相关官方接口见[文档分割](https://developer.apple.com/documentation/vision/vndetectdocumentsegmentationrequest)及[矩形检测](https://developer.apple.com/documentation/vision/vndetectrectanglesrequest)。

自动调整对单张明显卡片选择保留边距的倍率，最高1.8×；多个大小相近且不互相包含的候选会要求检查，小背景矩形与卡片内部嵌套框不会单独作为第二张卡。调整后再次检测，并比较按实际倍率投影的原目标框及最终边距。找不到、目标切换或仍贴边均不宣称自动完成；最小倍率仍贴边时要求移动手机/拉远。框检测不是卡片身份识别，仍须看预览确认；电脑不能改变手机的物理朝向，自动调整不触发拍照。

### 当前准备状态与实机限制

上面的MIUI UI缩放探索在真实Redmi上出现短滑动无效和反向跳档，尚不能作为稳定微调能力发布。当前新增控件准备改用Camera2专用USB相机组件，安装授权问题已发给用户，**尚未安装、尚未发布此版网页/助手**。原有线上连接/系统快门仍是之前已发布版本。

离线安装包由 `python3 scripts/build-android-camera.py` 构建，源在 `tools/android-camera/`，产物 `.runtime/redmi-zoom-20261030/android-camera/`。APK仅申请CAMERA，不声明INTERNET、存储或麦克风；Activity入口用DUMP权限限制为ADB shell/system调用，组件不对其他应用提供普通拍照入口。电脑以临时ADB转发连接私有LocalServerSocket，使用随机token和请求nonce；照片仅在内存中传输。安装/相机权限确认后才进行实机验收，范围以镜头真实回报为准（不保证第三方Camera2开放0.6×）。

## Camera2实机结果（2026-10-31）

用户已授权安装，MIUI本机确认后完成安装和相机权限。最终组件1.0.3（4）通过真实Redmi验证，镜头范围1–3×；实际回读1.6000000×、1.4991364×，自动约1.3995157×且整卡边框/目标一致性检查通过。

实机发现并修复：Activity密钥交接短暂延迟（只重试只读HMAC state握手）、TextureView位图方向和宽高（相对取景buffer显式变换）、卡片偏心时的对焦区域（按JPEG方向映射Camera2 post-zoom AF/AE区域，等待匹配request tag的AF锁定）、AF锁定timestamp先于future完成、对焦后再次核框、文档候选优先且忽略外部支架包围框。每次用户快门都在同一认证连接内重新检查取景/锁定对焦，再发出一次JPEG请求，防止重新唤起组件后沿用失效的对焦。

已实际核对focus timestamp与preview timestamp相同，最新JPEG的sensor timestamp更晚，AF状态4；JPEG为4208×3120、2,098,014字节，sha256为085cdf8ef3b0c02f38b18c9ab41049852e86482b3ac761a1de3ea403bf6ff280。照片只保存在本任务私有验收目录，未上传真实草稿或卡淘。

本机相关37项测试通过，最新图谱线上基线上的网页245项、生产构建及TS检查通过。实际发布及保留配置回读见 `docs/release-20261031-auction-camera.md`。
