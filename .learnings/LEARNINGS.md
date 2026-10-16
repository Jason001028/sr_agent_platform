# Learnings



## llama.cpp 版本兼容性
- 分类：knowledge_gap
- CentOS 7 部署验证表明，UD 与非 UD 权重均可用；此前问题来自 llama.cpp master 版本，回退到 release 版本后恢复。部署应固定已验证的 release 版本，并同时回归 UD/非 UD。

