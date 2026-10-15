# ADR-0002：先持久化每轮完成后的会话快照

日期：2026-10-16。范围：本地匿名、单工作进程主应用的重启恢复。

原MemorySaver只在进程存活期间保留历史与药材槽位。目标是关掉服务和浏览器之后能继续已完成的对话，不要求在模型调用或图中间节点恢复执行。

候选一是原生SQLite checkpointer。官方 [SqliteSaver](https://reference.langchain.com/python/langgraph.checkpoint.sqlite/SqliteSaver) 能保存图检查点，但会引入新依赖和多版本状态清理。核对PyPI依赖元数据：现锁定checkpoint 2.0.19；[SQLite 2.0.11](https://pypi.org/project/langgraph-checkpoint-sqlite/2.0.11/)要求checkpoint至少2.0.21且小于3，[SQLite 3.0.3](https://pypi.org/project/langgraph-checkpoint-sqlite/3.0.3/)要求checkpoint至少3。2.0.6与原依赖兼容，但不能据此跳过当前版本的安全与迁移审查。本轮不混入框架升级。

选择：独立SQLite仅保存末轮成功状态，模型/工具完整执行后以修订号条件写入。持久数据是JSON消息和受约束的药材槽位；LangGraph单轮执行不使用checkpointer。这样删除没有多版本checkpoint遗留，不保存pickle，未成功提交的一轮不改变上次完成状态。数据版本1通过PRAGMA及存储类型标记识别，首次表结构、标记与版本原子提交；未知版本拒绝加载，不猜测迁移。

会话Cookie签名密钥和数据位于同一持久卷；Cookie有明确有效期，数据库键只存凭证散列。删除标记与乐观修订防止并发旧请求复活已删除内容。默认保存20条近期消息、30天闲置保留；只清除药材焦点与删除整段会话是不同操作。

代价：不支持图节点级崩溃续跑、interrupt/resume、历史分支回放、长期语义记忆，也不宣称账号权限或多工作进程容量。请求中途崩溃需重试。后续若确有人工中断/恢复需求，应独立评审受支持的checkpointer和全量迁移，而不是把本快照表直接当作原生checkpoint库。

回滚：停机保留会话卷和数据库备份，回退应用代码可重新使用旧进程内实现，但旧实现不会读取本数据库；再次部署本版本并挂载原卷可继续恢复。不要通过删除卷回滚，也不要把药材库作为会话库。验证及使用见 [持久记忆说明](../PERSISTENT_MEMORY.md)。
