# 思考强度不可调 - 本机(不可调)基线 vs 另一台(可调)对比清单

> 生成: 2026-08-27 本机 WSL2 Ubuntu | opencode 1.18.23 sha de0724a3 | 用途: 拿到可调那台按 §5 同命令复跑，diff 即定位 | 已补 §5.8 潜在遗漏 11 项确保不漏采

## 1 环境 (本机不可调)

```
opencode version: 1.18.23
os: Linux 6.18.33.2-microsoft-standard-WSL2 x64
terminal: vscode 1.134.0 / xterm-256color
bin: /home/zmz/.opencode/bin/opencode (184584320, Aug 25 14:11)
cache models.json: /home/zmz/.cache/opencode/models.json (4334527, Aug 27 08:11)
paths:
  home  /home/zmz
  data  /home/zmz/.local/share/opencode
  cache /home/zmz/.cache/opencode
  config /home/zmz/.config/opencode
plugins:
  - opencode-supermemory@latest
  - file:///home/zmz/.opencode/plugins/graphify.js
```

## 2 配置原文 (本机已脱敏)

### 2.1 `~/.config/opencode/opencode.json`
```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["./plugins/caveman/plugin.js"],
  "provider": {
    "Alibaba (China)": {
      "options": {
        "apiKey": "sk-ws-H.ELRLLRH.jvOR.MEUCIBAr...（已截断）",
        "baseURL": "https://dashscope.aliyuncs.com/compatible-mode/v1"
      },
      "models": {
        "qwen3.5-plus-2026-02-15": { "name": "qwen3.5-plus-2026-02-15" },
        "qwen3.8-27b": { "name": "qwen3.8-27b" },
        "qwen3.5-plus-2026-04-20": { "name": "qwen3.5-plus-2026-04-20" }
      }
    }
  }
}
```

### 2.2 `~/.config/opencode/opencode.jsonc`
```json
{
  "plugin": ["opencode-supermemory@latest"],
  "$schema": "https://opencode.ai/config.json",
  "permission": {
    "read": { "~/.config/opencode/gsd-core/*": "allow" },
    "external_directory": { "~/.config/opencode/gsd-core/*": "allow" }
  }
}
```

### 2.3 `~/.config/opencode/tui.json` / `tui.jsonc`
```
不存在 (无自定义 keybinds/theme) -> variant_cycle 未显式绑定
```

### 2.4 `~/.config/opencode/settings.json`
```json
{}
```

### 2.5 `opencode debug config` 合并后 ( /tmp/full.json )
```
providers: ['Alibaba (China)']  # 注意: opencode Zen 为内置 provider，不在此列，需看 auth.json
model: None
small_model: None
plugin: ['opencode-supermemory@latest', 'file:///home/zmz/.opencode/plugins/graphify.js']
provider dump 仅 Alibaba，同 2.1
# 关键: 无 provider.opencode.models.<id>.variants 覆盖
```

### 2.6 `~/.local/share/opencode/auth.json` (仅 provider 名)
```
opencode (api)
openrouter (api)
Alibaba (China) (api)
github-models (api)
alibaba-cn (api)
```

## 3 模型能力 (本机 `~/.cache/opencode/models.json` opencode provider)

| 模型 | reasoning | reasoning_options |
|------|-----------|-------------------|
| muse-spark-1.2 | true | effort: [minimal,low,medium,high,xhigh] |
| muse-spark-1.2-contributor-free | true | effort: [minimal,low,medium,high,xhigh] |
| hy3-free | true | toggle + effort: [low,medium,high] |
| gpt-5.3-codex-spark | true | effort: [low,medium,high,xhigh] |
| deepseek-v4-flash-free | true | effort: [low,high,max] |
| x-preview-f-free | true | effort: [low,high,max] |
| mimo-v2.5-free | true | [] |
| nemotron-3-ultra-free | true | [] |

> 结论: 免费模型中 muse-spark 理论支持 5 档，但需 variant 层透传 reasoningEffort 才会显。

## 4 运行痕迹 (本机 `~/.local/share/opencode/opencode.db` session.model)

