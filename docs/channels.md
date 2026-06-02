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

邮件发送配置默认从项目根目录 `.env` / `.env.local` 或当前环境变量读取。

### 3.1 后端选择

- `TOUZIFENXI_EMAIL_BACKEND`
  - `auto`（**默认**）：先走企业 **EWS(443)**，失败再自动切 **QQ SMTP** 替补
  - `ews`：仅 Exchange Web Services（HTTPS 443）
  - `smtp`：仅传统 SMTP（读 `TOUZIFENXI_EMAIL_*`）
  - `qq`：仅 QQ SMTP 备用通道

### 3.2 QQ 备用（`auto` 失败时 / `backend=qq`）

必填项：

- `TOUZIFENXI_EMAIL_QQ_FROM` — 发件 QQ 邮箱，如 `944532395@qq.com`
- `TOUZIFENXI_EMAIL_QQ_PASSWORD` — QQ 邮箱 **SMTP 授权码**（不是登录密码）

可选项：

- `TOUZIFENXI_EMAIL_QQ_USERNAME` — 默认等于 `TOUZIFENXI_EMAIL_QQ_FROM`
- `TOUZIFENXI_EMAIL_QQ_FROM_NAME` — 发件人显示名
- `TOUZIFENXI_EMAIL_QQ_VERIFY_TLS` — 默认 true

固定连接：`smtp.qq.com:465` + SSL。

### 3.3 SMTP（`backend=smtp`）

必填项：

- `TOUZIFENXI_EMAIL_FROM`
  - 发件人邮箱
- `TOUZIFENXI_EMAIL_PASSWORD`
  - SMTP 授权码或密码

可选项：

- `TOUZIFENXI_EMAIL_USERNAME`
  - SMTP 登录用户名；不填时默认等于 `TOUZIFENXI_EMAIL_FROM`
- `TOUZIFENXI_EMAIL_FROM_NAME`
  - 发件人显示名称（可选）；例如 `投研简报机器人`
- `TOUZIFENXI_EMAIL_SMTP_HOST`
  - SMTP 主机
- `TOUZIFENXI_EMAIL_SMTP_PORT`
  - SMTP 端口
- `TOUZIFENXI_EMAIL_USE_SSL`
  - 是否使用 SSL，支持 `true/false`
- `TOUZIFENXI_EMAIL_VERIFY_TLS`
  - 是否校验 TLS 证书（默认 true）；支持 `true/false`
- `TOUZIFENXI_EMAIL_INSECURE`
  - 是否禁用 TLS 证书校验（默认 false）；支持 `true/false`
- `TOUZIFENXI_EMAIL_TIMEOUT_SECONDS`
  - SMTP 超时秒数

### 3.4 Exchange EWS（`backend=ews` 或 `auto` 主通道，cjhxfund 已验证）

必填项：

- `TOUZIFENXI_EMAIL_FROM` — 发件邮箱，如 `tylxts@cjhxfund.com`
- `TOUZIFENXI_EMAIL_PASSWORD` — 邮箱密码
- `TOUZIFENXI_EMAIL_USERNAME` — **NTLM 域账号**，如 `CJHX\tylxts`（Basic 不可用）

可选项：

- `TOUZIFENXI_EWS_URL` — 默认 `https://mail.cjhxfund.com/EWS/Exchange.asmx`
- `TOUZIFENXI_EWS_AUTH_TYPE` — 默认 `NTLM`
- `TOUZIFENXI_EWS_NTLM_DOMAIN` — 默认 `CJHX`（仅当未设 USERNAME 时用于拼 `DOMAIN\localpart`）
- `TOUZIFENXI_EMAIL_INSECURE=true` — 企业自签证书时关闭 TLS 校验

依赖：`pip install exchangelib`（已写入 `pyproject.toml`）。

## 4. QQ 邮箱默认行为

如果：

- `TOUZIFENXI_EMAIL_FROM` 是 `@qq.com`
- 并且没有显式填写 `TOUZIFENXI_EMAIL_SMTP_HOST`

则会自动使用：

- `smtp.qq.com`
- `465`
- `SSL`

这只是默认推断，不会覆盖你显式配置的主机和端口。

## 5. mail.cjhxfund.com 兼容行为

当 `TOUZIFENXI_EMAIL_SMTP_HOST=mail.cjhxfund.com` 时，默认使用：

- `587`
- `STARTTLS`

并且可以通过 `TOUZIFENXI_EMAIL_INSECURE=true`（或 `TOUZIFENXI_EMAIL_VERIFY_TLS=false`）来兼容内网/自签证书环境。

## 6. 与 skill 的关系

- 发送渠道属于项目级公共能力，不放在单个 skill 目录里。
- skill 只负责产出结果文件。
- 是否发送、发给谁、通过什么渠道发送，交给项目级渠道模块或自动化编排层处理。
