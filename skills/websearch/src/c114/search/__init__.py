"""搜索阶段横向能力包。

本包只收纳 step 3 相关的搜索 provider、结果筛选、ai_review、
YAML 渲染和请求级日志能力。上层 step/CLI 仍从 `workflow` 调用编排入口，
避免把 provider 细节重新散落到主流程文件里。
"""
