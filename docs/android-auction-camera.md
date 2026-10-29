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
