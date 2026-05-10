# Changelog

## may 10 
added stop all command

## v1.0.0

### Added
- 初始版本
- 两种注入模式：once（一次性永久注入）和 persistent（持续注入直到手动停止）
- `mpinject` 指令：支持 `<条目名>`、`<条目名> once`、`<条目名> stop`、`list`
- 支持最多 10 个自定义条目，每个可独立配置名称、标签名、内容和注入位置
- 三种注入位置：`user_message_before`、`user_message_after`、`system_prompt`
- `user_message_after` 模式下自动检测 LivingMemory 的 `<RAG-Faiss-Memory>` 标签，将内容插入到 RAG 标签之前
- 标签名命名规则与世界书一致：只禁止 `<`、`>`、换行，允许空格和中文
- 清理阶段 priority=3，注入阶段 priority=-501，与 PromptTags / LivingMemory / FirstWindowInject 安全共存
- 清理覆盖 prompt、system_prompt、contexts 中的字符串、字典、多模态三种消息格式