```
按 (id, providerID, variant) 计数 Top:
deepseek-v4-flash-free / opencode / max      14
glm-4.5-air / zai / default                   3
hy3-free / opencode / default                 2
x-preview-f-free / opencode / max             2
muse-spark-1.2-contributor-free / opencode / default  2  (2026-08-27, 2026-08-26)
muse-spark-1.2-contributor-free / opencode / xhigh    1  (2026-08-21 git - 1787271898967) <- 曾切到 xhigh
```

现象印证: 本机 **曾**调到 xhigh，近 2 次回落 default；`/models` 列表 `opencode models opencode` 仅显示基模名 `opencode/muse-spark-1.2-contributor-free` 无 `(high)` 后缀，符合“直选模型、无强度选项、UI 无标注”描述。变体在选中模型后的 variant 维度，需 `variant_cycle` 循环。

## 5 另一台(可调)待采命令 (原样粘贴输出回本文件 §5.1-5.9)

### 5.1 版本与路径
```bash
opencode --version; echo "---"; opencode debug info 2>&1 | head -n 30; echo "---"; opencode debug paths 2>&1 | head -n 20
ls -l ~/.opencode/bin/opencode; ls -l ~/.cache/opencode/models.json
```

### 5.2 配置原文
```bash
cat ~/.config/opencode/opencode.json; echo "==="; cat ~/.config/opencode/opencode.jsonc; echo "==="; cat ~/.config/opencode/tui.json 2>&1; cat ~/.config/opencode/tui.jsonc 2>&1; echo "==="; cat ~/.config/opencode/settings.json 2>&1
cat ~/.local/share/opencode/auth.json 2>&1 | python3 -m json.tool | grep -v '"key"'
```

### 5.3 合并后配置 (关键看是否有 provider.opencode.variants)
```bash
opencode debug config 2>&1 > /tmp/full.json
python3 -c "import json; d=json.load(open('/tmp/full.json')); print('providers:', list(d.get('provider',{}).keys())); print('model:', d.get('model')); import json as j; print(j.dumps(d.get('provider',{}).get('opencode',{}), indent=2, ensure_ascii=False)[:4000])"
python3 -c "import json; d=json.load(open('/tmp/full.json')); print(json.dumps({k:v for k,v in d.items() if k not in ['agent','mode','command']}, indent=2, ensure_ascii=False)[:6000])" | grep -A2 -B2 variant
```

### 5.4 模型能力 (对比 reasoning_options 是否一致)
```bash
python3 -c "import json; d=json.load(open('/home/zmz/.cache/opencode/models.json')); m=d['opencode']['models']; print(json.dumps(m.get('muse-spark-1.2-contributor-free'), indent=2, ensure_ascii=False))"
opencode models opencode 2>&1 | tr ',' '\n' | grep -E "spark|hy3|mimo"
```

### 5.5 运行痕迹 (看 variant 分布是否含 minimal/low/medium/high/xhigh)
```bash
python3 << 'PY'
import sqlite3, json, collections
con=sqlite3.connect('/home/zmz/.local/share/opencode/opencode.db'); cur=con.cursor()
cur.execute('SELECT model FROM session'); c=collections.Counter()
for r in cur.fetchall():
    try: o=json.loads(r[0]); c[(o.get('id'), o.get('variant'))]+=1
    except: pass
for k,v in sorted(c.items(), key=lambda x:-x[1])[:20]: print(k,v)
PY
```

### 5.6 UI 行为 (截图或文字描述)
- TUI 内 `/models` 选中 `muse-spark-1.2-contributor-free` 后，模型行是否显示 `(minimal/low/medium/high/xhigh)` 后缀？
- 按 `variant_cycle` (或自定义 keybind) 是否循环档位？
- `tui.json` 中 `keybinds.variant_cycle` 值？

