"""只读分享落地页渲染：将分享会话渲染为自包含的静态 HTML（无需前端登录）。"""
from __future__ import annotations

import html as _html

_BRAND = "#2f6bff"
_BG = "#f5f7fb"
_CARD = "#ffffff"
_TEXT = "#1f2430"
_MUTED = "#69707f"
_BORDER = "#e3e8f0"


def render(title: str, messages: list[dict]) -> str:
    body = []
    for m in messages:
        role = m.get("role")
        content = _html.escape(m.get("content") or "").replace("\n", "<br>")
        if role == "assistant":
            hits = m.get("hits") or []
            refs = ""
            if hits:
                items = "".join(
                    f'<li>{_html.escape(h.get("source", "未知来源"))}'
                    f'（相关度 {_html.escape(str(h.get("score", "")))}）</li>'
                    for h in hits
                )
                refs = f'<details class="refs"><summary>参考资料</summary><ul>{items}</ul></details>'
            thinking = _html.escape(m.get("reasoning") or "")
            think_html = (
                f'<details class="think"><summary>思考过程</summary><div class="think-body">{thinking.replace(chr(10), "<br>")}</div></details>'
                if thinking
                else ""
            )
            body.append(
                f'<div class="msg asst"><div class="role">助手</div>'
                f'<div class="bubble">{think_html}{content}{refs}</div></div>'
            )
        else:
            body.append(
                f'<div class="msg user"><div class="role">我</div>'
                f'<div class="bubble">{content}</div></div>'
            )
    thread = "\n".join(body) if body else '<p class="empty">（空会话）</p>'
    esc_title = _html.escape(title or "分享的会话")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc_title}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:{_BG}; color:{_TEXT}; font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
  .shell {{ max-width:760px; margin:0 auto; padding:40px 20px 60px; }}
  .bar {{ display:flex; align-items:center; gap:10px; margin-bottom:20px; color:{_MUTED}; font-size:13px; }}
  .bar .dot {{ width:10px; height:10px; border-radius:50%; background:{_BRAND}; }}
  .card {{ background:{_CARD}; border:1px solid {_BORDER}; border-radius:16px; padding:28px 24px; box-shadow:0 6px 24px rgba(20,30,60,.06); }}
  h1 {{ margin:0 0 16px; font-size:22px; line-height:1.4; font-weight:700; }}
  .msg {{ display:flex; gap:10px; margin:14px 0; }}
  .role {{ flex-shrink:0; width:46px; font-size:12px; font-weight:600; padding-top:8px; }}
  .asst .role {{ color:{_BRAND}; }}
  .user .role {{ color:{_MUTED}; }}
  .bubble {{ flex:1; background:{_BG}; border:1px solid {_BORDER}; border-radius:12px; padding:10px 14px; font-size:14px; line-height:1.7; word-break:break-word; }}
  .asst .bubble {{ background:{_CARD}; }}
  .refs {{ margin-top:8px; font-size:12px; color:{_MUTED}; }}
  .refs summary {{ cursor:pointer; }}
  .refs ul {{ margin:6px 0 0; padding-left:18px; }}
  .empty {{ color:{_MUTED}; }}
  .foot {{ margin-top:16px; text-align:center; color:{_MUTED}; font-size:12px; }}
</style>
</head>
<body>
<div class="shell">
  <div class="bar"><span class="dot"></span>MagicerRAG · 只读分享链接</div>
  <div class="card">
    <h1>{esc_title}</h1>
    <div>{thread}</div>
  </div>
  <div class="foot">由 MagicerRAG 生成 · 对话内容已由分享方授权公开</div>
</div>
</body>
</html>"""