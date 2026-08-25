# 双网卡分流修复指南（以太网 + WiFi）

> 适用环境：Windows 11 + WSL2 镜像网络 + Clash Verge(规则模式)
> 编写日期：2026-08-25，基于当日实测数据

---

## 0. 背景与目标架构

### 线路情况

| 线路 | 出口 | 特性 |
|---|---|---|
| 以太网（联通宽带，192.168.16.240 → 网关 192.168.16.1） | 60.10.16.41 河北廊坊联通 | 免费额度、2.5GbE；国内快，国际被阻断（机场/GitHub/opencode.ai/models.dev 等）|
| WLAN（移动，DHCP 10.167.63.x → 网关 10.167.63.169） | 移动网络 | 计费限额；可达机场服务器和境外 |

### 问题

默认路由有效 metric：WLAN=45 < 以太网=281 → **全部流量烧 WiFi 限额**。

### 目标架构

```
普通流量（国内站/DIRECT规则/CLI工具）──默认路由(metric 25)──→ 以太网 [免额度]
Clash隧道 → 机场IP(/32静态路由) ──────────────────────────→ WiFi  [仅代理流量]
拔网线：默认路由失效自动回落WiFi；插回自动恢复
```

物理约束：机场服务器（`*.the-best-airport.com:443`）只有移动线可达，
故"代理出境流量"必然消耗 WiFi 额度，此部分无法省略。

---

## 1. 如何修改

> 全部命令需在**管理员 PowerShell**中执行
> （Win+X → 终端(管理员)，或右键 PowerShell → 以管理员身份运行）

### 第 1 步：持久化接口优先级

```powershell
# 接口级 metric 写入网卡配置，重启不丢
Set-NetIPInterface -InterfaceAlias '以太网' -AddressFamily IPv4 -InterfaceMetric 25
Set-NetIPInterface -InterfaceAlias 'WLAN'  -AddressFamily IPv4 -InterfaceMetric 10000

# 路由级 metric 双保险（防 DHCP 重建默认路由时回弹）
Set-NetRoute -DestinationPrefix '0.0.0.0/0' -InterfaceAlias '以太网' -RouteMetric 0
Set-NetRoute -DestinationPrefix '0.0.0.0/0' -InterfaceAlias 'WLAN'  -RouteMetric 5000
```

改后有效值：以太网 = 0+25 = **25**，WLAN = 5000+10000 = **15000**

### 第 2 步：机场服务器静态路由（/32 精确制导）

```powershell
# 从订阅配置解析全部节点域名并加 /32 路由指向 WiFi 网关
$cfg = "$env:APPDATA\io.github.clash-verge-rev.clash-verge-rev\profiles\RKrj6pOcOgGF.yaml"
$hosts_ = Select-String -Path $cfg -Pattern "server:\s*(\S*\.the-best-airport\.com)" |
          ForEach-Object { $_.Matches[0].Groups[1].Value } | Sort-Object -Unique

foreach ($h in $hosts_) {
    $ip = (Resolve-DnsName $h -Type A -ErrorAction SilentlyContinue).IPAddress |
          Select-Object -First 1
    if ($ip) {
        New-NetRoute -DestinationPrefix "$ip/32" -InterfaceAlias 'WLAN' `
                     -NextHop '10.167.63.169' -RouteMetric 1 -PolicyStore PersistentStore
        Write-Host "已添加: $ip/32 ($h)"
    }
}
```

要点：
- `/32` 最长前缀匹配必然压过默认路由，只劫持发往机场的包
- `PersistentStore` 重启后仍生效
- Clash 本体**不要**用 ForceBindIP 启动（历史遗留手段，已弃用）

### 第 3 步：验证

| # | 命令 | 期望结果 |
|---|---|---|
| V1 | `Get-NetRoute -DestinationPrefix '0.0.0.0/0'` | 以太网有效25，WLAN有效15000 |
| V2 | `(Find-NetRoute -RemoteIPAddress 223.5.5.5)[1]` | NextHop = 192.168.16.1 |
| V3 | `(Find-NetRoute -RemoteIPAddress <机场IP>)[1]` | NextHop = 10.167.63.169 |
| V4 | 清华镜像下载测速 + 网卡计数器 | 以太网增长，WLAN 不动 |
| V5 | 经 `127.0.0.1:7897` 访问境外 + opencode 对话 | 正常 |

快速测速：

```powershell
# 国内直连(应走有线)
curl.exe -o NUL -w "%{speed_download} B/s`n" -r 0-10485760 `
  "https://pypi.tuna.tsinghua.edu.cn/packages/69/72/20cb30f3b39a9face296491a86adb6ff8f1a47a897e4d14667e6cf89d5c3/torch-2.5.1-cp313-cp313-manylinux1_x86_64.whl"
# 经代理出境(应走WiFi隧道)
curl.exe -x http://127.0.0.1:7897 -o NUL -w "%{http_code}`n" https://www.google.com
```

---

## 2. 日常维护：机场换 IP 后刷新路由

订阅更新后节点域名解析可能变化 → 表现为"国内正常、代理全挂"。
以管理员运行：

```powershell
# 删除旧的机场静态路由
Get-NetRoute -DestinationPrefix '*/32' -InterfaceAlias 'WLAN' |
  Where-Object { $_.NextHop -eq '10.167.63.169' } | Remove-NetRoute -Confirm:$false

# 重新执行上面【第 2 步】的解析+添加循环即可
```

想自动化可注册计划任务（登录时 + 每 6 小时）执行同一段脚本。

---

## 3. 如何恢复（完整回滚）

```powershell
# 1) 删除全部机场静态路由
Get-NetRoute -DestinationPrefix '*/32' -InterfaceAlias 'WLAN' |
  Where-Object { $_.NextHop -eq '10.167.63.169' } | Remove-NetRoute -Confirm:$false

# 2) 恢复自动 metric（回到系统自管状态）
Set-NetIPInterface -InterfaceAlias '以太网' -AutomaticMetric Enabled
Set-NetIPInterface -InterfaceAlias 'WLAN'  -AutomaticMetric Enabled

# 3) 如需恢复 WiFi 的 IPv6（本方案曾禁用）
Enable-NetAdapterBinding -Name 'WLAN' -ComponentID ms_tcpip6
```

回滚后即回到"系统自动跃点"的原始状态。

---

## 4. 故障速查表

| 症状 | 原因 | 处理 |
|---|---|---|
| 国内能上、代理全挂 | 机场换 IP，静态路由失配 | 执行第 2 节刷新 |
| 全部断网 | 网线断且 WiFi 也异常 | 检查两条线路；临时回滚第 3 节 |
| 流量又在烧 WiFi 限额 | Windows 大更新重置了 metric | 重跑第 1 步（30 秒）|
| opencode 报 "not available in your country" | 流量未过代理（直连中国 IP）| 检查 Clash 运行、V2/V6 |
| WSL 里 curl 正常但浏览器不通 | Clash 内核假死 | Verge 里重启内核 |

## 5. 已知限制

1. 代理出境流量必须消耗 WiFi 额度（机场只认移动线，物理决定）
2. 联通线对 GitHub 主站、opencode.ai、models.dev、sst.dev 等境外站阻断依旧存在——这些站经隧道访问即可
3. 若未来机场提供联通可达的中转入口，可删除静态路由让代理也走有线（收益只会更好）
