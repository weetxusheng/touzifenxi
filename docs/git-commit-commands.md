# 常用 Git 命令（复制即用）

**默认前提**：在仓库根目录执行；本仓库示例路径为 `/Users/xusheng/Documents/project/touzifenxi`，你本机请先 `cd` 到实际路径。

**常见顺序（记这个就够）**：`git status` → `git add …` → `git commit -m "…"` → `git pull --rebase`（可选，与别人协作时建议）→ `git push`。

---

## 一、查看（不改动任何东西）

| 命令 | 作用 |
|------|------|
| `git status` | 看哪些文件改了、哪些已暂存、当前分支名。提交前先看这个。 |
| `git diff` | 看**工作区**里未暂存的修改内容（和上次提交比）。 |
| `git diff --staged` | 看**已暂存**、即将随下次提交走的修改。 |
| `git log --oneline -10` | 看最近 10 条提交，一行一条。 |

复制用：

```bash
git status
```

```bash
git diff
```

```bash
git diff --staged
```

```bash
git log --oneline -10
```

---

## 二、暂存与提交（把本地改动记成一次提交）

| 命令 | 作用 |
|------|------|
| `git add -p` | **交互式**选片段暂存，适合大文件里只提交一部分。 |
| `git add .` | 把**当前目录及以下**所有改动加入暂存区（注意：在仓库根目录执行才是「整个仓库的改动」）。 |
| `git commit -m "说明"` | 用引号里的文字作为提交说明，生成一次本地提交。 |

```bash
git add -p
```

```bash
git add .
```

```bash
git commit -m "feat(file-comparison): 简短说明本次改动"
```

指定文件再提交（适合只动了几处）：

```bash
git add 路径/到/文件 && git commit -m "fix: 说明"
```

### 示例：只提交 `file-comparison` 这一块

**含义**：不把别的目录（例如 `src/touzifenxi`）混进这次提交，只暂存 skill 与其测试。

```bash
cd /Users/xusheng/Documents/project/touzifenxi
```

只暂存 skill 源码与配置等：

```bash
git add skills/file-comparison/
```

若测试也改了，再暂存测试目录：

```bash
git add tests/skills/file-comparison/
```

或一条命令暂存两处（提交前仍建议 `git status` 确认没有多余文件）：

```bash
git add skills/file-comparison/ tests/skills/file-comparison/
```

```bash
git commit -m "feat(file-comparison): 简短说明本次改动"
```

---

## 三、推送到远程（把本地提交上传到 GitHub 等）

**`git push` 在干什么**：把**当前分支**上已经存在的本地提交，传到远程仓库（默认远程名一般是 `origin`）。  
**不会自动帮你 `commit`**：必须先本地 `git commit` 成功，再 `push`。

| 命令 | 什么时候用 |
|------|------------|
| `git push` | 当前分支**曾经**已经和远程建立过跟踪（例如以前 `push -u` 过），最常用。 |
| `git push -u origin $(git branch --show-current)` | **第一次**把当前分支推到远程，或远程还没有这个分支时；`-u` 会记住「以后在这个分支上直接 `git push` 即可」。 |

**建议**：和别人共用分支时，推送前先拉一下再推，减少冲突：

```bash
git pull --rebase
```

```bash
git push
```

首次推送当前分支到 `origin`（复制一条即可，第二条等价于写死分支名）：

```bash
git push -u origin $(git branch --show-current)
```

---

## 四、从远程更新到本地（拉取）

| 命令 | 作用 |
|------|------|
| `git pull --rebase` | 把远程新提交拉下来，并把你**尚未推送**的本地提交「接」在后面，历史更直。团队协作时常用。 |
| `git pull` | 默认可能是 merge；若团队没统一规范，可与同事确认用哪一种。 |

```bash
git pull --rebase
```

---

## 五、修改上一次提交（仅限：还没推、或你清楚自己在改历史）

| 命令 | 作用 |
|------|------|
| `git add .` | 若你还有新改动要并进「上一次提交」，先暂存。 |
| `git commit --amend --no-edit` | 把暂存区内容并入**上一次提交**，提交说明不变。 |
| `git commit --amend -m "新说明"` | 同上，但改掉上一次提交的说明。 |

**注意**：若该提交已经 `push` 到远程，改历史后需要强推，容易影响他人，一般要避免。

```bash
git add .
```

```bash
git commit --amend --no-edit
```

```bash
git commit --amend -m "新的提交说明"
```

---

## 六、分支

```bash
# 新建并切换到新分支
git checkout -b feature/分支名
```

```bash
# 切到 main 并更新（远程有更新时）
git switch main && git pull --rebase
```

---

## 七、临时收起工作区（stash）

适合：改到一半要切分支，又不想提交半成品。

| 命令 | 作用 |
|------|------|
| `git stash push -m "说明"` | 把工作区（和默认暂存）先收起来，工作区变干净。 |
| `git stash list` | 看 stash 列表。 |
| `git stash pop` | 取出最近一条 stash 并应用到工作区。 |

```bash
git stash push -m "说明"
```

```bash
git stash list
```

```bash
git stash pop
```

---

## 八、撤销（会丢改动，慎用）

| 场景 | 命令 | 作用 |
|------|------|------|
| 已改文件，**还没** `git add` | `git checkout -- 路径/到/文件` | 丢弃该文件工作区修改，恢复成上次提交的样子。 |
| 已经 `git add`，想取消暂存 | `git restore --staged 路径/到/文件` | 文件改动还在，只是退出暂存区。 |

```bash
git checkout -- 路径/到/文件
```

```bash
git restore --staged 路径/到/文件
```

---

## 九、提交说明前缀（可选，团队统一时用）

| 前缀 | 含义 |
|------|------|
| `feat:` | 新功能 |
| `fix:` | 修复 |
| `docs:` | 文档 |
| `test:` | 测试 |
| `chore:` | 杂项（配置、脚本等） |

示例：`feat(file-comparison): 将结构化提示词改为从 md 加载`
