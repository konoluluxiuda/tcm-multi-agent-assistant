# 干净环境复现

本次复现日期为2026-10-31。范围是固定基础镜像、重新安装Python依赖、全新容器和空状态目录；复用本机已登记的只读数据。没有从原始PDF重做OCR，也没有在另一台机器验证部署。

先准备Docker、本项目CLI依赖及数据发布`var/data_releases/r1_20261030`。数据不随Git或镜像发布；原始JSON、两份PDF、数据库、索引必须与登记一致。登记含绝对路径，换机器需重新登记，不能直接修改hash绕过检查。参考[数据说明](DATA_RELEASE_BENCHMARK.md)。

在已准备资料的仓库根目录执行以下命令。名称、端口和输出目录须未被使用；本次已用`7916`和`v1`，下面用`7917`和`v2`避免覆盖：

```bash
.venv/bin/python -m scripts.build_research_image \
  --no-cache --tag tcm-research-chat:external-clean-v2 \
  --receipt var/external_clean_v2/build.json
.venv/bin/python -m scripts.check_clean_reproduction \
  --image tcm-research-chat:external-clean-v2 \
  --release var/data_releases/r1_20261030 \
  --out var/external_clean_v2/reproduction \
  --port 7917 --name tcm-clean-reproduction-v2
```

`--no-cache`使Docker重新执行依赖安装，镜像仍使用固定基础镜像digest。启动器只读挂载药材库、来源关联库、靶点库、原始JSON、PDF和发布包，单独挂载可写状态，关闭模型理解、执行模型、联网工具和追踪，不传模型密钥。

脚本通过真实HTTP依次验证：空工作区、直接字段查询、v1表、追加贮藏的v2、完整性筛选、澄清不建任务；下载三份XLSX逐格回读。随后停止并重命名容器，用相同镜像和同一状态目录创建新容器，保留原Cookie，验证会话、工作区和三个旧文件字节恢复，再追加用法与用量形成v3并核对旧单元格/文件不变。

成功输出`status=passed`，容器继续运行，可打开`http://127.0.0.1:7917/dataops/research`。报告在指定输出目录`summary.json`；实际会话、来源和文件均留在私有`var/`，不要提交到Git。

这项测试验证已完成记录的持久恢复；没有新增证明“运行中模型派发被杀”“未知HTTP请求自动恢复”或分布式容灾。未知派发继续停止，旧崩溃测试的结论不被本轮替换。失败后目录不复用，保留日志和停止的`*-before-recreation`容器，另选新名称执行。

本次已通过：21项受影响离线检查、零网络尝试；完整复现和恢复通过。启动脚本补上了原先遗漏的`ETCM_TARGET_DB`只读挂载。源目录曾意外为空，原因未确认；从本机原ZIP按登记hash恢复2076份药材JSON后，发布包完整性检查再次通过，其他原始数据组未重建。详见[完整验收](../audits/external_clean_v1/README.md)。
