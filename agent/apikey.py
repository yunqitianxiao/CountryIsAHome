"""API Key 的存放、读取与来源判定。

★这个模块存在的唯一理由是 **key 只能有一个出口**。
  在这之前 key 只有一条路进来：`.env`（或者 shell 环境变量），`llm.config()`
  每次调用现读 `os.environ`。现在多了一个写入方（网页），如果让它另存一个文件、
  或者让 llm 去别处取，就会出现"页面显示已配置、实际调用用的是另一把"这种
  两边打架的状态 —— 而且**页面上看不出来**。所以：`.env` 仍然是唯一的存放处，
  网页只是它的第二个写入口，写完就地更新 `os.environ`，`llm.py` 一行都不用改。

★★优先级**只翻了一个键**，不能整体翻。
  `.env.example` 里还有 `PORT`。验证脚本靠**环境变量**给每个测试服务指定端口
  （`Server.__enter__` 里 `env['PORT'] = ...`）。要是把 load_env 整体改成
  "`.env` 覆盖环境变量"，每个测试服务都会去抢 `.env` 里那个 8848，起第二个服务时
  端口就撞了 —— 而且是**静默撞**（判据打在别的进程身上）。所以只有 WEB_OWNED
  里那几个键翻优先级，其余一律保持 setdefault。

★优先级为什么值得翻：用户机器上 `~/.bashrc` 里本来就有一个 key，而 `.env` 根本
  不存在（2026-09 实测）。保持 setdefault 的话，他在网页上填的那把**重启后被
  shell 里那把盖掉** —— 填了没用，页面上还显示"已配置"。这正是本仓库一路在防的
  静默失败，只不过这次的"坏"是优先级顺序造成的。
"""

import os
import re

KEY_NAME = 'DEEPSEEK_API_KEY'

# 网页拥有写权限的键：这几个键 `.env` 覆盖环境变量。见文件头 ★★。
WEB_OWNED = (KEY_NAME,)

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 来源 ──────────────────────────────────────────────────────────────
# ★必须是**进程刚起来、load_env 还没跑**的那一刻的值 —— 它才是"shell 里那个"。
#   跑完 load_env 再取，取到的可能已经是 .env 里那把了，于是"环境变量被 .env
#   覆盖了"这句提醒永远不出现（因为两边看起来一样）。
_INHERITED = {}
if KEY_NAME not in _INHERITED:                    # reload 时不重新快照
    _INHERITED[KEY_NAME] = os.environ.get(KEY_NAME)


def env_file():
    """`.env` 的路径。

    ★`LICAI_ENV_FILE` 是给验证脚本用的隔离口。没有它，判据就会去动用户
    **真实的那份 `.env`** —— 跑一次验证就把人家填的 key 换了、或者把文件删了。
    "测试改了被测环境"比"判据不准"难查得多：它是**跑完之后**才发作的。
    """
    return os.environ.get('LICAI_ENV_FILE') or os.path.join(_HERE, '.env')


def read_file_key(path=None):
    """只把 .env 里那一个键读出来（不碰 os.environ）。"""
    path = path or env_file()
    if not os.path.exists(path):
        return ''
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith('#') or '=' not in s:
                    continue
                k, v = s.split('=', 1)
                if k.strip() == KEY_NAME:
                    return v.strip().strip('"').strip("'")
    except OSError:
        return ''
    return ''


def load_env(path=None):
    """读 `.env`。自己解析而不是硬依赖 python-dotenv —— 少一个能装不上的东西。"""
    path = path or env_file()
    if not os.path.exists(path):
        return False
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k in WEB_OWNED:
                # ★空值**不算**"我说了算"：`.env.example` 里那行就是空的，而
                #   文件头上写着"复制成 .env"。照抄一遍就把 shell 里好用的 key
                #   悄悄抹掉了 —— 用户只是照着说明做了件事，然后对话就降级了。
                #   想表达"就是没有 key"，用网页上的「清除」（它会把整行删掉）。
                if v:
                    os.environ[k] = v
            else:
                os.environ.setdefault(k, v)       # 真实环境变量优先，.env 只补缺
    return True


# ── 写 ────────────────────────────────────────────────────────────────

def _fmt(v):
    """写成 .env 里的一行。key 的字符集是安全的，引号那条路只是兜底。

    ★不做反斜杠转义：`load_env` 只负责剥掉外层引号，转义了它也不会还原。
      与其写一个"看着能转义、读回来是错的"的实现，不如把边界写清楚。
    """
    v = str(v)
    if re.match(r'^[A-Za-z0-9._\-]*$', v):
        return v
    return '"%s"' % v.replace('"', '')


