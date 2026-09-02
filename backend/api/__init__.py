"""FastAPI 骨架（阶段4：盘阵场景检索 + 懒生成预览 JPG）。

config 沿用 backend/config.py 的 env 式风格（不引 .env / 框架配置）。
API 依赖集中在 backend/api/app.py，仅在启服务/跑 api 测试时 import，
不影响 backend 其它零框架模块。
"""
