# AGENTS.md

给在这个仓库里写代码的 agent 看。使用 `canvaskit` 的说明在 `skills/canvaskit/SKILL.md`，面向人的说明在 `README.md`。

## 项目

面向 agent 的交大 Canvas 命令行工具，Python ≥ 3.10，依赖 httpx / typer / rich（3.10 另需 tomli），用 uv 管理。

```bash
git clone https://github.com/ZHS-XER/CanvasKit.git && cd CanvasKit
uv sync                 # 安装依赖
uv run pytest           # 单元测试，不访问网络
uv run canvaskit --help
uv tool install -e .    # 可选：把开发版装成全局命令，改代码即时生效
```

开发时如需让 Claude Code 直接使用仓库里的 SKILL.md，可以用链接代替复制：`ln -s "$PWD/skills/canvaskit" ~/.claude/skills/canvaskit`。`canvaskit skill install` 遇到链接时不会覆盖（除非 `--force`）。

CI（`.github/workflows/ci.yml`）在 Ubuntu / macOS × Python 3.10 / 3.12 上运行 `uv sync --locked` 与 pytest，并构建 wheel、从 wheel 安装后验证 `canvaskit skill install`。改了依赖要 `uv lock` 并提交 `uv.lock`。

改动后至少跑 `uv run pytest`。需要真实验证时用 `uv run canvaskit ... --json`，它会读本机的 token。

## 结构

| 文件 | 职责 |
|---|---|
| `src/canvaskit/config.py` | 凭证：环境变量 `CANVAS_TOKEN` / `CANVAS_BASE_URL`，其次 `~/.config/canvaskit/env`；课程配置 `~/.config/canvaskit/config.toml` |
| `src/canvaskit/canvas.py` | Canvas REST API：分页、课程名解析、文件索引（单元 + 文件库 + 页面链接里的隐藏文件，按 id 去重并标来源）、下载 |
| `src/canvaskit/library.py` | 每门课的文件清单 `~/.config/canvaskit/state/<course_id>.json`：已下载 / 本地已有 / 已提醒，新文件与更新判断，本地同名同大小匹配，平铺下载的目标路径 |
| `src/canvaskit/video.py` | 交大录播平台：LTI 登录链和录播、字幕、截图接口 |
| `src/canvaskit/formats.py` | 输出格式：srt、txt、slides.md、slides.json |
| `src/canvaskit/cli.py` | typer 命令，只做参数处理、选择、输出 |
| `src/canvaskit/skill.py` | `canvaskit skill install/uninstall/path`：把 SKILL.md 复制到 `~/.claude/skills/canvaskit/` |
| `skills/canvaskit/SKILL.md` | 给使用者 agent 的说明，命令或输出变了要同步更新。构建时打包进 wheel（`canvaskit/skill/SKILL.md`，见 `pyproject.toml`） |

`canvas.py` 只依赖 Canvas 标准 API，换 `CANVAS_BASE_URL` 可用于其他学校；`video.py` 是交大专属的。不要把交大专属的东西写进 `canvas.py`。

## 录播平台登录链（`video.py`）

1. `GET /api/v1/courses/:id/external_tools/sessionless_launch?id=8329`（“课堂视频新版”）得到一次性启动链接。
2. 跟随链接，依次自动提交 LTI 1.3 OIDC 表单。
3. 从最终跳转地址中取出 `jwt_token`。
4. 带 `jwt-token` 请求头访问 `https://v.sjtu.edu.cn/jy-application-resourcemanage`：
   - `/lms/launch-context` → teachingClassId
   - `/v1/subject_vod_list_new` → 录播列表
   - `/v1/course/ai/translate/{id}` → 字幕（`beforeAssemblyList` 逐句，`afterAssemblyList` 合并段落）
   - `/v1/course/ai/ppt?courseId={id}` → PPT 截图、OCR、时间点

这些是非公开接口，参考了 [SJTU-Canvas-Helper](https://github.com/Okabe-Rintarou-0/SJTU-Canvas-Helper)。接口失效时先对照它的最新实现。

## 约定（改命令时必须保持）

- 每个命令都支持 `--json`。stdout 只放结果；进度、警告、错误一律走 stderr（`err` console）。
- 批量命令每项返回 `status`：`downloaded` / `exists` / `unavailable` / `error` / `locked` / `would-download`；`files` 的文件状态是 `new` / `pending` / `downloaded` / `local` / `updated` / `locked`。新增状态要写进 SKILL.md。
- `sync` 自动下载字幕，课程文件只列出不下载；下载必须由用户选择（`download --id/--new/-m/--all`）。
- “新文件”指上次 sync 之后新出现的（列过即记为 `seen`），不是所有没下载的。更新判断看 `size` / `modified_at`，不看 `updated_at`（改名、移动也会变）。
- 课程文件平铺下载到 slides 文件夹；不覆盖不是 canvaskit 下载的同名文件（改用 ` (id)` 后缀），不动 slides 文件夹以外的文件。
- 幂等：输出已存在就跳过，`--force` 才覆盖。文件先写 `.part` 再改名。
- 单项失败不中断整批，记为 `status: "error"` 继续处理；致命错误退出码 `1`，缺 token / 配置错误退出码 `2`（typer 的参数错误也是 `2`）。
- 录播文件命名与 SJTU Canvas Helper 一致：`高等数学 第5周 周三 第2节.srt`，“第N节”是当天这门课的第几次录播。
- 面向 LLM 的输出尽量省 token（例如 txt 字幕合并段落）。
- 代码注释和 `--help` 用英文；README 和 SKILL.md 用中文。

## 安全

- 不要提交 token、`env` 文件、`config.toml`、`state/`、下载下来的字幕、截图、课件（`.gitignore` 已覆盖常见情况）。
- 测试只用手写的假数据，不要把真实课程数据（可能含老师和同学的个人信息）放进 `tests/`。
