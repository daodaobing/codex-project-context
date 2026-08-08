"""轻量「路径/文本 -> 类别」推断，项目无关。

类别只用于规则匹配打分，不包含任何业务假设。
"""

from __future__ import annotations

# 路径片段 -> 类别（高优先级）
_PATH_PATTERNS = {
    "auth": ["auth", "login", "user", "account", "session", "invite", "referral", "device"],
    "server": ["server", "ssh", "connect", "remote", "host", "tunnel"],
    "database": ["db", "database", "schema", "sql", "migration", "drizzle", "entitlement", "contract"],
    "ui": ["design", "ui", "component", "style", "theme"],
    "map": ["map", "grid", "overlay", "sprite", "mask", "fog"],
    "exploration": ["explore"],
    "release": ["release", "deploy", "staging", "acceptance", "desktop", "operations"],
    "api": ["api", "contract", "route", "endpoint"],
    "security": ["security", "secret"],
    "plans": ["plan", "roadmap"],
    "architecture": ["architecture"],
    "modules": ["module"],
    "changelog": ["changelog", "change"],
}

# 文档角色识别规则（按优先级匹配，先命中先得）
_ROLE_RULES = [
    ("architecture", ["architecture.md", "/architecture/"]),
    ("modules", ["modules.md", "/modules/"]),
    ("design", ["design-system", "design.md", "docs/design", "/design/"]),
    ("decisions", ["decisions.md", "docs/decisions", "docs/plans", "/plans/", "/decisions/"]),
    ("contracts", ["docs/contracts", "/contracts/"]),
    ("operations", ["docs/operations", "/operations/"]),
    ("changelog", ["changelog.md", "/changelog"]),
    ("security", ["docs/security", "/security/"]),
    ("release", ["docs/release", "/release/"]),
]


def doc_role(path: str) -> str:
    """统一识别文档角色：architecture/modules/design/decisions/contracts/operations/..."""
    lower = path.lower()
    for role, tokens in _ROLE_RULES:
        if any(tok in lower for tok in tokens):
            return role
    return "other"

# 文本关键词 -> 类别（低优先级）
_TEXT_PATTERNS = {
    "auth": ["auth", "login", "登录", "认证", "鉴权", "账号", "account", "session", "会话", "token",
             "权限", "permission", "password", "密码", "oauth", "sso", "device", "设备", "invite",
             "邀请", "referral", "推荐"],
    "server": ["server", "ssh", "服务器", "连接", "connection", "remote", "远程", "tunnel", "主机",
               "host", "端口", "port", "credential", "凭证", "agent"],
    "database": ["database", "db", "schema", "sql", "migration", "迁移", "orm", "drizzle", "room",
                 "query", "查询", "entitlement", "contract", "数据", "data"],
    "ui": ["design", "design-system", "页面", "page", "ui", "界面", "style", "样式", "component",
           "组件", "layout", "布局", "theme", "主题", "form", "表单", "button", "按钮", "dialog",
           "modal", "弹窗", "card", "卡片", "icon", "图标", "颜色", "color", "字体", "font"],
    "map": ["map", "地图", "迷雾", "fog", "mask", "overlay", "覆盖", "reveal", "brush", "擦除",
            "sprite", "角色", "小人", "grid", "网格", "tile", "瓦片", "marker", "location", "定位",
            "gps", "coordinate", "坐标", "move", "移动", "position", "位置", "城市", "city"],
    "exploration": ["explore", "exploration", "探索", "记录", "record", "距离", "distance",
                    "progress", "进度", "采样", "sample", "传感器", "sensor"],
    "release": ["release", "发布", "deploy", "部署", "acceptance", "验收", "staging", "preproduction",
                "production", "desktop", "打包", "channel", "渠道", "下载", "download", "upload",
                "上传", "r2", "cloudflare", "wrangler", "版本", "version", "上线"],
    "api": ["api", "endpoint", "路由", "route", "request", "请求", "response", "响应", "http",
            "rest", "webhook", "contract", "契约", "服务端", "backend", "服务"],
    "security": ["security", "安全", "secret", "密钥", "encrypt", "加密", "password", "密码",
                 "credential", "凭证", "审计", "audit"],
    "plans": ["plan", "计划", "roadmap", "decision", "决策", "milestone", "里程碑", "acceptance", "验收"],
    "architecture": ["architecture", "架构", "整体"],
    "modules": ["module", "模块"],
    "changelog": ["changelog", "变更日志", "版本记录"],
    "operations": ["operations", "运维", "操作手册", "运行", "staging", "preproduction"],
}


def categories_for(path: str, text: str = "") -> list[str]:
    """根据路径与文本推断类别，返回去重后的类别列表。"""
    cats: list[str] = []
    lower_path = path.lower()
    lower_text = (text or "").lower()
    for cat, tokens in _PATH_PATTERNS.items():
        if any(tok in lower_path for tok in tokens):
            cats.append(cat)
    for cat, tokens in _TEXT_PATTERNS.items():
        if any(tok in lower_text for tok in tokens):
            cats.append(cat)
    return sorted(set(cats))
