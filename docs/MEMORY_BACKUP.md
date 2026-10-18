# 会话在线备份与隔离恢复

本工具用于本地匿名会话数据库Schema v1，包含最近消息、来源焦点、修订号、删除标记及签名密钥。使用SQLite在线Backup API，读取已提交的WAL数据，不要求停止原服务；不通过复制单个运行中主文件实现备份。接口依据：[SQLite Backup API](https://www.sqlite.org/backup.html)、[Python sqlite3 backup](https://docs.python.org/3.11/library/sqlite3.html#sqlite3.Connection.backup)。

## 主机命令

以下路径是示例；`--source`必须指向实际会话库，`--ttl-days`必须与原服务的 `CHAT_MEMORY_TTL_DAYS` 一致。脚本不自动加载 `.env`，未显式指定时读取启动环境变量或默认30天。

```bash
python -m scripts.backup_memory backup \
  --source var/memory/conversations.sqlite3 \
  --out var/backups/memory-20261018 \
  --ttl-days 30 --retention-days 7

python -m scripts.backup_memory inspect --bundle var/backups/memory-20261018

python -m scripts.backup_memory restore \
  --bundle var/backups/memory-20261018 \
  --target var/restore-check-20261018/conversations.sqlite3
```

成功返回0，失败返回2及不含消息、凭证、路径的错误码。备份目录必须不存在；恢复目标及其WAL/SHM/journal文件都必须不存在。没有覆盖参数，不替换运行中的数据库。

备份包包含 `memory.sqlite3` 与 `manifest.json`。清单记录格式版本、Schema、SHA256、创建/失效时间、原会话TTL及移除过期内容数量，不包含Cookie、签名密钥或消息。完整性校验、用途标记、签名密钥格式和JSON快照检查必须通过。未来Schema及其他应用数据库拒绝使用，缺失源库不会被创建成空库。

恢复后使用清单中的 `ttl_seconds / 86400` 配置原会话TTL，并保留原 `CHAT_COOKIE_NAME`；不要因恢复而延长会话有效期。先在隔离服务检查有效Cookie、来源焦点和删除/过期分支，再安排停机切换；本工具不执行切换，也不构成数据库升级迁移。

## 运行中Docker的实际演练

验收脚本只允许 `tcm-research/chat` Compose标签的运行中容器与本机HTTP地址：

```bash
python -m scripts.verify_memory_backup \
  --container tcm-research-chat-1 \
  --url http://127.0.0.1:7872 \
  --out var/memory-backup-next
```

此脚本需要Docker访问权限和主应用CI依赖，会创建并清理两个专用测试会话。它通过stdin运行标准库备份模块，在容器tmpfs中生成包，以 `docker exec` 私有数据流转移两个固定文件；不修改镜像或安装源文件。Docker Desktop实测 `docker cp` 无法读取该tmpfs临时目录，因此不用该路径。

包恢复至新本地库，再启动独立本地API检查原Cookie能恢复消息/药典资料卡，以及已删除Cookie不恢复历史。原服务保持运行，临时容器目录在完成后清理。主机输出目录含真实会话及密钥，是私有运维产物，不是可公开的测试附件。

## 删除、过期和保留规则

1. 备份保留当时已有的删除标记与修订号，恢复后旧请求仍不能覆盖该删除。已过期内容只在备份副本中清理，不修改源会话；恢复时再按原更新时间与TTL清理一次，不因备份重置30天。
2. **删除前的旧备份仍可能含原消息。** 当前接口不能删除历史备份中的某条记录，也没有跨备份删除日志。不能宣称“删除后恢复任意旧备份也绝不复活”；需要完整删除时，运维者必须删除包含它的旧包和恢复副本，文件系统/异地快照也单独处理。
3. 默认备份有效期7天，允许1–30天；超过有效期工具拒绝恢复，`inspect`仍可查看过期状态。失效不等于物理删除。默认操作规程为每天至少检查一次包清单并删除过期包，演练完成后删除不再需要的恢复副本；当前没有安装定时任务或自动清理服务。
4. 新建包目录权限0700、数据库/清单/恢复文件0600，建议都放在被Git忽略的 `var/` 私有目录。备份包含可验证原Cookie的签名密钥，不能放进公开仓库、镜像、公共下载或验收报告。当前只做本机文件权限控制，不提供备份加密或远端传输。
5. SHA256用于发现损坏和意外改动，不是来自可信方的签名。不要从不可信来源接收备份或自行修改清单的TTL/时间；自动化运维、加密、异地留存、删除日志和灾难切换仍属于正式部署准入工作。

本工具只覆盖会话库。ETCM、药典索引/PDF、PubMed文献台账分别管理；仅恢复会话不会恢复文献批次或被引用来源。来源不匹配仍由现有工具拒绝，不能以恢复记忆代替恢复知识资料。

## 验证范围

新增13项工程测试：旧Cookie/新进程、删除与陈旧写入、备份/恢复时过期、活跃WAL提交/未提交隔离、源库只读、拒绝覆盖/残余WAL、校验篡改、失效包、未知Schema、缺失签名密钥、SQLite内部统计表、私有权限及CLI错误不泄露。完整39模块376项回归通过。

实际运行中7872容器完成5组演练：专用活跃/已删除会话、在线备份、隔离恢复、独立API恢复并核对原药典来源、原服务继续运行。实测TTL为30天、备份保留7天；原数据库没有被替换、容器没有重启。测试会话随后清除，包和恢复副本仍按上述私有保留策略管理。见[验收](../audits/memory_backup_v1/report.json)。

本轮完成M3-07的本机工具和手动运维范围；不代表账户权限、Schema升级、自动备份调度或生产灾难恢复已完成。
