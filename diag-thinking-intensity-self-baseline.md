# 思考强度不可调 - 本机(Live)基线 供另一台对比用 (反向分发)

> 生成: 2026-08-27 12:15 本机 WSL2 Ubuntu | opencode 1.18.21 (npm) sha c9485f62 | 用途: 本文件为**本机Live快照**，直接发给另一台(可调)那台，按 §5 同命令本地跑一遍后与本文件附录A逐段 diff 即可定位差异 | 脱敏已处理，apiKey截断
> 与旧基线差异: 旧基线为 1.18.23 + curl bin(177M) + Alibaba provider + 5 auth；本Live为 1.18.21 + npm bin(184M) + 无 provider + 仅 openrouter auth。但核心缺陷一致：无 provider.opencode.variants 覆盖，靠内置 reasoning_options 生成。

## 1 环境 (本机live不可调侧)

```
opencode version: 1.18.21
os: Linux 6.18.33.2-microsoft-standard-WSL2 x64
terminal: vscode 1.134.0 / xterm-256color
bin: /usr/local/bin/opencode -> ../lib/node_modules/opencode-ai/bin/opencode.exe (184498304, Aug 23 14:21)
bin cache: /home/zmz/.cache/opencode/bin/rg (5445512)
cache models.json: /home/zmz/.cache/opencode/models.json (4317537, Aug 26 23:10) sha ea5fa66e3c7ffdec
paths:
  home  /home/zmz
  data  /home/zmz/.local/share/opencode
  cache /home/zmz/.cache/opencode
  config /home/zmz/.config/opencode
  bin   /home/zmz/.cache/opencode/bin
  log   /home/zmz/.local/share/opencode/log
plugins:
  - file:///home/zmz/.config/opencode/plugins/caveman/plugin.js
  - file:///home/zmz/.opencode/.opencode/plugins/graphify.js
  - file:///home/zmz/.opencode/plugins/graphify.js
  (无 opencode-supermemory@latest，旧基线有)
node: v22.22.1 / npm 9.2.0 / uv 0.12.5
```

## 2 配置原文 (本机已脱敏, live精确)

### 2.1 `~/.config/opencode/opencode.json` (104B, mode 600)
```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["./plugins/caveman/plugin.js"]
}
```

### 2.2 `~/.config/opencode/opencode.jsonc`
```
不存在 (旧基线有 268B含 supermemory+permission)
```

### 2.3 `~/.config/opencode/tui.json` / `tui.jsonc`
```
不存在 (无自定义 keybinds/theme) -> variant_cycle 未显式绑定，与旧基线一致
```

### 2.4 `~/.config/opencode/settings.json`
```
不存在 (旧基线为 {} 3B)
```

### 2.5 `~/.opencode/opencode.json` (全局残留, 105B)
```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": [".opencode/plugins/graphify.js"]
}
```

### 2.6 `opencode debug config` 合并后 ( /tmp/full.json live )
```
providers: []   # 空，旧基线为 ['Alibaba (China)'] ; 注意 opencode Zen 为内置 provider，不在此列，需看 auth.json
model: None
small_model: None
plugin: ['file:///home/zmz/.config/opencode/plugins/caveman/plugin.js','file:///home/zmz/.opencode/.opencode/plugins/graphify.js','file:///home/zmz/.opencode/plugins/graphify.js']
permission: 无显式，仅 agent 默认 * allow
# 关键: 无 provider.opencode.models.<id>.variants 覆盖 (与旧基线一致，核心缺陷)
# 无 disabled_providers/enabled_providers/experimental/snapshot/autoupdate/mcp -> 均默认 (与旧基线一致)
```

### 2.7 `~/.local/share/opencode/auth.json` (仅 provider 名, key已截断)
```
openrouter (api) key=sk-or-v1-e758...2b2 (已截断)
# 旧基线有 5 个: opencode, openrouter, Alibaba (China), github-models, alibaba-cn
# live仅 1 个，opencode Zen 未登录 -> 可能影响 xhigh 权限，需另一台对比
```

## 3 模型能力 (本机 `~/.cache/opencode/models.json` opencode provider, live sha ea5fa66e)

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
| nemotron-3.5-lightning-free | true | [] |

> 结论同旧基线: muse-spark 理论支持5档，需 variant 层透传 reasoningEffort 才会显；mimo/nemotron 为 []属预期不可调。

