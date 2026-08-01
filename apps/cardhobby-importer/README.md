# 卡淘一键导入扩展

扩展只读取用户已经登录并主动打开的卡淘“我买到的”页面，不读取、保存或传输卡淘密码和 Cookie。

## 本地安装

1. 确认 Card Platform API 运行在 `http://127.0.0.1:8000`。
2. Chrome 打开 `chrome://extensions` 并启用“开发者模式”。
3. 点击“加载已解压的扩展程序”，选择本目录。
4. 登录卡淘，打开 `https://www.cardhobby.com.cn/order/myorders?status=0`。
5. 保持 Card Platform 页面打开，扩展会自动读取本地收藏者 UUID；点击扩展图标，
   扫描并核对导入预览。

首版扫描当前订单页。分页订单需要逐页打开后分别导入；商品 ID 幂等约束会跳过重复项。
