# 发送渠道说明

本文档说明项目级发送渠道的目录规划与当前已接入能力。

## 1. 目录规划

- 项目级发送渠道统一放在：
  - `src/touzifenxi/channels/`
- 这里放的是跨 skill 可复用的对外发送能力，例如：
  - 邮件
  - Webhook
  - 企业微信
  - 飞书

当前已接入：

- `src/touzifenxi/channels/email.py`
  - SMTP 邮件发送渠道

## 2. 当前邮件渠道能力

项目级 CLI 已提供独立命令：

```bash
PYTHONPATH=src ./.venv/bin/python -m touzifenxi.cli send-email \
  --to chengxusheng@cjhxfund.com \
  --subject "C114 简报" \
  --body-file reports/c114_brief.txt \
  --html-file reports/c114_brief.html \
  --attach reports/c114_brief.md
```

支持：

- `--to`
  - 一个或多个收件人
- `--subject`
  - 邮件主题
- `--body`
  - 直接传正文
- `--body-file`
  - 从文件读取正文
- `--attach`
  - 一个或多个附件
- `--html-file`
  - 从文件读取 HTML 正文；会和文本正文一起组成多部分邮件

路径规则：

- 可以传项目根目录相对路径
- 也可以传绝对路径

## 3. 环境变量

邮件发送配置默认从项目根目录 `.env.local` 或当前环境变量读取。

必填项：

- `TOUZIFENXI_EMAIL_FROM`
  - 发件人邮箱
- `TOUZIFENXI_EMAIL_PASSWORD`
  - SMTP 授权码或密码

可选项：

- `TOUZIFENXI_EMAIL_USERNAME`
  - SMTP 登录用户名；不填时默认等于 `TOUZIFENXI_EMAIL_FROM`
- `TOUZIFENXI_EMAIL_SMTP_HOST`
  - SMTP 主机
- `TOUZIFENXI_EMAIL_SMTP_PORT`
  - SMTP 端口
- `TOUZIFENXI_EMAIL_USE_SSL`
  - 是否使用 SSL，支持 `true/false`
- `TOUZIFENXI_EMAIL_TIMEOUT_SECONDS`
  - SMTP 超时秒数

## 4. QQ 邮箱默认行为

如果：

- `TOUZIFENXI_EMAIL_FROM` 是 `@qq.com`
- 并且没有显式填写 `TOUZIFENXI_EMAIL_SMTP_HOST`

则会自动使用：

- `smtp.qq.com`
- `465`
- `SSL`

这只是默认推断，不会覆盖你显式配置的主机和端口。

## 5. 与 skill 的关系

- 发送渠道属于项目级公共能力，不放在单个 skill 目录里。
- skill 只负责产出结果文件。
- 是否发送、发给谁、通过什么渠道发送，交给项目级渠道模块或自动化编排层处理。