live原始:
```json
muse-spark-1.2-contributor-free: {"id":"muse-spark-1.2-contributor-free","reasoning":true,"reasoning_options":[{"type":"effort","values":["minimal","low","medium","high","xhigh"]}],"provider":{"npm":"@ai-sdk/openai"}}
```

## 4 运行痕迹 (本机 `~/.local/share/opencode/opencode.db` session.model, live仅4条)

```
按 (id, variant, providerID) 计数:
x-preview-f-free / opencode / max      2  (1787466295616, 1787466544080)
hy3-free / opencode / high             1  (1787652651974)
muse-spark-1.2-contributor-free / opencode / xhigh 1  (1787803020204 latest)
# 旧基线有 deepseek-v4-flash/max 14, glm/default 3 等更多历史；live库已精简，但同样证实曾切到 xhigh/high/max，现部分回落
```

现象印证同旧基线: `opencode models opencode` 仅显示基模名 `opencode/muse-spark-1.2-contributor-free` 无 `(high)` 后缀，符合“直选模型、无强度选项”描述；变体在选中模型后的 variant 维度，需 `variant_cycle` 循环。需另一台提供 TUI 截图对比是否同样。

## 5 给另一台的对比方法 (收到本文件后，另一台本地跑同命令 diff)

### 5.1 另一台本地自检命令 (与本附录A同标题，原样复跑)

```bash
# 5.1 版本与路径
opencode --version; echo "---"; opencode debug info 2>&1 | head -n 30; echo "---"; opencode debug paths 2>&1 | head -n 20
which opencode; ls -l $(which opencode) 2>&1 | head -n 5; sha256sum $(which opencode) 2>&1 | cut -c1-16; node -v 2>&1; npm -v 2>&1; ls -l ~/.cache/opencode/models.json 2>&1; sha256sum ~/.cache/opencode/models.json 2>&1 | cut -c1-16

# 5.2 配置原文
cat ~/.config/opencode/opencode.json; echo "==="; cat ~/.config/opencode/opencode.jsonc 2>&1; echo "==="; cat ~/.config/opencode/tui.json 2>&1; cat ~/.config/opencode/tui.jsonc 2>&1; echo "==="; cat ~/.config/opencode/settings.json 2>&1
cat ~/.opencode/opencode.json 2>&1
ls -la ~/.config/opencode/ 2>&1 | head -n 30; ls -la opencode.json* tui.json* .opencode 2>&1 | head -n 30

# 5.3 合并后配置 (关键看是否有 provider.opencode.variants)
opencode debug config 2>&1 > /tmp/full.json
python3 -c "import json; d=json.load(open('/tmp/full.json')); print('providers:', list(d.get('provider',{}).keys())); print('model:', d.get('model')); import json as j; print(j.dumps(d.get('provider',{}).get('opencode',{}), indent=2, ensure_ascii=False)[:4000])"
cat /tmp/full.json | grep -i variant

# 5.4 模型能力
python3 -c "import json; d=json.load(open('/home/zmz/.cache/opencode/models.json')); print(json.dumps(d['opencode']['models'].get('muse-spark-1.2-contributor-free'), indent=2, ensure_ascii=False))"
opencode models opencode 2>&1 | tr ',' '\n' | grep -E "spark|hy3|mimo|nemotron"

# 5.5 运行痕迹
python3 << 'PY'
import sqlite3, json, collections
con=sqlite3.connect('/home/zmz/.local/share/opencode/opencode.db'); cur=con.cursor()
cur.execute('SELECT model FROM session'); c=collections.Counter()
for r in cur.fetchall():
    try: o=json.loads(r[0]); c[(o.get('id'), o.get('variant'), o.get('providerID'))]+=1
    except: pass
for k,v in sorted(c.items(), key=lambda x:-x[1])[:20]: print(k,v)
PY

# 5.6 环境/托管/账户/日志 (潜在遗漏11项)
env | grep -E "OPENCODE|ANTHROPIC|OPENAI" | sort; echo "---managed---"; ls -la /etc/opencode 2>&1 | head -n 20; ls -la "/Library/Application Support/opencode" 2>&1 | head -n 20
cat ~/.local/share/opencode/auth.json 2>&1 | python3 -m json.tool 2>&1 | grep -v '"key"'
tail -n 100 ~/.local/share/opencode/log/opencode.log 2>&1 | grep -iE "reasoning|variant|effort|warn|error" | tail -n 20
opencode debug agent build 2>&1 | head -n 60
```