### 5.7 一键采全脚本 (另一台复制整段执行, 输出重定向贴回 - 含 §5.8 全部)
```bash
# 保存为 /tmp/collect.sh 后 bash /tmp/collect.sh > /tmp/other-machine.txt 2>&1，发回贴到 §5
echo "===5.1 version==="; opencode --version; which opencode; sha256sum $(which opencode) 2>&1 | cut -c1-16; node -v 2>&1; npm -v 2>&1; opencode debug info 2>&1 | head -n 30; opencode debug paths 2>&1 | head -n 20; ls -l ~/.opencode/bin/opencode; ls -l ~/.cache/opencode/models.json; sha256sum ~/.cache/opencode/models.json 2>&1 | cut -c1-16
echo "===5.2 env==="; env | grep -E "OPENCODE|ANTHROPIC|OPENAI" | sort; echo "---managed---"; ls -la /etc/opencode 2>&1 | head -n 20; ls -la "/Library/Application Support/opencode" 2>&1 | head -n 20
echo "===5.3 config==="; cat ~/.config/opencode/opencode.json 2>&1; echo "---jsonc---"; cat ~/.config/opencode/opencode.jsonc 2>&1; echo "---tui---"; cat ~/.config/opencode/tui.json 2>&1; cat ~/.config/opencode/tui.jsonc 2>&1; cat ~/.config/opencode/settings.json 2>&1; echo "---project---"; ls -la opencode.json* tui.json* .opencode 2>&1 | head -n 30; cat opencode.json 2>&1 | head -n 50
echo "===5.4 merged==="; opencode debug config 2>&1 > /tmp/full.json; python3 -c "import json; d=json.load(open('/tmp/full.json')); print('providers',list(d.get('provider',{}).keys())); print('model',d.get('model'),'small',d.get('small_model')); import json as j; print(j.dumps({k:d[k] for k in ['disabled_providers','enabled_providers','experimental','autoupdate','snapshot'] if k in d},indent=2,ensure_ascii=False)[:1200])"; python3 -c "import json; d=json.load(open('/tmp/full.json')); print(json.dumps(d.get('provider',{}).get('opencode',{}),indent=2,ensure_ascii=False)[:5000])"
echo "===5.5 models==="; python3 -c "import json; d=json.load(open('/home/zmz/.cache/opencode/models.json')); print(json.dumps(d['opencode']['models']['muse-spark-1.2-contributor-free'],indent=2,ensure_ascii=False))"; opencode models opencode 2>&1 | tr ',' '\n' | grep -E "spark|hy3|mimo|nemotron"
echo "===5.6 history==="; python3 << 'PY'
import sqlite3, json, collections
con=sqlite3.connect('/home/zmz/.local/share/opencode/opencode.db'); cur=con.cursor()
cur.execute('SELECT model FROM session'); c=collections.Counter()
for r in cur.fetchall():
    try: o=json.loads(r[0]); c[(o.get('id'),o.get('variant'))]+=1
    except: pass
for k,v in sorted(c.items(), key=lambda x:-x[1])[:20]: print(k,v)
PY
echo "===5.7 logs==="; tail -n 100 ~/.local/share/opencode/log/opencode.log 2>&1 | grep -iE "reasoning|variant|effort|warn|error" | tail -n 20
echo "===5.8 auth==="; cat ~/.local/share/opencode/auth.json 2>&1 | python3 -m json.tool 2>&1 | grep -v '"key"'
echo "===5.9 agents==="; opencode debug agent build 2>&1 | head -n 80
```

> 贴回后填 §5.1-5.7，下节自动判因

## 5.8 潜在遗漏项自检 (本机已查, 另一台必采 - 否则对比必漏)

> 若仅采 §5.1-5.7 而漏 §5.8，则 env/托管/项目级/二进制/缓存/账户/策略 等隐性差异无法对比，必漏因。§5.7 一键脚本已含全部，单条对照见下表细化。

