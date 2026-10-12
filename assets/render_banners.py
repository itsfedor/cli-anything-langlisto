#!/usr/bin/env python3
"""Render README banners (light/dark) into assets/."""
import asyncio
from playwright.async_api import async_playwright

TPL = """
<!doctype html><html><head><meta charset="utf-8"><style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    width:1600px; height:420px; background:__BG__; color:__FG__;
    font-family:'Carlito','DejaVu Sans','Helvetica Neue',sans-serif;
    display:flex; align-items:center; justify-content:space-between;
    padding:0 96px; overflow:hidden;
  }
  .left { display:flex; flex-direction:column; gap:18px; }
  .kicker { font-size:20px; letter-spacing:4px; text-transform:uppercase; color:__ACCENT__; font-weight:700; }
  h1 { font-size:64px; font-weight:800; letter-spacing:-1.5px; }
  .sub { font-size:26px; color:__MUTED__; font-weight:400; }
  .by { font-size:20px; color:__MUTED__; margin-top:6px; }
  .shelf { display:flex; gap:18px; align-items:flex-end; }
  .book { width:86px; border-radius:10px 10px 4px 4px; box-shadow:0 18px 40px rgba(0,0,0,.18); }
  .b1 { height:250px; background:linear-gradient(180deg,__ACCENT__,__ACCENT2__); }
  .b2 { height:290px; background:linear-gradient(180deg,__ACCENT2__,__ACCENT__); }
  .b3 { height:220px; background:__CARD__; border:2px solid __LINE__; }
  .b4 { height:270px; background:linear-gradient(180deg,__ACCENT3__,__ACCENT2__); }
</style></head><body>
  <div class="left">
    <div class="kicker">LangListo teacher workspace</div>
    <h1>cli-anything-langlisto</h1>
    <div class="sub">Students, classes, schedule, materials &amp; books — from your terminal</div>
    <div class="by">unofficial · MIT · by Fedor Molodtsov</div>
  </div>
  <div class="shelf">
    <div class="book b1"></div><div class="book b2"></div><div class="book b3"></div><div class="book b4"></div>
  </div>
</body></html>
"""

LIGHT = dict(BG="#f6f7f9", FG="#111418", MUTED="#5b6470",
             ACCENT="#0f64f5", ACCENT2="#268afa", ACCENT3="#7fb3fb",
             CARD="#ffffff", LINE="#d9dee6")
DARK = dict(BG="#0d1117", FG="#f0f3f6", MUTED="#9aa4b2",
            ACCENT="#4d8dff", ACCENT2="#7fb3fb", ACCENT3="#123c7a",
            CARD="#161b22", LINE="#2b3440")


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1600, "height": 420}, device_scale_factor=2)
        for name, cfg in [("banner-light", LIGHT), ("banner-dark", DARK)]:
            html = TPL
            for k, v in cfg.items():
                html = html.replace(f"__{k}__", v)
            await pg.set_content(html)
            await pg.wait_for_timeout(250)
            el = await pg.query_selector("body")
            path = f"/root/hermes/github-work/build/cli-anything-langlisto/assets/{name}.png"
            await el.screenshot(path=path)
            print("saved", path)
        await b.close()

asyncio.run(main())