### 5.2 一键采全脚本 (另一台复制整段执行，与本附录A逐段 diff)
```bash
# 保存为 /tmp/collect_other.sh 后 bash /tmp/collect_other.sh > /tmp/other-live.txt 2>&1
echo "===5.1 version==="; opencode --version; which opencode; sha256sum $(which opencode) 2>&1 | cut -c1-16; node -v 2>&1; npm -v 2>&1; opencode debug info 2>&1 | head -n 30; opencode debug paths 2>&1 | head -n 20; ls -l ~/.cache/opencode/models.json; sha256sum ~/.cache/opencode/models.json 2>&1 | cut -c1-16
echo "===5.3 config==="; cat ~/.config/opencode/opencode.json 2>&1; echo "---jsonc---"; cat ~/.config/opencode/opencode.jsonc 2>&1; echo "---tui---"; cat ~/.config/opencode/tui.json 2>&1; cat ~/.config/opencode/tui.jsonc 2>&1; cat ~/.config/opencode/settings.json 2>&1; echo "---opencode---"; cat ~/.opencode/opencode.json 2>&1; echo "---project---"; ls -la opencode.json* tui.json* .opencode 2>&1 | head -n 30
echo "===5.4 merged==="; opencode debug config 2>&1 > /tmp/full.json; python3 -c "import json; d=json.load(open('/tmp/full.json')); print('providers',list(d.get('provider',{}).keys())); print('model',d.get('model'),'small',d.get('small_model'))"; python3 -c "import json; d=json.load(open('/tmp/full.json')); print(json.dumps(d.get('provider',{}).get('opencode',{}),indent=2,ensure_ascii=False)[:5000])"; cat /tmp/full.json | grep -i variant
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
echo "===5.8 env/auth==="; env | grep -E "OPENCODE" | sort; cat ~/.local/share/opencode/auth.json 2>&1 | python3 -m json.tool 2>&1 | grep -v '"key"'
```

### 5.3 diff 方法 (另一台本地与本文件对比)
```bash
# 假设本文件为 /tmp/self-baseline.md (本机live)
diff -u <(grep -A2 "providers:" /tmp/self-baseline.md) <(cat /tmp/other-live.txt | grep -A2 "providers")
diff -u <(sed -n '/### A1/,/### A2/p' diag-thinking-intensity-self-baseline.md) <(cat /tmp/other-live.txt | sed -n '/===5.1/,/===5.3/p')
# 最关键: A5 merged 中 provider.opencode 不存在即为本机缺陷，若另一台存在即定因
```

## 6 判定 (另一台与本机live对比)

| 另一台差异 | 定因 | 本机修复 (待你确认后执行) |
|------------|------|---------------------------|
| 另一台 `opencode debug config` 含 `provider.opencode.models.muse-spark-1.2-contributor-free.variants` | **缺显式 variants 覆盖** | 补 §6.1 片段 (推荐) |
| 另一台 `tui.json` 含 `keybinds.variant_cycle` | **快捷键未绑定致不可达** | 补 §6.2 |
| 另一台 `opencode --version` >1.18.21 或 `sha` 不同 (curl vs npm) | **版本/构建差** | `opencode upgrade` + 删 `~/.cache/opencode/models.json` 重拉 |
| 另一台 `models.json sha/reasoning_options` 不同 (本机 ea5fa66e 4317537) | **缓存差异** | 同上重拉 |
| 另一台 `OPENCODE_CONFIG*` 非空或 `/etc/opencode` 存在 | **环境/托管劫持** | 清理变量/托管文件 |
| 另一台 `./opencode.json` 存在 | **项目级覆盖** | 对齐项目配置 |
| 另一台 `auth.json` 含 `opencode` 且在线 (本机仅 openrouter) | **账户/配额差** | `opencode auth login` 重登 Zen |
| 均无差异但另一台 UI 有后缀 (minimal/low/medium/high/xhigh) | **操作路径误解** | 培训: 选中模型后用 `variant_cycle` |

### 6.1 修复片段 (本机拟加, 发给另一台验证有效后再合并)
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

## 7 验证 (修复后, 两台同跑)
```bash
opencode debug config 2>&1 | grep -A30 '"muse-spark'
# TUI: /models 选中 muse-spark 行应显示 (minimal/low/medium/high/xhigh)
# 新会话 sqlite: SELECT model FROM session ORDER BY time_created DESC LIMIT 1 应含 "variant":"high"
```

## 附录A 本机Live完整原始输出 (另一台逐段 diff 用, 已脱敏, 2026-08-27 12:15)

> 另一台按 §5.1 同命令执行后与本附录逐段对比，若某段不同即为疑点。本节为可直接对比的基线。

