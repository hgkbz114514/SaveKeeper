"""生成应用图标 assets/icon.ico（无需外部素材）。

设计：深蓝圆角方块 + 白色存档卡片 + 金色对勾，16~256 多尺寸。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZES = [16, 24, 32, 48, 64, 128, 256]
BG = (32, 62, 129, 255)      # 深蓝
CARD = (245, 248, 255, 255)  # 近白
ACCENT = (255, 190, 60, 255)  # 金


def render(size: int) -> Image.Image:
    s = size * 8  # 超采样后缩小，边缘平滑
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 背景圆角方块
    pad = int(s * 0.04)
    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=int(s * 0.22), fill=BG)

    # 存档卡片（略斜的矩形）
    cx0, cy0 = int(s * 0.24), int(s * 0.20)
    cx1, cy1 = int(s * 0.76), int(s * 0.74)
    d.rounded_rectangle([cx0, cy0, cx1, cy1], radius=int(s * 0.06), fill=CARD)

    # 卡片上的两条“文字”线
    line_w = int(s * 0.05)
    for i, frac in enumerate((0.34, 0.46)):
        y = int(s * frac)
        d.rounded_rectangle(
            [int(s * 0.33), y, int(s * (0.67 - i * 0.10)), y + line_w],
            radius=line_w // 2,
            fill=(150, 165, 200, 255),
        )

    # 金色对勾（覆盖右下角，暗示“已校验 / 安全”）
    pts = [
        (int(s * 0.56), int(s * 0.62)),
        (int(s * 0.66), int(s * 0.73)),
        (int(s * 0.86), int(s * 0.45)),
    ]
    d.line(pts, fill=ACCENT, width=int(s * 0.10), joint="curve")

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    out_dir = Path(__file__).parent / "assets"
    out_dir.mkdir(exist_ok=True)
    ico = out_dir / "icon.ico"
    frames = [render(sz) for sz in SIZES]
    frames[-1].save(ico, format="ICO", sizes=[(sz, sz) for sz in SIZES])

    # 顺便存一张 512 PNG 给 GitHub / README 用
    render(512).save(out_dir / "icon.png", format="PNG")
    print(f"已生成 {ico} （{len(SIZES)} 个尺寸）")
    print(f"已生成 {out_dir / 'icon.png'}")


if __name__ == "__main__":
    main()
