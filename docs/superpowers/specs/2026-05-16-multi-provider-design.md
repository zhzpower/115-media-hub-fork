# 多网盘扩展设计

**日期**: 2026-05-16
**分支**: feat/multi-provider
**目标**: 新增天翼云盘、123云盘、阿里云盘支持，同时建立 Provider 抽象层，便于后续扩展。

---

## 1. 架构概览

### 1.1 当前问题

- `app/providers/pan115.py` 和 `quark.py` 各自独立，无统一接口
- `core.py` 中硬编码 `COOKIE_HEALTH_PROVIDERS = ("115", "quark")`、`DEFAULT_MOUNT_POINTS` 等
- `normalize_subscription_provider` 仅支持 2 路分支
- 路由、服务、前端散落大量 `if provider == "115"` / `elif provider == "quark"` 链条
- 前端 provider 选择器、配置表单全部硬编码

### 1.2 目标架构

```
app/providers/
├── base.py          # CloudProvider 抽象基类
├── registry.py      # 全局注册表
├── common.py        # 共用工具（已有）
├── pan115.py        # 115 实现
├── quark.py         # 夸克实现
├── tianyi.py        # 天翼云盘 [新]
├── pan123.py        # 123云盘 [新]
├── aliyun.py        # 阿里云盘 [新]
├── pansou.py        # 搜索聚合（不动）
└── tmdb.py          # TMDB（不动）
```

所有新增 provider 注册到 registry，core.py 通过 registry 而非硬编码名访问 provider。

---

## 2. Provider 抽象层

### 2.1 CloudProvider 基类

```python
class CloudProvider:
    # === 元数据 ===
    name: str              # "tianyi"
    label: str             # "天翼云盘"
    link_type: str         # URL pattern 对应的 link_type

    # === 认证 ===
    auth_type: str         # "cookie" | "oauth2" | "refresh_token"
    config_keys: List[str] # 需要的配置字段，如 ["cookie_tianyi"]

    # === 能力声明 ===
    supports_folder_browse: bool = True
    supports_share_receive: bool = True
    supports_offline: bool = False
    supports_fixed_share_link: bool = False
    supports_strm: bool = False
    supports_monitor: bool = False

    # === 限流 ===
    rate_limit_seconds: float = 0.0

    # === 核心 API（子类实现） ===
    def list_entries(self, cookie, cid, offset, limit, **kwargs) -> dict
    def create_folder(self, cookie, cid, name) -> dict
    def resolve_folder_id_by_path(self, cookie, path) -> str
    def ensure_folder_id_by_path(self, cookie, path) -> str

    def resolve_share_payload(self, cookie, share_url, raw_text, receive_code) -> dict
    def list_share_entries(self, cookie, share_payload, cid, offset, limit) -> dict
    def prepare_share_receive(self, cookie, share_payload, cid) -> dict
    def submit_share_receive(self, cookie, receive_payload, files) -> dict

    def submit_offline_task(self, cookie, resource_url, folder_id) -> dict  # 可选
    def probe_connectivity(self, cookie) -> bool
```

### 2.2 注册表

```python
# app/providers/registry.py
_providers: Dict[str, CloudProvider] = {}

def register(p: CloudProvider) -> None
def get(name: str) -> CloudProvider
def get_by_link_type(link_type: str) -> Optional[CloudProvider]
def list_all() -> List[CloudProvider]
def list_enabled() -> List[CloudProvider]      # 仅已启用
def get_capabilities() -> List[dict]            # 给前端的清单
```

Provider 在模块加载时自注册：

```python
# app/providers/tianyi.py
from .base import CloudProvider
from .registry import register

class TianyiProvider(CloudProvider):
    name = "tianyi"
    label = "天翼云盘"
    # ...

register(TianyiProvider())
```

### 2.3 能力检查替代 if/elif

```python
# 旧：if provider != "115": share_link_url = ""
# 新：
p = get_provider(provider_name)
if not p.supports_fixed_share_link:
    share_link_url = ""
```

---

## 3. 认证模型

### 3.1 三种认证类型

| auth_type | 字段 | 网盘 | 说明 |
|-----------|------|------|------|
| `cookie` | `cookie_xxx` | 115, 夸克, 123 | 浏览器 Cookie 字符串 |
| `oauth2` | `cookie_tianyi` | 天翼云盘 | Cookie + OAuth2 SSO 换 AccessToken，1-2h 过期需定时刷新 |
| `refresh_token` | `aliyun_refresh_token` | 阿里云盘 | 手机扫码获取 refresh_token，非 Cookie |

### 3.2 设置页 UI 适配

- `auth_type=cookie`：单个 textarea + "从浏览器复制 Cookie" 提示
- `auth_type=oauth2`：textarea + 状态指示器（AccessToken 有效期倒计时）
- `auth_type=refresh_token`：单行 input + "获取 token" 外链按钮（`https://aliyuntoken.vercel.app/`）