### A1 安装与版本指纹
```
1.18.21
/usr/local/bin/opencode -> ../lib/node_modules/opencode-ai/bin/opencode.exe
-rwxr-xr-x 2 root root 184498304 Aug 23 14:21 /usr/local/lib/node_modules/opencode-ai/bin/opencode.exe
sha256: c9485f62576606db (截断16位, 完整见 sha256sum /usr/local/lib/node_modules/opencode-ai/bin/opencode.exe)
node v22.22.1
npm 9.2.0
uv 0.12.5 (x86_64-unknown-linux-gnu)
vscode 1.134.0 x64 terminal xterm-256color
os: Linux jianyanyuan4 6.18.33.2-microsoft-standard-WSL2 #1 SMP PREEMPT_DYNAMIC Thu Jun 18 21:54:43 UTC 2026 x86_64 GNU/Linux
opencode debug info: 1.18.21 / Linux 6.18.33.2-microsoft-standard-WSL2 x64 / vscode 1.134.0 / xterm-256color / plugins: file:///home/zmz/.config/opencode/plugins/caveman/plugin.js, file:///home/zmz/.opencode/.opencode/plugins/graphify.js, file:///home/zmz/.opencode/plugins/graphify.js
paths: home /home/zmz, data /home/zmz/.local/share/opencode, bin /home/zmz/.cache/opencode/bin, cache /home/zmz/.cache/opencode, config /home/zmz/.config/opencode, log /home/zmz/.local/share/opencode/log
models.json: 4317537 Aug 26 23:10 sha ea5fa66e3c7ffdec
```

### A2 环境变量 (全量, 含代理)
```
OPENCODE=1
OPENCODE_PID=343427
http_proxy=http://172.26.112.1:7890
https_proxy=http://172.26.112.1:7890
# 无 OPENCODE_CONFIG / OPENCODE_CONFIG_CONTENT / OPENCODE_CONFIG_DIR / OPENCODE_TUI_CONFIG / OPENCODE_CALLER
# 无 no_proxy (旧基线有 no_proxy 含 tsinghua/openrouter/dashscope)
# 完整 env 无 _EXTENSION_OPENCODE_PORT (旧基线有 64553)
```

### A3 托管/远端/项目级配置
```
ls /etc/opencode -> No such file or directory
ls /Library/Application Support/opencode -> No such file or directory
ls ~/.config/opencode -> 7项, 仅 opencode.json(104B), 无 opencode.jsonc/tui.json/settings.json
  total 56 drwxr-xr-x 7 zmz ... opencode.json 104B
ls ./opencode.json* / ./tui.json* / ./.opencode -> 均 No such file or directory (项目根无覆盖)
ls ~/.opencode -> 5项: .gitignore, bin/, node_modules/, opencode.json(105B), package-lock.json, package.json, plugins/
opencode debug config well-known/managed 命中: 无
```

### A4 全局配置原文 (精确)
```jsonc
// ~/.config/opencode/opencode.json (104B, mode 600)
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["./plugins/caveman/plugin.js"]
}
// ~/.config/opencode/opencode.jsonc 不存在
// ~/.config/opencode/tui.json* 不存在
// ~/.config/opencode/settings.json 不存在
// ~/.opencode/opencode.json (105B)
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": [".opencode/plugins/graphify.js"]
}
// ~/.config/opencode/package.json {"dependencies":{"@opencode-ai/plugin":"1.17.9"}}
```

### A5 合并后配置 (opencode debug config /tmp/full.json live, 关键)
```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["file:///home/zmz/.config/opencode/plugins/caveman/plugin.js","file:///home/zmz/.opencode/.opencode/plugins/graphify.js","file:///home/zmz/.opencode/plugins/graphify.js"],
  "plugin_origins": [{"spec":"file:///home/zmz/.config/opencode/plugins/caveman/plugin.js","source":"/home/zmz/.config/opencode","scope":"global"},{"spec":"file:///home/zmz/.opencode/.opencode/plugins/graphify.js","source":"/home/zmz/.opencode/opencode.json","scope":"global"},{"spec":"file:///home/zmz/.opencode/plugins/graphify.js","source":"/home/zmz/.opencode","scope":"global"}],
  "agent": {"cavecrew-investigator":{"model":"haiku",...},"cavecrew-builder":{...},"cavecrew-reviewer":{...}},
  "mode": {},
  "command": {"caveman":..., "caveman-stats":...},
  "username": "zmz"
  // 无 provider 字段 -> 空对象 {} (providers [])
  // 无 disabled_providers/enabled_providers/experimental/instructions/snapshot/autoupdate -> 均默认
  // 无 provider.opencode -> 关键: 无 variants 覆盖，靠内置 reasoning_options 生成 (与旧基线一致)
}
providers: []
model: None
small_model: None
grep variant -> 无命中
```

