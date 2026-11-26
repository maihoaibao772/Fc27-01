#!/usr/bin/env python3
"""
miao_color.py — Tiêm bộ lọc màu nhẹ (DM_MIAO_COLOR) vào Metal present shader của host nxnative.
  DM_MIAO_COLOR=0 : Mặc định (Gốc — không can thiệp)
  DM_MIAO_COLOR=1 : Cỏ tươi sống động (tăng nhẹ độ rực màu & tương phản, hợp màn OLED)
  DM_MIAO_COLOR=2 : Điện ảnh (tương phản sâu hơn, hơi ấm nhẹ)
Credit: miaomiaochinhmau
"""

import argparse
import re
from pathlib import Path


COLOR_HELPER_METAL = r"""
// === MIAO_COLOR_FILTER (miaomiaochinhmau) ===
static inline float4 miao_apply_color_grade(float4 c, int mode) {
    if (mode == 0) return c;
    float3 rgb = max(c.rgb, float3(0.0));
    float luma = dot(rgb, float3(0.2126, 0.7152, 0.0722));
    if (mode == 1) {
        // Cỏ tươi sống động (Vivid): tăng nhẹ saturation + contrast vừa phải
        rgb = mix(float3(luma), rgb, 1.18);
        rgb = (rgb - 0.5) * 1.06 + 0.5;
        rgb.g *= 1.03;
    } else if (mode == 2) {
        // Điện ảnh (Cinematic): tương phản sâu hơn, tông ấm nhẹ
        rgb = mix(float3(luma), rgb, 1.08);
        rgb = (rgb - 0.5) * 1.12 + 0.5;
        rgb *= float3(1.03, 1.01, 0.96);
    }
    return float4(clamp(rgb, 0.0, 1.0), c.a);
}
"""