### 3.3 Cookie 健康检查

`probe_connectivity` 由每个 provider 自行实现，COOKIE_HEALTH_PROVIDERS 从 registry 动态获取。

---

## 4. 设置页 Provider 启用/关闭

### 4.1 配置持久化

`settings.json` 新增：

```json
{
  "provider_enabled": {
    "115": true,
    "quark": true,
    "tianyi": false,
    "123pan": false,
    "aliyun": false
  }
}
```

115 和 quark 默认 true（兼容现有用户），新网盘默认 false。

### 4.2 设置页 UI

「网盘认证」section 内每个网盘一个折叠块：

```
┌─ 网盘认证 ──────────────────────────────────────┐
│  ▸ 115网盘  [══════ ● 已启用]  Cookie: ****      │
│  ▸ 夸克网盘 [══════ ● 已启用]  Cookie: ****      │
│  ▸ 天翼云盘 [══════ ○ 未启用]                    │
│  ▸ 123云盘  [══════ ○ 未启用]                    │
│  ▸ 阿里云盘 [══════ ○ 未启用]                    │
└──────────────────────────────────────────────────┘
```

展开后：认证输入区 + 能力标签（分享转存/离线下载/STRM）+ 健康状态。

### 4.3 关闭网盘的联动

- 订阅任务：该 provider 跳过扫描周期，配置保留
- 导入历史：保留
- 收藏目录：保留
- 监控任务：自动暂停
- Cookie：保留不清空
- 前端确认弹窗：列出受影响的任务数量

### 4.4 `/api/providers` 端点

```json
[
  {"name": "115", "label": "115网盘", "auth_type": "cookie", "enabled": true,
   "supports_offline": true, "supports_fixed_share_link": true,
   "supports_strm": true, "supports_monitor": true},
  {"name": "tianyi", "label": "天翼云盘", "auth_type": "oauth2", "enabled": false, ...}
]
```

前端只在 UI 中展示 `enabled: true` 的 provider。

---

## 5. 路由统一

### 5.1 文件夹浏览

旧路由保留兼容，新增统一路由：

```
GET  /resource/browse?provider=xxx&cid=xxx
POST /resource/browse/create-folder?provider=xxx
GET  /resource/browse/share?provider=xxx&url=xxx&code=xxx
POST /resource/browse/share/receive?provider=xxx
```

### 5.2 资源导入

现有 `/resource/import` 端点改为从 URL 自动识别 provider：

1. `detect_resource_link_type(url)` 获取 link_type
2. `registry.get_by_link_type(link_type)` 获取 provider
3. 调用 provider 对应方法

---

## 6. 前端改造

### 6.1 核心原则

**不改布局，只改数据来源。** 所有硬编码 provider 列表改为从 `/api/providers` 动态渲染。

### 6.2 改造清单

| 文件 | 位置 | 改动 |
|------|------|------|
| `templates/partials/pages/resource.html:20-23` | provider 过滤按钮 | 动态生成 |
| `templates/partials/pages/settings.html:284-346` | cookie 输入框 + 健康卡片 | 按 provider 列表动态生成折叠块 |
| `templates/partials/modals/subscription.html:17-22` | 订阅 provider 下拉 | 动态 option |
| `static/js/modules/resource/core.js:137-156` | linkTypeLabel 映射 | 改为启动时从 `/api/providers` 加载 |
| `static/js/modules/resource/core.js:159-165` | getResourceProviderByLinkType | 改为查 providerMeta |
| `static/js/modules/resource/core.js:294-296` | getResourceFolderApiPrefix | 改为统一路由 `/resource/browse?provider=` |
| `static/js/modules/resource/core.js:284-291` | resourceItemMatchesProviderFilter | 动态映射 |
| `static/js/modules/resource/core.js:304-307` | isProviderCookieConfigured | 动态检查 |
| `static/js/modules/tabs/settings.js:72-73` | collectSettingsPayload | 动态收集各 provider 字段 |
| `static/js/modules/tabs/settings.js:472` | cookie 健康请求 | 动态 provider 列表 |
| `static/js/modules/subscription/ui.js:154-197` | 订阅 UI provider 逻辑 | 能力驱动 |
| `static/js/modules/subscription/folders.js:325,591` | provider guard | 改为 `supports_fixed_share_link` 检查 |
| `static/js/modules/resource/import-modal.js:95,413,439` | provider 特定逻辑 | 能力驱动 |
| `static/js/modules/scraper/core.js:417-418` | 刮削 provider 列表 | 动态渲染 |

### 6.3 启动加载流程

```js
// boot.js init()
const providerList = await fetch('/api/providers').json();
window.providerMeta = providerList;  // 全局可用
```

---

## 7. PanSou → Provider 映射

在 registry 中实现一步映射：

```python
def get_by_link_type(link_type: str) -> Optional[CloudProvider]:
    for p in _providers.values():
        if p.link_type == link_type:
            return p
    return None
```

