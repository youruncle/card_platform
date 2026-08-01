# 卡淘桌面内嵌式导入

该助手会启动：

- 一个只监听本机的桥接服务，供 Card Platform 主页面调用；
- 一个 Chrome App 独立窗口，用于登录卡淘和展示“我买到的”订单。

卡淘 Cookie 只写入本机目录：

`data/cardhobby-browser-profile/`

此目录已加入 `.gitignore`。Card Platform 不接收卡淘手机号、密码、Cookie 或验证码。

## 启动

先启动 Card Platform API，然后执行：

```bash
./scripts/cardhobby-desktop-import.sh
```

桥接服务默认为 `http://127.0.0.1:8765`，独立浏览器调试端口为
`127.0.0.1:9223`，均只监听本机。

首次使用：

1. 打开 Card Platform：`http://127.0.0.1:3000/`。
2. 点击页面右上角“卡淘导入”，再点击“打开卡淘登录窗口”。
3. 在独立窗口内自行完成登录。
4. 进入“我买到的”页面并等待订单加载。
5. 回到 Card Platform 导入对话框，点击“扫描当前订单页”。
6. 核对识别结果，补齐不确定字段，再确认导入。

后续启动会复用专用目录中的卡淘登录会话。若需彻底退出，可关闭独立窗口并删除
`data/cardhobby-browser-profile/`；删除后会丢失该专用会话。

## 可配置项

- `CARD_PLATFORM_API_URL`：默认 `http://127.0.0.1:8000`
- `CARDHOBBY_HELPER_PORT`：默认 `8765`
- `CARDHOBBY_DEBUG_PORT`：默认 `9223`
- `CARDHOBBY_PROFILE_DIR`：专用浏览器配置目录
- `CARDHOBBY_CHROME_PATH`：Chrome/Chromium 可执行文件路径