def patch_present_shader(engine_dir: Path) -> bool:
    """
    Tìm file chứa present blit/bilinear fragment shader trong dm/gfx/nvn/
    và chèn helper miao_apply_color_grade đọc biến môi trường DM_MIAO_COLOR lúc khởi tạo.
    """
    gfx_dir = engine_dir / "dm/gfx/nvn"
    if not gfx_dir.is_dir():
        print(f"[COLOR] Không tìm thấy {gfx_dir}, bỏ qua bộ lọc màu.")
        return False

    # Tìm file .m/.c chứa chuỗi "DM_PRESENT_UPSCALE" (nơi chứa present pipeline)
    mtl_candidates = list(gfx_dir.glob("*.m")) + list(gfx_dir.glob("*.mm"))
    target_m = None
    for p in mtl_candidates:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "DM_PRESENT_UPSCALE" in txt and "fragment float4" in txt:
            target_m = p
            break

    if not target_m:
        print("[COLOR] Cảnh báo: không thấy present shader nội tuyến trong dm/gfx/nvn/*.m — bỏ qua an toàn (game vẫn build bình thường).")
        return False

    src = target_m.read_text(encoding="utf-8")
    if "MIAO_COLOR_FILTER" in src:
        print(f"[COLOR] Đã tiêm bộ lọc màu vào {target_m.name} trước đó.")
        return True

    # Tìm đoạn fragment shader present: thường nhận texture2d<float> và return tex.sample(...)
    # Tìm chữ ký fragment float4 <name>(...) { ... return ...; } gần từ khóa present/blit
    pat = re.compile(
        r"(fragment\s+float4\s+[A-Za-z0-9_]*present[A-Za-z0-9_]*\s*\([^)]*\)\s*\{)([^}]*?)(return\s+([^;]+);)",
        re.IGNORECASE | re.DOTALL,
    )
    m = pat.search(src)
    if not m:
        print("[COLOR] Không khớp chữ ký present fragment shader an toàn — bỏ qua để tránh lỗi biên dịch Metal.")
        return False

    # Chèn helper ngay trước fragment shader và đọc hằng số mode qua function constant hoặc buffer/define
    # Để tuyệt đối không phá ABI buffer binding của present pass, ta biên dịch 3 biến thể hoặc dùng define runtime:
    # Cách an toàn nhất không đổi buffer layout: tạo 2 entry point phụ (present_miao_1, present_miao_2) hoặc
    # inject trực tiếp vào chuỗi nguồn Metal trước lúc compile bằng getenv("DM_MIAO_COLOR")!
    # Kiểm tra xem chuỗi nguồn Metal nằm trong C string literal (NSString / const char*)
    env_inject_pat = re.compile(r"(newLibraryWithSource:\s*)([A-Za-z0-9_]+)(\s+options:)")
    # Ở bước tạo chuỗi nguồn Metal: ta thêm #define MIAO_COLOR_MODE ... động theo getenv("DM_MIAO_COLOR")
    # Tìm đoạn return trong present shader và bọc bằng miao_apply_color_grade(..., MIAO_COLOR_MODE)
    body_before = m.group(1) + m.group(2)
    ret_expr = m.group(4).strip()
    new_frag = (
        COLOR_HELPER_METAL.replace("\n", "\\n\"\n\"")
        if '\\n"' in m.group(0)
        else COLOR_HELPER_METAL
    )

    # Nếu shader viết trong raw C string (không có \n"), chỉ chèn trực tiếp
    if '\\n"' in m.group(0):
        print("[COLOR] Present shader đang ở dạng C-escaped string; dùng chế độ tiêm an toàn theo #define MIAO_COLOR_MODE.")
        replaced_frag = (
            m.group(1)
            + m.group(2)
            + f"float4 _mc = ({ret_expr}); "
            + "if (MIAO_COLOR_MODE > 0) { "
            + "float3 _rgb = max(_mc.rgb, float3(0.0)); "
            + "float _l = dot(_rgb, float3(0.2126, 0.7152, 0.0722)); "
            + "if (MIAO_COLOR_MODE == 1) { _rgb = mix(float3(_l), _rgb, 1.18); _rgb = (_rgb - 0.5) * 1.06 + 0.5; _rgb.g *= 1.03; } "
            + "else if (MIAO_COLOR_MODE == 2) { _rgb = mix(float3(_l), _rgb, 1.08); _rgb = (_rgb - 0.5) * 1.12 + 0.5; _rgb *= float3(1.03, 1.01, 0.96); } "
            + "_mc = float4(clamp(_rgb, 0.0, 1.0), _mc.a); } return _mc;"
        )
    else:
        replaced_frag = (
            COLOR_HELPER_METAL
            + "\n"
            + body_before
            + f"return miao_apply_color_grade(({ret_expr}), MIAO_COLOR_MODE);"
        )

    patched = src[: m.start()] + replaced_frag + src[m.end() :]

    # Gắn macro MIAO_COLOR_MODE vào MTLCompileOptions (preprocessorMacros) theo getenv("DM_MIAO_COLOR")
    if "MIAO_COLOR_MODE" not in src:
        # Tìm nơi gọi newLibraryWithSource:... options:nil hoặc options:opts
        def _add_macro(match):
            prefix, src_var, opt_part = match.group(1), match.group(2), match.group(3)
            return (
                f"{prefix}[NSString stringWithFormat:@\"#define MIAO_COLOR_MODE %d\\n%@\", "
                f"(getenv(\"DM_MIAO_COLOR\") ? atoi(getenv(\"DM_MIAO_COLOR\")) : 0), {src_var}]{opt_part}"
            )

        patched_2, cnt = env_inject_pat.subn(_add_macro, patched, count=1)
        if cnt == 0:
            print("[COLOR] Không tìm thấy newLibraryWithSource để truyền MIAO_COLOR_MODE — bỏ qua an toàn.")
            return False
        patched = patched_2

    target_m.write_text(patched, encoding="utf-8")
    print(f"[COLOR] Đã tiêm bộ lọc màu miaomiaochinhmau vào {target_m} thành công!")
    return True


def main():
    ap = argparse.ArgumentParser(description="Tiêm bộ lọc màu miaomiaochinhmau vào host Metal")
    ap.add_argument("--engine-dir", type=Path, required=True, help="Thư mục engine nxnative")
    args = ap.parse_args()
    patch_present_shader(args.engine_dir.resolve())


if __name__ == "__main__":
    main()