### A6 模型能力 (models.json live, opencode provider, 93 models)
```json
// sha ea5fa66e3c7ffdec 4317537 2026-08-26
muse-spark-1.2: {"id":"muse-spark-1.2","reasoning":true,"reasoning_options":[{"type":"effort","values":["minimal","low","medium","high","xhigh"]}],"provider":{"npm":"@ai-sdk/openai"}}
muse-spark-1.2-contributor-free: {"id":"muse-spark-1.2-contributor-free","reasoning":true,"reasoning_options":[{"type":"effort","values":["minimal","low","medium","high","xhigh"]}],"provider":{"npm":"@ai-sdk/openai"}}
hy3-free: {"id":"hy3-free","reasoning":true,"reasoning_options":[{"type":"toggle"},{"type":"effort","values":["low","medium","high"]}] }
mimo-v2.5-free: {"id":"mimo-v2.5-free","reasoning":true,"reasoning_options":[]}
nemotron-3-ultra-free: {"id":"nemotron-3-ultra-free","reasoning":true,"reasoning_options":[]}
nemotron-3.5-lightning-free: {"id":"nemotron-3.5-lightning-free","reasoning":true,"reasoning_options":[]}
gpt-5.3-codex-spark: {"id":"gpt-5.3-codex-spark","reasoning":true,"reasoning_options":[{"type":"effort","values":["low","medium","high","xhigh"]}],"provider":{"npm":"@ai-sdk/openai"}}
deepseek-v4-flash-free: {"id":"deepseek-v4-flash-free","reasoning":true,"reasoning_options":[{"type":"effort","values":["low","high","max"]}] }
x-preview-f-free: {"id":"x-preview-f-free","reasoning":true,"reasoning_options":[{"type":"effort","values":["low","high","max"]}] }
// opencode models opencode -> opencode/muse-spark-1.2-contributor-free (裸名无后缀), opencode/hy3-free, opencode/mimo-v2.5-free 等
```

### A7 运行痕迹 (opencode.db live, 4条)
```
muse-spark-1.2-contributor-free/xhigh provider=opencode time=1787803020204 latest
hy3-free/high provider=opencode time=1787652651974
x-preview-f-free/max provider=opencode time=1787466544080
x-preview-f-free/max provider=opencode time=1787466295616
DIST: x-preview/max 2, hy3/high 1, muse-spark/xhigh 1
# 旧基线 DIST: deepseek/max 14, glm/default 3, muse-spark/default 2, muse-spark/xhigh 1 等
```

### A8 账户与日志
```
auth.json providers: openrouter (api) key=sk-or-v1-e758...2b2 (已截断，仅1个)
# 旧基线 5个；live未登录 opencode Zen -> 另一台若已登录且可调，可能为配额差
opencode providers list: (仅提示 help，无已登录 opencode)
log ~/.local/share/opencode/log/opencode.log 最近 200 行 grep reasoning/variant/effort -> 无报错 (仅 INFO permission ask)
示例: stream providerID=opencode modelID=muse-spark-1.2-contributor-free llm.runtime=ai-sdk
```

### A9 Agent 与权限
```
build agent: 名称 build, 无私有 model/variants, permission * allow, doom_loop ask, external_directory ask (allow /tmp/opencode/* 等 7 条)
# 与旧基线一致，无 agent 级覆盖
opencode debug agent build 同 §4，无覆盖
```

### A10 对比方法 (另一台与本Live对比)
```bash
# 另一台执行 §5.2 一键脚本 > /tmp/other-live.txt 后，与本附录逐段 diff
diff -u <(sed -n '/### A1/,/### A2/p' diag-thinking-intensity-self-baseline.md) <(grep -A30 "===5.1 version===" /tmp/other-live.txt)
diff -u <(sed -n '/### A5/,/### A6/p' diag-thinking-intensity-self-baseline.md) <(grep -A30 "===5.4 merged===" /tmp/other-live.txt)
# 重点: A5 中 provider.opencode 不存在即为本机缺陷；若另一台存在 variants 即为可调原因
# 次重点: auth.json 是否含 opencode (live仅 openrouter)
```