| # | 排查维 | 本机实测 | 另一台待补命令 | 漏采后果 |
|---|--------|----------|---------------|----------|
| 7 | 环境变量覆盖 | `OPENCODE=1, OPENCODE_CALLER=vscode, OPENCODE_PID=1486915` 无 `OPENCODE_CONFIG/_CONTENT/_DIR/_TUI_CONFIG` | `env \| grep -E "OPENCODE" \| sort` | 自定义路径指向旧配置，`debug config` 看不出 |
| 8 | 托管/远端配置 | `/etc/opencode` 无, `.well-known/opencode` 无命中 | `ls -la /etc/opencode 2>&1; ls -la /Library/Application\ Support/opencode 2>&1; opencode debug config 2>&1 \| grep -iE "well-known|managed|remote"` | 组织下发 `disabled_providers` 静默禁用变体 |
| 9 | 项目级覆盖 | `./opencode.json*` 无, `./.opencode/` 无, `./tui.json*` 无 | `ls -la opencode.json* tui.json* .opencode 2>&1; cat opencode.json 2>&1 \| head -n 40` | 项目配置覆盖全局 `variants` |
| 10 | 安装方式/二进制指纹 | `1.18.23 /home/zmz/.opencode/bin/opencode sha de0724a3` via curl, `node v22.22.1 npm 11.17.0 uv 0.12.1` | `which opencode; sha256sum $(which opencode) \| cut -c1-16; opencode --version; node -v; npm -v` | npm/curl/brew 版本同号但构建不同，变体生成逻辑不一致 |
| 11 | VSCode 扩展/入口 | `sst-dev.opencode` + vscode 调用 (`OPENCODE_CALLER=vscode`, `_EXTENSION_OPENCODE_PORT=64553`) | `code --list-extensions \| grep -i opencode; echo $OPENCODE_CALLER` | IDE 以 `--pure` 或旧 server 启动，无视全局配置 |
| 12 | 缓存指纹 | `models.json sha 0c8a4668 4.3MB Aug27` | `sha256sum ~/.cache/opencode/models.json \| cut -c1-16; wc -l ~/.cache/opencode/models.json; ls -l ~/.cache/opencode/models.json` | 缓存过期/损坏致 `reasoning_options` 为空 |
| 13 | 账户/授权/配额 | Zen `opencode` api 已登录, `auth.json` 5 凭据未过期（仅名已脱敏） | `cat ~/.local/share/opencode/auth.json \| python3 -m json.tool \| grep -v '"key"' ; opencode providers 2>&1 \| head -n 20; curl -s https://opencode.ai/zen/v1/models 2>&1 \| head` | token 过期/非 contributor致免费档位无 `xhigh` 权限 |
| 14 | 启停策略 | `permission` 仅 `read/external_directory allow`, `snapshot true` 默认, `disabled_providers/enabled_providers/experimental` 均无 | `python3 -c "import json; d=json.load(open('/tmp/full.json')); [print(k, json.dumps(d.get(k),ensure_ascii=False)[:400]) for k in ['disabled_providers','enabled_providers','experimental','snapshot','autoupdate','permission','instructions'] if k in d]"` | `enabled_providers` 限死或 `experimental.policies` 拒 `opencode` |
| 15 | Agent 级覆盖 | `build` agent 当前 `muse-spark-default`, `plan` 同，无 agent 私有 `model/variants` | `opencode debug agent build 2>&1 \| head -n 60; cat ~/.config/opencode/agents/*.md \| grep -i model` | agent `model: openai/gpt-5` 覆盖全局 `variants` |
| 16 | 日志/报错 | `~/.local/share/opencode/log/opencode.log` 无 `reasoningEffort` 报错 | `tail -n 100 ~/.local/share/opencode/log/opencode.log \| grep -iE "reasoning|variant|effort|error|warn" \| tail -n 20` | 静默失败无 UI 提示 |
| 17 | UI 录屏 | 本机确认: `/models` 直选无后缀、未按 `variant_cycle` | 另一台 `TUI 截图/录屏`: /models 列表 + 选中行后缀 + 按 `variant_cycle` 后的 variant 名 | 文字描述易漏循环交互 |

## 6 判定 (贴回后对照)

