# M0-05：受控来源与上传结果

日期：2026-10-12。原医疗应用已移除 data/uploads 的整目录 StaticFiles 挂载。

## 公共资料逐文件登记

公共资料指管理员明确选择供本地应用使用者查阅的共享文献，不是用户上传资料。

- `GET /sources/{source_id}` 只提供 [`resources/source_manifest.json`](../resources/source_manifest.json) 登记的文件；没有目录浏览或任意路径下载。
- ID 由规范相对路径生成；每项同时保存文件 SHA-256。只允许 data/raw、data/raw_extras 的 PDF，以及 data/parsed_docs 的 PNG。不会登记数据库、docstore、ETCM JSON 或 uploads。
- 每次读取校验文件位置、无符号链接、大小、文件签名和 SHA-256；不匹配返回 404。响应发送同一份已验证字节，避免验证后重新打开另一个文件。
- 单文件上限 64 MiB；返回 MIME 类型、nosniff 和 private/no-store。当前实现读入内存，不是大型资料流式服务或容量验收。
- 清单缺失时默认拒绝；清单格式错误则启动明确失败。修改清单后需要重启。

当前明确登记原 data/raw 的 4 份 PDF 和 71 份对应 picture-N PNG，共 75 项。页面截图、表格文件、raw_extras、数据库和上传文件不在清单内。此登记是应用访问策略，不代表已确认资料的公开分发许可。

登记命令必须明确提供文献文件；只生成元数据，不复制文件：

```bash
python -m scripts.register_sources \
  --documents data/raw/brain_tumor_2024.pdf data/raw/brain_tumors_ucni.pdf \
              data/raw/covid_chest_xray_2024.pdf data/raw/skin_lesion_2023.pdf \
  --include-derived-images
```

可通过 SOURCE_MANIFEST_PATH 指定另一个清单。命令会替换该清单，应先审查所选文件；不要把用户上传或私人文献放入公共资料目录并登记。include-derived-images 按文献文件名筛选 picture-N 文件，管理员仍需核对其来源关联，该命令本身不证明图像由该 PDF 生成。

## 兼容现有检索引用

新建与读取索引时，source_url 将本地源链接规范为 `/sources/s_...`。外部 HTTP/HTTPS 文献 URL 保持原值，file/javascript 和内部目录路径不作为本地下载链接输出。

旧 `/data/{path}` 是受控兼容路由，只将已登记的路径重定向到新接口；未知路径返回 404。旧 localhost:8000 地址和当前 sources 地址会规范成相对链接，不需要重新生成旧向量即可取得已登记资料。没有重新开放整个 data。

原 `/uploads/...` 不再提供下载。更新或重新导入公共 PDF/图片导致内容变化后，原登记哈希将失效，需要管理员重新核对和登记；不会静默提供新内容。

## 会话专属图像结果

图像功能开启时，上传结果返回 `/artifacts/{random_token}`：

- 需携带发布时相同的 session_id Cookie；无 Cookie、其他 Cookie、未知或过期 token 均返回 404。
- token 为随机值，默认有效 10 分钟。最多 128 项、总量 64 MiB、单图 8 MiB，按需清理过期项并淘汰最旧项。
- 内容是不可变内存 PNG 快照；进程重启后失效，不重新读取共享输出文件。
- 单进程锁覆盖图像生成与快照，生成前移除旧输出，防止并发请求串图；普通文本聊天不会附带其他请求的共享图像。
- 上传输入成功/失败后均删除临时文件。语音生成结果直接返回发起请求，不再生成可下载的磁盘 URL。

这是匿名会话的 Cookie/随机 token 访问边界，**不是账户级鉴权**。取得同一匿名 Cookie 和 token 等同于取得该会话的访问凭据；不要共享它们。登录所有权、持久会话和多进程身份管理仍属 M3。原 CV 共享文件后端只支持单应用进程，此锁不是跨进程锁。

旧上传文件可能仍在本地磁盘，但不再通过 HTTP 静态目录暴露。本轮不删除用户原有文件，也不把私有结果登记为公共文献。

## 验证与部署

```bash
python -m unittest discover -s tests -p test_source_access.py -v
python -m unittest discover -s tests -p test_upload_privacy.py -v
```

前者的 HTTP 测试需要 FastAPI/HTTPX；后者需要原医疗容器依赖，CV 和 LLM 调用使用测试替身。实际运行与测试范围见 [验收报告](../audits/source_access/report.md)。

当前主容器挂载源码，已重启加载新接口。全量原医疗镜像尚未重建发布；新建无源码挂载的容器时需构建当前代码，并在服务内挂载已登记文件和所需 RAG 存储。旧 0.1.0 镜像不包含本轮修复。
