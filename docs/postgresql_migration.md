# PostgreSQL 迁移说明

当前系统分两层：

- `active backend`: 配置了 `TOUZIFENXI_DATABASE_URL` 时，正式运行在 PostgreSQL。
- `fallback backend`: 未配置 PostgreSQL 时，回退到本地 SQLite。

## 当前阶段

当前阶段已经完成：

- PostgreSQL 作为正式主库
- 统一数据库配置入口
- PostgreSQL schema 文件
- Alembic 脚手架
- 过程追踪层表结构
- 周度池、日推、追踪中心均可直接读写 PostgreSQL

## 环境变量

```bash
export TOUZIFENXI_DATABASE_URL='postgresql://user:password@localhost:5432/touzifenxi'
```

## 初始化

```bash
cd /Users/xusheng/Documents/project/touzifenxi
source .venv/bin/activate
touzifenxi init-db
```

这会按当前 active backend 初始化数据库：

1. 如果配置了 PostgreSQL，就初始化远端 PostgreSQL 正式库
2. 否则回退初始化本地 SQLite

## Alembic

迁移入口：

```bash
alembic upgrade head
```

默认会读取：

- `alembic.ini`
- `TOUZIFENXI_DATABASE_URL`

## 下一阶段

接下来主要是继续拆 `storage.py` 的 repository 层，让双后端代码更干净；运行主链本身已经切到 PostgreSQL。