| 另一台差异 | 定因 | 本机修复 |
|------------|------|----------|
| 另一台 `opencode.json` 含 `provider.opencode.models.muse-spark-1.2-contributor-free.variants` | **缺显式 variants 覆盖** | 补 §6.1 片段 (推荐) |
| 另一台 `tui.json` 含 `keybinds.variant_cycle` | **快捷键未绑定致不可达** | 补 §6.2 |
| 另一台 `opencode --version` > 1.18.23 或 `sha` 不同 | **版本/构建差，内置变体未刷新** | `opencode upgrade` + 删 `~/.cache/opencode/models.json` 重拉 |
| 另一台 `models.json sha/reasoning_options` 不同 | **缓存差异** | 同上重拉 |
| 另一台 `OPENCODE_CONFIG*` 非空或 `/etc/opencode` 存在 | **环境/托管配置劫持** | 清理变量/托管文件 |
| 另一台 `./opencode.json` 或 `./.opencode/` 存在 | **项目级覆盖** | 对齐项目配置 |
| 另一台 `auth.json` / `opencode providers` 状态不同 | **账户/配额差** | 重登 Zen / 换 contributor 账号 |
| 另一台 `enabled_providers`/`experimental` 不同 | **策略限死** | 删 `enabled_providers` 限制 |
| 均无差异但另一台 UI 有后缀 | **操作路径误解: /models 直选 vs variant 维度** | 培训: 选中模型后用 `variant_cycle` |

### 6.1 修复片段 (本机拟加, 待你确认后执行)
`~/.config/opencode/opencode.json` 合并加入:
```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "opencode": {
      "models": {
        "muse-spark-1.2-contributor-free": {
          "variants": {
            "minimal": { "reasoningEffort": "minimal" },
            "low":     { "reasoningEffort": "low" },
            "medium":  { "reasoningEffort": "medium" },
            "high":    { "reasoningEffort": "high" },
            "xhigh":   { "reasoningEffort": "xhigh" }
          }
        },
        "muse-spark-1.2": {
          "variants": {
            "minimal": { "reasoningEffort": "minimal" },
            "low":     { "reasoningEffort": "low" },
            "medium":  { "reasoningEffort": "medium" },
            "high":    { "reasoningEffort": "high" },
            "xhigh":   { "reasoningEffort": "xhigh" }
          }
        }
      }
    }
  }
}
```

### 6.2 快捷键 (可选)
`~/.config/opencode/tui.json`:
```json
{ "$schema": "https://opencode.ai/tui.json", "keybinds": { "variant_cycle": "ctrl+v" } }
```

## 7 验证 (修复后)
```bash
opencode debug config 2>&1 | grep -A30 '"muse-spark'
# TUI: /models 选中 muse-spark 行应显示 (minimal/low/medium/high/xhigh)
# 新会话 sqlite: SELECT model FROM session ORDER BY time_created DESC LIMIT 1  应含 "variant":"high" 且 tokens_reasoning 随档位变化
```

## 附录A 本机完整原始输出 (另一台逐段 diff 用, 已脱敏, 2026-08-27 08:42)

> 本附录为 **本机可直接对比的完整基线**，另一台按同标题执行对应命令后 diff。若某段另一台输出不同即为疑点。

### A1 安装与版本指纹
```
1.18.23
/home/zmz/.opencode/bin/opencode
-rwxr-xr-x 1 zmz zmz 177M Aug 25 14:11 /home/zmz/.opencode/bin/opencode
sha256: de0724a36eaf3166e7f1ff38d0f4478b95ccc47725e9597b3fe66d3d3e18baa2
node v22.22.1
npm 11.17.0
uv 0.12.1 (x86_64-unknown-linux-gnu)
vscode 1.134.0 110a328ea54b42367b803ec53ee0bf52ef26b419 x64
ext: sst-dev.opencode
os: Linux jianyanyuan4 6.18.33.2-microsoft-standard-WSL2 #1 SMP PREEMPT_DYNAMIC Thu Jun 18 21:54:43 UTC 2026 x86_64 GNU/Linux
opencode debug info: 1.18.23 / Linux 6.18.33.2-microsoft-standard-WSL2 x64 / vscode 1.134.0 / xterm-256color / plugins: opencode-supermemory@latest, file:///home/zmz/.opencode/plugins/graphify.js
paths: home /home/zmz, data /home/zmz/.local/share/opencode, bin /home/zmz/.cache/opencode/bin, cache /home/zmz/.cache/opencode, config /home/zmz/.config/opencode
```

