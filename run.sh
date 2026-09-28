#!/usr/bin/env bash
# 启动本地站点。所有重活都在 .venv 里，不碰系统的 python3
# （系统里那个 torch 是 CPU 版，你别的项目还在用，不能覆盖）。
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "没有 .venv。先建一个："
  echo "  uv venv --python 3.10 .venv"
  echo "  uv pip install --python .venv/bin/python -r requirements.txt"
  exit 1
fi

# ★本机的 ALL_PROXY 是 socks://（少了 5），requests 不认这个 scheme，
#   会让"本该走代理"的请求以一个和网络无关的 ProxyError 炸掉。
#   境外接口（DeepSeek）靠 http_proxy/https_proxy，不受影响。
unset ALL_PROXY all_proxy

# 补齐缺的依赖（首次跑、或换了机器）
.venv/bin/python - <<'PY' 2>/dev/null || true
import importlib
for m in ('flask','pandas','akshare','einops','safetensors','dotenv'):
    importlib.import_module(m)
PY

# ── 前端构建产物 ──────────────────────────────────────────────────────
# ★跑这个站**不需要 npm**：`web/dist/` 是入库的构建产物（先例：graph.html
#   本来就是入库的），打开就用这条承诺靠它兑现。
#   只有改 `web/src/` 的人才需要 node —— 那种时候要**自己**跑
#   `cd web && npm ci && npm run build`，这里不替他跑（构建要几秒到几十秒，
#   而且 npm 装依赖要联网，塞进"点一下就能开"的启动脚本里是帮倒忙）。
#
# ★但**产物不在**时必须当场说清楚。不说的话症状是：服务起来了、
#   首页 404 或白屏，而那句报错里一个"前端"字样都没有。
DIST_HTML=web/dist/index.html
if [ ! -f "$DIST_HTML" ]; then
  echo "缺少新前端的构建产物：$DIST_HTML" >&2
  echo "它不是手写的，是 web/src 构建出来的。在能联网的机器上跑一次：" >&2
  echo "  cd web && npm ci && npm run build" >&2
  echo "构建产物要一起带来（dist/ 是入库的，正常 clone 下来就有）。" >&2
  exit 1
fi

# ★产物比源码旧 = 改了前端忘了构建。这里**只警告、不自动构建**：
#   自动构建会把这个失误悄悄抹掉，而 verify_agent.py 里那条 check_dist_fresh
#   （逐字节比 dist 和重新构建的结果）正是靠"它会露出来"才成立的。
if [ -d web/node_modules ] && [ -n "$(find web/src web/index.html -newer "$DIST_HTML" -print -quit 2>/dev/null)" ]; then
  echo "⚠ web/src 比 web/dist 新 —— 改了前端但没重新构建，现在跑的是旧页面。"
  echo "  cd web && npm run build"
fi

exec .venv/bin/python app.py