def write_env(path, values):
    """把 values 合并写进 `.env`：**其它行一个字都不动**。

    ★为什么不 `open(path,'w')` 重写一遍：用户那份 .env 里还有 DEEPSEEK_MODEL、
      PORT、和他自己写的注释。整份重写 = 把他配的东西静默删掉，而他只是改了个
      key，根本不会想到去检查别的行还在不在。
    ★先写 `.tmp` 再 `os.replace`：写一半被打断（Ctrl-C、磁盘满）不会留下
      半份 .env —— 那会是个连注释都缺一半、但看着还挺正常的文件。
    """
    lines = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    pending, out = dict(values), []
    for ln in lines:
        s = ln.strip()
        k = s.split('=', 1)[0].strip() if ('=' in s and not s.startswith('#')) else None
        out.append('%s=%s' % (k, _fmt(pending.pop(k))) if k in pending else ln)
    for k, v in pending.items():
        out.append('%s=%s' % (k, _fmt(v)))
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out).rstrip('\n') + '\n')
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)                     # 里面是 key，别让同机器别人读
    except OSError:
        pass


def remove_env(path, key):
    """把某个键整行删掉（不写成 `KEY=` 空行：空行会被读成"有这个键"）。"""
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        lines = f.read().splitlines()
    out = []
    for ln in lines:
        s = ln.strip()
        k = s.split('=', 1)[0].strip() if ('=' in s and not s.startswith('#')) else None
        if k != key:
            out.append(ln)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out).rstrip('\n') + ('\n' if out else ''))
    os.replace(tmp, path)


# ── 对外 ──────────────────────────────────────────────────────────────

def mask(v):
    """★页面上只许出现掩码。

    前 6 后 4：够用户认出"是不是我填的那把"，又不够拿去用。短到一定长度的
    整个盖掉 —— 那种长度的东西露头尾就等于露出来了。
    """
    v = (v or '').strip()
    if not v:
        return ''
    if len(v) <= 12:
        return '•' * len(v)
    return v[:6] + '…' + v[-4:]


SOURCE_TEXT = {
    'web': '网页保存（本机 .env）',
    'shell': '系统环境变量',
    'none': '还没配',
}


def state():
    """给页面看的一份状态。★**不含完整 key**，只有掩码。"""
    file_key = read_file_key()
    live = (os.environ.get(KEY_NAME) or '').strip()
    src = 'web' if file_key else ('shell' if live else 'none')
    return {
        'configured': bool(live),
        'mask': mask(live),
        'source': src,
        'source_text': SOURCE_TEXT[src],
        # 环境变量里那把（进程启动时就有的）。非空 ⇒ 页面上要提醒一句：
        # 它现在是不是被 .env 盖住了。不提醒的话，"我明明在 .bashrc 里配过"
        # 和"怎么不生效"会同时成立，而用户看不到任何线索。
        'shell_mask': mask(_INHERITED.get(KEY_NAME)),
        'env_file': os.path.basename(env_file()),
        'env_file_exists': os.path.exists(env_file()),
    }


def save(v):
    """写进 .env **并且立刻生效**（不用重启服务）。

    ★只写文件不更新 os.environ 是个很自然的写法，症状是"保存成功，但这次对话
      还是说没配 key，重启一下就好了" —— 用户会以为是自己操作错了。
    """
    v = (v or '').strip()
    if not v:
        raise ValueError('key 不能为空')
    if not re.match(r'^[A-Za-z0-9._\-]{8,200}$', v):
        raise ValueError('这看着不像 API key（只允许字母、数字和 . _ -）')
    write_env(env_file(), {KEY_NAME: v})
    os.environ[KEY_NAME] = v
    return state()


def clear():
    """删掉网页保存的那把。

    ★删完要**把环境变量里那把还原回 os.environ**：`.env` 里那行没了之后，
      下次启动 load_env 不会再写它，于是 shell 里那把会重新生效。不还原的话，
      "现在没配"（进程内）和"重启后已配"（文件状态）这两句话会同时成立 ——
      用户看到的是"清除成功了"，下次开服务又冒出来。
    """
    remove_env(env_file(), KEY_NAME)
    base = _INHERITED.get(KEY_NAME)
    if base:
        os.environ[KEY_NAME] = base
    else:
        os.environ.pop(KEY_NAME, None)
    return state()
