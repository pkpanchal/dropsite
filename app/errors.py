"""Branded, self-contained error page (Coral theme). Used by the global
exception handlers and by the site-serving 404 fallback."""

FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="8" fill="#EF6A43"/>'
    '<path d="M16 23V10M10.5 15.5 16 10l5.5 5.5" fill="none" stroke="#fff" '
    'stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)

_MESSAGES = {
    404: ("Page not found", "We couldn't find what you were looking for. The link may be wrong, or the site may have been removed."),
    403: ("No access", "You don't have permission to view this."),
    500: ("Something went wrong", "An unexpected error occurred on our side. Please try again in a moment."),
}


def error_page_html(code: int, message: str | None = None) -> str:
    title, default_msg = _MESSAGES.get(code, ("Error", "Something went wrong."))
    body = message or default_msg
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{code} · Dropsite</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
<style>
  *{{box-sizing:border-box;}}
  body{{margin:0; min-height:100vh; background:#FBF7F0; color:#2A2118;
       font-family:'Hanken Grotesk',sans-serif; display:flex; align-items:center;
       justify-content:center; padding:24px; text-align:center;}}
  .wrap{{max-width:460px;}}
  .mark{{width:46px; height:46px; border-radius:13px; background:#EF6A43; display:flex;
         align-items:center; justify-content:center; margin:0 auto 26px;}}
  .code{{font:500 13px/1 'JetBrains Mono',monospace; letter-spacing:.18em; color:#B7A48E;
         text-transform:uppercase;}}
  h1{{font:800 30px/1.15 'Hanken Grotesk',sans-serif; letter-spacing:-.02em; margin:12px 0 0;}}
  p{{font:400 16px/1.55 'Hanken Grotesk',sans-serif; color:#867866; margin:10px 0 0;}}
  a.btn{{display:inline-block; margin-top:26px; font:700 14px/1 'Hanken Grotesk',sans-serif;
         color:#fff; background:#EF6A43; text-decoration:none; padding:13px 22px;
         border-radius:99px; box-shadow:0 12px 26px -12px rgba(239,106,67,.9);}}
</style></head>
<body><div class="wrap">
  <div class="mark"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V6"/><path d="m5 12 7-7 7 7"/></svg></div>
  <div class="code">Error {code}</div>
  <h1>{title}</h1>
  <p>{body}</p>
  <a class="btn" href="/">Back to Dropsite</a>
</div></body></html>"""