PanSou 搜索结果 → `detect_resource_link_type(url)` → `get_by_link_type()` → 找到对应 provider。

---

## 8. 每 Provider 独立限流

基类提供统一限流实现：

```python
class CloudProvider:
    rate_limit_seconds: float = 0.0
    _rate_limit_lock = threading.Lock()
    _last_request_monotonic = 0.0

    def throttle(self):
        if self.rate_limit_seconds <= 0:
            return
        with self._rate_limit_lock:
            elapsed = time.monotonic() - self._last_request_monotonic
            if elapsed < self.rate_limit_seconds:
                time.sleep(self.rate_limit_seconds - elapsed)
            self._last_request_monotonic = time.monotonic()
```

各子类只需设置 `rate_limit_seconds`，无需各自实现。

---

## 9. 能力矩阵

| 能力 | 115 | 夸克 | 天翼 | 123 | 阿里 |
|------|:---:|:---:|:---:|:---:|:---:|
| auth_type | cookie | cookie | oauth2 | cookie | refresh_token |
| 文件夹浏览 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 分享解析/转存 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 离线下载 | ✅ | ❌ | ❌ | ✅ | ❌ |
| 固定分享链接 | ✅ | ❌ | ❌ | ❌ | ❌ |
| STRM (302) | ✅ | ✅ | ✅ | ✅ | ✅ |
| 监控 | ✅ | ❌ | ❌ | ❌ | ❌ |

---

## 10. 实现阶段

### Phase 1：抽象层 + 改造 115/Quark（零回归）

1. 创建 `app/providers/base.py` — CloudProvider 基类
2. 创建 `app/providers/registry.py` — 注册表
3. 重构 `pan115.py` — 函数组织为 `Pan115Provider` 类，注册
4. 重构 `quark.py` — 函数组织为 `QuarkProvider` 类，注册
5. `core.py` 引入 registry，保留旧函数名转发
6. 新增 `/api/providers` 端点
7. 新增 `provider_enabled` 配置 key + 默认值

### Phase 2：前端数据驱动改造（UI 不变）

8. 前端启动加载 `/api/providers`
9. 所有硬编码 provider 列表改为动态渲染
10. 设置页改为动态折叠块 + 启用开关
11. 订阅/导入/刮削的 provider 选择器改为能力驱动
12. 统一路由 `/resource/browse`

### Phase 3：新增 3 个网盘

#### 13. 天翼云盘 (`tianyi.py`)
- API 端点：`cloud.189.cn` / `api.cloud.189.cn`
- 认证：OAuth2 SSO → AccessToken（需定时刷新）
- 分享：解析分享页 → 获取文件列表 → 转存
- 注意：AccessToken 1-2h 过期，需在每次 API 调用前检查刷新

#### 14. 123云盘 (`pan123.py`)
- API 端点：`www.123pan.com` / `www.123684.com` / `www.123865.com` / `www.123912.com`
- 认证：Cookie
- 分享：解析分享页 → 文件列表 → 转存
- 离线：`submit_offline_task` 支持 magnet/BT

#### 15. 阿里云盘 (`aliyun.py`)
- API 端点：`api.aliyundrive.com` / `openapi.aliyundrive.com`
- 认证：refresh_token（手机扫码获取）
- 分享：解析分享页 → 文件列表 → 转存
- 注意：需区分资源库和备份盘；OpenAPI 有调用频率限制

---

## 11. core.py 向后兼容策略

Phase 1 改造后，`core.py` 保留旧函数名转发，避免破坏现有调用方：

```python
# core.py 底部，provider import 区域
from .providers.registry import get as _get_provider

def list_115_entries(cookie, cid, **kwargs):
    return _get_provider("115").list_entries(cookie, cid, **kwargs)

def list_quark_entries(cookie, cid, **kwargs):
    return _get_provider("quark").list_entries(cookie, cid, **kwargs)
# ...
```

Phase 2 逐步将路由/服务中的直接调用迁移为 `get_provider(name).method()`。Phase 3 新 provider 不走转发层，直接用 registry。

---

## 12. STRM 接口预留

STRM 302 播放后续实现，但现在预留接口定义：

```python
class CloudProvider:
    supports_strm: bool = False

    def resolve_download_url(self, cookie, file_id) -> str:
        """返回文件的 302 直链 URL，子类按需实现"""
        raise NotImplementedError
```

`app/routes/strm.py` 未来改造为：根据 provider name → registry → `resolve_download_url()` → 302 redirect。当前 115 的 RSA 解密逻辑保留在 `Pan115Provider.resolve_download_url` 中。

---

## 13. 错误隔离

- 每个 provider 的 API 调用异常在 registry dispatch 层捕获
- 一个 provider 故障不影响其他 provider 的正常使用
- `/api/providers` 返回中增加 `healthy: true/false` 字段
- 前端对 unhealthy provider 显示警告但不隐藏