### A2 环境变量 (全量 sort, 含代理)
```
OPENCODE=1
OPENCODE_CALLER=vscode
OPENCODE_PID=1486915
_EXTENSION_OPENCODE_PORT=64553
http_proxy=http://127.0.0.1:7897
https_proxy=http://127.0.0.1:7897
no_proxy=localhost,127.0.0.1,192.168.0.0/16,10.0.0.0/8,.tsinghua.edu.cn,.tuna.tsinghua.edu.cn,openrouter.ai,dashscope.aliyuncs.com
# 无 OPENCODE_CONFIG / OPENCODE_CONFIG_CONTENT / OPENCODE_CONFIG_DIR / OPENCODE_TUI_CONFIG
# 完整 env 见上文 bash 输出, 关键已列
```

### A3 托管/远端/项目级配置
```
ls /etc/opencode -> No such file or directory
ls /Library/Application Support/opencode -> No such file or directory
ls ~/.config/opencode -> 11 项, 仅 opencode.json(671B), opencode.jsonc(268B), settings.json(3B), 无 tui.json
ls ./opencode.json* / ./tui.json* / ./.opencode -> 均 No such file or directory (项目根无覆盖)
opencode debug config well-known/managed 命中: 无
```

### A4 全局配置原文 (精确字节)
```jsonc
// ~/.config/opencode/opencode.json (671B, mode 600)
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["./plugins/caveman/plugin.js"],
  "provider": { "Alibaba (China)": { "options": { "apiKey": "sk-ws-H.ELRLLRH.jvOR.MEUCIBAr...（已截断，原文 671B）", "baseURL": "https://dashscope.aliyuncs.com/compatible-mode/v1" }, "models": { "qwen3.5-plus-2026-02-15": {"name":"qwen3.5-plus-2026-02-15"}, "qwen3.8-27b": {"name":"qwen3.8-27b"}, "qwen3.5-plus-2026-04-20": {"name":"qwen3.5-plus-2026-04-20"} } } }
}
// ~/.config/opencode/opencode.jsonc (268B)
{ "plugin": ["opencode-supermemory@latest"], "$schema": "https://opencode.ai/config.json", "permission": { "read": {"~/.config/opencode/gsd-core/*":"allow"}, "external_directory": {"~/.config/opencode/gsd-core/*":"allow"} } }
// ~/.config/opencode/settings.json (3B)
{}
// ~/.config/opencode/tui.json* 不存在
// ~/.config/opencode/package.json {"type":"commonjs"}
```

### A5 合并后配置 (opencode debug config /tmp/full.json 845357B sha 5c75615903c2800b)
```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["opencode-supermemory@latest","file:///home/zmz/.opencode/plugins/graphify.js"],
  "provider": {"Alibaba (China)": {"options":{"apiKey":"sk-ws-H...","baseURL":"https://dashscope.aliyuncs.com/compatible-mode/v1"},"models":{"qwen3.5-plus-2026-02-15":{"name":"qwen3.5-plus-2026-02-15"},"qwen3.8-27b":{"name":"qwen3.8-27b"},"qwen3.5-plus-2026-04-20":{"name":"qwen3.5-plus-2026-04-20"}}}},
  "permission": {"read":{"~/.config/opencode/gsd-core/*":"allow"},"external_directory":{"~/.config/opencode/gsd-core/*":"allow"}},
  "plugin_origins": [{"spec":"opencode-supermemory@latest","source":"/home/zmz/.config/opencode","scope":"global"},{"spec":"file:///home/zmz/.opencode/plugins/graphify.js","source":"/home/zmz/.opencode","scope":"global"}],
  "username": "zmz"
  // 无 disabled_providers/enabled_providers/experimental/instructions/snapshot/autoupdate/mcp -> 均默认
  // 无 provider.opencode -> 关键: 无 variants 覆盖，靠内置 reasoning_options 生成
}
```

