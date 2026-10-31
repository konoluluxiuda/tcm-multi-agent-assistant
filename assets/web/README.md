# 本地页面资源

页面从`/static/`读取这里的文件，不需要公网CDN。版本沿用此前模板引用，不是本轮框架升级：Bootstrap 5.3.0、Font Awesome Free 6.4.0、marked 4.0.2。每个第三方目录保留原许可证；下载地址、字节数和SHA256见[vendor/manifest.json](vendor/manifest.json)。favicon.svg为本项目的简易图标。

更新资源时固定版本、同步许可证及清单，运行`tests/test_runtime_status.py`和离线浏览器验收。服务只挂载`assets/web`，不会公开整个仓库或数据目录。容器需包含此目录；`Dockerfile.research`已增加复制步骤，本轮不表示镜像已构建或发布。
