# 本地候选更新：2026-10-20

后续本地更新：7876已运行c4de8bc，见[最新交付](RESEARCH_LOCAL_C4DE8BC.md)。本文件保留此前快照修复与0d65be4候选验收记录。

状态：本地候选容器验收完成；用户已明确本次范围为“提交Git，并更新本地Docker（暂不上传远程）”。原7873保留；GitHub及Docker Hub不上传，已发布镜像d0d3bc3保持原内容。本次提交后，从干净提交构建本地标签`tcm-research-chat:local-20261020`并更新7876候选，构建回执保存在本机`var/research_update_20261020/build_committed.json`。

## 可查看的结果

- 地址：**http://127.0.0.1:7876/**。
- 本地更新标签：`tcm-research-chat:local-20261020`；提交前验收标签为`working-20261020`，约408 MB。
- 项目/容器：`tcm-research-candidate` / `tcm-research-candidate-chat-1`。
- 内容：ETCM中文来源候选及关联、靶点反向成分、候选路径、关系焦点与重启恢复、用途改述、研究收藏/备份/证据笔记、疾病富集来源查询。
- 当前是确定性研究工具配置，PubMed和AI整理关闭，不自动调用模型；7875合成AI预览配置与此不同。

候选使用独立Cookie `research_chat_candidate_session`、独立命名卷`tcm-research-candidate_research-memory`。没有自动迁入7873或7875的聊天/收藏；需要迁移收藏时由用户导出JSON再导入副本。原7860/7863/7872/7873容器ID未变，原服务未被替换。

镜像来自父提交f5d4bf3及当前工作树，源码树SHA256为`de46e6520e1b74e9bdd4a0e924602b1a07727366241c442750c983a52556a5ab`。实际镜像ID与71个已复制运行文件均已核对，详见[验收摘要](../../audits/research_update_20261020/summary.json)。这是提交前验收镜像；提交后本地标签的准确提交号、镜像ID及源码指纹以`build_committed.json`为准。镜像构建仅用代码白名单，不含原始资料、数据库、会话、Cookie或真实密钥。

## 修复的部署问题

旧Compose仅挂载基础药材与药典，不能自动启用新增ETCM派生索引。增加可选`compose.research.etcm.yaml`，保持原基础Compose兼容，不将派生资料打包到镜像。

实际容器验收先后发现两个问题：关联库使用WAL模式，单文件挂载缺少伴随文件；原始派生目录权限0700，非root容器不能进入。没有修改源目录权限，也没有切换root。新增`scripts.prepare_etcm_runtime`，用SQLite在线Backup API读取已提交WAL内容，在新目录创建独立快照，转成DELETE日志模式并检查完整性、元数据和跨库版本绑定。

快照父目录0700供宿主私有保存，子目录`catalogs`0755仅用于Docker只读绑定，内部两个索引文件0644，源文件与源目录权限不变。快照和原库的物理文件SHA可因日志格式变化而不同；资料源指纹、靶点导出指纹及元数据必须一致。失败输出不覆盖重试，换一个新目录；使用前必须确认命令成功及receipt.json存在。这不是生产自动原子发布机制。

## 复现

先准备新运行快照，已有输出禁止覆盖：

```bash
python -m scripts.prepare_etcm_runtime --out var/etcm_runtime_next
python -m scripts.build_research_image --tag tcm-research-chat:local-20261020
```

使用已准备的本机数据启动独立候选：

```bash
RESEARCH_IMAGE=tcm-research-chat:local-20261020 \
RESEARCH_PORT=7876 RESEARCH_COOKIE_NAME=research_chat_candidate_session \
ENABLE_PUBMED=0 \
ETCM_RUNTIME_DIR="$PWD/var/etcm_runtime_next/catalogs" \
ETCM_SOURCE_HOST=/home/zry/workspace/HDCTI/dataset/ETCM2.0 \
docker compose -p tcm-research-candidate \
  -f compose.research.yaml -f compose.research.etcm.yaml up -d --no-build chat
```

当前已运行实例使用`var/research_update_20261020/runtime_catalogs/catalogs`；如管理此实例，将上述ETCM_RUNTIME_DIR改为该绝对路径。原始ETCM根目录用于按需成分详情，同样只读。新机需要自行准备药典、基础库、派生库和来源，代码/镜像不会携带它们。不要直接把当前在线WAL主文件复制成运行快照。

## 验证及保留项

70模块603项离线回归通过，跳过0、网络尝试0。新增3项快照测试验证已提交WAL内容、独立日志与私有父目录权限、禁止覆盖和版本错配拒绝。Compose解析验证通过。

真实容器浏览器20项检查通过：12项收藏及实际容器重启恢复、8项疾病富集与JSON/Markdown下载检查。候选非root、根文件系统只读、全部资料绑定只读；无挂载的镜像另验证未包含私有载荷。测试收藏、下载和临时Cookie文件已清理。首轮脚本误认为工具配置有AI复选框，及两次挂载失败均留存于本地`var/research_update_20261020/`，没有用健康接口掩盖关联功能失败。

本次执行范围：Git本地提交、从提交版本重建镜像、更新7876并复核健康及源码指纹。未执行：原7873切换、原会话迁移、Git push、Hub发布、真实模型调用、临床质量验收；现有端口与卷不会自动切换或删除。