### A6 模型能力 (models.json 4.2M sha 0c8a466825e3e783)
```json
// opencode provider 全部免费/相关模型 reasoning_options
muse-spark-1.2: {"id":"muse-spark-1.2","reasoning":true,"reasoning_options":[{"type":"effort","values":["minimal","low","medium","high","xhigh"]}],"provider":{"npm":"@ai-sdk/openai"}}
muse-spark-1.2-contributor-free: {"id":"muse-spark-1.2-contributor-free","reasoning":true,"reasoning_options":[{"type":"effort","values":["minimal","low","medium","high","xhigh"]}],"provider":{"npm":"@ai-sdk/openai"}}
hy3-free: {"id":"hy3-free","reasoning":true,"reasoning_options":[{"type":"toggle"},{"type":"effort","values":["low","medium","high"]}] }
mimo-v2.5-free: {"id":"mimo-v2.5-free","reasoning":true,"reasoning_options":[]}
nemotron-3-ultra-free: {"id":"nemotron-3-ultra-free","reasoning":true,"reasoning_options":[]}
nemotron-3.5-lightning-free: {"id":"nemotron-3.5-lightning-free","reasoning":true,"reasoning_options":[]}
gpt-5.3-codex-spark: {"id":"gpt-5.3-codex-spark","reasoning":true,"reasoning_options":[{"type":"effort","values":["low","medium","high","xhigh"]}],"provider":{"npm":"@ai-sdk/openai"}}
deepseek-v4-flash-free: {"id":"deepseek-v4-flash-free","reasoning":true,"reasoning_options":[{"type":"effort","values":["low","high","max"]}] }
x-preview-f-free: {"id":"x-preview-f-free","reasoning":true,"reasoning_options":[{"type":"effort","values":["low","high","max"]}] }
```

### A7 运行痕迹 (opencode.db 最近20条)
```
ses_fbf7202b muse-spark-1.2-contributor-free/default  New session - 2026-08-27T00:10:42.374Z  1787789442374
ses_fc84ac47 muse-spark-1.2-contributor-free/default  Example code changes in llms-from-scratch.md (fork  1787641019268
ses_fc938c68 deepseek-v3/default  Find efficient decode alternatives (@explore subag  1787625421168
ses_fc9391a1 deepseek-v3/default  Find efficient token decode (@explore subagent)  1787625399791
ses_fc93d984 x-preview-f-free/max  Example code changes in llms-from-scratch.md  1787625105343
ses_fceb9428 x-preview-f-free/max  New session - 2026-08-24T00:58:34.745Z  1787533114745
ses_fcee1405 hy3-free/default  New session - 2026-08-24T00:14:53.858Z  1787530493858
ses_fde4b18a muse-spark-1.2-contributor-free/xhigh  git - 2026-08-21T00:24:58.967Z  1787271898967
ses_fe6d7199 hy3-free/default  Creating AGENTS.md for LLMs-from-scratch repo  1787128505955
ses_0073f8c8 kimi-k2.5/default  New session - 2026-08-13T01:33:09.881Z  1786584789881
... (余 10 条见 §4 分布)
DIST: deepseek-v4-flash-free/max 14, glm-4.5-air/default 3, muse-spark/default 2, muse-spark/xhigh 1, hy3/default 2, x-preview/max 2
```

### A8 账户与日志
```
auth providers: opencode, openrouter, Alibaba (China), github-models, alibaba-cn (5 api, key 已脱敏)
opencode providers: OpenCode Zen api ●, OpenRouter api ●, Alibaba api ●, github-models api ●
log ~/.local/share/opencode/log/opencode.log 最近 200 行 grep reasoning/variant/effort -> 无报错 (仅 INFO)
supermemory.json: autoRecallEveryPrompt false, compactionThreshold 0.8
```

### A9 Agent 与权限
```
build agent: 无私有 model/variants, permission * allow, doom_loop ask, question deny, plan_enter/plan_exit deny
# opencode debug agent build 同 §4，无覆盖
```

### A10 对比方法
```bash
# 另一台执行 §5.7 一键脚本 > /tmp/other-machine.txt 后，与本附录逐段 diff
diff -u <(cat 本附录A1) <(other A1)  # 版本/指纹
diff -u <(cat 本附录A5) <(other A5)  # 合并配置最关键: 有无 provider.opencode.variants
# 重点: A5 中 provider.opencode 不存在即为本机缺陷
```
